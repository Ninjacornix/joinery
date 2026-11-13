"""After the engine moves, Joinery's own hook config follows it; an edited one waits for
Repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery.agents import Agent, all_agents, get
from joinery.catalog import ROOT
from joinery.wiring import _content, refresh_gate, written_engine


@pytest.mark.parametrize("agent", list(all_agents()), ids=lambda a: a.id)
def test_joinerys_own_file_is_recognised_for_any_engine(agent: Agent) -> None:
    for engine in ["/old/engine", "/Applications/Joinery old.app/Contents/Resources"]:
        text = _content(agent, engine)
        assert written_engine(agent, text) is not None
    edited = _content(agent, "/old/engine").replace("joinery", "joinery-fork", 1)
    assert written_engine(agent, edited) is None


def test_a_moved_engine_is_followed(tmp_path: Path) -> None:
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
