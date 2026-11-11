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

from joinery import parts, tools
from joinery.authoring import (
    edit_document,
    edit_graph,
    edit_vector,
    run_offline,
)
from joinery.authoring import inspect_document as inspect_native
from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.recipe import load_recipe, recipe_paths
from joinery.tools import blender
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

        @server.tool()
        @tool_errors
        async def set_material_value(
            source: str,
            material: str,
            socket: str,
            value: float | list[float],
            exports: list[str] | None = None,
        ) -> dict[str, Any]:
            """Set one shader value of a named Blender material that no map drives, such as Roughness 0.4 or Base Color [r, g, b] (0-1). Only the material in the user's part selection may change; anything else changing fails the edit. Exports default to the model's existing GLB."""
            _name(material, "material")
            _name(socket, "socket")
            numbers = value if isinstance(value, list) else [value]
            if not 1 <= len(numbers) <= 4 or not all(_finite(x) for x in numbers):
                raise ValueError("The value must be a finite number or 3 to 4 numbers.")
            parts.check_targets(
                parts.prompt_targets(project, source), material=material
            )
            return await _edit(
                project,
                source,
                blender.material_value_code(material, socket, value),
                _exports(project, source, exports),
                f"Set {socket} of {material} to {json.dumps(value)}",
            )

        @server.tool()
        @tool_errors
        async def adjust_map(
            source: str,
            material: str,
            image: str,
            op: str,
            params: dict[str, Any] | None = None,
            exports: list[str] | None = None,
        ) -> dict[str, Any]:
            """Change the pixels of one texture map a Blender material uses, so the game sees the change (shader nodes added between a map and the shader are lost on export). op is one of: invert; gamma {gamma}; levels {in_low, in_high, gamma, out_low, out_high}; contrast {factor}; tint {color: [r, g, b], strength}; hue_sat_value {hue (-0.5 to 0.5), saturation, value}. invert, gamma, levels and contrast take an optional channel (R, G, B or A) for packed maps such as ORM. Only the selected map may change. Exports default to the model's existing GLB."""
            _name(material, "material")
            _name(image, "image")
            given = dict(params or {})
            _map_params(op, given)
            parts.check_targets(
                parts.prompt_targets(project, source), material=material, image=image
            )
            return await _edit(
                project,
                source,
                blender.adjust_map_code(material, image, op, given),
                _exports(project, source, exports),
                f"Adjust map {image} of {material}: {op} {json.dumps(given, sort_keys=True)}",
            )

        @server.tool()
        @tool_errors
        async def bake_height_to_normal(
            source: str,
            material: str,
            height_image: str,
            strength: float = 1.0,
            exports: list[str] | None = None,
        ) -> dict[str, Any]:
            """Bake a Blender material's height map into a tangent-space normal map (<material>_normal_baked) wired to the shader, so the game shows the height detail: glTF cannot carry a height map. The height map keeps driving displacement in Blender. Needs UVs. Exports default to the model's existing GLB."""
            _name(material, "material")
            _name(height_image, "height image")
            if not _finite(strength) or not 0 < strength <= 10:
                raise ValueError("Strength must be above 0 and at most 10.")
            parts.check_targets(
                parts.prompt_targets(project, source),
                material=material,
                image=height_image,
            )
            return await _edit(
                project,
                source,
                blender.bake_height_code(material, height_image, float(strength)),
                _exports(project, source, exports),
                f"Bake {height_image} of {material} into a normal map",
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
            tool: str,
            source: str,
            code: str,
            exports: list[str],
            note: str = "",
            keep_macro_shape_keys: bool | None = None,
        ) -> dict[str, Any]:
            """Operate a native document in its actual offline app. Blender code uses bpy from factory settings; code that names MPFB2 (extension bl_ext.system.mpfb) or Rigify gets it enabled first, as addon_utils.enable(name, default_set=True); Krita code receives app/document (use Krita for pixel art too); GIMP code is GIMP 3 Python-Fu (gi.repository Gimp, Gegl) that receives `image` (None for a new .xcf: create it with Gimp.Image.new); target parts with find_layer(image, name) (searches groups) and find_channel(image, name) plus image.select_item; colours via Gegl.Color.new('#rrggbb') (Color.set_rgba is linear); exports are PNGs, the composite unless layer_exports[path] = 'layer name' exports one layer; SuperCollider code is a complete NRT patch with output paths and exit. Surge XT code is the JSON source: {"synth": "surge-xt", "seconds", "sample_rate", "parameters": {name: value in Surge's display units}, "notes": [{"pitch", "start", "duration", "velocity"}]} with WAV exports; inspect a Surge source to list every parameter with its default. Save/export and replay recording are automatic except the SuperCollider render. Use art/sources/ for .blend and art/models/ for GLB exports. Read document/source context first; retain existing named parts. Requires approved art direction and user review. Blender GLB exports bake MPFB2's body-shape macro shape keys ($md-…) into the mesh and keep other shape keys (expressions) as morph targets; keep_macro_shape_keys=true keeps the macros too, and the model remembers the choice (omit it to keep the current one)."""
            if tool not in allowed:
                raise ValueError("Use this application's owning server.")
            if keep_macro_shape_keys is not None and tool != "blender":
                raise ValueError("keep_macro_shape_keys is a Blender export option.")
            if keep_macro_shape_keys is None:
                return await edit_document(project, tool, source, code, exports, note)
            return await edit_document(
                project,
                tool,
                source,
                code,
                exports,
                note,
                keep_macro_shape_keys=keep_macro_shape_keys,
            )

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


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _name(value: str, what: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"Give the {what} by its name in the model.")


def _map_params(op: str, params: dict[str, Any]) -> None:
    """Refuse unknown adjustments and parameters before Blender starts."""
    if op not in blender.MAP_OPS:
        raise ValueError(
            f"Unknown map adjustment {op!r}. Use one of: {', '.join(blender.MAP_OPS)}."
        )
    unknown = set(params) - blender.MAP_OPS[op]
    if unknown:
        raise ValueError(
            f"{op} does not take {', '.join(sorted(unknown))}. "
            f"It takes: {', '.join(sorted(blender.MAP_OPS[op])) or 'nothing'}."
        )
    for key, value in params.items():
        if key == "channel":
            if str(value).upper() not in {
                "R",
                "G",
                "B",
                "A",
                "RED",
                "GREEN",
                "BLUE",
                "ALPHA",
            }:
                raise ValueError("channel is R, G, B or A.")
        elif key == "color":
            if (
                not isinstance(value, list)
                or len(value) != 3
                or not all(_finite(x) and 0 <= x <= 1 for x in value)
            ):
                raise ValueError("color is three numbers from 0 to 1.")
        elif not _finite(value):
            raise ValueError(f"{key} must be a finite number.")
    if op == "tint" and "color" not in params:
        raise ValueError("tint needs a color.")
    if params.get("gamma", 1) <= 0:
        raise ValueError("gamma must be above 0.")


async def _edit(
    project: Path, source: str, code: str, exports: list[str], note: str
) -> dict[str, Any]:
    """A Blender edit; edit_document puts the textures back when it fails."""
    return await edit_document(project, "blender", source, code, exports, note)


def _exports(project: Path, source: str, exports: list[str] | None) -> list[str]:
    """The given exports, or the model's GLB exports already on record."""
    if exports is not None:
        return exports
    for path in recipe_paths(project):
        recipe = load_recipe(path)
        if source in recipe.outputs:
            return [p for p in recipe.outputs if Path(p).suffix == ".glb"]
    return []


def main(family: str) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    make_server(args.project.resolve(), family).run()
