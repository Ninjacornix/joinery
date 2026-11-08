"""Pinned GIMP MCP functions over its real SDK, with an explicit fake plugin."""

import asyncio
import json
from types import SimpleNamespace

import gimp_mcp_server as upstream
import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError

REQUESTS = [
    (
        "new_canvas",
        {"width": 32, "height": 16, "name": "Technical texture", "fill": "transparent"},
    ),
    ("create_layer", {"name": "Paint", "opacity": 50, "image_index": 1}),
    (
        "rename_layer",
        {"old_name": "Paint", "new_name": "Colour — café", "image_index": 1},
    ),
    ("duplicate_layer", {"layer_name": "Paint", "image_index": 1}),
    ("delete_layer", {"layer_name": "Paint", "image_index": 1}),
    (
        "scale_image",
        {"width": 64, "height": 32, "interpolation": "nearest", "image_index": 1},
    ),
    ("crop_to_rect", {"x": 2, "y": 3, "width": 8, "height": 9, "image_index": 1}),
    (
        "resize_canvas",
        {"width": 128, "height": 64, "anchor": "center", "image_index": 1},
    ),
    (
        "fill_rectangle",
        {
            "x": 1,
            "y": 2,
            "width": 5,
            "height": 6,
            "color": "#ff0000",
            "layer_name": "Paint",
            "image_index": 1,
        },
    ),
    (
        "add_text",
        {
            "text": "Technical label — 音楽",
            "x": 2,
            "y": 3,
            "size": 12,
            "image_index": 1,
        },
    ),
    (
        "adjust_brightness_contrast",
        {"brightness": 10, "contrast": -5, "layer_name": "Paint", "image_index": 1},
    ),
    ("save_xcf", {"file_path": "/work/source.xcf", "image_index": 1}),
    *[
        (
            "export_image",
            {
                "file_path": "/work/export." + extension,
                "format": extension,
                "flatten": False,
                "image_index": 1,
            },
        )
        for extension in ("png", "jpeg", "webp", "tiff")
    ],
    ("undo", {"steps": 2, "image_index": 1}),
    ("redo", {"steps": 2, "image_index": 1}),
]


@pytest.fixture
def plugin(monkeypatch):
    calls = []

    def send(command, params):
        calls.append((command, params))
        return {
            "status": "success",
            "results": {
                "command": command,
                "params": params,
                "plugin_result": "observed",
            },
        }

    monkeypatch.setattr(
        upstream, "get_gimp_connection", lambda: SimpleNamespace(send_command=send)
    )
    return calls


@pytest.mark.parametrize("name,arguments", REQUESTS)
def test_gimp_edit_request_reaches_exact_image_layer_and_native_plugin_arguments(
    plugin, name, arguments
):
    result = asyncio.run(upstream.mcp.call_tool(name, arguments))
    payload = json.loads(result[0].text)
    assert plugin and plugin[-1][0] == name
    for key, value in arguments.items():
        assert plugin[-1][1][key] == value
    assert payload["plugin_result"] == "observed"
    assert payload["params"] == plugin[-1][1]


@pytest.mark.parametrize("name,arguments", REQUESTS)
def test_gimp_plugin_save_edit_and_export_failures_are_not_reported_as_success(
    monkeypatch, name, arguments
):
    monkeypatch.setattr(
        upstream,
        "get_gimp_connection",
        lambda: SimpleNamespace(
            send_command=lambda *_: {
                "status": "error",
                "error": "fixture: native operation failed",
            }
        ),
    )
    with pytest.raises(ToolError, match="native operation failed"):
        asyncio.run(upstream.mcp.call_tool(name, arguments))


def test_gimp_exposes_valid_schemas_for_every_registered_tool():
    tools = asyncio.run(upstream.mcp.list_tools())
    assert len(tools) >= 50
    assert len({tool.name for tool in tools}) == len(tools)
    for tool in tools:
        assert tool.description
        Draft202012Validator.check_schema(tool.inputSchema)


def test_missing_gimp_plugin_is_truthfully_disconnected(monkeypatch):
    def absent(_self):
        raise ConnectionError("fixture: GIMP plugin is not running")

    monkeypatch.setattr(upstream.GimpConnection, "connect", absent)
    result = asyncio.run(upstream.mcp.call_tool("check_server", {}))
    assert json.loads(result[0].text)["connected"] is False


@pytest.mark.parametrize("arguments", [{}, {"width": "large", "height": 16}])
def test_gimp_malformed_canvas_does_not_contact_plugin(plugin, arguments):
    with pytest.raises(ToolError):
        asyncio.run(upstream.mcp.call_tool("new_canvas", arguments))
    assert not plugin
