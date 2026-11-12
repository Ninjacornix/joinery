"""Bug reports players save, read by Joinery: where they are found, the CLI, and the
debugger taking one (ADR 0059). The game side is tested in tests/blueprints."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import bug_reports, cli, debugging


def _project(tmp_path: Path, name: str = "Test Game", extra: str = "") -> Path:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text(
        f'config_version=5\n\n[application]\n\nconfig/name="{name}"\n{extra}'
    )
    return project


def _report(root: Path, report_id: str, note: str = "I fell through the floor.", **extra: Any) -> Path:
    folder = root / report_id
    folder.mkdir(parents=True)
    data = {
        "format": "joinery-bug-report",
        "version": 1,
        "id": report_id,
        "note": note,
        "created": "2026-11-12T10:00:00" if report_id < "2" else "2026-11-12T" + report_id[-8:],
        "game": "Test Game",
        "game_version": "1.2",
        "joinery": "0.5.0",
        "build": "4.7.2-stable macOS arm64",
        "os": "macOS",
        "seed": 1234,
        "scene": "res://game/level.tscn",
        "frame": 900,
        "seconds": 60,
        "errors": 1,
        "replay": "replay.replay",
        "log": "log.txt",
        **extra,
    }
    (folder / "report.json").write_text(json.dumps(data))
    (folder / "replay.replay").write_text('{"format": "joinery-replay"}\n')
    (folder / "log.txt").write_text(
        "[10] PRINT: level started\n"
        "[850] SCRIPT ERROR: Invalid access to property 'floor' (res://game/level.gd:40)\n"
        "[860] WARNING: something odd\n"
    )
    return folder


def test_user_folder_follows_godots_naming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bug_reports.Path, "home", staticmethod(lambda: tmp_path / "home"))
    project = _project(tmp_path, "My: Game")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert bug_reports.user_data_dir(project) == (
        tmp_path / "home/Library/Application Support/Godot/app_userdata/My_ Game"
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert bug_reports.user_data_dir(project) == (
        tmp_path / "home/.local/share/godot/app_userdata/My_ Game"
    )
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "project.godot").write_text(
        '[application]\nconfig/name="Game"\nconfig/use_custom_user_dir=true\n'
        'config/custom_user_dir_name="studio/game"\n'
    )
    assert bug_reports.user_data_dir(custom) == tmp_path / "home/.local/share/studio_game"


def test_reports_are_found_newest_first_and_by_short_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    user = tmp_path / "user"
    monkeypatch.setattr(bug_reports, "user_data_dir", lambda _p: user)
    _report(user / "joinery_reports", "20261112-100000-aaaa")
    _report(project / ".joinery/reports", "20261112-110000-bbbb", note="Music stops.")
    (user / "joinery_reports/not-a-report").mkdir()
    found = bug_reports.list_reports(project)
    assert [r.id for r in found] == ["20261112-110000-bbbb", "20261112-100000-aaaa"]
    assert bug_reports.find(project, "20261112-10").id == "20261112-100000-aaaa"
    with pytest.raises(ValueError, match="matches 2 reports"):
        bug_reports.find(project, "20261112")
    with pytest.raises(ValueError, match="no bug report"):
        bug_reports.find(project, "1999")
    with pytest.raises(ValueError, match="looks like"):
        bug_reports.find(project, "../etc")


def test_import_copies_a_players_report_into_the_project(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sent = _report(tmp_path / "mail", "20261112-120000-cccc")
    report = bug_reports.import_report(project, sent)
    assert report.folder == project / ".joinery/reports/20261112-120000-cccc"
    assert report.replay is not None and report.replay.is_file()
    with pytest.raises(ValueError, match="already in the project"):
        bug_reports.import_report(project, sent)


def test_cli_lists_and_opens_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(bug_reports, "user_data_dir", lambda _p: None)
    _report(project / ".joinery/reports", "20261112-100000-aaaa")
    assert cli.main(["reports", "list", "--project", str(project)]) == 0
    assert "- 20261112-100000-aaaa  I fell through the floor." in capsys.readouterr().out
    assert cli.main(["reports", "open", "20261112-100000", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Note: I fell through the floor." in out
    assert "Seed 1234 · scene res://game/level.tscn · frame 900" in out
    assert "Invalid access to property 'floor'" in out
    assert "/debug report 20261112-100000-aaaa" in out
    assert cli.main(["reports", "open", "20261112-100000", "--project", str(project), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["seed"] == 1234 and data["replay"].endswith("replay.replay")
    assert cli.main(["reports", "open", "nope", "--project", str(project)]) == 2


def test_debugger_takes_a_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(bug_reports, "user_data_dir", lambda _p: None)
    folder = _report(project / ".joinery/reports", "20261112-100000-aaaa")
    request = debugging.debug_request({"report": "20261112-100000-aaaa"}, project)
    assert request.error == (
        "SCRIPT ERROR: Invalid access to property 'floor' (res://game/level.gd:40)"
    )
    assert "Bug report 20261112-100000-aaaa from a player: I fell through the floor." in request.output
    assert str(folder / "replay.replay") in request.output
    assert "level started" in request.log
    assert request.scene == "res://game/level.tscn"
    assert request.locations() == ["res://game/level.gd:40"]
    # A report with no error in its log still gives the debugger the player's words.
    quiet = _report(project / ".joinery/reports", "20261112-100001-bbbb", note="Too dark.")
    (quiet / "log.txt").write_text("[1] PRINT: hello\n")
    request = debugging.debug_request({"report": "20261112-100001-bbbb"}, project)
    assert request.error == "Player report: Too dark."
    with pytest.raises(ValueError, match="inside a project"):
        debugging.debug_request({"report": "x"})
