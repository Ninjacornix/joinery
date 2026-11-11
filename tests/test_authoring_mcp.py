"""User-facing native tool contracts over the actual MCP SDK in-memory transport."""

import asyncio
import json
import math
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from joinery.mcp_runtime import PolicyStop
from joinery.runner import Result
from servers import authoring

FAMILIES = {
    "joinery-blender-ops": {
        "inspect_document",
        "document_capabilities",
        "operate_document",
        "transform_parts",
        # Typed part edits (servers/authoring.py).
        "set_material_value",
        "adjust_map",
        "bake_height_to_normal",
        # Maps, material copies, bones and shape keys (servers/blender_ops/ops.py).
        "make_map_editable",
        "paint_map",
        "replace_map",
        "split_material",
        "edit_bone",
        "edit_shape_key",
        # Whole-model operations (servers/blender_ops/model_ops.py).
        "apply_scale",
        "set_pivot",
        "reduce_triangles",
        "set_texel_density",
        # Parametric kits (servers/blender_ops/kits.py).
        "kit_list",
        "kit_make",
        "kit_batch",
        "kit_edit",
        "scatter",
        # Animations (servers/blender_ops/animation.py).
        "make_cycle",
        "edit_action",
        "audit_animations",
        "animation_pass",
    },
    "joinery-paint": {
        "inspect_document",
        "document_capabilities",
        "operate_document",
        "operate_vector",
        # Typed Krita operations on a painting (servers/paint/ops.py).
        "edit_image",
    },
    "joinery-sound": {
        "inspect_document",
        "document_capabilities",
        "operate_document",
        # Typed sound operations (servers/sound/ops.py).
        "inspect_music",
        "compose_music",
        "edit_music",
        "render_music",
        "process_audio",
        "set_surge_parameters",
        # Adaptive music (servers/sound/tracks.py).
        "inspect_track",
        "compose_track",
        "edit_track",
    },
    "joinery-materialmaker": {
        "inspect_document",
        "document_capabilities",
        "operate_graph",
    },
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled=["blender","krita","inkscape","supercollider","surge-xt","material-maker"]\n'
    )
    return tmp_path


def request(project: Path, family: str, tool: str, arguments: dict):
    async def run():
        async with Client(authoring.make_server(project, family)) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


