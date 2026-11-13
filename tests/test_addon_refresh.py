"""A project's add-on is checked against Joinery's and updated from its own editor."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from joinery import addon_refresh
from joinery.bootstrap import update_addon


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("joinery.bootstrap.editor_running", lambda _project: False)
    (tmp_path / "project.godot").write_text("config_version=5\n")
    return tmp_path


def test_state_follows_the_project_copy(project: Path) -> None:
    assert addon_refresh.state(project)["state"] == "missing"
    update_addon(project)
    assert addon_refresh.state(project)["state"] == "current"
    # Godot's own files beside the add-on's never make it look old.
    (project / "addons/joinery/icons/ui/menu.svg.import").write_text("[remap]\n")
    assert addon_refresh.state(project)["state"] == "current"
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    report = addon_refresh.state(project)
    assert report["state"] == "stale"
    assert report["installed"] != report["available"]


def test_update_from_the_editor_waits_updates_and_reopens(project: Path) -> None:
    update_addon(project)
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    started: list[Path] = []
    scheduled = addon_refresh.schedule(
        project,
        wait_pid=0,
        editor="/opt/joinery/bin/godot",
        start=lambda plan: started.append(plan) or 42,
    )
    assert scheduled == {
        "state": "scheduled",
        "helper": 42,
        "project": str(project.resolve()),
    }
    plan = json.loads(started[0].read_text())
    assert plan["relaunch_argv"] == [
        str(Path("/opt/joinery/bin/godot")),  # The system's own separators.
        "--editor",
        "--path",
        str(project.resolve()),
    ]
    launched: list[list[str]] = []
    outcome = addon_refresh.run(
        started[0], launch=lambda argv, _home: launched.append(argv)
    )
    assert outcome["ok"] is True and outcome["state"] == "updated"
    assert addon_refresh.state(project)["state"] == "current"
    assert launched == [plan["relaunch_argv"]]
    assert not started[0].exists()


def test_a_failed_update_still_reopens_and_says_why(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    update_addon(project)
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")

    def refuse(_project: Path) -> dict[str, str]:
        raise ValueError("The disk is full.")

    monkeypatch.setattr("joinery.bootstrap.update_addon", refuse)
    plan = project / addon_refresh.PLAN
    plan.write_text(
        json.dumps({"project": str(project), "wait_pid": 0, "relaunch_argv": ["godot"]})
    )
    launched: list[list[str]] = []
    outcome = addon_refresh.run(plan, launch=lambda argv, _home: launched.append(argv))
    assert outcome["ok"] is False
    assert launched == [["godot"]]
    report = addon_refresh.state(project)
    assert report["state"] == "stale"
    assert report["last_error"] == "The disk is full."


def test_an_editor_that_stays_open_is_never_reopened_later(project: Path) -> None:
    plan = project / addon_refresh.PLAN
    plan.parent.mkdir()
    plan.write_text(
        json.dumps(
            {"project": str(project), "wait_pid": os.getpid(), "relaunch_argv": ["x"]}
        )
    )
    launched: list[list[str]] = []
    outcome = addon_refresh.run(
        plan, wait_seconds=0.0, launch=lambda argv, _home: launched.append(argv)
    )
    assert outcome["ok"] is False
    assert launched == []
    assert not (project / "addons/joinery").exists()


def test_a_mac_app_is_opened_as_an_app(tmp_path: Path) -> None:
    editor = "/Applications/Joinery.app/Contents/MacOS/Joinery"
    assert addon_refresh.relaunch_argv(editor, tmp_path) == [
        "open",
        "-n",
        str(Path("/Applications/Joinery.app")),
        "--args",
        "--editor",
        "--path",
        str(tmp_path),
    ]
