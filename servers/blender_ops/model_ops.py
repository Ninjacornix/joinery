"""Whole-model operations of joinery-blender-ops: scale, pivot, triangles, texel density.

They bring a model in line with the art bible's checked rules (`joinery match`)
and are typed so an agent never needs raw bpy for them. Each is an ordinary
Blender edit (`edit_document`): snapshot, recipe, GLB export and review apply.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from joinery.tools import blender_model_ops as model
from servers.authoring import Step, _name, run_step
from servers.errors import tool_errors


def _names(objects: list[str]) -> list[str]:
    if not isinstance(objects, list) or not 1 <= len(objects) <= 64:
        raise ValueError("objects is a list of 1 to 64 object names.")
    for name in objects:
        _name(name, "object")
    return list(dict.fromkeys(objects))


def plan_apply_scale(
    project: Path, source: str, objects: list[str], exports: list[str] | None = None
) -> Step:
    names = _names(objects)
    return Step(
        source,
        model.apply_scale_code(names),
        "Apply scale on " + ", ".join(names),
        exports,
    )


def plan_set_pivot(
    project: Path,
    source: str,
    pivot: str = "bottom-centre",
    exports: list[str] | None = None,
) -> Step:
    return Step(
        source,
        model.set_pivot_code(pivot),
        f"Move the model's pivot to its {pivot}",
        exports,
    )


def plan_reduce_triangles(
    project: Path, source: str, max_triangles: int, exports: list[str] | None = None
) -> Step:
    return Step(
        source,
        model.reduce_triangles_code(max_triangles),
        f"Decimate to at most {max_triangles} triangles",
        exports,
    )


def plan_set_texel_density(
    project: Path,
    source: str,
    objects: list[str],
    px_per_m: float,
    power_of_two: bool = False,
    exports: list[str] | None = None,
) -> Step:
    names = _names(objects)
    return Step(
        source,
        model.texel_density_code(names, px_per_m, power_of_two),
        f"Texel density {px_per_m:g} px/m on " + ", ".join(names),
        exports,
    )


async def apply_scale(
    project: Path, source: str, objects: list[str], exports: list[str] | None
) -> dict[str, Any]:
    return await run_step(project, plan_apply_scale(project, source, objects, exports))


async def set_pivot(
    project: Path, source: str, pivot: str, exports: list[str] | None
) -> dict[str, Any]:
    return await run_step(project, plan_set_pivot(project, source, pivot, exports))


async def reduce_triangles(
    project: Path, source: str, max_triangles: int, exports: list[str] | None
) -> dict[str, Any]:
    return await run_step(
        project, plan_reduce_triangles(project, source, max_triangles, exports)
    )


async def set_texel_density(
    project: Path,
    source: str,
    objects: list[str],
    px_per_m: float,
    power_of_two: bool,
    exports: list[str] | None,
) -> dict[str, Any]:
    return await run_step(
        project,
        plan_set_texel_density(
            project, source, objects, px_per_m, power_of_two, exports
        ),
    )


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the whole-model operations to joinery-blender-ops."""

    @server.tool(name="apply_scale")
    @tool_errors
    async def apply_scale_tool(
        source: str, objects: list[str], exports: list[str] | None = None
    ) -> dict[str, Any]:
        """Apply the scale of named objects in a Blender model (scale becomes 1, the model looks the same), as the art bible's applied-scale rule asks. Exports default to the model's existing GLB."""
        return await apply_scale(project, source, objects, exports)

    @server.tool(name="set_pivot")
    @tool_errors
    async def set_pivot_tool(
        source: str, pivot: str = "bottom-centre", exports: list[str] | None = None
    ) -> dict[str, Any]:
        """Move a Blender model so its origin sits at the bottom-centre (lowest point at 0, centred across) or the centre of its bounds. A model of one mesh gets its origin moved into the geometry. Exports default to the model's existing GLB."""
        return await set_pivot(project, source, pivot, exports)

    @server.tool(name="reduce_triangles")
    @tool_errors
    async def reduce_triangles_tool(
        source: str, max_triangles: int, exports: list[str] | None = None
    ) -> dict[str, Any]:
        """Decimate (collapse) every mesh of a Blender model by one ratio, applied, so the model has at most max_triangles triangles. Exports default to the model's existing GLB."""
        return await reduce_triangles(project, source, max_triangles, exports)

    @server.tool(name="set_texel_density")
    @tool_errors
    async def set_texel_density_tool(
        source: str,
        objects: list[str],
        px_per_m: float,
        power_of_two: bool = False,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Bring named mesh objects to a texel density (base colour texture pixels per metre): a texture that is too dense is resized smaller (to a power of two with power_of_two); one too sparse has the object's UVs scaled about their centre, so the texture repeats more often. Exports default to the model's existing GLB."""
        return await set_texel_density(
            project, source, objects, px_per_m, power_of_two, exports
        )
