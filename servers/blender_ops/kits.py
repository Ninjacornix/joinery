"""Parametric kits in joinery-blender-ops: make, vary, change and scatter kit models.

Each operation is typed: the agent names a kit and its parameters, never code.
Joinery writes the Blender recipe from the kit (joinery.kits), so the models
replay with `joinery rebuild`, and every result is staged for the user's review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import kits
from joinery.scatter import scatter
from servers.errors import tool_errors


async def make_set(
    project: Path,
    kit: str,
    params: dict[str, Any] | None,
    *,
    count: int,
    seed: int,
    vary: float | dict[str, float] | None,
    name: str,
    folder: str,
    lods: int,
    colours: dict[str, str] | None,
) -> dict[str, Any]:
    chosen = name or kits.free_name(project, kit, count, folder)
    made = kits.plan(
        project,
        kit,
        params,
        seed=seed,
        name=chosen,
        count=count,
        vary=vary,
        folder=folder,
        lods=lods,
        colours=colours,
    )
    return await kits.make(project, made)


async def edit_set(
    project: Path,
    asset: str,
    params: dict[str, Any] | None,
    seed: int | None,
    colours: dict[str, str] | None,
) -> dict[str, Any]:
    made, marker = kits.edit_plan(project, asset, params, seed=seed, colours=colours)
    if (
        made.params == marker.get("params")
        and made.seed == marker.get("seed")
        and made.colours == marker.get("colours", {})
    ):
        return {"status": "unchanged", "note": "These are the values it has already."}
    return await kits.make(project, made, replacing=asset)


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the kit operations to joinery-blender-ops."""

    @server.tool(name="kit_list", annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def kit_list_tool() -> dict[str, Any]:
        """List the parametric kits (procedural generators run in Blender): each kit's typed parameters (kind, default, range or choices, what it does), its materials as the art bible makes them (palette colour, roughness, metallic), and the triangle budget and pivot of its default folder."""
        return kits.describe(project)

    @server.tool(name="kit_make")
    @tool_errors
    async def kit_make_tool(
        kit: str,
        params: dict[str, Any] | None = None,
        seed: int = 1,
        name: str = "",
        folder: str = "",
        lods: int = 0,
        colours: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make one model from a kit in Blender: a .blend source under art/sources/kits/ and a GLB in folder (default: the kit's, e.g. art/environment), with a replayable recipe and a joinery.lock entry. params are the kit's own (kit_list); unnamed ones keep their defaults. The seed picks the random details. Materials use the art bible's palette, roughness and metallic ranges; the model fits the bible's triangle budget and pivot for its folder. lods (0-2) adds <name>_lod1.glb… with about half the triangles each. colours {material: '#rrggbb'} picks palette colours. name defaults to the kit's (a free one)."""
        return await make_set(
            project,
            kit,
            params,
            count=1,
            seed=seed,
            vary=0.0,
            name=name,
            folder=folder,
            lods=lods,
            colours=colours,
        )

    @server.tool(name="kit_batch")
    @tool_errors
    async def kit_batch_tool(
        kit: str,
        count: int,
        params: dict[str, Any] | None = None,
        seed: int = 1,
        vary: float | dict[str, float] | None = None,
        name: str = "",
        folder: str = "",
        lods: int = 0,
        colours: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a set of count (2-100) models from one kit in one Blender run, each different: <name>_01.glb, <name>_02.glb… each with its own .blend. vary is how far each model strays from params: a share of each parameter's range (default 0.25), or {param: share} for some parameters only. "50 rocks, mossier, smaller" is kit_batch(kit="rock", count=50, params={"moss": 0.6, "size": 0.6}). The same seed makes the same set. Use kit_edit to change the whole set later."""
        if count < 2:
            raise ValueError("A batch has 2 or more models; use kit_make for one.")
        return await make_set(
            project,
            kit,
            params,
            count=count,
            seed=seed,
            vary=vary,
            name=name,
            folder=folder,
            lods=lods,
            colours=colours,
        )

    @server.tool(name="kit_edit")
    @tool_errors
    async def kit_edit_tool(
        asset: str,
        params: dict[str, Any] | None = None,
        seed: int | None = None,
        colours: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Change models made from a kit: asset is any of its GLB or .blend files; params are the kit parameters to change (the rest keep their values). The kit makes the models again in Blender with the new values and replaces the old ones, keeping their names, so scenes that place them pick up the change. A model of a batch changes with its whole set."""
        return await edit_set(project, asset, params, seed, colours)

    @server.tool(name="scatter")
    @tool_errors
    async def scatter_tool(
        scene: str,
        assets: list[str],
        count: int,
        seed: int = 1,
        area: list[float] | None = None,
        surface: str = "",
        spacing: float = 1.0,
        scale: list[float] | None = None,
        align: str = "up",
        paths: list[Any] | None = None,
        path_clearance: float = 1.0,
        path_band: float = 0.0,
        parent: str = ".",
        name: str = "Scatter",
        height: float = 0.0,
    ) -> dict[str, Any]:
        """Place count instances of models (assets: GLBs, e.g. a kit_batch set, picked at random) in a saved scene that isn't on screen, as one Node3D group (name) of instanced models under parent. Instances keep spacing metres apart (seeded, so the same call places the same instances), get a random turn and a scale in [smallest, largest], and sit on surface: a node of the scene that instances a GLB (its transform counts) or a GLB at the origin; without one, at height. area is [min_x, min_z, max_x, max_z] in metres (default: the surface's extent). align "normal" tilts them to the surface. paths (Path3D node names or [[x, z], …] point lists) are kept clear by path_clearance metres; path_band > 0 also keeps them within that many metres beyond it ("by the path"). Scattering again with the same name replaces the group."""
        return scatter(
            project,
            scene,
            assets,
            count,
            seed=seed,
            area=area,
            surface=surface,
            spacing=spacing,
            scale=tuple(scale) if scale is not None else (0.8, 1.2),
            align=align,
            paths=paths or [],
            path_clearance=path_clearance,
            path_band=path_band,
            parent=parent,
            name=name,
            height=height,
        )
