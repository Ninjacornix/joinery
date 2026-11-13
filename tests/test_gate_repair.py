"""Repair (and setup) replace only Joinery's own hook entries: the user's other settings
and any hook that isn't Joinery's survive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery.agents import get
from joinery.catalog import ROOT
from joinery.cli import main as cli_main
from joinery.wiring import _command_str, _content, decoded

FOREIGN = {"type": "command", "command": "./scripts/lint-before-bash.sh", "timeout": 5}

#: (agent, hook event, the user's own extra keys) for the JSON-configured agents.
CASES = [
    ("claude", "PreToolUse", {"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}),
    ("codex", "PreToolUse", {"description": "My hooks", "notes": ["keep me"]}),
    ("gemini", "BeforeTool", {"theme": "Dracula", "telemetry": {"target": "local"}}),
]


def repair(project: Path, agent: str) -> int:
    return cli_main(
        ["connect", "repair-gate", "--agent", agent, "--project", str(project)]
    )


def joinery_hooks(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        hook
        for group in groups
        for hook in group["hooks"]
        if "joinery" in decoded(str(hook.get("command", "")))
        and "hook" in decoded(str(hook["command"]))
    ]


@pytest.fixture(params=["posix", "windows"])
def command_form(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Each case in the POSIX command form and the encoded PowerShell one Windows
    writes, whichever system the tests run on."""
    form = str(request.param)
    monkeypatch.setattr("joinery.wiring._on_windows", lambda: form == "windows")
    return form


@pytest.mark.parametrize("agent_id,event,extra", CASES)
def test_repair_keeps_the_users_settings_and_hooks(
    agent_id: str,
    event: str,
    extra: dict[str, Any],
    tmp_path: Path,
    command_form: str,
) -> None:
    agent = get(agent_id)
    path = tmp_path / agent.config_path
    path.parent.mkdir(parents=True)
    stale = json.loads(_content(agent, "/old/engine"))
    config = {**stale, **extra}
    # The user's own hook beside Joinery's stale one, in the same group, plus one of
    # their own on another event.
    config["hooks"][event][0]["hooks"].append(FOREIGN)
    config["hooks"]["SessionStart"] = [{"hooks": [FOREIGN]}]
    path.write_text(json.dumps(config, indent=2))

    assert repair(tmp_path, agent_id) == 0
    repaired = json.loads(path.read_text())
    for key, value in extra.items():
        if key == "telemetry":
            assert repaired[key] == {"target": "local", "enabled": False}
        else:
            assert repaired[key] == value
    groups = repaired["hooks"][event]
    assert FOREIGN in [hook for group in groups for hook in group["hooks"]]
    assert repaired["hooks"]["SessionStart"] == [{"hooks": [FOREIGN]}]
    ours = joinery_hooks(groups)
    assert [hook["command"] for hook in ours] == [_command_str(str(ROOT), agent_id)]
    assert "/old/engine" not in decoded(path.read_text())
    # A second Repair changes nothing.
    before = path.read_text()
    assert repair(tmp_path, agent_id) == 0
    assert path.read_text() == before


def test_repair_leaves_opencodes_own_config_and_plugins(tmp_path: Path) -> None:
    agent = get("opencode")
    plugin = tmp_path / agent.config_path
    plugin.parent.mkdir(parents=True)
    plugin.write_text(_content(agent, "/old/engine"))
    other = plugin.parent / "my-plugin.js"
    other.write_text("export const Mine = async () => ({})\n")
    settings = tmp_path / "opencode.json"
    settings.write_text(json.dumps({"model": "anthropic/claude", "plugin": ["x"]}))
    assert repair(tmp_path, "opencode") == 0
    assert plugin.read_text() == _content(agent, str(ROOT))
    assert other.read_text() == "export const Mine = async () => ({})\n"
    assert json.loads(settings.read_text()) == {
        "model": "anthropic/claude",
        "plugin": ["x"],
    }


def test_a_broken_config_is_never_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / get("claude").config_path
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    assert repair(tmp_path, "claude") == 1
    assert "not valid JSON" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_text() == "{ not json"


def test_a_fresh_project_gets_joinerys_file(tmp_path: Path) -> None:
    assert repair(tmp_path, "claude") == 0
    path = tmp_path / get("claude").config_path
    assert path.read_text() == _content(get("claude"), str(ROOT))
