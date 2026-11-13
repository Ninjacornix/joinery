"""After the engine moves, Joinery's own hook config follows it; an edited one waits for
Repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery.agents import Agent, all_agents, get
from joinery.bootstrap import refresh_managed_gates
from joinery.catalog import ROOT
from joinery.wiring import _content, refresh_gate, written_engine


@pytest.fixture(params=["posix", "windows"])
def command_form(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Each case in the POSIX command form and the encoded PowerShell one Windows
    writes, whichever system the tests run on."""
    form = str(request.param)
    monkeypatch.setattr("joinery.wiring._on_windows", lambda: form == "windows")
    return form


@pytest.mark.parametrize("agent", list(all_agents()), ids=lambda a: a.id)
def test_joinerys_own_file_is_recognised_for_any_engine(
    agent: Agent, command_form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for engine in [
        "/old/engine",
        "/Applications/Joinery old.app/Contents/Resources",
        "C:\\Program Files\\Joinery's engine",
    ]:
        text = _content(agent, engine)
        assert written_engine(agent, text) is not None
    # Someone points the hook at another program: no longer Joinery's file.
    monkeypatch.setattr("joinery.wiring._HOOK_FOOT", ["joinery-fork", "hook"])
    edited = _content(agent, "/old/engine")
    monkeypatch.undo()
    monkeypatch.setattr("joinery.wiring._on_windows", lambda: command_form == "windows")
    assert written_engine(agent, edited) is None


def test_a_moved_engine_is_followed(tmp_path: Path, command_form: str) -> None:
    claude = get("claude")
    path = tmp_path / claude.config_path
    path.parent.mkdir(parents=True)
    path.write_text(_content(claude, "/somewhere/else"))
    assert refresh_gate(tmp_path, claude)
    assert path.read_text() == _content(claude, str(ROOT))
    assert not refresh_gate(tmp_path, claude)  # Already current.


def test_an_edited_or_missing_file_is_left_alone(tmp_path: Path) -> None:
    claude = get("claude")
    assert not refresh_gate(tmp_path, claude)
    path = tmp_path / claude.config_path
    path.parent.mkdir(parents=True)
    settings = json.loads(_content(claude, "/somewhere/else"))
    settings["model"] = "opus"
    path.write_text(json.dumps(settings, indent=2) + "\n")
    before = path.read_text()
    assert not refresh_gate(tmp_path, claude)
    assert path.read_text() == before


def test_an_update_migrates_joinerys_hook_in_either_form(
    tmp_path: Path, command_form: str
) -> None:
    claude = get("claude")
    path = tmp_path / claude.config_path
    path.parent.mkdir(parents=True)
    settings = json.loads(_content(claude, "/somewhere/else"))
    settings["model"] = "opus"
    path.write_text(json.dumps(settings, indent=2) + "\n")
    refresh_managed_gates(tmp_path)
    updated = json.loads(path.read_text())
    assert updated["model"] == "opus"
    assert updated["hooks"] == json.loads(_content(claude, str(ROOT)))["hooks"]
