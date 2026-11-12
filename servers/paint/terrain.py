"""Typed terrain operations in joinery-paint: a game's heightmap and paint mask.

The images are made by Godot running the terrain feature's bake on a document of
operations (joinery.terrain), so every change has a replayable recipe and is
staged for the user's review like any other painting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import terrain
from servers.errors import tool_errors

_OPS_HELP = (
    "Operations, applied in order; positions are [x, z] metres in the terrain's own "
    "space, centred on its node; heights are metres above the node; every brush takes "
    "at, radius (8) and falloff (0-1, share of the radius that fades, 0.5): "
    '{"op": "noise", "amplitude" (4 m), "scale" (feature size, 32 m), "octaves" (4), '
    '"seed"?, "ridged"?, "offset"?}; {"op": "raise" | "lower", "at", "radius", '
    '"amount" m}; {"op": "flatten", "at", "radius", "height"? (m; default the ground '
    'at its centre)}; {"op": "smooth", "at"? (whole terrain without), "radius", '
    '"strength" 0-1, "iterations" 1-20}; {"op": "erode", "iterations" 1-50, "talus" '
    '(steepest rise per metre that stays, 0.6), "amount"}; {"op": "path", "points" '
    '[[x, z], …], "width" m, "falloff"? m, "depth"? m sunk, "paint"? "r"|"g"|"b"|"a"} '
    'eases the ground along it to a gentle profile; {"op": "paint", "channel" '
    '"r"|"g"|"b"|"a", "at", "radius", "value" (1 paints, 0 erases)}. Mask channels: '
    "r, g, b paint the Terrain's layers 1-3; a is for scatter."
)

_NEW_HELP = (
    "Make a terrain for a game with the terrain feature, in Godot: "
    "art/terrain/<name>/height.png (16-bit) and mask.png (RGBA), with a replayable "
    "recipe. size is [x, z] metres (64 x 64); resolution the points along a side (33, "
    "65, 129, 257, 513 or 1025); heights run from min_height to max_height metres; "
    "seed makes noise repeatable. base starts from a heightmap painted in Krita (PNG "
    "or EXR under art/), base_mask from a painted mask. Returns the settings to give "
    "the Terrain node in the scene. Requires approved art direction and user review."
)
_EDIT_HELP = (
    "Change a terrain: raise, lower, flatten or smooth an area, erode, carve a path, "
    "or repaint the mask. The operations are added to its log and Godot makes both "
    "images again; the user reviews the change."
)


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the terrain operations to the joinery-paint server."""

    @server.tool(name="terrain_info", annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def terrain_info_tool(name: str = "") -> dict[str, Any]:
        """List the game's terrains (or one): size, resolution, height range, seed, the operations so far, and the settings the Terrain node needs (heightmap, mask, size, min_height, max_height). Read res://features/terrain/GUIDE.md before using terrain in a scene."""
        return terrain.describe(project, name)

    @server.tool(name="terrain_new", description=_NEW_HELP + " " + _OPS_HELP)
    @tool_errors
    async def terrain_new_tool(
        name: str,
        size: list[float] | None = None,
        resolution: int = 129,
        min_height: float = 0.0,
        max_height: float = 20.0,
        seed: int = 1,
        ops: list[dict[str, Any]] | None = None,
        base: str = "",
        base_mask: str = "",
    ) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "resolution": resolution,
            "min_height": min_height,
            "max_height": max_height,
            "seed": seed,
            "base": base,
            "base_mask": base_mask,
        }
        if size is not None:
            settings["size"] = size
        return await terrain.create(project, name, settings, ops or [])

    @server.tool(name="terrain_edit", description=_EDIT_HELP + " " + _OPS_HELP)
    @tool_errors
    async def terrain_edit_tool(name: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        return await terrain.edit(project, name, ops)