@pytest.mark.parametrize("family", FAMILIES)
def test_discovery_exposes_only_real_family_capabilities(
    project: Path, family: str
) -> None:
    async def run() -> None:
        async with Client(authoring.make_server(project, family)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == FAMILIES[family]
            readers = {
                "inspect_document",
                "document_capabilities",
                "inspect_music",
                "kit_list",
                "audit_animations",
                "inspect_track",
            }
            for name in readers & set(tools):
                assert tools[name].annotations.read_only_hint is True
            for name in set(tools) - readers:
                assert (
                    tools[name].annotations is None
                    or not tools[name].annotations.read_only_hint
                )
                assert tools[name].input_schema["type"] == "object"
            assert not any("approve" in name or "accept" in name for name in tools)

    asyncio.run(run())


@pytest.mark.parametrize(
    "family,tool,source",
    [
        ("joinery-blender-ops", "blender", "art/sources/hammer.blend"),
        ("joinery-paint", "krita", "art/icon.kra"),
        ("joinery-sound", "surge-xt", "audio/sources/creak.json"),
        ("joinery-sound", "supercollider", "audio/sources/impact.scd"),
    ],
)
def test_user_native_operation_routes_exact_source_code_and_exports(
    project: Path, monkeypatch: pytest.MonkeyPatch, family: str, tool: str, source: str
) -> None:
    operation = AsyncMock(return_value={"status": "staged", "requires_review": True})
    monkeypatch.setattr(authoring, "edit_document", operation)
    exports = ["audio/impact.wav"] if family == "joinery-sound" else ["art/export.png"]
    result = request(
        project,
        family,
        "operate_document",
        {
            "tool": tool,
            "source": source,
            "code": "retain existing parts — café",
            "exports": exports,
            "note": "user requested recolour",
        },
    )
    assert not result.is_error
    assert json.loads(result.content[0].text) == {
        "status": "staged",
        "requires_review": True,
    }
    operation.assert_awaited_once_with(
        project,
        tool,
        source,
        "retain existing parts — café",
        exports,
        "user requested recolour",
    )


@pytest.mark.parametrize(
    "family,wrong_tool",
    [
        ("joinery-blender-ops", "krita"),
        ("joinery-paint", "blender"),
        ("joinery-paint", "inkscape"),
        ("joinery-sound", "krita"),
        ("joinery-paint", "aseprite"),
        ("joinery-sound", "gimp"),
    ],
)
def test_wrong_owning_server_refuses_before_native_execution(
    project: Path, monkeypatch: pytest.MonkeyPatch, family: str, wrong_tool: str
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(authoring, "edit_document", operation)
    result = request(
        project,
        family,
        "operate_document",
        {"tool": wrong_tool, "source": "art/source", "code": "", "exports": []},
    )
    assert result.is_error
    assert "owning server" in result.content[0].text
    operation.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"tool": "krita"},
        {
            "tool": "krita",
            "source": "art/icon.kra",
            "code": "",
            "exports": "art/icon.png",
        },
    ],
)
def test_malformed_mcp_input_never_reaches_native_application(
    project: Path, monkeypatch: pytest.MonkeyPatch, arguments: dict
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(authoring, "edit_document", operation)
    assert request(project, "joinery-paint", "operate_document", arguments).is_error
    operation.assert_not_awaited()


@pytest.mark.parametrize(
    "error,expected",
    [
        (ValueError("source document is missing"), "source document is missing"),
        (
            PermissionError("select this application first"),
            "select this application first",
        ),
        (RuntimeError("Krita could not save"), "Krita could not save"),
        (PolicyStop("denied package"), "Rule Zero STOP: denied package"),
    ],
)
def test_expected_application_errors_retain_actionable_reason_on_mcp_wire(
    project: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    monkeypatch.setattr(authoring, "inspect_native", AsyncMock(side_effect=error))
    result = request(
        project,
        "joinery-paint",
        "inspect_document",
        {"tool": "krita", "source": "art/icon.kra"},
    )
    assert result.is_error
    assert expected in result.content[0].text
    assert "Traceback" not in result.content[0].text


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("position", [1, 2, -3], [1, 2, -3]),
        ("scale", [2, 0.5, -1], [2, 0.5, -1]),
        ("rotation_degrees", [90, -180, 45], [math.pi / 2, -math.pi, math.pi / 4]),
    ],
)
@pytest.mark.parametrize("part_name", ["Head", "Écrou", "Part 'quoted' $(literal)"])
def test_named_blender_transform_changes_only_requested_part(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: list,
    expected: list,
    part_name: str,
) -> None:
    operation = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "edit_document", operation)
    result = request(
        project,
        "joinery-blender-ops",
        "transform_parts",
        {
            "source": "art/sources/hammer.blend",
            "transforms": [{"name": part_name, field: value}],
            "exports": ["art/models/hammer.glb"],
        },
    )
    assert not result.is_error
    head = SimpleNamespace(
        location=[0, 0, 0], scale=[1, 1, 1], rotation_euler=[0, 0, 0]
    )
    grip = SimpleNamespace(
        location=[0, -1, 0], scale=[1, 1, 1], rotation_euler=[0, 0, 0]
    )
    code = operation.await_args.args[3]
    script = project / "named-part-operation.py"
    script.write_text(code)
    runpy.run_path(
        str(script),
        init_globals={
            "bpy": SimpleNamespace(
                data=SimpleNamespace(objects={part_name: head, "Grip": grip})
            )
        },
    )
    attribute = {
        "position": "location",
        "scale": "scale",
        "rotation_degrees": "rotation_euler",
    }[field]
    assert getattr(head, attribute) == pytest.approx(expected)
    assert vars(grip) == {
        "location": [0, -1, 0],
        "scale": [1, 1, 1],
        "rotation_euler": [0, 0, 0],
    }
    assert operation.await_args.args[:3] == (
        project,
        "blender",
        "art/sources/hammer.blend",
    )
    assert operation.await_args.args[4] == ["art/models/hammer.glb"]


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"name": None},
        {"name": ""},
        {"name": "   "},
        {"name": "Head", "delete": True},
        {"name": "Head", "position": [1, 2]},
        {"name": "Head", "scale": [1, 2, 3, 4]},
        {"name": "Head", "position": "1,2,3"},
        {"name": "Head", "rotation_degrees": [0, "ninety", 0]},
        *[
            {"name": "Head", field: [value, 0, 0]}
            for field in ("position", "scale", "rotation_degrees")
            for value in (True, float("nan"), float("inf"), -float("inf"))
        ],
    ],
)
def test_invalid_named_transforms_are_rejected_before_record_or_worker(
    project: Path, monkeypatch: pytest.MonkeyPatch, change: dict
) -> None:
    operation = AsyncMock()
    monkeypatch.setattr(authoring, "edit_document", operation)
    result = request(
        project,
        "joinery-blender-ops",
        "transform_parts",
        {
            "source": "art/sources/hammer.blend",
            "transforms": [change],
            "exports": ["art/models/hammer.glb"],
        },
    )
    assert result.is_error
    operation.assert_not_awaited()


