"""A typed Inkscape operation: change one element of an SVG by its id.

`edit_vector_element` reads the project's own SVG, changes one element's
attributes, style properties, transform or text after checking each value,
and saves and exports the document through Inkscape exactly as
`operate_vector` does. The recipe carries the whole document it wrote and its
note names the change, so `joinery rebuild` replays it byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from joinery import authoring
from joinery.recipe import load_recipe, recipe_paths
from joinery.tools import inkscape
from servers.errors import tool_errors


def recorded_export(project: Path, source: str) -> str:
    """The PNG an SVG's recipe already exports."""
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except (OSError, ValueError):
            continue
        if source in recipe.outputs:
            for output in recipe.outputs:
                if output != source and Path(output).suffix.lower() == ".png":
                    return output
    raise ValueError(f"{source} has no recorded PNG export; name the export.")


async def edit_vector_element(
    project: Path,
    source: str,
    element_id: str,
    *,
    attributes: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    transform: str | None = None,
    clear_transform: bool = False,
    text: str | None = None,
    export: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    source = source.removeprefix("res://")
    if Path(source).suffix.lower() != ".svg" or Path(source).parts[:1] != ("art",):
        raise ValueError("edit_vector_element changes an SVG under art/.")
    path = authoring.confined(project, source)
    if not path.is_file():
        raise ValueError(f"{source} is missing; make it with operate_vector first.")
    if path.stat().st_size > 5_000_000:
        raise ValueError(f"{source} is over 5 MB; edit it in Inkscape.")
    svg, changed = inkscape.edit_element(
        path.read_text(encoding="utf-8"),
        element_id,
        attributes=attributes,
        style=style,
        transform=transform,
        clear_transform=clear_transform,
        text=text,
    )
    target = (
        export.removeprefix("res://") if export else recorded_export(project, source)
    )
    summary = json.dumps(changed, sort_keys=True)
    result = await authoring.edit_vector(
        project,
        source,
        svg,
        target,
        f"Element {element_id} of {source}: {summary}." + (f" {note}" if note else ""),
    )
    return {**result, "element": element_id, "changed": changed}


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the typed Inkscape operation to the joinery-paint server."""

    @server.tool(name="edit_vector_element")
    @tool_errors
    async def edit_vector_element_tool(
        source: str,
        element_id: str,
        attributes: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
        transform: str | None = None,
        clear_transform: bool = False,
        text: str | None = None,
        export: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Change one element of an Inkscape SVG under art/, named by its id, and export the PNG through Inkscape. attributes: {name: value} (null removes; prefixed names such as inkscape:label use the document's namespaces; the id, event handlers and links outside the document are refused); style: {property: value} merged into the style attribute (null removes; url() only to #ids); transform: an SVG transform list (matrix, translate, scale, rotate, skewX, skewY), or clear_transform to remove it; text: the text of a <text>, <tspan>, <title> or <desc> holding no other elements. Everything else in the document stays as it is. The export defaults to the PNG the SVG already exports. Requires approved art direction and user review."""
        return await edit_vector_element(
            project,
            source,
            element_id,
            attributes=attributes,
            style=style,
            transform=transform,
            clear_transform=clear_transform,
            text=text,
            export=export,
            note=note,
        )
