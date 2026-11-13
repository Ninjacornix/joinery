"""Blender: baked pixels survive the save, extensions load on demand, and the
official MCP's window-only tools are never offered or allowed."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest
from mcp.types import Tool

from joinery import tools
from joinery.catalog import blocked_tools, load_catalog
from joinery.gateway import ProjectGateway
from joinery.hook import Severity, mcp_violation
from joinery.mcp_runtime import Readiness, ServerSpec
from joinery.tools.base import WorkerExec
from joinery.tools.blender import (
    BAKE_FOR_EXPORT,
    EXPORT_TEXTURES,
    MPFB_HINT,
    MPFB_HUMAN,
    PACK_CHANGED_IMAGES,
    SETTLE_IMAGES,
    recreated_images,
)

BLENDER = tools.get("blender")

#: The official Blender MCP tools that need Blender's window. Each returns
#: "not available in background mode" in the offline worker (probed against
#: blender_mcp 2cea8d56 in joinery-blender-worker).
WINDOW_ONLY = {
    "get_screenshot_of_area_as_image",
    "get_screenshot_of_window_as_image",
    "get_screenshot_of_window_as_json",
    "jump_to_tab_by_name",
    "jump_to_tab_by_space_type",
    "jump_to_view3d_object_by_name",
    "jump_to_view3d_object_data_by_name",
}


def _script(code: str, base: str = "") -> str:
    return BLENDER.script(
        "art/sources/crate.blend", base, code, ["art/models/crate.glb"]
    )


def test_changed_images_are_packed_before_pack_all_and_the_save() -> None:
    script = _script("pass")
    pack = script.index(PACK_CHANGED_IMAGES)
    assert pack < script.index("bpy.ops.file.pack_all()")
    assert pack < script.index("bpy.ops.wm.save_as_mainfile(")
    assert pack < script.index("_joinery_export_textures(os.path")
    # pack_all() alone skips GENERATED images and never repacks a packed one.
    assert "is_dirty" in PACK_CHANGED_IMAGES and "'GENERATED'" in PACK_CHANGED_IMAGES
    compile(script, "recipe.py", "exec")


def test_export_applies_modifiers_after_the_save_keeping_shape_keys() -> None:
    # Godot cannot run Blender modifiers, so the GLB gets them applied; meshes
    # with shape keys are baked first (the exporter would drop their keys).
    # Both happen after the save, so the .blend keeps its modifiers.
    script = _script("pass")
    save = script.index("bpy.ops.wm.save_as_mainfile(")
    bake = script.index("_joinery_bake_for_export()\n")
    export = script.index("bpy.ops.export_scene.gltf(")
    assert save < bake < export
    assert "export_apply=True" in script[export:]
    assert "show_only_shape_key" in BAKE_FOR_EXPORT
    assert "m.type != 'ARMATURE'" in BAKE_FOR_EXPORT
    no_export = BLENDER.script("art/sources/crate.blend", "", "pass", [])
    assert "_joinery_bake_for_export()" not in no_export


def test_an_image_made_again_is_found_with_its_copies_oldest_first() -> None:
    before = ["trim_albedo", "glass_albedo", "skin.001", "Render Result"]
    after = [
        *before,
        "trim_albedo.002",
        "trim_albedo.001",
        "glass_albedo.001",
        "new_map",
        "skin.002",
    ]
    assert recreated_images(before, after) == {
        "trim_albedo": ["trim_albedo.001", "trim_albedo.002"],
        "glass_albedo": ["glass_albedo.001"],
    }
    # A suffixed name the model already had is its own image, not a copy.
    assert recreated_images(["a", "a.001"], ["a", "a.001"]) == {}


def test_a_rebaked_image_replaces_the_original_before_the_save() -> None:
    script = _script("bpy.data.images.new('trim_albedo', 8, 8)")
    snapshot = script.index("_joinery_images_before = [")
    code = script.index("bpy.data.images.new('trim_albedo', 8, 8)")
    settle = script.index("_joinery_settle_images(_joinery_images_before)")
    assert snapshot < code < settle < script.index(PACK_CHANGED_IMAGES)
    assert settle < script.index("bpy.ops.wm.save_as_mainfile(")
    assert "user_remap" in SETTLE_IMAGES and "keep.name = original" in SETTLE_IMAGES
    # An image nothing uses is dropped from the saved .blend; no file for it.
    assert "image.users == 0" in EXPORT_TEXTURES
    compile(script, "recipe.py", "exec")
    # Blender runs the very same naming logic.
    assert SETTLE_IMAGES.startswith(inspect.getsource(recreated_images))


@pytest.mark.parametrize(
    ("code", "module"),
    [
        (
            "from bl_ext.system.mpfb.services.humanservice import HumanService",
            "bl_ext.system.mpfb",
        ),
        ("bpy.ops.pose.rigify_generate()", "rigify"),
    ],
)
def test_extensions_named_in_the_code_are_enabled_before_it(
    code: str, module: str
) -> None:
    script = _script(code, base="art/sources/crate.blend")
    enable = script.index(f"addon_utils.enable({module!r}, default_set=True)")
    assert script.index("open_mainfile") < enable < script.index(code)
    assert "Refresh tools in the Joinery sidebar." in script
    compile(script, "recipe.py", "exec")


def test_plain_code_enables_no_extension() -> None:
    code = "bpy.ops.mesh.primitive_cube_add(size=1)"
    script = _script(code)
    assert "addon_utils" not in script[: script.index(code)]
    # The export turns MPFB2 on only for a file holding an MPFB2 human, to
    # read what each object is (body, clothes) for picking in the editor.
    assert "def _joinery_enable_mpfb():" in script


def test_agents_are_told_how_to_enable_mpfb() -> None:
    from servers.authoring import make_server

    assert "bl_ext.system.mpfb" in MPFB_HINT and "default_set=True" in MPFB_HINT
    assert dict(BLENDER.mcp_notes)["execute_blender_code"].endswith(MPFB_HINT)
    server = make_server(Path("."), "joinery-blender-ops")
    described = {
        tool.name: tool.description or "" for tool in asyncio.run(server.list_tools())
    }
    assert "bl_ext.system.mpfb" in described["operate_document"]


def test_readiness_checks_rigify_and_mpfb_in_the_worker() -> None:
    by_feature: dict[str, list[WorkerExec]] = {}
    for check in BLENDER.checks:
        if isinstance(check, WorkerExec):
            by_feature.setdefault(check.feature, []).append(check)
    assert any("'rigify'" in " ".join(c.argv) for c in by_feature["rigify"])
    mpfb = by_feature["mpfb"]
    assert any(
        c.tier == 2
        and c.on_failure == "needs_image"
        and "'bl_ext.system.mpfb'" in " ".join(c.argv)
        for c in mpfb
    )
    deep = next(c for c in mpfb if c.tier == "deep")
    assert MPFB_HUMAN in deep.argv
    # Its pass marker accepts an absent MPFB2: the tier-2 check owns that state.
    import re

    assert re.search(deep.expect, "JOINERY_MPFB_ABSENT")
    assert re.search(deep.expect, "JOINERY_MPFB_HUMAN 19158")
    assert not re.search(deep.expect, "JOINERY_MPFB_HUMAN 0")


def test_window_only_blender_mcp_tools_are_blocked_by_policy(tmp_path: Path) -> None:
    assert set(blocked_tools(load_catalog(), "blender")) == WINDOW_ONLY
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    for name in WINDOW_ONLY:
        verdict = mcp_violation("blender", name, tmp_path)
        assert verdict is not None and verdict.severity is Severity.DENY
    for name in (
        "execute_blender_code",
        "render_viewport_to_path",
        "get_objects_summary",
    ):
        assert mcp_violation("blender", name, tmp_path) is None


class _FakeConnection:
    def __init__(self, spec: ServerSpec, project: Path) -> None:
        names = sorted(
            WINDOW_ONLY | {"execute_blender_code", "render_viewport_to_path"}
        )
        self.tools = {
            name: Tool(
                name=name,
                description=f"{name} upstream.",
                input_schema={"type": "object"},
            )
            for name in names
        }
        self.readiness = Readiness(
            name=spec.name,
            state="discovered",
            installed=True,
            connected=True,
            tools=names,
        )

    async def connect(self, timeout: float = 30) -> None:
        return None

    async def close(self) -> None:
        return None


def test_discovery_hides_blocked_tools_and_adds_the_mpfb_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    spec = ServerSpec(name="blender", command="docker", source="sandbox")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([spec], []))
    monkeypatch.setattr("joinery.gateway.MCPConnection", _FakeConnection)

    async def run() -> dict[str, Any]:
        gateway = ProjectGateway(tmp_path)
        try:
            return await gateway.discover()
        finally:
            await gateway.close()

    found = {tool["name"]: tool for tool in asyncio.run(run())["tools"]}
    assert set(found) == {"execute_blender_code", "render_viewport_to_path"}
    assert found["execute_blender_code"]["description"].startswith(
        "execute_blender_code upstream."
    )
    assert found["execute_blender_code"]["description"].endswith(MPFB_HINT)
    assert (
        found["render_viewport_to_path"]["description"]
        == "render_viewport_to_path upstream."
    )
