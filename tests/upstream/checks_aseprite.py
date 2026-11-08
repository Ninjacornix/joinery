"""Run the exact Aseprite MCP implementation against a fake CLI boundary.

No licensed executable is substituted or claimed to have rendered pixels.
"""

import asyncio
from pathlib import Path

import aseprite_mcp.tools  # noqa: F401 - Registers the pinned upstream tools.
import pytest
from aseprite_mcp import mcp
from aseprite_mcp.core.commands import AsepriteCommand, command_timeout
from jsonschema import Draft202012Validator


@pytest.fixture
def sprite(tmp_path):
    source = tmp_path / "hero's animation.aseprite"
    source.write_bytes(b"opaque unit fixture, not a real sprite")
    return str(source)


@pytest.fixture
def commands(monkeypatch):
    calls = []

    def run(arguments):
        script = (
            Path(arguments[arguments.index("--script") + 1]).read_text()
            if "--script" in arguments
            else ""
        )
        calls.append((list(arguments), script))
        if "--save-as" in arguments:
            Path(arguments[arguments.index("--save-as") + 1]).write_bytes(
                b"fake export"
            )
        return True, "OK"

    monkeypatch.setattr(AsepriteCommand, "run_command", run)
    return calls


def text(result):
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


def call(name, arguments):
    result = asyncio.run(mcp.call_tool(name, arguments))
    message = text(result)
    assert not message.startswith(("Failed", "ERROR", "Invalid")), message
    return message


@pytest.mark.parametrize(
    "name,arguments,expected_lua",
    [
        ("add_layer", {"layer_name": "Outline"}, "newLayer"),
        ("rename_layer", {"layer_name": "Paint", "new_name": "Colour"}, "Colour"),
        ("delete_layer", {"layer_name": "Paint"}, "deleteLayer"),
        ("add_frames", {"count": 3, "duration_ms": 80}, "80 / 1000.0"),
        ("set_frame_duration", {"frame_index": 1, "duration_ms": 120}, "120 / 1000.0"),
        ("set_layer_visibility", {"layer_name": "Paint", "visible": False}, "false"),
        ("set_layer_opacity", {"layer_name": "Paint", "opacity": 128}, "128"),
        ("set_tag", {"name": "walk", "from_frame": 1, "to_frame": 3}, "walk"),
        (
            "draw_rectangle_at",
            {
                "layer_name": "Paint",
                "frame_index": 1,
                "x": 2,
                "y": 3,
                "width": 4,
                "height": 5,
                "color": "#ff0000",
                "fill": True,
            },
            "Point(2, 3), Point(5, 7)",
        ),
    ],
)
def test_aseprite_sprite_layer_frame_tag_and_drawing_tools_target_existing_document(
    sprite, commands, name, arguments, expected_lua
):
    call(name, {"filename": sprite, **arguments})
    argv, lua = commands[-1]
    assert sprite in argv
    assert expected_lua in lua
    assert "--batch" in argv and "--script" in argv


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("add_layer", {"layer_name": "Paint"}),
        ("rename_layer", {"layer_name": "Paint", "new_name": "Colour"}),
        ("delete_layer", {"layer_name": "Paint"}),
        ("add_frames", {"count": 2}),
        ("set_layer_visibility", {"layer_name": "Paint", "visible": False}),
    ],
)
def test_aseprite_lua_failure_cannot_turn_into_fabricated_success(
    sprite, monkeypatch, name, arguments
):
    monkeypatch.setattr(
        AsepriteCommand,
        "run_command",
        lambda _args: (True, "ERROR:fixture operation failed\n"),
    )
    result = asyncio.run(mcp.call_tool(name, {"filename": sprite, **arguments}))
    assert "Failed" in text(result) and "fixture operation failed" in text(result)


@pytest.mark.parametrize("extension", ["png", "gif", "jpg"])
def test_aseprite_export_checks_actual_output_existence(
    sprite, commands, tmp_path, extension
):
    target = tmp_path / ("walk." + extension)
    call(
        "export_sprite",
        {"filename": sprite, "output_filename": str(target), "format": extension},
    )
    assert target.is_file()
    assert commands[-1][0] == ["--batch", sprite, "--save-as", str(target)]


def test_aseprite_zero_exit_without_export_is_a_failure(sprite, monkeypatch, tmp_path):
    monkeypatch.setattr(AsepriteCommand, "run_command", lambda _args: (True, ""))
    result = asyncio.run(
        mcp.call_tool(
            "export_sprite",
            {"filename": sprite, "output_filename": str(tmp_path / "missing.png")},
        )
    )
    assert "Failed" in text(result) and "wrote no file" in text(result)


@pytest.mark.parametrize("width,height", [(0, 16), (-1, 16), (16, 0)])
def test_aseprite_bad_canvas_size_does_not_launch_cli(commands, width, height):
    result = asyncio.run(
        mcp.call_tool("create_canvas", {"width": width, "height": height})
    )
    assert "must be > 0" in text(result)
    assert not commands


@pytest.mark.parametrize(
    "value,expected",
    [("0", 60), ("-1", 60), ("nan", 60), ("inf", 60), ("soon", 60), ("2.5", 2.5)],
)
def test_aseprite_execution_deadline_is_always_bounded(monkeypatch, value, expected):
    monkeypatch.setenv("ASEPRITE_TIMEOUT_SECONDS", value)
    assert command_timeout() == expected


def test_aseprite_every_registered_tool_has_a_valid_schema():
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) >= 100 and len({tool.name for tool in tools}) == len(tools)
    for tool in tools:
        Draft202012Validator.check_schema(tool.inputSchema)
