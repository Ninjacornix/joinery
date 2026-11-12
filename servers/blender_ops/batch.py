"""blender_batch: several typed Blender operations on one model, in one call.

An agent sends an ordered list of {op, arguments} of the typed operations it
would otherwise call one by one. Every step is checked first, with the very
checks its own tool runs (argument types, names, the part selection), and the
whole batch is refused at the first invalid step before Blender starts. The
steps then run in one Blender process (`joinery.authoring.edit_document_steps`),
each recorded as the recipe its own call would record, and the agent gets one
short result: a line per step and the files that changed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError, validate_call

from joinery.authoring import DocumentStep, edit_document_steps
from servers.authoring import (
    Step,
    _exports,
    plan_adjust_map,
    plan_bake_height_to_normal,
    plan_set_material_value,
    plan_transform_parts,
)
from servers.blender_ops.animation import (
    model_source,
    plan_edit_action,
    plan_make_cycle,
)
from servers.blender_ops.model_ops import (
    plan_apply_scale,
    plan_reduce_triangles,
    plan_set_pivot,
    plan_set_texel_density,
)
from servers.blender_ops.ops import plan_edit_bone, plan_edit_shape_key
from servers.errors import tool_errors

#: The typed operations a batch may hold: each edits one Blender model in one
#: Blender script. Operations that also run another app (make_map_editable,
#: paint_map, replace_map), read the model in Blender first (split_material,
#: animation_pass) or make new documents (the kits) are called on their own.
PLANNERS: dict[str, Callable[..., Step]] = {
    "transform_parts": plan_transform_parts,
    "set_material_value": plan_set_material_value,
    "adjust_map": plan_adjust_map,
    "bake_height_to_normal": plan_bake_height_to_normal,
    "apply_scale": plan_apply_scale,
    "set_pivot": plan_set_pivot,
    "reduce_triangles": plan_reduce_triangles,
    "set_texel_density": plan_set_texel_density,
    "edit_bone": plan_edit_bone,
    "edit_shape_key": plan_edit_shape_key,
    "make_cycle": plan_make_cycle,
    "edit_action": plan_edit_action,
}
#: The same argument validation the MCP server gives each tool's own call.
_CHECKED = {name: validate_call(planner) for name, planner in PLANNERS.items()}
#: Operations that name their model "model" (a .blend or its GLB), not "source".
_BY_MODEL = frozenset({"make_cycle", "edit_action"})
MAX_STEPS = 16
#: One line per step in the result, at most this long.
LINE = 160


def _first_error(error: ValidationError) -> str:
    row = error.errors()[0]
    where = ".".join(str(part) for part in row["loc"])
    return f"{where}: {row['msg']}" if where else str(row["msg"])


def _plan_one(
    project: Path, source: str, row: Any, exports: list[str] | None
) -> tuple[str, Step]:
    if not isinstance(row, dict) or set(row) - {"op", "arguments"}:
        raise ValueError('Each step is {"op": name, "arguments": {...}}.')
    op = row.get("op")
    if op not in PLANNERS:
        raise ValueError(
            f"{op!r} cannot be batched. Batch these: {', '.join(PLANNERS)}."
        )
    arguments = row.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError(  # noqa: TRY004 - reported to the agent as a tool error
            "arguments is an object of the operation's own arguments."
        )
    arguments = dict(arguments)
    key = "model" if op in _BY_MODEL else "source"
    named = arguments.pop(key, None)
    if named is not None and model_source(project, str(named)) != source:
        raise ValueError(f"Every step edits the batch's model, {source}.")
    given = arguments.pop("exports", None)
    if given is not None and [str(e).removeprefix("res://") for e in given] != [
        e.removeprefix("res://") for e in exports or []
    ]:
        raise ValueError("Give exports once, for the whole batch.")
    try:
        step = _CHECKED[op](project, **{key: source}, **arguments)
    except ValidationError as error:
        raise ValueError(_first_error(error)) from None
    except TypeError as error:
        raise ValueError(str(error)) from None
    return op, step


def plan_batch(
    project: Path, source: str, steps: list[Any], exports: list[str] | None
) -> tuple[str, list[tuple[str, Step]]]:
    """Check every step before any runs; refuse the batch at the first bad one."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps is a list of 1 to {MAX_STEPS} operations.")
    document = model_source(project, source)
    planned: list[tuple[str, Step]] = []
    for number, row in enumerate(steps, 1):
        op = row.get("op") if isinstance(row, dict) else None
        try:
            planned.append(_plan_one(project, document, row, exports))
        except ValueError as error:
            raise ValueError(
                f"Step {number} ({op or '?'}) is not valid, so no step ran: {error}"
            ) from error
    return document, planned


