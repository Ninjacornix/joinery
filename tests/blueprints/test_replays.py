"""Recorded play sessions and bug reports in stock Godot (ADR 0059).

A scripted session (`replay_session.gd`) is recorded by the game core on two
blueprints, replayed headless, and must end in the same state, twice. A change to the
player's speed must be caught at the first checkpoint after it matters, with what
differed; a replay with an input taken out must diverge too. A session that ends in
F8 (`report_session.gd`) must leave a bug report whose files say what happened, and
whose replay plays back.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from joinery import bug_reports, replays
from tests.blueprints.godot_run import (
    boot,
    godot_binary,
    make_project,
    needs_godot,
    run_godot,
)

HERE = Path(__file__).resolve().parent
SESSION = "res://tests/replay_session.gd"
REPORT_SESSION = "res://tests/report_session.gd"
#: The player's top speed in each blueprint, and a file that sets it.
SPEED = {
    "platformer-2d": ("game/player/player.gd", "run_speed: float = 130.0"),
    "top-down": ("game/player/player.gd", "move_speed: float = 260.0"),
}


def _project(tmp_path: Path, blueprint: str) -> Path:
    project = make_project(tmp_path, blueprint)
    for name in ("replay_session.gd", "report_session.gd"):
        shutil.copyfile(HERE / name, project / "tests" / name)
    return project


def _record(project: Path, out: Path, seed: int = 4321) -> replays.Replay:
    run_godot(
        project,
        SESSION,
        ("--settings-path={settings}", f"--joinery-record={out}", f"--joinery-seed={seed}"),
        timeout=180,
        marker="session",
    )
    return replays.read(out)


def _replay(project: Path, path: Path) -> replays.Outcome:
    binary = godot_binary()
    assert binary is not None
    return replays.run(project, path, godot=binary)


@needs_godot
@pytest.mark.parametrize("blueprint", ["platformer-2d", "top-down"])
def test_a_recorded_session_replays_identically(tmp_path: Path, blueprint: str) -> None:
    project = _project(tmp_path, blueprint)
    recorded = _record(project, tmp_path / "walk.replay")
    header = recorded.header
    assert header["seed"] == 4321
    assert header["boot"] == "script"
    assert header["start"]["scene"] == "res://core/main.tscn"
    assert header["start"]["autostart"] is True
    assert header["joinery"] and header["build"]
    assert recorded.events >= 20 and recorded.checks >= 3
    assert recorded.steady, "a --fixed-fps run is steady"

    kept = replays.add(project, tmp_path / "walk.replay", "walk")
    assert kept == project / "tests/replays/walk.replay"
    first = _replay(project, kept)
    second = _replay(project, kept)
    assert first.ok, first.line()
    assert first.checks == recorded.checks
    assert first.final_hash and first.final_hash == second.final_hash
    assert first.build == first.recorded_build

    # The game changes: the player runs faster. The first checkpoint after the walk
    # starts no longer matches, and the report says the player is elsewhere.
    path, text = SPEED[blueprint]
    script = project / path
    source = script.read_text(encoding="utf-8")
    assert text in source
    faster = text.replace("130.0", "190.0").replace("260.0", "330.0")
    script.write_text(source.replace(text, faster), encoding="utf-8")
    changed = _replay(project, kept)
    assert changed.status == "diverged", changed.line()
    assert changed.frame is not None and changed.frame % 60 == 0
    assert any(d.startswith("players:") for d in changed.differs), changed.differs
    assert "Players: changed" in changed.differs


@needs_godot
def test_a_normal_run_records_and_replays_with_its_arguments(tmp_path: Path) -> None:
    """A run the engine started (as the editor's Run does) replays without a script."""
    project = _project(tmp_path, "top-down")
    out = tmp_path / "run.replay"
    boot(
        project,
        frames=200,
        user_args=("--autostart", "--settings-path={settings}", f"--joinery-record={out}"),
    )
    recorded = replays.read(out)
    assert recorded.header["boot"] == "engine"
    assert recorded.header["start"]["args"] == ["--autostart"]
    assert recorded.checks == 3
    assert "--autostart" in replays.command(recorded, "P", "R", godot="g", work="W")
    outcome = _replay(project, out)
    assert outcome.ok, outcome.line()
    assert outcome.checks == 3


@needs_godot
def test_a_replay_with_an_input_taken_out_diverges(tmp_path: Path) -> None:
    project = _project(tmp_path, "platformer-2d")
    recorded = _record(project, tmp_path / "walk.replay")
    lines = recorded.path.read_text(encoding="utf-8").splitlines()
    pressed = next(
        i for i, line in enumerate(lines) if '"a":"move_right","d":true' in line.replace(" ", "")
    )
    tampered = tmp_path / "tampered.replay"
    tampered.write_text("\n".join(lines[:pressed] + lines[pressed + 1 :]) + "\n")
    outcome = _replay(project, tampered)
    assert outcome.status == "diverged"
    assert outcome.frame is not None and outcome.seconds == outcome.frame / 60
    assert "Players: changed" in outcome.differs
    assert "walk" not in outcome.line() and "diverged at frame" in outcome.line()


@needs_godot
def test_a_bug_report_saves_note_log_and_a_replay_that_plays(tmp_path: Path) -> None:
    project = _project(tmp_path, "platformer-2d")
    result = run_godot(
        project,
        REPORT_SESSION,
        ("--settings-path={settings}", "--joinery-seed=99"),
        timeout=180,
        marker="report",
    )
    found = re.search(r"^REPORT (.+)$", result.output, re.MULTILINE)
    assert found, result.output[-2000:]
    folder = Path(found.group(1).strip())
    assert folder.parent.name == "joinery_reports"
    assert sorted(p.name for p in folder.iterdir()) == ["log.txt", "replay.replay", "report.json"]
    data = json.loads((folder / "report.json").read_text())
    assert data["format"] == "joinery-bug-report"
    assert data["id"] == folder.name
    assert data["note"] == "The coin vanished when I jumped."
    assert data["seed"] == 99
    assert data["game"] == "Blueprint Test"
    assert data["joinery"] and data["engine"] and data["build"]
    assert data["scene"] == "res://game/level.tscn"
    assert data["seconds"] == 60 and data["frame"] > 0
    assert data["replay"] == "replay.replay"
    log = (folder / "log.txt").read_text()
    assert "A warning the report's log must carry." in log

    replay = replays.read(folder / "replay.replay")
    assert replay.header["seed"] == 99
    assert replay.frames == data["frame"]
    assert replay.header["focus"] == {"from": max(0, data["frame"] - 3600), "to": data["frame"]}
    played = _replay(project, folder / "replay.replay")
    assert played.ok, played.line()

    # Joinery finds it where the game saved it, and hands it to the debugger.
    report = bug_reports.load(folder)
    assert report.summary()["note"] == data["note"]
    text = bug_reports.debug_text(report)
    assert data["note"] in text["output"] and str(folder / "replay.replay") in text["output"]
    kept = replays.add(project, folder)
    assert kept.name == "replay.replay"
