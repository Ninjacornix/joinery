"""Headless playtests: play a game for a while with a bot and report what went wrong.

``joinery playtest --project P`` runs the game in stock Godot 4.7, headless and offline,
with the bot in ``joinery/playtest_bot`` driving it through the game's own input map
(ADR 0038). The run is deterministic for a seed: the bot's every choice comes from it and
the game runs at a fixed 60 physics frames a second, so ``--seed`` replays a run. A run
never writes inside the game: the bot's report, screenshots, the game's ``user://`` and its
settings all live in ``.joinery/playtests/<id>/``, which Godot never imports.

The bot maps the level as it plays and explores toward the parts it hasn't been to; the
report says what share of the navigable area it reached and marks the rest on a top-down
map (``coverage.png`` in the run's folder). ``--reach`` names places to head for first
(nodes whose names hold the words), and the report says whether, and when, it got there.

Screenshots need rendering, so they come from a second, windowed replay of the same seed
that saves a picture at each notable moment the headless run found. It runs in the
Joinery editor binary with ``JOINERY_BACKGROUND=1`` where there is one, so the window never
takes focus, and is skipped where nothing can draw (Linux without a display).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from joinery import playtest_heat, playtest_report
from joinery.catalog import ROOT

BOT = Path(__file__).resolve().parent / "playtest_bot" / "bot.gd"
STOCK_GODOT = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"
FOLDER = Path(".joinery") / "playtests"
#: Runs kept per project; older ones are removed.
KEEP = 10
DEFAULT_SECONDS = 120.0
MAX_SECONDS = 1800.0
#: Real seconds with no heartbeat from the bot before the game counts as frozen.
FREEZE_SECONDS = 45.0
SHOTS_LIMIT = 8
#: Words dropped from a place to reach: node names don't carry them.
_ARTICLES = frozenset({"the", "a", "an"})
#: The replay ``--record`` keeps in the run's folder.
REPLAY_NAME = "play.replay"

Progress = Callable[[float, float], None]


@dataclass
class Options:
    project: Path
    seconds: float = DEFAULT_SECONDS
    seed: int = 0
    scene: str = ""
    focus: str = ""
    softlock: float = 15.0
    screenshots: bool = False
    #: A blockout's routes (JSON): the bot only walks them (ADR 0043).
    route: Path | None = None
    out: Path | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    #: Play an exported game's pack (the .pck a build ships) instead of the project.
    pack: Path | None = None
    #: Measure the game too: "perf" or "access" (``joinery perf`` / ``joinery access``).
    probe: str = ""
    #: Record each rendered frame's luminance (flash measurement; windowed runs only).
    luma: bool = False
    #: Places to reach first: words naming nodes ("key room" finds KeyRoom).
    reach: list[str] = field(default_factory=list)
    #: Have the game core record the run as a replay (``<out>/play.replay``, ADR 0059).
    record: bool = False

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["project"] = str(self.project)
        data["route"] = str(self.route) if self.route else ""
        data.pop("out")
        data.pop("extra_env")
        data.pop("pack")
        if self.pack is not None:
            data["pack"] = str(self.pack)
        return data


def config(project: Path) -> dict[str, Any]:
    """``[agents]`` playtest settings: ``playtest_after_change`` (false), ``playtest_minutes`` (2)."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    agents = raw.get("agents", {})
    agents = agents if isinstance(agents, dict) else {}
    after = agents.get("playtest_after_change", False)
    minutes = agents.get("playtest_minutes", DEFAULT_SECONDS / 60)
    if (
        not isinstance(minutes, (int, float))
        or isinstance(minutes, bool)
        or minutes <= 0
    ):
        minutes = DEFAULT_SECONDS / 60
    return {
        "playtest_after_change": after is True,
        "playtest_minutes": min(float(minutes), MAX_SECONDS / 60),
    }


def godot_binary() -> Path:
    """The Godot that plays: stock 4.7 where Joinery ships one, else the Joinery editor.

    ``JOINERY_PLAYTEST_GODOT`` and then ``JOINERY_TEST_GODOT`` override it.
    """
    for name in ("JOINERY_PLAYTEST_GODOT", "JOINERY_TEST_GODOT"):
        value = os.environ.get(name)
        if value and Path(value).is_file():
            return Path(value)
    if STOCK_GODOT.is_file():
        return STOCK_GODOT
    from joinery.distribution import engine_binary

    return engine_binary()