def _line(number: int, op: str, step: Step, result: dict[str, Any]) -> str:
    text = f"{number}. {op}: {step.note}"
    summary = result.get("summary")
    if isinstance(summary, list) and summary:
        text += " → " + "; ".join(str(s) for s in summary)
    return text if len(text) <= LINE else text[: LINE - 1] + "…"


async def blender_batch(
    project: Path,
    source: str,
    steps: list[Any],
    exports: list[str] | None = None,
    keep_macro_shape_keys: bool | None = None,
) -> dict[str, Any]:
    document, planned = plan_batch(project, source, steps, exports)
    outcome = await edit_document_steps(
        project,
        document,
        [DocumentStep(step.code, step.note, step.inputs) for _op, step in planned],
        _exports(project, document, exports),
        keep_macro_shape_keys=keep_macro_shape_keys,
    )
    lines: list[str] = []
    changed: set[str] = set()
    for number, ((op, step), result) in enumerate(
        zip(planned, outcome.results, strict=False), 1
    ):
        shown = step.after(result) if step.after is not None else result
        lines.append(_line(number, op, step, shown))
        changed.update(str(o) for o in result.get("outputs", []))
    if outcome.failed is not None:
        failed = outcome.failed + 1
        total = len(planned)
        kept = (
            f"Steps 1-{failed - 1} are applied and staged ({'; '.join(lines)}). "
            if failed > 2
            else f"Step 1 is applied and staged ({lines[0]}). "
            if failed == 2
            else "Nothing changed. "
        )
        rest = (
            f"steps {failed}-{total} did not change anything"
            if failed < total
            else "it did not change anything"
        )
        raise RuntimeError(
            f"Step {failed} of {total} ({planned[outcome.failed][0]}) failed: "
            f"{outcome.error} {kept}The batch stopped there; {rest}. Fix that step "
            "and send it and the ones after it as a new batch."
        )
    return {
        "source": document,
        "status": "staged",
        "requires_review": True,
        "steps": lines,
        "changed": sorted(changed),
        "recipe": outcome.results[-1]["recipe"],
        "seconds": round(outcome.seconds, 1),
    }


def register(server: MCPServer[Any], project: Path) -> None:
    """Add blender_batch to joinery-blender-ops."""

    @server.tool(name="blender_batch")
    @tool_errors
    async def blender_batch_tool(
        source: str,
        steps: list[dict[str, Any]],
        exports: list[str] | None = None,
        keep_macro_shape_keys: bool | None = None,
    ) -> dict[str, Any]:
        """Run several typed operations on one Blender model in one call and one Blender run. Prefer it whenever a change to one model takes two or more of these operations: transform_parts, set_material_value, adjust_map, bake_height_to_normal, apply_scale, set_pivot, reduce_triangles, set_texel_density, edit_bone, edit_shape_key, make_cycle, edit_action. steps is an ordered list of {"op": name, "arguments": {...}} where arguments are that operation's own arguments without source/model and exports (the batch gives them: source is the .blend or its GLB; exports default to the model's GLB). Every step is checked before Blender starts; an invalid step refuses the whole batch. Steps run in order; the first that fails stops the batch and the error says which step and why (steps before it stay applied). Each step is recorded as its own replayable recipe, exactly as if called alone. Returns one line per step and the changed files."""
        return await blender_batch(
            project, source, steps, exports, keep_macro_shape_keys
        )
