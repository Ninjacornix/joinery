"""Character operations of joinery-blender-ops: an MPFB2 human's body shape, and which
shape keys the game gets.

Typed so an agent never writes raw bpy or MPFB2 calls for them. Each is an
ordinary Blender edit (`edit_document`): snapshot, recipe, GLB export and
review apply. `bake_shape_keys` also records the model's choice of morph
targets in its recipe, so later edits and `joinery rebuild` export the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from joinery import parts
from joinery.tools import blender_character as character
from joinery.tools.blender_export import ExportShapeKeys
from servers.authoring import Step, _name, run_step
from servers.errors import tool_errors

#: Part kinds that stand for (a piece of) one character mesh.
_CHARACTER_KINDS = frozenset({"object", "region", "body_shape", "shape_key"})


def _check_character(project: Path, source: str, obj: str) -> None:
    """Refuse a mesh outside the prompt's selection (no selection: the whole model)."""
    targets = parts.prompt_targets(project, source)
    if not targets or not obj:
        return
    if any(
        t.get("object") == obj and t.get("part_kind") in _CHARACTER_KINDS
        for t in targets
    ):
        return
    labels = ", ".join(t.get("label") or parts.chip_label(t) for t in targets)
    raise ValueError(
        f"{obj!r} is not part of the selection ({labels}). Pick it, or a region of it, "
        "in the parts list first."
    )


def plan_set_body_shape(
    project: Path,
    source: str,
    object: str,
    macros: dict[str, float] | None = None,
    targets: dict[str, float] | None = None,
    exports: list[str] | None = None,
) -> Step:
    obj = object
    _name(obj, "body mesh")
    checked_macros, checked_targets = character.check_body_shape(macros, targets)
    _check_character(project, source, obj)
    said = [f"{k} {v:g}" for k, v in {**checked_macros, **checked_targets}.items()]
    return Step(
        source,
        character.body_shape_code(obj, checked_macros, checked_targets),
        f"Body shape of {obj}: " + ", ".join(said),
        exports,
    )


def plan_bake_shape_keys(
    project: Path,
    source: str,
    keep: list[str] | None,
    values: dict[str, float] | None = None,
    object: str = "",
    in_source: bool = False,
    exports: list[str] | None = None,
) -> Step:
    obj = object
    if obj:
        _name(obj, "mesh")
    kept, checked = character.check_shape_keys(keep, values)
    _check_character(project, source, obj)
    if kept is None:
        game = "body-shape keys baked, other keys kept"
    else:
        game = "keep " + (", ".join(kept) if kept else "no shape keys")
    note = f"Game shape keys: {game}"
    if checked:
        note += "; values " + ", ".join(f"{k} {v:g}" for k, v in checked.items())
    if in_source:
        note += "; baked in the .blend too"
    return Step(
        source,
        character.shape_keys_code(obj, checked, kept, in_source),
        note,
        exports,
        shape_keys=ExportShapeKeys(keep=kept),
    )


async def set_body_shape(
    project: Path,
    source: str,
    obj: str,
    macros: dict[str, float] | None,
    targets: dict[str, float] | None,
    exports: list[str] | None,
) -> dict[str, Any]:
    return await run_step(
        project, plan_set_body_shape(project, source, obj, macros, targets, exports)
    )


async def bake_shape_keys(
    project: Path,
    source: str,
    keep: list[str] | None,
    values: dict[str, float] | None,
    obj: str,
    in_source: bool,
    exports: list[str] | None,
) -> dict[str, Any]:
    return await run_step(
        project,
        plan_bake_shape_keys(project, source, keep, values, obj, in_source, exports),
    )


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the character operations to joinery-blender-ops."""

    @server.tool(name="set_body_shape")
    @tool_errors
    async def set_body_shape_tool(
        source: str,
        object: str,
        macros: dict[str, float] | None = None,
        targets: dict[str, float] | None = None,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Change the body of an MPFB2 human through MPFB2 itself. object is its body mesh (a region part's object). macros (0-1): gender, age, muscle, weight, height, proportions, cupsize, firmness, african, asian, caucasian. targets: MPFB2 detail targets by name, weight 0-1 (0 removes one), such as "l-upperarm-shoulder-muscle-incr" and its "r-" twin for bulkier shoulders; a picked body region lists the targets and macros for it. Clothes and the rig are refitted where MPFB2 can (a generated Rigify rig whose meta-rig was deleted keeps its bones; the result says so). The GLB gets the new shape baked in. Exports default to the model's existing GLB."""
        return await set_body_shape(project, source, object, macros, targets, exports)

    @server.tool(name="bake_shape_keys")
    @tool_errors
    async def bake_shape_keys_tool(
        source: str,
        keep: list[str] | None,
        values: dict[str, float] | None = None,
        object: str = "",
        in_source: bool = False,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Choose which shape keys of a Blender model reach the game. keep lists the game's morph targets (its expressions or visemes); every other key is baked into the exported mesh at its value, so the GLB is lighter and looks as the model does. keep null goes back to the default (MPFB2 body-shape keys baked, other keys kept). values sets shape key values first (on object, or on every mesh with that key). The .blend keeps every key, still editable, unless in_source is true (only when the user asks: MPFB2 cannot change a baked body again). The choice is recorded and kept by later edits. Exports default to the model's existing GLB."""
        return await bake_shape_keys(
            project, source, keep, values, object, in_source, exports
        )