@pytest.mark.parametrize(
    "family,selected",
    [
        ("joinery-blender-ops", ["blender"]),
        ("joinery-paint", ["krita", "inkscape"]),
        ("joinery-sound", ["supercollider", "surge-xt"]),
        ("joinery-materialmaker", ["material-maker"]),
    ],
)
@pytest.mark.parametrize(
    "available", [False, True], ids=["worker-unavailable", "worker-present"]
)
def test_capabilities_report_actual_runtime_and_do_not_equate_selection_with_installation(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    selected: list[str],
    available: bool,
) -> None:
    execute = AsyncMock(
        return_value=Result(
            available,
            json.dumps({tool: tool != "inkscape" for tool in selected}),
            "",
            0.01,
        )
    )
    monkeypatch.setattr(authoring, "run_offline", execute)
    result = request(project, family, "document_capabilities", {})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["runtime_available"] is available
    assert data["review_required"] is True and data["network"] == "none"
    assert {t["id"] for t in data["tools"]} == set(selected)
    assert {t["id"] for t in data["tools"] if t["application_installed"]} == (
        set(selected) - {"inkscape"} if available else set()
    )
    assert execute.await_args.kwargs == {"readonly": True}


def test_graph_and_vector_mcp_routes_preserve_user_inputs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = {"nodes": [{"id": "user-node"}], "connections": []}
    edit_graph = AsyncMock(return_value={"status": "staged"})
    vector = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "edit_graph", edit_graph)
    monkeypatch.setattr(authoring, "edit_vector", vector)
    assert not request(
        project,
        "joinery-materialmaker",
        "operate_graph",
        {
            "source": "art/stone.ptex",
            "graph": graph,
            "output_directory": "art/textures",
        },
    ).is_error
    assert not request(
        project,
        "joinery-paint",
        "operate_vector",
        {"source": "art/icon.svg", "svg": "<svg/>", "export": "art/icon.png"},
    ).is_error
    edit_graph.assert_awaited_once_with(
        project, "art/stone.ptex", graph, "art/textures"
    )
    vector.assert_awaited_once_with(project, "art/icon.svg", "<svg/>", "art/icon.png")


@pytest.mark.parametrize("field", ["position", "scale", "rotation_degrees"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_local_sdk_nonfinite_transform_is_rejected_even_before_json_serialization(
    project: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: float
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    operation = AsyncMock()
    monkeypatch.setattr(authoring, "edit_document", operation)
    server = authoring.make_server(project, "joinery-blender-ops")
    with pytest.raises(ToolError, match="finite numeric"):
        asyncio.run(
            server.call_tool(
                "transform_parts",
                {
                    "source": "art/sources/assembly.blend",
                    "transforms": [{"name": "Head", field: [value, 0, 0]}],
                    "exports": ["art/models/assembly.glb"],
                },
            )
        )
    operation.assert_not_awaited()
