from pathlib import Path

import pytest

from joinery.catalog import load_catalog
from joinery.installer import menu_tools, run_menu, validate_choice, write_selection


def number(tool_id: str) -> str:
    ids = [t.id for t in menu_tools(load_catalog())]
    return str(ids.index(tool_id) + 1)


def drive(answers: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    shown: list[str] = []
    pending = iter(answers)
    result = run_menu(load_catalog(), current, lambda _: next(pending), shown.append)
    return result, shown


def test_toggles_tools() -> None:
    result, _ = drive([f"{number('aseprite')} {number('blender')}", ""], ["blender"])
    assert result == ["aseprite", "godot"]


def test_required_tool_cannot_be_removed() -> None:
    result, shown = drive([number("godot"), ""], [])
    assert result == ["godot"]
    assert any("required" in line for line in shown)


def test_tool_without_mcp_cannot_be_selected() -> None:
    result, shown = drive([number("krita"), ""], [])
    assert result == ["godot"]
    assert any("no vetted MCP" in line for line in shown)


def test_bad_input_is_ignored() -> None:
    result, shown = drive(["99 abc", ""], [])
    assert result == ["godot"]
    assert any("Unknown choice" in line for line in shown)


def test_noninteractive_rejects_unavailable_tool() -> None:
    with pytest.raises(ValueError, match="krita"):
        validate_choice(load_catalog(), ["krita"])


def test_write_selection_keeps_other_settings(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text('[audio]\nx = 1\n\n[tools]\nenabled = [\n  "a",\n]\n')
    write_selection(["blender", "godot"], config)
    assert (
        config.read_text()
        == '[audio]\nx = 1\n\n[tools]\nenabled = ["blender", "godot"]\n'
    )


def test_write_selection_adds_section(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text("[audio]\nx = 1\n")
    write_selection(["godot"], config)
    assert '[tools]\nenabled = ["godot"]' in config.read_text()
