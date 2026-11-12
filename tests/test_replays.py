"""Replays as tests, without Godot: reading files, keeping them, judging runs, the
staged-change check and the CLI (ADR 0059). Godot runs are in tests/blueprints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import cli, playtest, replays, submission
from joinery.runner import Result

HEADER = {
    "format": "joinery-replay",
    "version": 1,
    "seed": 42,
    "boot": "script",
    "build": "4.7.2-stable (official) ed1daf0bf macOS arm64",
    "joinery": "0.5.0",
    "physics_ticks": 60,
    "window": [64, 64],
    "settings": "[audio]\nmaster=0.5\n",
    "start": {
        "scene": "res://core/main.tscn",
        "main_scene": "res://core/main.tscn",
        "autostart": True,
        "args": ["--autostart"],
    },
}


def _write(path: Path, header: dict[str, Any] | None = None, end: dict[str, Any] | None = None) -> Path:
    lines = [
        header or HEADER,
        {"f": 3, "e": [{"t": "act", "a": "move_right", "d": True, "s": 1.0}]},
        {"f": 60, "check": {"hash": "aaa", "state": {}}},
        {"f": 90, "e": [{"t": "act", "a": "move_right", "d": False, "s": 0.0}, {"t": "call", "node": "Game", "method": "restart_level", "args": []}]},
        {"f": 120, "check": {"hash": "bbb", "state": {}}},
        {"end": end or {"frame": 130, "errors": 0, "steady": True}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Test Game"\n')
    return project


def _console(result: dict[str, Any]) -> str:
    return "Godot Engine v4.7\n" + replays.RESULT_PREFIX + json.dumps(result) + "\n"


def test_read_counts_frames_events_and_checks(tmp_path: Path) -> None:
    replay = replays.read(_write(tmp_path / "walk.replay"))
    assert (replay.frames, replay.events, replay.checks) == (130, 3, 2)
    assert replay.seconds == pytest.approx(130 / 60)
    assert replay.steady and replay.name == "walk"
    assert replay.summary()["seed"] == 42
    (tmp_path / "other.replay").write_text('{"format": "something"}\n')
    with pytest.raises(ValueError, match="not a Joinery replay"):
        replays.read(tmp_path / "other.replay")
    (tmp_path / "broken.replay").write_text("{not json\n")
    with pytest.raises(ValueError, match="isn't JSON"):
        replays.read(tmp_path / "broken.replay")


def test_add_keeps_a_replay_and_refuses_partial_or_taken_names(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _write(tmp_path / "rec" / "play-2026.replay")
    kept = replays.add(project, source, "jump over gap")
    assert kept == project / "tests/replays/jump-over-gap.replay"
    assert kept.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        replays.add(project, source, "jump over gap")
    partial = _write(tmp_path / "partial.replay", {**HEADER, "partial": True})
    with pytest.raises(ValueError, match="middle of a session"):
        replays.add(project, partial)
    report = tmp_path / "report-1"
    _write(report / "replay.replay")
    assert replays.add(project, report, "from-report").name == "from-report.replay"


def test_replays_check_is_on_once_a_replay_exists_unless_turned_off(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert replays.config(project) == {"replays": False}
    _write(project / "tests/replays/walk.replay")
    assert replays.config(project) == {"replays": True}
    (project / "joinery.toml").write_text("[checks]\nreplays = false\n")
    assert replays.config(project) == {"replays": False}


def test_command_starts_like_the_recording(tmp_path: Path) -> None:
    replay = replays.read(_write(tmp_path / "a.replay"))
    argv = replays.command(replay, "/p", "/r.replay", godot="godot", work="/w")
    assert argv[:6] == ["godot", "--headless", "--path", "/p", "--fixed-fps", "60"]
    assert argv[6:8] == ["--script", replays.BOOT_SCRIPT]
    assert argv[argv.index("--") + 1 :] == [
        "--joinery-replay=/r.replay",
        "--settings-path=/w/settings.cfg",
        "--saves-dir=/w/saves",
        "--autostart",
    ]
    engine = {**HEADER, "boot": "engine", "physics_ticks": 120}
    engine["start"] = {**HEADER["start"], "scene": "res://game/arena.tscn"}
    other = replays.read(_write(tmp_path / "b.replay", engine))
    argv = replays.command(other, "/p", "/r", godot="g", work="/w")
    assert "--script" not in argv
    assert argv[4:7] == ["--fixed-fps", "120", "res://game/arena.tscn"]


def test_outcome_reads_pass_divergence_errors_and_crashes(tmp_path: Path) -> None:
    replay = replays.read(_write(tmp_path / "walk.replay"))
    build = HEADER["build"]
    passed = replays.outcome(
        "walk",
        _console({"ok": True, "checks": 2, "final_hash": "ff", "build": build, "recorded_build": build}),
        0,
        replay,
    )
    assert passed.ok and passed.checks == 2 and passed.line() == "walk: passed (2 checkpoints)."

    diverged = replays.outcome(
        "walk",
        _console(
            {
                "ok": False,
                "checks": 1,
                "build": "other build",
                "recorded_build": build,
                "divergence": {"frame": 120, "differs": ["players: [(1, 2)] -> [(3, 2)]", "Players: changed"]},
            }
        ),
        1,
        replay,
    )
    assert diverged.status == "diverged" and diverged.frame == 120 and diverged.seconds == 2.0
    assert "diverged at frame 120 (2 s)" in diverged.line()
    assert "Players: changed" in diverged.line()
    assert "another build can differ" in diverged.note

    errors = replays.outcome(
        "walk",
        _console(
            {
                "ok": False,
                "checks": 2,
                "errors": [{"message": "Invalid call", "where": "res://game/player.gd:12", "frame": 75}],
            }
        ),
        1,
        replay,
    )
    assert errors.status == "errors" and errors.frame == 75
    assert "Invalid call (res://game/player.gd:12, frame 75)" in errors.line()

    crashed = replays.outcome("walk", "SCRIPT ERROR: Parse Error\nsegfault\n", 139, replay)
    assert crashed.status == "failed" and "exit code 139" in crashed.detail
    assert crashed.errors == ["SCRIPT ERROR: Parse Error"]


def test_errors_the_recording_had_too_do_not_fail_it(tmp_path: Path) -> None:
    replay = replays.read(_write(tmp_path / "w.replay", end={"frame": 130, "errors": 2}))
    got = replays.outcome("w", _console({"ok": False, "errors": [{"message": "x", "frame": 3}]}), 0, replay)
    assert got.status == "passed"


def test_worker_script_quotes_and_split_runs(tmp_path: Path) -> None:
    header = {**HEADER, "settings": "[locale]\ncode='de'\n"}
    replay = replays.read(_write(tmp_path / "tests/replays/it's.replay", header))
    script = replays._script([replay], "after")
    assert "printf '%s' '[locale]\ncode='\\''de'\\''\n' > '/tmp/after-it'\\''s/settings.cfg'" in script
    assert "'--joinery-replay=/work/tests/replays/it'\\''s.replay'" in script
    output = "noise\nREPLAY_BEGIN a\nline 1\nline 2\nREPLAY_END a\nREPLAY_BEGIN b\nx\nREPLAY_END b\n"
    assert replays.split_runs(output) == {"a": "line 1\nline 2", "b": "x"}


def _fake_runner(results: dict[str, dict[str, dict[str, Any]]]) -> tuple[Any, list[tuple[str, Path]]]:
    """A worker that answers each replay by the stage it runs in ("after" or "before")."""
    calls: list[tuple[str, Path]] = []

    async def runner(argv: list[str], root: Path, timeout: int) -> Result:
        tag = "before" if "/tmp/before-" in argv[-1] else "after"
        calls.append((tag, root))
        out = []
        for name, data in results[tag].items():
            out += [f"REPLAY_BEGIN {name}", replays.RESULT_PREFIX + json.dumps(data), f"REPLAY_END {name}"]
        return Result(True, "\n".join(out), "", 1.0)

    return runner, calls


def test_staged_check_compares_with_the_game_before_the_change(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project / "tests/replays/walk.replay")
    _write(project / "tests/replays/jump.replay")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "project.godot").write_text((project / "project.godot").read_text())
    _write(stage / "tests/replays/walk.replay")
    _write(stage / "tests/replays/jump.replay")
    off = {"ok": False, "checks": 1, "build": "linux", "recorded_build": "mac", "divergence": {"frame": 60, "differs": ["nodes: 5 -> 6"]}}
    runner, calls = _fake_runner(
        {
            # On the worker's build "walk" was already off its recording before the
            # change, the same way: not this change's doing. "jump" played right and
            # doesn't any more.
            "before": {"walk": off, "jump": {"ok": True, "checks": 2}},
            "after": {"walk": off, "jump": {**off, "divergence": {"frame": 120, "differs": ["players: a -> b"]}}},
        }
    )
    outcomes = asyncio.run(replays.check_stage(project, stage, runner))
    by_name = {o.name: o for o in outcomes}
    assert by_name["walk"].ok and "exactly as before the change" in by_name["walk"].note
    assert by_name["jump"].status == "diverged" and by_name["jump"].frame == 120
    assert "played like the recording before this change" in by_name["jump"].detail
    assert [tag for tag, _ in calls] == ["after", "before"]
    # The baseline is kept, so the next check of the same game runs the stage only.
    cached = list((project / replays.BASELINES).glob("baseline-*.json"))
    assert len(cached) == 1
    asyncio.run(replays.check_stage(project, stage, runner))
    assert [tag for tag, _ in calls] == ["after", "before", "after"]
    text = replays.problems_text(outcomes)
    assert text.startswith("A recorded play session no longer plays the same: jump: diverged at frame 120")
    assert "walk" not in text


def test_submission_sends_back_a_failing_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    _write(stage / "tests/replays/walk.replay")
    runner, _calls = _fake_runner(
        {
            "before": {"walk": {"ok": True, "checks": 2}},
            "after": {"walk": {"ok": False, "checks": 0, "divergence": {"frame": 60, "differs": ["Players: changed"]}}},
        }
    )
    monkeypatch.setattr(submission, "run_offline", lambda argv, root, seconds: runner(argv, root, seconds))
    tx = type("Tx", (), {"project": project, "stage": stage})()
    problems = asyncio.run(submission.run_replays(tx, imported=True))  # type: ignore[arg-type]
    (problem,) = problems
    assert problem.check == "replays" and problem.fixable
    assert "walk: diverged at frame 60 (1 s)" in problem.message
    assert problem.paths == ["tests/replays/walk.replay"]


def test_cli_adds_and_lists_replays(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    source = _write(tmp_path / "play.replay")
    assert cli.main(["replay", "add", str(source), "--project", str(project), "--name", "walk"]) == 0
    assert "Added tests/replays/walk.replay (2.17 s, 3 inputs, 2 checkpoints)." in capsys.readouterr().out
    assert cli.main(["replay", "list", "--project", str(project), "--json"]) == 0
    (listed,) = json.loads(capsys.readouterr().out)
    assert listed["name"] == "walk" and listed["seed"] == 42
    assert cli.main(["replay", "run", "nope", "--project", str(project)]) == 1
    assert "nope: failed: There is no tests/replays/nope.replay." in capsys.readouterr().out


def test_playtest_seeds_the_game_and_can_record(tmp_path: Path) -> None:
    options = playtest.Options(project=tmp_path, seed=99, record=True)
    args = playtest._bot_args(options, tmp_path)
    assert "--joinery-seed=99" in args
    assert f"--joinery-record={tmp_path / 'play.replay'}" in args
    # The screenshot replay never records over it.
    assert not any(a.startswith("--joinery-record") for a in playtest._bot_args(options, tmp_path, shots=[5]))
