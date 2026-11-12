"""Recorded play sessions as regression tests (ADR 0059).

The game core records a session (``res://core/replays/replays.gd``): every input on its
physics frame, the random seed, the start scene, the build and a state checkpoint every
second. A replay file is JSON lines: a header, ``{"f", "e": [events]}`` and
``{"f", "check": {hash, state}}`` lines, and an ``{"end"}`` line.

    joinery replay add <file> --project P [--name N]   # keep it in tests/replays/
    joinery replay run --project P [NAME…|--all]        # replay headless, report divergence
    joinery replay list --project P

A replay runs headless at the recorded physics rate (``--fixed-fps``), with the recorded
seed, settings and window size, an empty save folder and a HOME of its own, and compares
each checkpoint. On the build that recorded it the state must match exactly; on another
build (another engine, platform or CPU) the checks are compared against what the same
replay did on that build before the change (the staged-change check does this).

Every staged change that touches the game runs the project's replays when
``[checks] replays`` is on (the default once ``tests/replays/`` has any): a replay that
now diverges or errors sends the change back with the frame and what differed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from joinery.runner import Result

FORMAT = "joinery-replay"
FOLDER = Path("tests") / "replays"
SUFFIX = ".replay"
BOOT_SCRIPT = "res://core/replays/replay_boot.gd"
RESULT_PREFIX = "JOINERY_REPLAY "
#: Real seconds a replay may take per recorded game second, plus start-up.
SECONDS_PER_GAME_SECOND = 2.0
STARTUP_SECONDS = 60.0
#: Where staged-change checks keep what each replay did before the change.
BASELINES = Path(".joinery") / "replays"
#: Godot console lines that mean the game broke.
_ERROR = re.compile(r"^(SCRIPT ERROR|USER SCRIPT ERROR|ERROR:|USER ERROR)", re.MULTILINE)
_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Replay:
    """A replay file, read: its header, how long it is and what it holds."""

    path: Path
    header: dict[str, Any]
    frames: int
    events: int
    checks: int
    end: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def ticks(self) -> int:
        return int(self.header.get("physics_ticks") or 60)

    @property
    def seconds(self) -> float:
        return self.frames / max(self.ticks, 1)

    @property
    def build(self) -> str:
        return str(self.header.get("build", ""))

    @property
    def steady(self) -> bool:
        """Recorded at a fixed frame rate, so it replays frame for frame."""
        return bool(self.end.get("steady", True))

    def summary(self) -> dict[str, Any]:
        start = self.header.get("start") or {}
        return {
            "name": self.name,
            "path": str(self.path),
            "seconds": round(self.seconds, 2),
            "frames": self.frames,
            "events": self.events,
            "checks": self.checks,
            "seed": self.header.get("seed"),
            "scene": start.get("scene", ""),
            "build": self.build,
            "joinery": self.header.get("joinery", ""),
            "created": self.header.get("created", ""),
            "steady": self.steady,
            "partial": bool(self.header.get("partial", False)),
        }


@dataclass
class Outcome:
    """What one replay did: ``passed``, ``diverged``, ``errors`` or ``failed`` (didn't run)."""

    name: str
    status: str
    detail: str = ""
    frame: int | None = None
    seconds: float | None = None
    differs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    final_hash: str = ""
    checks: int = 0
    build: str = ""
    recorded_build: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def line(self) -> str:
        """One sentence for a person or an agent."""
        if self.ok:
            return f"{self.name}: passed ({self.checks} checkpoints)."
        where = (
            f" at frame {self.frame} ({self.seconds:g} s)"
            if self.frame is not None and self.seconds is not None
            else (f" at frame {self.frame}" if self.frame is not None else "")
        )
        text = f"{self.name}: {self.status}{where}: {self.detail}"
        if self.differs:
            text += " Differs: " + "; ".join(self.differs[:6]) + "."
        if self.errors:
            text += " Errors: " + "; ".join(self.errors[:3]) + "."
        return text

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def read(path: Path) -> Replay:
    """Read a replay file; raises ValueError in words when it isn't one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{path} can't be read: {exc}") from None
    header: dict[str, Any] | None = None
    frames = events = checks = 0
    end: dict[str, Any] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            raise ValueError(f"{path.name} has a line that isn't JSON.") from None
        if not isinstance(line, dict):
            continue
        if header is None:
            header = line
            continue
        if isinstance(line.get("end"), dict):
            end = line["end"]
            frames = max(frames, int(end.get("frame", 0)))
            continue
        frames = max(frames, int(line.get("f", 0)))
        if "check" in line:
            checks += 1
        elif isinstance(line.get("e"), list):
            events += len(line["e"])
    if header is None or header.get("format") != FORMAT:
        raise ValueError(f"{path.name} is not a Joinery replay.")
    return Replay(path, header, frames, events, checks, end)


def folder(project: Path) -> Path:
    return project / FOLDER


def project_replays(project: Path) -> list[Path]:
    where = folder(project)
    return sorted(where.glob("*" + SUFFIX)) if where.is_dir() else []


def config(project: Path) -> dict[str, Any]:
    """``[checks] replays``: on by default once the project keeps any replay."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    checks = raw.get("checks", {})
    checks = checks if isinstance(checks, dict) else {}
    setting = checks.get("replays")
    have = bool(project_replays(project))
    return {"replays": have if not isinstance(setting, bool) else setting and have}


def add(project: Path, source: Path, name: str = "") -> Path:
    """Copy a replay into ``tests/replays/`` (a bug report's folder works too)."""
    if source.is_dir():
        source = source / "replay.replay"
    replay = read(source)
    if replay.header.get("partial"):
        raise ValueError(
            f"{source.name} starts in the middle of a session, so it can't replay exactly. "
            "Record a shorter session."
        )
    base = _NAME.sub("-", name or source.stem).strip("-.") or "replay"
    target = folder(project) / (base.removesuffix(SUFFIX) + SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.resolve() != source.resolve():
        raise ValueError(f"tests/replays/{target.name} already exists; pick another --name.")
    if target.resolve() != source.resolve():
        shutil.copyfile(source, target)
    return target


def godot_binary() -> Path:
    """The Godot that replays: ``JOINERY_REPLAY_GODOT``, else the one playtests use."""
    value = os.environ.get("JOINERY_REPLAY_GODOT")
    if value and Path(value).is_file():
        return Path(value)
    from joinery import playtest

    return playtest.godot_binary()


def command(
    replay: Replay, project_arg: str, replay_arg: str, *, godot: str, work: str
) -> list[str]:
    """The Godot command that replays ``replay`` (``work`` holds its settings and saves)."""
    header = replay.header
    start = header.get("start") or {}
    args = [
        godot,
        "--headless",
        "--path",
        project_arg,
        "--fixed-fps",
        str(replay.ticks),
    ]
    if header.get("boot") == "script":
        args += ["--script", BOOT_SCRIPT]
    else:
        scene = str(start.get("scene", ""))
        if scene and scene != start.get("main_scene") and scene.startswith("res://"):
            args.append(scene)
    args += [
        "--",
        f"--joinery-replay={replay_arg}",
        f"--settings-path={work}/settings.cfg",
        f"--saves-dir={work}/saves",
        *[str(a) for a in start.get("args", []) if isinstance(a, str)],
    ]
    return args


def outcome(name: str, console: str, code: int | None, replay: Replay) -> Outcome:
    """Judge one run from its console: the replay's result line and Godot's errors."""
    data: dict[str, Any] | None = None
    for line in console.splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                parsed = json.loads(line[len(RESULT_PREFIX) :])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                data = parsed
    console_errors = [line.strip() for line in console.splitlines() if _ERROR.match(line)]
    if data is None:
        tail = "\n".join(console.strip().splitlines()[-8:])
        return Outcome(
            name,
            "failed",
            "The game stopped before the replay finished"
            + (f" (exit code {code})" if code else "")
            + (f": {tail}" if tail else "."),
            errors=console_errors[:10],
            build=replay.build,
            recorded_build=replay.build,
        )
    ticks = replay.ticks
    divergence = data.get("divergence") or {}
    errors = [
        f"{e.get('message', '')} ({e.get('where', '')}, frame {e.get('frame', '?')})"
        for e in data.get("errors", [])
        if isinstance(e, dict)
    ] or console_errors[:10]
    result = Outcome(
        name,
        "passed",
        checks=int(data.get("checks", 0)),
        final_hash=str(data.get("final_hash", "")),
        build=str(data.get("build", "")),
        recorded_build=str(data.get("recorded_build", "")),
    )
    if not replay.steady:
        result.note = (
            "Recorded without a fixed frame rate: frame-rate-dependent logic may not "
            "replay exactly."
        )
    if divergence:
        frame = int(divergence.get("frame", 0))
        result.status = "diverged"
        result.frame = frame
        result.seconds = round(frame / max(ticks, 1), 2)
        result.differs = [str(d) for d in divergence.get("differs", [])]
        result.detail = str(divergence.get("error", "")) or (
            "The game's state no longer matches the recording."
        )
        if result.build and result.recorded_build and result.build != result.recorded_build:
            result.note = (
                f"Recorded on {result.recorded_build}, replayed on {result.build}: "
                "another build can differ by itself."
            )
    if errors and result.status == "passed" and int(replay.end.get("errors", 0)) == 0:
        result.status = "errors"
        result.detail = "The replay hit errors the recording didn't."
        result.frame = _first_frame(data.get("errors", []))
        if result.frame is not None:
            result.seconds = round(result.frame / max(ticks, 1), 2)
    result.errors = errors[:10]
    return result


def _first_frame(errors: Iterable[Any]) -> int | None:
    for entry in errors:
        if isinstance(entry, dict) and "frame" in entry:
            return int(entry["frame"])
    return None


def _env(home: Path) -> dict[str, str]:
    from joinery import playtest

    return playtest._env(home, {})


def run(project: Path, path: Path, *, godot: Path | None = None) -> Outcome:
    """Replay one file headless on this machine and judge it."""
    project = project.resolve()
    try:
        replay = read(path)
    except ValueError as exc:
        return Outcome(path.stem, "failed", str(exc))
    if replay.header.get("partial"):
        return Outcome(replay.name, "failed", "It starts mid-session, so it can't replay.")
    binary = godot or godot_binary()
    with tempfile.TemporaryDirectory(prefix="joinery-replay-") as temp:
        work = Path(temp)
        (work / "saves").mkdir()
        (work / "settings.cfg").write_text(str(replay.header.get("settings", "")))
        env = _env(work)
        from joinery import playtest

        playtest._ensure_imported(binary, project, env)
        argv = command(
            replay, str(project), str(path.resolve()), godot=str(binary), work=str(work)
        )
        limit = STARTUP_SECONDS + replay.seconds * SECONDS_PER_GAME_SECOND
        try:
            done = subprocess.run(
                argv, env=env, capture_output=True, text=True, timeout=limit, check=False
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            return Outcome(
                replay.name,
                "failed",
                f"The replay didn't finish in {limit:.0f} s; the game may be frozen.",
                errors=[line for line in out.splitlines() if _ERROR.match(line)][:5],
            )
    return outcome(replay.name, done.stdout + done.stderr, done.returncode, replay)


def run_all(project: Path, names: Iterable[str] = ()) -> list[Outcome]:
    wanted = {n.removesuffix(SUFFIX) for n in names}
    paths = [p for p in project_replays(project) if not wanted or p.stem in wanted]
    missing = wanted - {p.stem for p in paths}
    found = [Outcome(n, "failed", f"There is no tests/replays/{n}{SUFFIX}.") for n in sorted(missing)]
    return found + [run(project, p) for p in paths]


# --- The staged-change check -----------------------------------------------------------

OfflineRun = Callable[[list[str], Path, int], Awaitable[Result]]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _script(replays: list[Replay], tag: str) -> str:
    """One shell script that replays each file in the worker, each result marked."""
    parts = []
    for replay in replays:
        work = f"/tmp/{tag}-{replay.name}"
        argv = command(
            replay,
            "/work",
            f"/work/{FOLDER.as_posix()}/{replay.path.name}",
            godot="godot",
            work=work,
        )
        settings = str(replay.header.get("settings", ""))
        parts.append(
            f"mkdir -p {_quote(work + '/saves')}; "
            f"printf '%s' {_quote(settings)} > {_quote(work + '/settings.cfg')}; "
            f"echo {_quote('REPLAY_BEGIN ' + replay.name)}; "
            + " ".join(_quote(a) for a in argv)
            + f" 2>&1; echo {_quote('REPLAY_END ' + replay.name)}"
        )
    return "; ".join(parts)


def split_runs(output: str) -> dict[str, str]:
    """Each replay's console from a worker run of ``_script``."""
    runs: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in output.splitlines():
        if line.startswith("REPLAY_BEGIN "):
            current, lines = line.split(" ", 1)[1].strip(), []
        elif line.startswith("REPLAY_END ") and current is not None:
            runs[current] = "\n".join(lines)
            current = None
        elif current is not None:
            lines.append(line)
    return runs


def fingerprint(root: Path) -> str:
    """What the game is made of, so a baseline knows when it is stale."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel.parts[0] in {".godot", ".joinery", ".git", "tests"}:
            continue
        if path.suffix in {".gd", ".tscn", ".tres", ".gdshader", ".godot", ".cfg", ".json"}:
            digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:16]


#: Brings a worker copy's import cache up to date before replaying.
IMPORT = "godot --headless --path /work --editor --import >/dev/null 2>&1; "


async def check_stage(
    project: Path,
    stage: Path,
    runner: OfflineRun,
    timeout: int = 600,
    *,
    imported: bool = False,
) -> list[Outcome]:
    """Replay the project's replays on the staged change, in the offline worker.

    The worker's Godot is another build than the editor that recorded them, so each
    replay is judged against what it did on the worker before the change (cached under
    ``.joinery/replays/``), or against its own recording when that build made it.
    """
    files = project_replays(stage)
    if not files:
        return []
    replays: list[Replay] = []
    outcomes: list[Outcome] = []
    for path in files:
        try:
            replays.append(read(path))
        except ValueError as exc:
            outcomes.append(Outcome(path.stem, "failed", str(exc)))
    if not replays:
        return outcomes
    script = ("" if imported else IMPORT) + _script(replays, "after")
    got = await runner(["sh", "-c", script], stage, timeout)
    after = split_runs(got.output)
    baseline = await _baseline(project, replays, runner, timeout)
    for replay in replays:
        console = after.get(replay.name)
        if console is None:
            outcomes.append(
                Outcome(replay.name, "failed", "The replay didn't run: " + got.output[-600:])
            )
            continue
        result = outcome(replay.name, console, None, replay)
        before = baseline.get(replay.name)
        if result.status == "diverged" and before is not None:
            result = _against_baseline(result, before)
        outcomes.append(result)
    return outcomes


def _against_baseline(after: Outcome, before: dict[str, Any]) -> Outcome:
    """A replay that diverged from its recording on this build before the change too,
    at the same frame with the same state: the change didn't do it."""
    if (
        before.get("status") == "diverged"
        and before.get("frame") == after.frame
        and before.get("differs") == after.differs
    ):
        after.status = "passed"
        after.note = (
            "Differs from its recording on this build, exactly as before the change "
            f"(frame {after.frame})."
        )
    elif before.get("status") == "passed" and after.status == "diverged":
        after.detail = "It played like the recording before this change and doesn't now."
    return after


async def _baseline(
    project: Path, replays: list[Replay], runner: OfflineRun, timeout: int
) -> dict[str, dict[str, Any]]:
    """What each replay does on the worker without the change (cached per game state)."""
    cache = project / BASELINES / f"baseline-{fingerprint(project)}.json"
    try:
        stored = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    keys = {r.name: hashlib.sha256(r.path.read_bytes()).hexdigest()[:16] for r in replays}
    missing = [r for r in replays if (stored.get(r.name) or {}).get("key") != keys[r.name]]
    if missing:
        from joinery.transactions import clone_tree

        scratch = Path(tempfile.mkdtemp(prefix="joinery-replay-base-"))
        try:
            copy = scratch / "project"
            clone_tree(project, copy)
            shutil.rmtree(copy / ".godot", ignore_errors=True)
            target = folder(copy)
            target.mkdir(parents=True, exist_ok=True)
            for replay in missing:
                shutil.copyfile(replay.path, target / replay.path.name)
            prepared = [
                Replay(target / r.path.name, r.header, r.frames, r.events, r.checks, r.end)
                for r in missing
            ]
            got = await runner(
                ["sh", "-c", IMPORT + _script(prepared, "before")], copy, timeout
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        runs = split_runs(got.output)
        for replay in missing:
            console = runs.get(replay.name)
            if console is None:
                continue
            stored[replay.name] = {
                "key": keys[replay.name],
                **outcome(replay.name, console, None, replay).as_dict(),
            }
        cache.parent.mkdir(parents=True, exist_ok=True)
        for old in cache.parent.glob("baseline-*.json"):
            if old != cache:
                old.unlink(missing_ok=True)
        cache.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    return stored


def problems_text(outcomes: list[Outcome]) -> str:
    """The send-back message for replays that failed a staged change."""
    failed = [o for o in outcomes if not o.ok]
    lines = [o.line() + (f" {o.note}" if o.note else "") for o in failed]
    return (
        "A recorded play session no longer plays the same: "
        + " ".join(lines)
        + " If the change is meant to alter this, say so; the user can record the replay again."
    )


# --- CLI -----------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    add_cmd = actions.add_parser("add", help="keep a recorded play as a test in tests/replays/")
    add_cmd.add_argument("file", type=Path, help="a .replay file, or a bug report folder")
    add_cmd.add_argument("--project", type=Path, required=True)
    add_cmd.add_argument("--name", default="", help="the replay's name in tests/replays/")
    add_cmd.add_argument("--json", action="store_true")
    run_cmd = actions.add_parser("run", help="replay headless and report where it diverges")
    run_cmd.add_argument("names", nargs="*", help="replays in tests/replays/ (default: all)")
    run_cmd.add_argument("--project", type=Path, required=True)
    run_cmd.add_argument("--all", action="store_true", help="every replay (the default)")
    run_cmd.add_argument("--file", type=Path, help="replay this file instead")
    run_cmd.add_argument("--json", action="store_true")
    list_cmd = actions.add_parser("list", help="list the project's replays")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    if not (project / "project.godot").is_file():
        print(f"{project} is not a Godot project.", file=sys.stderr)
        return 2
    if args.action == "add":
        try:
            target = add(project, args.file, args.name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        summary = read(target).summary()
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(
                f"Added {target.relative_to(project).as_posix()} "
                f"({summary['seconds']:g} s, {summary['events']} inputs, "
                f"{summary['checks']} checkpoints)."
            )
        return 0
    if args.action == "list":
        listed = []
        for path in project_replays(project):
            try:
                listed.append(read(path).summary())
            except ValueError as exc:
                listed.append({"name": path.stem, "error": str(exc)})
        if args.json:
            print(json.dumps(listed, indent=2))
        elif not listed:
            print("No replays yet. Record a play, then: joinery replay add <file>.")
        for entry in [] if args.json else listed:
            print(
                f"- {entry['name']}: "
                + (
                    entry["error"]
                    if "error" in entry
                    else f"{entry['seconds']:g} s, seed {entry['seed']}"
                )
            )
        return 0
    if args.file is not None:
        outcomes = [run(project, args.file)]
    else:
        outcomes = run_all(project, args.names)
    if args.json:
        print(json.dumps([o.as_dict() for o in outcomes], indent=2))
    elif not outcomes:
        print("No replays in tests/replays/.")
    for item in [] if args.json else outcomes:
        print(item.line() + (f" ({item.note})" if item.note else ""))
    return 0 if all(o.ok for o in outcomes) else 1
