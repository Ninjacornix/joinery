"""Agent hook wiring: every supported agent, portable repo config, fail-closed."""

import base64
import json
from pathlib import Path

from joinery.agents import all_agents
from joinery.catalog import ROOT
from joinery.wiring import unwireable, write_agent_wiring


def config_text(path: Path) -> str:
    """Inspect the actual hook payload on both Unix shells and PowerShell."""
    text = path.read_text()
    if path.suffix == ".js":
        return text
    value = json.loads(text)
    commands: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            command = node.get("command")
            if isinstance(command, str) and " -EncodedCommand " in command:
                commands.append(
                    base64.b64decode(command.split()[-1]).decode("utf-16-le")
                )
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return text + "\n" + "\n".join(commands)


def test_writes_config_for_every_wireable_agent(tmp_path: Path) -> None:
    written = write_agent_wiring(tmp_path)
    for agent in all_agents():
        if agent.blocking_hook:
            assert agent.config_path in written
            assert (tmp_path / agent.config_path).is_file()


def test_scaffolded_config_pins_engine_install(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path)
    # A game project lives elsewhere, so its hook command must point at the
    # concrete engine install to find `joinery`.
    claude = json.loads((tmp_path / ".claude/settings.json").read_text())
    command = claude["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command
    semantic = config_text(tmp_path / ".claude/settings.json")
    assert str(ROOT) in semantic and all(
        token in semantic for token in ("joinery", "hook", "--agent", "claude")
    )


def test_portable_config_has_no_machine_path(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path, portable=True)
    for agent in all_agents():
        if not agent.blocking_hook:
            continue
        text = (tmp_path / agent.config_path).read_text()
        assert str(ROOT) not in text


def test_portable_claude_uses_project_dir_var(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path, portable=True)
    text = config_text(tmp_path / ".claude/settings.json")
    assert "${CLAUDE_PROJECT_DIR}" in text or "$env:CLAUDE_PROJECT_DIR" in text


def test_every_agent_runs_joinery_hook(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path)
    for agent in all_agents():
        if agent.blocking_hook:
            assert "joinery" in config_text(tmp_path / agent.config_path)
            assert "hook" in config_text(tmp_path / agent.config_path)


def test_all_supported_agents_are_wireable() -> None:
    # The four agents we support must all have a blocking gate; if one did not,
    # fail-closed would skip it, and that must be a conscious change.
    assert unwireable() == []


def test_generated_configs_are_valid_json(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path)
    for name in (".claude/settings.json", ".codex/hooks.json", ".gemini/settings.json"):
        json.loads((tmp_path / name).read_text())


def test_opencode_scaffold_disables_nothing(tmp_path: Path) -> None:
    # Nothing is disabled or removed: every tool stays callable and is judged
    # per call. The #5894 subagent gap is covered by the sandbox, not by denying
    # opencode's task tool.
    written = write_agent_wiring(tmp_path)
    assert "opencode.json" not in written
    assert not (tmp_path / "opencode.json").exists()
    plugin = (tmp_path / ".opencode/plugin/joinery-gate.js").read_text()
    assert "deny" not in plugin


def test_no_agent_config_disables_a_tool(tmp_path: Path) -> None:
    write_agent_wiring(tmp_path)
    for agent in all_agents():
        text = (tmp_path / agent.config_path).read_text()
        # Permission/feature switches that would remove a tool outright.
        for forbidden in ('"task": "deny"', "web_search", '"disabled"'):
            assert forbidden not in text, (agent.id, forbidden)


def test_every_config_declares_its_agent(tmp_path: Path) -> None:
    # --agent tells the hook how to signal a Rule Zero stop in that agent.
    write_agent_wiring(tmp_path)
    for agent in all_agents():
        text = config_text(tmp_path / agent.config_path)
        assert "--agent" in text and agent.id in text


def test_codex_scaffold_does_not_disable_web_search(tmp_path: Path) -> None:
    # Web search reads the web, it does not create assets, so it stays allowed.
    write_agent_wiring(tmp_path)
    codex_dir = tmp_path / ".codex"
    for path in codex_dir.rglob("*"):
        assert "web_search" not in path.read_text()