def window_binary() -> tuple[Path, dict[str, str]] | None:
    """The Godot for the screenshot replay and its environment, or None when nothing draws."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return None
    env = {"JOINERY_BACKGROUND": "1"}
    try:
        from joinery.distribution import engine_binary

        return engine_binary(), env
    except ValueError:
        pass
    try:
        return godot_binary(), env
    except ValueError:
        return None


def _run_dir(project: Path) -> Path:
    root = project / FOLDER
    root.mkdir(parents=True, exist_ok=True)
    runs = sorted(p for p in root.iterdir() if p.is_dir())
    for old in runs[: max(0, len(runs) - KEEP + 1)]:
        shutil.rmtree(old, ignore_errors=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / f"{stamp}-{secrets.token_hex(3)}"
    path.mkdir()
    return path


def _env(out: Path, extra: dict[str, str]) -> dict[str, str]:
    """A HOME of the run's own: the game's saves and settings never touch the player's."""
    home = out / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "GODOT_SILENCE_ROOT_WARNING": "1",
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "share"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "APPDATA": str(home / "appdata"),
        **extra,
    }


def _bot_args(
    options: Options, out: Path, *, shots: list[int] | None = None
) -> list[str]:
    args = [
        f"--playtest-out={out}",
        f"--seed={options.seed}",
        f"--seconds={options.seconds:g}",
        f"--softlock={options.softlock:g}",
        f"--settings-path={out / 'settings.cfg'}",
        f"--saves-dir={out / 'saves'}",
        # The game core seeds the game's own random numbers from it (ADR 0059).
        f"--joinery-seed={options.seed}",
    ]
    if options.record and not shots:
        args.append(f"--joinery-record={out / REPLAY_NAME}")
    if options.scene:
        args.append(f"--scene={options.scene}")
    words = [
        w
        for w in "".join(c if c.isalnum() else " " for c in options.focus).split()
        if len(w) > 2
    ]
    if words:
        args.append("--focus=" + ",".join(words[:8]).lower())
    places = reach_words(options.reach)
    if places:
        args.append("--reach=" + ",".join(places))
    if shots:
        args.append("--shots=" + ",".join(str(f) for f in shots))
    if options.route is not None:
        args.append(f"--route={options.route}")
    if options.probe:
        args.append(f"--probe={options.probe}")
    if options.luma:
        args.append("--luma")
    return args


def reach_words(places: list[str]) -> list[str]:
    """Places to reach as the bot matches them against node names: "the key room" is
    "keyroom" (lower case, letters and digits, no articles)."""
    words: list[str] = []
    for place in places:
        kept = [
            "".join(c for c in part if c.isalnum())
            for part in place.lower().replace("_", " ").replace("-", " ").split()
        ]
        word = "".join(p for p in kept if p not in _ARTICLES)[:40]
        if word and word not in words:
            words.append(word)
    return words[:8]


def _ensure_imported(godot: Path, project: Path, env: dict[str, str]) -> None:
    """A project never opened in the editor has no import cache; Godot makes one first."""
    if (project / ".godot").is_dir():
        return
    subprocess.run(
        [str(godot), "--headless", "--path", str(project), "--import"],
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )


