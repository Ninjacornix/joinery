"""Typed animation operations of joinery-blender-ops.

* `make_cycle` writes a procedural motion (walk, run, idle, jump, attack, wave)
  on a Rigify rig or a hand-made humanoid, as Joinery's own keyframes.
* `edit_action` changes one action: retime, amplitude, bounce, offset,
  foot_lock, loop_seam, mirror, rename, delete or copy.
* `audit_animations` (read-only) compares a character's actions with the clips
  the game plays; `animation_pass` makes the missing ones in one edit.

Each is an ordinary Blender edit (preimage, recipe, lock, GLB export, review),
scope-guarded to the armature, and refuses actions outside the prompt's part
selection. Each records the actions it touched in the stage, so the review
renders them as before/after clips.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import blueprints, parts
from joinery.authoring import confined, edit_document
from joinery.tools import blender_anim
from servers.authoring import _exports, _name
from servers.errors import tool_errors

#: Where an operation lists the actions it touched, for the review's clips.
REVIEW_FILE = ".joinery/animation_review.json"
#: A clip-name list a game script declares, such as the blueprints' animator.
_DECLARED = re.compile(r"ANIMATION_NAMES\b[^=\n]*=\s*\[([^\]]*)\]")


def model_source(project: Path, model: str) -> str:
    """The .blend a model is made in, given the .blend itself or its GLB."""
    relative = model.removeprefix("res://")
    if Path(relative).suffix != ".blend":
        _tool, relative, _resource = parts.owner_of(project, relative)
    if not confined(project, relative).is_file():
        raise ValueError(f"{relative} is not a Blender model in this project.")
    return relative


def check_selection(
    project: Path, source: str, armature: str, actions: list[str], new: bool
) -> None:
    """Refuse an action outside the prompt's part selection.

    With no part picked in this model, the whole model is in scope. A new
    action may be made when the selection holds one of the model's
    animations, or its armature (or one of its bones).
    """
    targets = parts.prompt_targets(project, source)
    if not targets:
        return
    picked = {t.get("action", "") for t in targets if t.get("part_kind") == "animation"}
    rigs = {
        t.get("object", "")
        for t in targets
        if t.get("part_kind") in {"object", "bone", "animation"}
    }
    for action in actions:
        if action in picked or (new and (picked or (armature and armature in rigs))):
            continue
        labels = ", ".join(t.get("label") or parts.chip_label(t) for t in targets)
        raise ValueError(
            f"Animation {action!r} is not part of the selection ({labels}). Pick it in "
            "the Blender tab's animation list first, or clear the selection."
        )


def note_review(project: Path, source: str, result: dict[str, Any]) -> None:
    """List the actions an edit touched, so the review renders them as clips."""
    rows = [r for r in result.get("animations", []) if isinstance(r, dict)]
    if not rows:
        return
    path = project / REVIEW_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    found: list[dict[str, str]] = []
    if path.is_file():
        try:
            found = list(json.loads(path.read_text()))
        except (ValueError, TypeError):
            found = []
    for row in rows:
        action = "" if row.get("deleted") else str(row.get("action", ""))
        was = str(row.get("was", action) if row.get("op") != "copy" else "")
        found.append(
            {
                "source": source,
                "action": action,
                "was": was,
                "recipe": str(result.get("recipe", "")),
            }
        )
    path.write_text(json.dumps(found))


def _summary(rows: list[dict[str, Any]]) -> list[str]:
    """One line per action an edit wrote: "run: 0.71 s loop, 24 fps, bounce 7.2 cm"."""
    lines = []
    for row in rows:
        if row.get("deleted"):
            lines.append(f"{row.get('action')}: deleted")
            continue
        loop = "loop" if row.get("loop") else "once"
        line = (
            f"{row.get('action')}: {float(row.get('seconds', 0)):.2f} s {loop}, "
            f"{float(row.get('fps', 0)):g} fps, bounce {100 * float(row.get('bounce', 0)):.1f} cm"
        )
        if row.get("contact_frames"):
            line += f", feet slide {1000 * float(row.get('foot_slide', 0)):.1f} mm"
        lines.append(line)
    return lines


def expected_clips(project: Path) -> tuple[list[str], list[str]]:
    """The clip names the game plays, and where each list came from."""
    names: list[str] = []
    found_in: list[str] = []
    record = blueprints.read_record(project)
    if record is not None:
        for blueprint in blueprints.list_blueprints():
            if blueprint.id == record.blueprint:
                declared = list(getattr(blueprint.hooks, "animations", []) or [])
                if declared:
                    names += declared
                    found_in.append(f"the {blueprint.id} blueprint")
    game = project / "game"
    for script in sorted(game.rglob("*.gd")) if game.is_dir() else []:
        text = script.read_text(encoding="utf-8", errors="replace")
        for match in _DECLARED.finditer(text):
            listed = re.findall(r"[\"']([A-Za-z0-9_ .-]+)[\"']", match.group(1))
            if listed:
                names += listed
                found_in.append(script.relative_to(project).as_posix())
    return list(dict.fromkeys(names)), list(dict.fromkeys(found_in))


async def audit(project: Path, model: str) -> dict[str, Any]:
    """Which clips the game plays that the model has, and which it lacks."""
    source = model_source(project, model)
    report = await parts.report(project, source)
    present = [p.action for p in report.parts if p.part_kind == "animation"]
    expected, found_in = expected_clips(project)
    have = {name.lower() for name in present}
    missing = [name for name in expected if name.lower() not in have]
    return {
        "source": source,
        "expected": expected,
        "expected_from": found_in,
        "present": present,
        "missing": missing,
        "makeable": [n for n in missing if n in blender_anim.CLIP_KINDS],
        "not_makeable": [n for n in missing if n not in blender_anim.CLIP_KINDS],
    }


async def make_cycle(
    project: Path,
    model: str,
    kind: str,
    params: dict[str, Any] | None,
    armature: str,
    exports: list[str] | None,
) -> dict[str, Any]:
    checked = blender_anim.check_cycle(kind, params)
    source = model_source(project, model)
    names = blender_anim.cycle_names(kind, checked)
    check_selection(project, source, armature, names, True)
    result = await edit_document(
        project,
        "blender",
        source,
        blender_anim.make_cycle_code(source, armature, kind, checked),
        _exports(project, source, exports),
        f"Make {kind} ({', '.join(names)}) {json.dumps(checked, sort_keys=True)}",
    )
    note_review(project, source, result)
    return {**result, "summary": _summary(result.get("animations", []))}


async def edit_action(
    project: Path,
    model: str,
    action: str,
    ops: list[dict[str, Any]],
    armature: str,
    exports: list[str] | None,
) -> dict[str, Any]:
    _name(action, "action")
    checked = blender_anim.check_edit_ops(ops)
    source = model_source(project, model)
    check_selection(project, source, armature, [action], False)
    made = [str(op["name"]) for op in checked if op["op"] == "copy"]
    check_selection(project, source, armature, made, True)
    result = await edit_document(
        project,
        "blender",
        source,
        blender_anim.edit_action_code(source, armature, action, checked),
        _exports(project, source, exports),
        f"Action {action}: "
        + ", ".join(json.dumps(op, sort_keys=True) for op in checked),
    )
    note_review(project, source, result)
    return {**result, "summary": _summary(result.get("animations", []))}


async def animation_pass(
    project: Path, model: str, armature: str, exports: list[str] | None
) -> dict[str, Any]:
    found = await audit(project, model)
    source = str(found["source"])
    if not found["expected"]:
        raise ValueError(
            "This game declares no animation clips (a blueprint's hooks.animations or a "
            "script's ANIMATION_NAMES), so there is nothing to compare. Use make_cycle."
        )
    makes: list[tuple[str, dict[str, Any]]] = [
        (blender_anim.CLIP_KINDS[name], {}) for name in found["makeable"]
    ]
    if not makes:
        return {**found, "made": [], "status": "unchanged"}
    check_selection(project, source, armature, list(found["makeable"]), True)
    result = await edit_document(
        project,
        "blender",
        source,
        blender_anim.pass_code(source, armature, makes),
        _exports(project, source, exports),
        "Animation pass: " + ", ".join(found["makeable"]),
    )
    note_review(project, source, result)
    return {
        **found,
        **result,
        "made": list(found["makeable"]),
        "summary": _summary(result.get("animations", [])),
    }


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the animation operations to joinery-blender-ops."""

    @server.tool(name="make_cycle")
    @tool_errors
    async def make_cycle_tool(
        model: str,
        kind: str,
        params: dict[str, Any] | None = None,
        armature: str = "",
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Make a character motion in Blender as Joinery's own keyframes on its rig (Rigify or MPFB2 Rigify through the FK controls; a hand-made humanoid through bones named hips/spine/chest/neck/head/thigh/shin/foot/upper_arm/forearm/hand with sides). kind: walk, run, idle (loops); jump (makes jump_start, jump_air (loop) and jump_land); jump_start, jump_air, jump_land; attack or swing; wave (loop). Feet stay planted while on the ground (two-bone leg IK), loops meet seamlessly. params (all optional): speed (0.25-3, how fast it plays), length (seconds), stride, bounce, arm_swing (multipliers, 1 = natural), lean (degrees forward), loop (bool), side (left/right, attack and wave), name (action name, default the kind), replace (true to make an existing action again). model is the .blend or its GLB; armature is needed when the model has several. The GLB exports the action under its own name for Godot."""
        return await make_cycle(project, model, kind, params, armature, exports)

    @server.tool(name="edit_action")
    @tool_errors
    async def edit_action_tool(
        model: str,
        action: str,
        ops: list[dict[str, Any]],
        armature: str = "",
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Change one animation (Blender action) of a model, in order: {"op": "retime", "speed": 1.25 | "length": 0.8 (s)}; {"op": "amplitude", "bones": group or [bone names], "channel": "rotation" | "location" | "all", "factor": 1.3} (scales motion around its average pose); {"op": "bounce", "factor": 1.3} (body's up-and-down motion; feet stay planted); {"op": "offset", "frames": 3} (shifts timing; cyclic for a loop) or {"op": "offset", "bones": ..., "location": [x, y, z] m, "rotation_degrees": [x, y, z]}; {"op": "foot_lock", "threshold"?: 0.02} (pins each foot while it is on the ground); {"op": "loop_seam"} (last pose meets the first); {"op": "mirror"} (left and right swap); {"op": "rename", "name"}; {"op": "copy", "name"}; {"op": "delete"} (alone). Groups: all, root, hips, spine, head, legs, arms, left_leg, right_leg, left_arm, right_arm. "Make the run bouncier" is [{"op": "bounce", "factor": 1.3}]."""
        return await edit_action(project, model, action, ops, armature, exports)

    @server.tool(
        name="audit_animations", annotations=ToolAnnotations(read_only_hint=True)
    )
    @tool_errors
    async def audit_animations_tool(model: str) -> dict[str, Any]:
        """Compare a character's actions with the clips the game plays (its blueprint's animation names and any ANIMATION_NAMES a game script declares): expected, present, missing, and which missing ones make_cycle can make. Changes nothing."""
        return await audit(project, model)

    @server.tool(name="animation_pass")
    @tool_errors
    async def animation_pass_tool(
        model: str, armature: str = "", exports: list[str] | None = None
    ) -> dict[str, Any]:
        """Audit a character's animations against the clips the game plays and make every missing one it can (idle, walk, run, jump_start, jump_air, jump_land, attack, wave) in one Blender edit, with natural defaults. Existing actions are left alone; tune them afterwards with edit_action."""
        return await animation_pass(project, model, armature, exports)
