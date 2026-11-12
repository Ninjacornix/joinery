"""Kept saves, without Godot: keeping them, comparing what came back, which changes
can break saves, the staged-change check and the CLI (ADR 0066). Godot runs are in
tests/blueprints/test_save_compat.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import cli, reasons, saves, submission
from joinery.runner import Result

SAVE: dict[str, Any] = {
    "format": 1,
    "version": 1,
    "game": "Test Game",
    "blueprint": "platformer-2d",
    "saved_at": 1700000000.0,
    "level": "res://game/level.tscn",
    "playtime": 12.5,
    "options": {},
    "nodes": {"coin_1": {"collected": True}, "level": {"falls": 2, "time": 12.0}},
    "extra": {},
}
SAVED: dict[str, Any] = {
    "level": "res://game/level.tscn",
    "playtime": 12.5,
    "nodes": {"coin_1": {"collected": True}, "level": {"falls": 2, "time": 12.0}},
    "extra": {"unlocks": ["red"]},
    "line": "Coins: 1 of 3",
}


def _project(tmp_path: Path, version: str = "") -> Path:
    project = tmp_path / "game"
    project.mkdir(parents=True)
    text = 'config_version=5\n[application]\nconfig/name="Test Game"\n'
    if version:
        text += f'config/version="{version}"\n'
    text += "[save]\ndata_version=1\n"
    (project / "project.godot").write_text(text)
    return project


def _slot(path: Path, data: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or SAVE))
    return path


def _console(raw: dict[str, Any]) -> str:
    return "Godot Engine v4.7\n" + saves.RESULT_PREFIX + json.dumps(raw) + "\n"


def _loaded(**changes: Any) -> dict[str, Any]:
    loaded = json.loads(json.dumps(SAVED))
    loaded.update(changes)
    return {"status": "loaded", "version": 1, "data_version": 1, "saved": SAVED, "loaded": loaded}


def test_add_keeps_slots_under_the_game_version_with_a_summary(tmp_path: Path) -> None:
    project = _project(tmp_path, "0.3")
    run = tmp_path / "run"
    _slot(run / "saves/slot_0.json")
    _slot(run / "saves/slot_1.json")
    added = saves.add(project, run, "forest", load=False)
    assert [k.relative for k in added] == [
        "tests/saves/0.3/forest-slot0.json",
        "tests/saves/0.3/forest-slot1.json",
    ]
    summary = json.loads((project / "tests/saves/0.3/forest-slot0.summary.json").read_text())
    assert summary["loaded"] is False and summary["level"] == "res://game/level.tscn"
    with pytest.raises(ValueError, match="already exists"):
        saves.add(project, run, "forest", load=False)
    listed = [k.describe() for k in saves.kept(project)]
    assert [item["name"] for item in listed] == ["0.3/forest-slot0", "0.3/forest-slot1"]
    assert listed[0]["nodes"] == 2 and listed[0]["data_version"] == 1
    # Without a game version the folder is the save's data version.
    other = _project(tmp_path / "b")
    (kept,) = saves.add(other, _slot(tmp_path / "slot_2.json"), load=False)
    assert kept.relative == "tests/saves/save-v1/slot_2.json"


def test_add_refuses_what_isnt_a_save(tmp_path: Path) -> None:
    project = _project(tmp_path)
    bad = tmp_path / "slot_1.json"
    bad.write_text('{"hello": 1}')
    with pytest.raises(ValueError, match="isn't a save made by the game's save system"):
        saves.add(project, bad, load=False)
    with pytest.raises(ValueError, match="No save files"):
        saves.add(project, tmp_path / "nothing-here", load=False)


def test_saves_check_is_on_once_a_save_is_kept_unless_turned_off(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert saves.config(project) == {"saves": False}
    saves.add(project, _slot(tmp_path / "slot_1.json"), load=False)
    assert saves.config(project) == {"saves": True}
    (project / "joinery.toml").write_text("[checks]\nsaves = false\n")
    assert saves.config(project) == {"saves": False}


def test_compare_names_lost_keys_nodes_values_and_the_slot_line() -> None:
    assert saves.compare(_loaded(), {"line": "Coins: 1 of 3"}) == []
    renamed = _loaded(nodes={"coin_1": {"taken": False}, "level": {"falls": 2, "time": 12.00001}})
    renamed["loaded"]["line"] = "Coins: 0 of 3"
    found = saves.compare(renamed, {"line": "Coins: 1 of 3"})
    # A float a hair off is the same value; the renamed key is lost.
    assert found == [
        "coin_1.collected is gone (was true)",
        "the slot list said “Coins: 1 of 3” and now says “Coins: 0 of 3”",
    ]
    moved = _loaded(level="res://game/other.tscn", nodes={"level": {"falls": 3, "time": 12.0}})
    lost = saves.compare(moved)
    assert "loads into res://game/other.tscn instead of res://game/level.tscn" in lost
    assert any(line.startswith("node coin_1 isn't in the game after loading") for line in lost)
    assert "level.falls was 2, loads as 3" in lost
    no_extra = _loaded(extra={})
    assert saves.compare(no_extra) == ['extra data unlocks isn\'t restored (saved ["red"])']


def test_judge_reads_loaded_lost_failed_and_crashed(tmp_path: Path) -> None:
    kept = saves.Kept(tmp_path / "tests/saves/1.0/a.json", "1.0", SAVE, {"line": "Coins: 1 of 3"})
    assert saves.judge(kept, _console(_loaded())).ok
    lost = saves.judge(kept, _console(_loaded(nodes={"level": SAVED["nodes"]["level"]})))
    assert lost.status == "lost" and lost.detail == "loads but loses data:"
    assert lost.line().startswith("tests/saves/1.0/a.json: loads but loses data: node coin_1")
    refused = saves.judge(
        kept,
        _console(
            {
                "status": "failed",
                "reason": "This save is from an older version that cannot be upgraded.",
                "version": 1,
                "data_version": 2,
            }
        ),
    )
    assert refused.status == "failed"
    assert "older version" in refused.detail and "a migration step is missing" in refused.detail
    crashed = saves.judge(kept, "SCRIPT ERROR: Invalid call\nboom", 1)
    assert crashed.status == "failed" and "stopped before the save loaded (exit code 1)" in crashed.detail
    assert crashed.errors == ["SCRIPT ERROR: Invalid call"]


def test_only_changes_to_what_saves_are_made_of_are_relevant(tmp_path: Path) -> None:
    stage, original = tmp_path / "stage", tmp_path / "original"
    for root in (stage, original):
        (root / "game").mkdir(parents=True)
    (stage / "game/coin.gd").write_text("func save_state() -> Dictionary:\n\treturn {}\n")
    (stage / "game/hud.gd").write_text("extends Control\n")
    (original / "game/old.gd").write_text("SaveGame.register('x', a, b)\n")

    def change(path: str) -> list[dict[str, str]]:
        return [{"path": path, "before": "a", "after": "b"}]

    assert saves.relevant(stage, original, change("game/coin.gd"))
    assert not saves.relevant(stage, original, change("game/hud.gd"))
    # A script that saved before the change (now deleted) counts too.
    assert saves.relevant(stage, original, change("game/old.gd"))
    assert saves.relevant(stage, original, change("project.godot"))
    assert saves.relevant(stage, original, change("game/save_migrations.gd"))
    assert saves.relevant(stage, original, change("features/save-load/save_game.gd"))
    assert not saves.relevant(stage, original, change("art/hero.png"))


def test_worker_script_writes_the_check_and_marks_each_save(tmp_path: Path) -> None:
    project = _project(tmp_path)
    saves.add(project, _slot(tmp_path / "it's.json"), load=False, version="1.0")
    (kept,) = saves.kept(project)
    script = saves._script([kept], "after")
    assert "> /tmp/joinery-save-check/save_check.gd" in script
    # Names are made safe when kept; the check's source is quoted whole.
    assert "'--joinery-save=/work/tests/saves/1.0/it-s.json'" in script
    assert saves.CHECK_SCRIPT.read_text().replace("'", "'\\''") in script
    assert "'--saves-dir=/tmp/after-save-0/saves'" in script
    output = "x\nSAVE_BEGIN 1.0/a\nline\nSAVE_END 1.0/a\n"
    assert saves.split_runs(output) == {"1.0/a": "line"}


def _fake_runner(results: dict[str, dict[str, dict[str, Any]]]) -> tuple[Any, list[str]]:
    """A worker that answers each save by the stage it runs in ("after" or "before")."""
    calls: list[str] = []

    async def runner(argv: list[str], root: Path, timeout: int) -> Result:
        tag = "before" if "/tmp/before-save" in argv[-1] else "after"
        calls.append(tag)
        out = []
        for name, data in results[tag].items():
            out += [f"SAVE_BEGIN {name}", saves.RESULT_PREFIX + json.dumps(data), f"SAVE_END {name}"]
        return Result(True, "\n".join(out), "", 1.0)

    return runner, calls


def _stage(tmp_path: Path, project: Path) -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "project.godot").write_text((project / "project.godot").read_text())
    for kept in saves.kept(project):
        target = stage / kept.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(kept.path.read_bytes())
        summary = kept.path.with_name(kept.path.stem + saves.SUMMARY)
        target.with_name(summary.name).write_bytes(summary.read_bytes())
    return stage


def test_staged_check_reports_only_what_the_change_made_worse(tmp_path: Path) -> None:
    project = _project(tmp_path)
    saves.add(project, _slot(tmp_path / "a.json"), version="1.0", load=False)
    saves.add(project, _slot(tmp_path / "b.json"), version="1.0", load=False)
    stage = _stage(tmp_path, project)
    drifting = _loaded(nodes={"coin_1": {"collected": True}, "level": {"falls": 2, "time": 13.0}})
    renamed = _loaded(nodes={"coin_1": {"taken": True}, "level": {"falls": 2, "time": 12.0}})
    runner, calls = _fake_runner(
        {
            # "a" drifted on the worker before the change too; "b" loaded cleanly and
            # loses its coin now.
            "before": {"1.0/a": drifting, "1.0/b": _loaded()},
            "after": {"1.0/a": drifting, "1.0/b": renamed},
        }
    )
    outcomes = asyncio.run(saves.check_stage(project, stage, runner))
    by_name = {o.name: o for o in outcomes}
    assert by_name["1.0/a"].ok and by_name["1.0/a"].note == "Did the same before this change."
    assert by_name["1.0/b"].status == "lost"
    assert by_name["1.0/b"].lost == ["coin_1.collected is gone (was true)"]
    assert calls == ["after", "before"]
    asyncio.run(saves.check_stage(project, stage, runner))
    assert calls == ["after", "before", "after"], "the baseline is kept per game state"
    text = saves.problems_text(outcomes)
    assert text.startswith("An old save no longer loads as it did: tests/saves/1.0/b.json")
    assert "save/data_version in project.godot to 2" in text
    assert "res://game/save_migrations.gd" in text and "1.0/a" not in text


def test_submission_sends_back_a_broken_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    saves.add(project, _slot(tmp_path / "a.json"), version="1.0", load=False)
    stage = _stage(tmp_path, project)
    refused = {"status": "failed", "reason": "This save is damaged.", "version": 1, "data_version": 1}
    runner, _calls = _fake_runner({"before": {"1.0/a": _loaded()}, "after": {"1.0/a": refused}})
    monkeypatch.setattr(
        submission, "run_offline", lambda argv, root, seconds: runner(argv, root, seconds)
    )
    tx = type("Tx", (), {"project": project, "stage": stage})()
    (problem,) = asyncio.run(submission.run_saves(tx, imported=True))
    assert problem.check == "saves" and problem.fixable
    assert problem.paths == ["tests/saves/1.0/a.json"]
    (item,) = problem.reasons()
    assert item.rule == "saves.load_failed" and item.file == "tests/saves/1.0/a.json"
    assert "This save is damaged." in item.measured
    assert "save/data_version" in item.fix and "res://game/save_migrations.gd" in item.fix
    assert reasons.rules_of([problem]) == ["saves.load_failed"]


def test_cli_adds_lists_and_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, "1.2")
    source = _slot(tmp_path / "slot_1.json")
    args = ["saves", "add", str(source), "--project", str(project), "--name", "cave", "--no-load"]
    assert cli.main(args) == 0
    assert "Kept tests/saves/1.2/cave.json (res://game/level.tscn, 2 saved nodes)." in (
        capsys.readouterr().out
    )
    assert cli.main(["saves", "list", "--project", str(project), "--json"]) == 0
    (listed,) = json.loads(capsys.readouterr().out)
    assert listed["name"] == "1.2/cave" and listed["loaded"] is False

    def run_file(project: Path, path: Path, **_: Any) -> saves.Outcome:
        return saves.Outcome("1.2/cave", "tests/saves/1.2/cave.json", "lost", "loads but loses data:", ["coin_1.collected is gone (was true)"])

    monkeypatch.setattr(saves, "run_file", run_file)
    assert cli.main(["saves", "check", "--project", str(project)]) == 1
    assert "coin_1.collected is gone" in capsys.readouterr().out