def _play(
    command: list[str],
    env: dict[str, str],
    limit: float,
    progress: Progress | None,
    total: float,
    stop: threading.Event | None,
) -> tuple[str, int | None, float | None, float]:
    """Run Godot; returns (console, exit code, froze-at seconds or None, last game second)."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    state = {"beat": time.monotonic(), "t": 0.0}

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            if line.startswith("PLAYTEST "):
                state["beat"] = time.monotonic()
                try:
                    state["t"] = float(json.loads(line[9:]).get("t", 0))
                except (ValueError, AttributeError):
                    continue
                if progress is not None:
                    progress(state["t"], total)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    started = time.monotonic()
    froze: float | None = None
    while process.poll() is None:
        now = time.monotonic()
        if stop is not None and stop.is_set():
            process.kill()
            break
        if now - state["beat"] > FREEZE_SECONDS and now - started > FREEZE_SECONDS:
            froze = state["t"]
            process.kill()
            break
        if now - started > limit:
            froze = state["t"]
            process.kill()
            break
        time.sleep(0.1)
    process.wait()
    reader.join(timeout=5)
    return "".join(lines), process.returncode, froze, state["t"]


def run(
    options: Options,
    progress: Progress | None = None,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Play once and return the report (``joinery.playtest_report.build``) plus ``out``."""
    project = options.project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"{project} is not a Godot project.")
    options.seconds = max(5.0, min(float(options.seconds), MAX_SECONDS))
    if not options.seed:
        options.seed = secrets.randbelow(900_000) + 100_000
    out = options.out or _run_dir(project)
    out.mkdir(parents=True, exist_ok=True)
    godot = godot_binary()
    env = _env(out, options.extra_env)
    if options.pack is None:
        _ensure_imported(godot, project, env)
    where = (
        ["--main-pack", str(options.pack)]
        if options.pack is not None
        else ["--path", str(project)]
    )
    command = [
        str(godot),
        "--headless",
        *where,
        "--fixed-fps",
        "60",
        "--script",
        str(BOT),
        "--",
        *_bot_args(options, out),
    ]
    console, code, froze, last_t = _play(
        command, env, options.seconds * 4 + 120, progress, options.seconds, stop
    )
    (out / "console.log").write_text(console, encoding="utf-8")
    bot = _read_bot(out / "bot.json")
    public = {**options.public(), "project": str(project)}
    report = playtest_report.build(
        bot, console, code, options=public, froze_at=froze, last_t=last_t
    )
    report["engine"] = godot.name
    report["out"] = str(out)
    report["heat"] = playtest_heat.run_heat(bot)
    if report.get("coverage") and (out / "coverage.png").is_file():
        report["coverage"]["map"] = str(out / "coverage.png")
    if options.record and (out / REPLAY_NAME).is_file():
        report["replay"] = str(out / REPLAY_NAME)
    # Saves the run made through the game's save system (ADR 0066): samples to keep.
    slots = sorted((out / "saves").glob("slot_*.json")) if (out / "saves").is_dir() else []
    if slots:
        report["saves"] = [str(slot) for slot in slots]
    if options.screenshots and bot is not None and not (stop and stop.is_set()):
        _screenshots(report, options, out, project)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _read_bot(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _screenshots(
    report: dict[str, Any], options: Options, out: Path, project: Path
) -> None:
    """Replay the seed in a window and keep a picture of each notable moment."""
    found = window_binary()
    if found is None:
        report["screenshots_note"] = "No display to draw screenshots on."
        return
    binary, extra = found
    frames: list[int] = []
    for item in report["items"]:
        if item.get("t") is not None and item["severity"] != "note":
            frames.append(max(1, int(float(item["t"]) * 60)))
    for moment in report.get("moments", []):
        # The run's last frame only when nothing else is worth a picture.
        if moment.get("label") != "end" or not frames:
            frames.append(max(1, int(moment.get("frame", 0))))
    frames = sorted({f for f in frames if f > 0})[:SHOTS_LIMIT]
    if not frames:
        return
    taken = replay_shots(options, frames, out / "shots", (binary, extra))
    report["screenshots"] = [str(p) for p in taken.values() if p.is_file()]
    for item in report["items"]:
        if item.get("t") is None:
            continue
        nearest = min(frames, key=lambda f: abs(f - int(float(item["t"]) * 60)))
        if taken[nearest].is_file() and abs(nearest - float(item["t"]) * 60) <= 60:
            item["shot"] = str(taken[nearest])


def replay_shots(
    options: Options,
    frames: list[int],
    shots_dir: Path,
    window: tuple[Path, dict[str, str]],
    resolution: str = "960x540",
) -> dict[int, Path]:
    """Replay `options.seed` in a window and save a picture at each frame (60 a second)."""
    binary, extra = window
    project = options.project.resolve()
    shots_dir.mkdir(parents=True, exist_ok=True)
    frames = sorted(set(frames))
    replay = Options(
        project=project,
        seconds=min(options.seconds, frames[-1] / 60 + 1),
        seed=options.seed,
        scene=options.scene,
        focus=options.focus,
        softlock=options.softlock,
        reach=list(options.reach),
    )
    command = [
        str(binary),
        "--path",
        str(project),
        "--fixed-fps",
        "60",
        "--resolution",
        resolution,
        # Without vsync the replay runs as fast as it can draw, not in real time.
        "--disable-vsync",
        "--script",
        str(BOT),
        "--",
        *_bot_args(replay, shots_dir, shots=frames),
    ]
    env = _env(shots_dir, {**options.extra_env, **extra})
    _play(command, env, replay.seconds * 6 + 120, None, replay.seconds, None)
    return {f: shots_dir / f"shot_{f:06d}.png" for f in frames}


async def run_async(
    options: Options, progress: Progress | None = None
) -> dict[str, Any]:
    """`run` off the event loop; cancelling the awaiting task stops the game."""
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def report_progress(t: float, total: float) -> None:
        if progress is not None:
            loop.call_soon_threadsafe(progress, t, total)

    try:
        return await asyncio.to_thread(run, options, report_progress, stop)
    except asyncio.CancelledError:
        stop.set()
        raise


def for_agent(report: dict[str, Any], limit: int = 25) -> dict[str, Any]:
    """The report as the playtest tool returns it: the items, goals and checks, trimmed."""
    keep = ("status", "seed", "seconds", "played_seconds", "style", "player", "counts")
    data: dict[str, Any] = {key: report.get(key) for key in keep}
    data["summary"] = playtest_report.summary_line(report)
    data["items"] = [
        {
            k: v
            for k, v in item.items()
            if k
            in {
                "id",
                "severity",
                "title",
                "detail",
                "where",
                "t",
                "count",
                "repro",
                "shot",
            }
        }
        for item in report.get("items", [])[:limit]
    ]
    data["goals"] = report.get("goals", {})
    data["checks"] = report.get("checks", {})
    data["perf"] = report.get("perf", {})
    coverage = report.get("coverage") or {}
    if coverage:
        data["coverage"] = {
            key: coverage.get(key)
            for key in ("percent", "visited", "navigable", "cell", "unreached", "map")
        }
    if report.get("reach"):
        data["reach"] = report["reach"]
    heat = report.get("heat") or {}
    if heat:
        data["heat"] = {
            "deaths": [d["at"] for d in heat.get("deaths", [])][:10],
            "stuck": [s["at"] for s in heat.get("stuck", [])][:10],
        }
    data["screenshots"] = report.get("screenshots", [])
    data["note"] = (
        "Timings and deaths come from a bot pressing keys with some aim, not a person; "
        "treat balance as a hint. Rerun with the same seed to reproduce."
    )
    return data


def reach_line(place: dict[str, Any]) -> str:
    """'reached at 0:42', 'not reached (closest 3.2)', 'nothing in the game is called that'."""
    if place.get("reached"):
        return f"reached at {playtest_report.stamp(float(place.get('t') or 0))}"
    if not place.get("found"):
        return "nothing in the game is called that"
    closest = place.get("closest")
    return "not reached" + (
        f" (closest {float(closest):g})" if closest is not None else ""
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    length = parser.add_mutually_exclusive_group()
    length.add_argument("--minutes", type=float, help="how long to play (default 2)")
    length.add_argument("--seconds", type=float, help="how long to play, in seconds")
    parser.add_argument(
        "--seed", type=int, default=0, help="replay a run (default: new seed)"
    )
    parser.add_argument(
        "--scene", default="", help="play this scene instead of the main one"
    )
    parser.add_argument("--focus", default="", help="words naming what to aim for")
    parser.add_argument(
        "--softlock",
        type=float,
        default=15.0,
        help="seconds without progress that count as stuck",
    )
    parser.add_argument(
        "--screenshots", action="store_true", help="replay in a window for pictures"
    )
    parser.add_argument(
        "--route",
        type=Path,
        help="only walk a blockout's routes (JSON from joinery.blockout.route)",
    )
    parser.add_argument(
        "--reach",
        action="append",
        default=[],
        help="a place to head for first (a node's name); repeat for more",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="record the run as a replay (joinery replay add keeps it as a test)",
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="don't play: where recent playtests died, got stuck and spent time",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        help="with --heatmap: how many recent playtests (default [agents] heatmap_runs)",
    )
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    project: Path = args.project
    if getattr(args, "heatmap", False):
        heat = playtest_heat.aggregate(
            project.resolve(), last=int(args.last) if args.last else None
        )
        if args.json:
            print(json.dumps(heat))
        else:
            print("\n".join(playtest_heat.summary_lines(heat)))
        return 0
    if args.seconds is not None:
        seconds = float(args.seconds)
    elif args.minutes is not None:
        seconds = float(args.minutes) * 60
    else:
        seconds = config(project)["playtest_minutes"] * 60
    if args.scene and not str(args.scene).startswith("res://"):
        print("--scene takes a res:// path.", file=sys.stderr)
        return 2
    options = Options(
        project=project,
        seconds=seconds,
        seed=int(args.seed),
        scene=str(args.scene),
        focus=str(args.focus),
        softlock=float(args.softlock),
        screenshots=bool(args.screenshots),
        route=args.route,
        reach=[str(r) for r in args.reach],
        record=bool(getattr(args, "record", False)),
    )

    def show(t: float, total: float) -> None:
        if not args.json:
            print(
                f"\rPlaying… {playtest_report.stamp(t)} of {playtest_report.stamp(total)}",
                end="",
                file=sys.stderr,
                flush=True,
            )

    try:
        report = run(options, show)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(file=sys.stderr)
    print(playtest_report.summary_line(report))
    for item in report["items"]:
        when = (
            f" at {playtest_report.stamp(item['t'])}" if item["t"] is not None else ""
        )
        where = f" ({item['where']})" if item["where"] else ""
        print(f"- [{item['severity']}] {item['title']}{when}: {item['detail']}{where}")
    coverage = report.get("coverage") or {}
    if coverage:
        print(
            f"Explored {coverage['percent']:g}% of the level"
            + (f"; map: {coverage['map']}" if coverage.get("map") else "")
        )
    for place in report.get("reach", []):
        print(f"- {place['target']}: {reach_line(place)}")
    print(f"Report: {report['out']}/report.json")
    if report.get("replay"):
        print(
            f"Replay: {report['replay']} (keep it as a test: joinery replay add <file>)"
        )
    if report.get("saves"):
        print(
            f"Saves: {len(report['saves'])} in {report['out']}/saves (keep them so later "
            f"changes must still load them: joinery saves add {report['out']})"
        )
    return 0
