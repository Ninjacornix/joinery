from pathlib import Path

import pytest

from joinery.catalog import CustomTool, load_catalog, load_project_tools
from joinery.installer import (
    Selection,
    menu_tools,
    run_menu,
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
    result, shown = drive([number("krita"), ""], [])
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
    with pytest.raises(ValueError, match="krita"):
        validate_choice(load_catalog(), ["krita"])


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
