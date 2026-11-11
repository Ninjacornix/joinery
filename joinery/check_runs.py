"""Seeded runs of the game for the accessibility and performance checks.

Both checks play the game with the playtest bot (ADR 0038) and ask it to measure too
(``--probe``, `joinery/playtest_bot/bot_probe.gd`):

* headless in stock Godot: the game's logic, scene and UI, fast and deterministic;
* in a window (the Joinery editor binary with ``JOINERY_BACKGROUND=1``, so it never takes
  focus), for what only drawing shows: frame times with rendering, draw calls,
  primitives, video memory, shader compilations, and the picture's luminance for
  flashes. It is skipped where nothing can draw, and the check says so.

Nothing is written inside the game: runs live under ``.joinery/playtests/``.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import playtest

#: Game seconds each run plays.
HEADLESS_SECONDS = 30.0
WINDOW_SECONDS = 20.0
#: The seed both runs share, so a finding can be replayed.
SEED = 4242


@dataclass
class Run:
    """One run's numbers: the probe's, the bot's frame times and the playtest report."""

    probe: dict[str, Any] = field(default_factory=dict)
    perf: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    seed: int = SEED
    windowed: bool = False


def _read(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        bot = json.loads((Path(report["out"]) / "bot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return {}, {}
    probe = bot.get("probe") if isinstance(bot, dict) else None
    perf = bot.get("perf") if isinstance(bot, dict) else None
    return (
        probe if isinstance(probe, dict) else {},
        perf if isinstance(perf, dict) else {},
    )


def headless(
    project: Path, kind: str, *, seconds: float = HEADLESS_SECONDS, seed: int = SEED
) -> Run:
    """Play headless in stock Godot with the probe on."""
    try:
        report = playtest.run(
            playtest.Options(project=project, seconds=seconds, seed=seed, probe=kind)
        )
    except (OSError, ValueError) as exc:
        return Run(error=f"The game couldn't be played headless: {exc}", seed=seed)
    probe, perf = _read(report)
    if not probe:
        return Run(
            report=report,
            seed=seed,
            error="The game didn't finish its headless run, so it wasn't measured: "
            + playtest_summary(report),
        )
    return Run(probe=probe, perf=perf, report=report, seed=seed)


def windowed(
    project: Path,
    kind: str,
    *,
    seconds: float = WINDOW_SECONDS,
    seed: int = SEED,
    luma: bool = False,
) -> Run:
    """Play the same seed in a window that never takes focus, with the probe on."""
    found = playtest.window_binary()
    if found is None:
        return Run(
            error="No display to draw on, so nothing drawn was measured.", seed=seed
        )
    binary, extra = found
    project = project.resolve()
    out = playtest._run_dir(project)
    options = playtest.Options(
        project=project, seconds=seconds, seed=seed, probe=kind, luma=luma
    )
    command = [
        str(binary),
        "--path",
        str(project),
        "--fixed-fps",
        "60",
        "--resolution",
        "1280x720",
        "--disable-vsync",
        "--script",
        str(playtest.BOT),
        "--",
        *playtest._bot_args(options, out),
    ]
    env = playtest._env(out, extra)
    try:
        console, code, froze, _last = playtest._play(
            command, env, seconds * 8 + 120, None, seconds, None
        )
    except OSError as exc:
        return Run(error=f"The windowed run didn't start: {exc}", seed=seed)
    (out / "console.log").write_text(console, encoding="utf-8")
    probe, perf = _read({"out": str(out)})
    if not probe or froze is not None:
        return Run(
            seed=seed,
            windowed=True,
            error=f"The windowed run didn't finish (exit {code}); see {out}/console.log.",
        )
    return Run(
        probe=probe, perf=perf, report={"out": str(out)}, seed=seed, windowed=True
    )


def playtest_summary(report: dict[str, Any]) -> str:
    items = report.get("items", [])
    serious = [
        str(i.get("detail", ""))
        for i in items
        if isinstance(i, dict) and i.get("severity") in {"critical", "high"}
    ]
    return serious[0] if serious else str(report.get("status", "no report"))


#: An empty scene: what drawing anything at all costs on this machine in a window.
_EMPTY_PROJECT = """config_version=5

[application]

config/name="Joinery baseline"
run/main_scene="res://main.tscn"
"""
_EMPTY_SCENE = """[gd_scene format=3]

[node name="Main" type="Node3D"]

[node name="Camera" type="Camera3D" parent="."]
"""


def baseline(project: Path, seconds: float = 6.0) -> dict[str, float]:
    """Frame times of an empty scene in the same window, cached per binary.

    A window costs something to draw whatever is in it (and an editor binary more than
    an exported game); the performance check takes this off before scaling a game's
    frame times to the target.
    """
    found = playtest.window_binary()
    if found is None:
        return {}
    binary = found[0]
    try:
        stat = binary.stat()
    except OSError:
        return {}
    key = f"{binary}:{stat.st_size}:{int(stat.st_mtime)}"
    cache = project / ".joinery" / "checks" / "baseline.json"
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return {k: float(v) for k, v in cached["frame_ms"].items()}
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    with tempfile.TemporaryDirectory(prefix="joinery-baseline-") as folder:
        empty = Path(folder)
        (empty / "project.godot").write_text(_EMPTY_PROJECT, encoding="utf-8")
        (empty / "main.tscn").write_text(_EMPTY_SCENE, encoding="utf-8")
        run = windowed(empty, "perf", seconds=seconds)
        frame = {
            name: float(run.perf.get(f"frame_ms_{name}", 0) or 0)
            for name in ("p50", "p95", "p99")
        }
    if run.error or not frame["p50"]:
        return {}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "frame_ms": frame}), encoding="utf-8")
    return frame
