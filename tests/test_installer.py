import json
import sys
from pathlib import Path

import pytest

from joinery.catalog import CustomTool, ProjectTools, load_catalog, load_project_tools
from joinery.cli import check_mcp_config, main
from joinery.installer import (
    Selection,
    custom_choices,
    menu_tools,
    run_menu,
    tool_menu,
    validate_choice,
    write_mcp_config,
    write_selection,
)

SDXL = CustomTool(
    id="sdxl", name="SDXL", domain="2d", server="sdxl", command="sdxl-mcp"
)


def number(tool_id: str) -> str:
    ids = [t.id for t in menu_tools(load_catalog())]
    return str(ids.index(tool_id) + 1)


def drive(answers: list[str], current: list[str]) -> tuple[Selection, list[str]]:
    shown: list[str] = []
    pending = iter(answers)
    result = run_menu(
        load_catalog(), current, (), lambda _: next(pending), shown.append
    )
    return result, shown


def test_toggles_tools() -> None:
    result, _ = drive([f"{number('aseprite')} {number('blender')}", ""], ["blender"])
    assert result.tools == ["aseprite", "godot"]


def test_required_tool_cannot_be_removed() -> None:
    result, shown = drive([number("godot"), ""], [])
    assert result.tools == ["godot"]
    assert any("required" in line for line in shown)


def test_preferred_tool_without_mcp_cannot_be_selected() -> None:
    result, shown = drive([number("surge-xt"), ""], [])
    assert result.tools == ["godot"]
    assert any("own MCP in progress" in line for line in shown)


def test_user_can_add_own_program_with_own_mcp() -> None:
    answers = ["a", "My Painter", "2d", "painter", "painter-mcp", "--port 9", ""]
    result, shown = drive(answers, [])
    assert result.custom[0].id == "my-painter"
    assert result.custom[0].args == ("--port", "9")
    assert any("My Painter" in line for line in shown)


def test_invalid_custom_tool_is_refused() -> None:
    result, shown = drive(["a", "X", "2d", "blender", "x", "", ""], [])
    assert result.custom == []
    assert any("Not added" in line for line in shown)


def test_bad_input_is_ignored() -> None:
    result, shown = drive(["99 abc", ""], [])
    assert result.tools == ["godot"]
    assert any("Unknown choice" in line for line in shown)


def test_noninteractive_rejects_unavailable_tool() -> None:
    with pytest.raises(ValueError, match="surge-xt"):
        validate_choice(load_catalog(), ["surge-xt"])


def test_write_selection_keeps_other_settings(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text('[audio]\nx = 1\n\n[tools]\nenabled = [\n  "a",\n]\n')
    write_selection(Selection(["blender", "godot"], []), config)
    assert (
        config.read_text()
        == '[audio]\nx = 1\n\n[tools]\nenabled = ["blender", "godot"]\n'
    )


def test_write_selection_adds_section(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text("[audio]\nx = 1\n")
    write_selection(Selection(["godot"], []), config)
    assert '[tools]\nenabled = ["godot"]' in config.read_text()


def test_custom_tools_round_trip_and_replace(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    other = CustomTool(
        id="o", name="O", domain="3d", server="o", command="o", args=("a b",)
    )
    write_selection(Selection(["godot"], [SDXL, other]), config)
    assert load_project_tools(config).custom == (SDXL, other)
    write_selection(Selection(["godot"], [other]), config)
    assert load_project_tools(config).custom == (other,)
    assert load_project_tools(config).enabled == ("godot",)


def test_mcp_config_registers_custom_and_keeps_existing(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"godot": {"command": "g"}}}')
    write_mcp_config(Selection(["godot"], [SDXL]), tmp_path)
    text = (tmp_path / ".mcp.json").read_text()
    assert '"godot"' in text and '"sdxl-mcp"' in text


def test_nonpreferred_choices_are_custom_and_selectable() -> None:
    menu = tool_menu(load_catalog(), ProjectTools())
    tools = menu["tools"]
    assert isinstance(tools, list)
    godot = next(tool for tool in tools if tool["id"] == "godot")
    alternatives = [o for o in godot["mcp_options"] if o["state"] == "alternative"]
    assert {o["label"] for o in alternatives} == {
        "hi-godot/godot-ai",
        "tomyud1/godot-mcp",
    }
    assert all(o["usable"] and o["custom"]["domain"] == "engine" for o in alternatives)
    assert all(not o["custom"]["command"] for o in alternatives)
    assert all(o["custom"]["server"] != "godot" for o in alternatives)
    assert godot["mcp_options"][0]["state"] == "chosen"
    krita = next(tool for tool in tools if tool["id"] == "krita")
    assert krita["mcp_options"][0]["label"] == "joinery-paint"
    assert krita["available"]
    assert not krita["enabled"]


def test_setup_saves_custom_alternative_and_replaces_required_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choice = {
        "id": "hi-godot-godot-ai",
        "name": "Godot (hi-godot/godot-ai)",
        "domain": "engine",
        "server": "hi-godot-godot-ai",
        "command": "/Applications/My MCP/server",
        "args": ["--name", "a b"],
    }
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "blender",
                "--custom-mcps",
                json.dumps([choice]),
            ],
            monkeypatch,
        )
        == 0
    )
    config = tmp_path / "joinery.toml"
    selected = load_project_tools(config)
    assert selected.enabled == ("blender",)
    assert selected.custom[0].command == choice["command"]
    assert selected.custom[0].args == ("--name", "a b")
    assert check_mcp_config(tmp_path, config) == []
    registered = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert registered[choice["server"]] == {
        "command": choice["command"],
        "args": choice["args"],
    }


@pytest.mark.parametrize("payload", ["{}", '[{"id":"x"}]', "[false]"])
def test_bad_custom_choices_leave_existing_config_untouched(
    tmp_path: Path,
    payload: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "joinery.toml"
    original = '[tools]\nenabled = ["godot"]\n'
    config.write_text(original)
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps",
                payload,
            ],
            monkeypatch,
        )
        == 1
    )
    assert config.read_text() == original
    assert not (tmp_path / ".mcp.json").exists()


def test_blank_custom_command_is_not_saved() -> None:
    tool = SDXL.model_dump()
    tool["command"] = " "
    with pytest.raises(ValueError, match="launch command"):
        custom_choices(json.dumps([tool]), [])


def test_custom_choices_file_keeps_executable_and_arguments_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choice = SDXL.model_dump()
    choice["command"] = '/Applications/Tool "quoted"/mcp'
    choice["args"] = ["$(touch NEVER_EXECUTE)", '"quoted"', "a b", "\n"]
    choices_file = tmp_path / "choices.json"
    choices_file.write_text(json.dumps([choice]))
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps-file",
                str(choices_file),
            ],
            monkeypatch,
        )
        == 0
    )
    custom = load_project_tools(tmp_path / "joinery.toml").custom[0]
    assert custom.command == choice["command"]
    assert list(custom.args) == choice["args"]
    assert not (tmp_path / "NEVER_EXECUTE").exists()


def test_missing_choices_file_leaves_project_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        _cli(
            [
                "setup",
                "--project",
                str(tmp_path),
                "--tools",
                "",
                "--custom-mcps-file",
                str(tmp_path / "missing.json"),
            ],
            monkeypatch,
        )
        == 1
    )
    assert not (tmp_path / "joinery.toml").exists()


def _cli(arguments: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["joinery", *arguments])
    return main()
