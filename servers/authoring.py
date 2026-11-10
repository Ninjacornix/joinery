"""Shared native authoring tools; project staging is owned by the parent gateway."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import tools
from joinery.authoring import edit_document, edit_graph, edit_vector, run_offline
from joinery.authoring import inspect_document as inspect_native
from joinery.catalog import load_catalog, load_project_tools, selection_ids
from servers.errors import tool_errors


def make_server(project: Path, family: str) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(family)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_document(tool: str, source: str) -> dict[str, Any]:
        """Inspect actual named objects/materials/layers or native graph/patch content before editing a source. Never saves the source document."""
        return await inspect_native(project, tool, source)

    if family == "joinery-blender-ops":

        @server.tool()
        @tool_errors
        async def transform_parts(
            source: str, transforms: list[dict[str, Any]], exports: list[str]
        ) -> dict[str, Any]:
            """Change named Blender source parts with typed position/rotation_degrees/scale arrays. Preserves the native model and refreshes existing GLB exports with a replayable operation record."""
            for change in transforms:
                if (
                    not isinstance(change.get("name"), str)
                    or not change["name"].strip()
                    or set(change)
                    - {
                        "name",
                        "position",
                        "rotation_degrees",
                        "scale",
                    }
                ):
                    raise ValueError(
                        "Each transform needs a source object name and supported transform fields."
                    )
                for key, value in change.items():
                    if key != "name" and (
                        not isinstance(value, list)
                        or len(value) != 3
                        or not all(
                            isinstance(x, (int, float))
                            and not isinstance(x, bool)
                            and math.isfinite(x)
                            for x in value
                        )
                    ):
                        raise ValueError(
                            "Transform values must be finite numeric arrays of length three."
                        )
            import json

            code = (
                "import math\nfor change in "
                + repr(transforms)
                + ":\n    obj = bpy.data.objects[change['name']]\n    if 'position' in change: obj.location = change['position']\n    if 'scale' in change: obj.scale = change['scale']\n    if 'rotation_degrees' in change: obj.rotation_euler = [math.radians(x) for x in change['rotation_degrees']]\n"
            )
            return await edit_document(
                project,
                "blender",
                source,
                code,
                exports,
                "Named part transforms: " + json.dumps(transforms),
            )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def document_capabilities() -> dict[str, Any]:
        """Describe native formats and actual offline execution requirements."""
        selected = set(
            selection_ids(load_catalog(), load_project_tools(project / "joinery.toml"))
        )
        # Each tool names every program its adapter runs (one name, or a list
        # when it needs several); all of them must be present.
        programs: dict[str, str | list[str]] = {
            key: value[0] if len(value) == 1 else list(value)
            for module in tools.family_tools(family)
            for key, value in module.capability_requirements().items()
            if key in selected
        }
        got = await run_offline(
            [
                "python3",
                "-c",
                "import shutil,json; print(json.dumps({k:all(shutil.which(p) for p in ([v] if isinstance(v,str) else v)) for k,v in "
                + repr(programs)
                + ".items()}))",
            ],
            project,
            30,
            readonly=True,
        )
        installed = json.loads(got.stdout) if got.ok else {}
        return {
            "tools": [
                {"id": tool, "application_installed": installed.get(tool, False)}
                for tool in programs
            ],
            "runtime": "joinery-creative-worker",
            "runtime_available": got.ok,
            "network": "none",
            "review_required": True,
            "source_policy": "Edit existing native sources. Raw scripts are logged and replayable. Art direction must be approved before mutation.",
        }

    allowed = tools.script_tools(family)
    if allowed:

        @server.tool()
        @tool_errors
        async def operate_document(
            tool: str, source: str, code: str, exports: list[str], note: str = ""
        ) -> dict[str, Any]:
            """Operate a native document in its actual offline app. Blender code uses bpy from factory settings; code that names MPFB2 (extension bl_ext.system.mpfb) or Rigify gets it enabled first, as addon_utils.enable(name, default_set=True); Krita code receives app/document (use Krita for pixel art too); GIMP code is GIMP 3 Python-Fu (gi.repository Gimp, Gegl) that receives `image` (None for a new .xcf: create it with Gimp.Image.new); target parts with find_layer(image, name) (searches groups) and find_channel(image, name) plus image.select_item; colours via Gegl.Color.new('#rrggbb') (Color.set_rgba is linear); exports are PNGs, the composite unless layer_exports[path] = 'layer name' exports one layer; SuperCollider code is a complete NRT patch with output paths and exit. Surge XT code is the JSON source: {"synth": "surge-xt", "seconds", "sample_rate", "parameters": {name: value in Surge's display units}, "notes": [{"pitch", "start", "duration", "velocity"}]} with WAV exports; inspect a Surge source to list every parameter with its default. Save/export and replay recording are automatic except the SuperCollider render. Use art/sources/ for .blend and art/models/ for GLB exports. Read document/source context first; retain existing named parts. Requires approved art direction and user review."""
            if tool not in allowed:
                raise ValueError("Use this application's owning server.")
            return await edit_document(project, tool, source, code, exports, note)

    if family == "joinery-materialmaker":

        @server.tool()
        @tool_errors
        async def operate_graph(
            source: str, graph: dict[str, Any], output_directory: str
        ) -> dict[str, Any]:
            """Save a native Material Maker graph and export its real Godot material/textures offline. Preserve existing graph node ids and links when editing."""
            return await edit_graph(project, source, graph, output_directory)

    if family == "joinery-paint":

        @server.tool()
        @tool_errors
        async def operate_vector(source: str, svg: str, export: str) -> dict[str, Any]:
            """Edit a self-contained native Inkscape SVG document and export PNG through Inkscape, preserving existing element ids."""
            return await edit_vector(project, source, svg, export)

    # A family's typed operations live in servers/<package>/ops.py, which
    # exposes register(server, project).
    package = tools.FAMILY_PACKAGES.get(family)
    if package and importlib.util.find_spec(f"servers.{package}.ops") is not None:
        importlib.import_module(f"servers.{package}.ops").register(server, project)

    return server


def main(family: str) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    make_server(args.project.resolve(), family).run()
