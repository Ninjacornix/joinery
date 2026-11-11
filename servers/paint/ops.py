"""Typed Krita operations on a painting of its own (a `.kra` under art/).

`edit_image` adds layers to an existing Krita document (a palette map, fills,
strokes, filter layers) or resizes it, then saves the document and refreshes
its exports, recording a replayable recipe exactly like `operate_document`.
Earlier layers are never painted over, so every step stays a layer the user
can hide or delete in Krita.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from joinery import authoring
from joinery.recipe import IMAGE_SUFFIXES, load_recipe, recipe_paths
from joinery.tools import krita_ops
from servers.errors import tool_errors


def recorded_exports(project: Path, source: str) -> list[str]:
    """The images a Krita document's recipe already exports."""
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except (OSError, ValueError):
            continue
        if source in recipe.outputs:
            return [
                o
                for o in recipe.outputs
                if o != source and Path(o).suffix.lower() in IMAGE_SUFFIXES
            ]
    return []


def _describe(row: dict[str, Any]) -> str:
    if row["op"] == "resize":
        return f"resize to {row['width']}x{row['height']}"
    if row["op"] == "palette_map":
        return f"palette map ({len(row['colours'])} colours, ΔE {row['max_delta_e']:g})"
    return f"{row['op']} {row['name']!r}"


async def edit_image(
    project: Path,
    source: str,
    ops: list[dict[str, Any]],
    exports: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    if Path(source).suffix.lower() != ".kra" or Path(source).parts[:1] != ("art",):
        raise ValueError("edit_image changes a Krita document (.kra) under art/.")
    if not authoring.confined(project, source).is_file():
        raise ValueError(f"{source} is missing; make it with operate_document first.")
    checked = krita_ops.check_ops(ops, krita_ops.IMAGE_OPS)
    if exports is None:
        exports = recorded_exports(project, source)
    summary = ", ".join(_describe(row) for row in checked)
    return await authoring.edit_document(
        project,
        "krita",
        source,
        krita_ops.paint_code(checked),
        exports,
        f"Image edit of {source}: {summary}." + (f" {note}" if note else ""),
    )


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the typed Krita operations to the joinery-paint server."""

    @server.tool(name="edit_image")
    @tool_errors
    async def edit_image_tool(
        source: str,
        ops: list[dict[str, Any]],
        exports: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Change an existing Krita painting (.kra under art/) with typed operations, each its own layer, in order: {"op": "palette_map", "colours": ["#rrggbb", …], "max_delta_e"} covers every pixel further than max_delta_e (CIEDE2000, default 12) from the palette with its nearest palette colour; {"op": "fill", "color", "rect"?}; {"op": "stroke", "points", "color", "width"}; {"op": "adjust", "filter": invert | levels | hue_saturation | blur | desaturate, "params"}; {"op": "resize", "width", "height"} scales the whole document. Every op takes an optional "name" and (except resize) "opacity". Exports default to the images the painting already exports. Requires approved art direction and user review."""
        return await edit_image(project, source, ops, exports, note)
