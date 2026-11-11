"""Typed Blender animation operations: procedural cycles, action edits, an animation pass.

Every motion is written by Joinery's own code, operating Blender: keyframes on
the rig's controls, solved with a small two-bone leg IK so planted feet stay
planted. Nothing is downloaded and no motion model is used.

* `make_cycle_code` writes one action (or the three of a jump) of a known kind:
  walk, run, idle, jump_start, jump_air, jump_land, attack (swing) and wave.
* `edit_action_code` applies typed edits to one action: retime, amplitude,
  bounce, offset, foot_lock, loop_seam, mirror, rename, delete and copy.
* `measure_code` reports what an action does (length, loop seam, bounce, foot
  slide during contact), read-only, for reviews and tests.

Rigs: a Rigify rig (also MPFB2's) is driven through its FK controls with the
IK/FK switch keyed to FK, so the deform bones the game gets follow. A hand-made
humanoid is driven through its own bones, found by name (thigh, upper_leg,
LeftUpLeg, shin, calf, lower_leg, foot, upper_arm, forearm, hand, hips, spine,
chest, neck, head, each with a side).

Each builder returns code for an ordinary Blender edit wrapped in the scope
guard, so only the armature may change, and the recipe replays it.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from joinery.tools.blender import guarded
from joinery.tools.blender_anim_edit import EDIT_CODE
from joinery.tools.blender_anim_pose import POSE_CODE

#: Procedural kinds, the actions each makes, and whether they loop by default.
KINDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "walk": (("walk",), True),
    "run": (("run",), True),
    "idle": (("idle",), True),
    "jump": (("jump_start", "jump_air", "jump_land"), False),
    "jump_start": (("jump_start",), False),
    "jump_air": (("jump_air",), True),
    "jump_land": (("jump_land",), False),
    "attack": (("attack",), False),
    "swing": (("swing",), False),
    "wave": (("wave",), True),
}
#: Natural length in seconds of each motion at speed 1.
BASE_SECONDS = {
    "walk": 1.0,
    "run": 0.7,
    "idle": 2.4,
    "jump_start": 0.4,
    "jump_air": 0.6,
    "jump_land": 0.45,
    "attack": 0.6,
    "swing": 0.6,
    "wave": 1.2,
}
#: make_cycle parameters: (low, high) for numbers; None for the others.
CYCLE_PARAMS: dict[str, tuple[float, float] | None] = {
    "speed": (0.25, 3.0),
    "length": (0.2, 10.0),
    "stride": (0.0, 2.0),
    "bounce": (0.0, 3.0),
    "arm_swing": (0.0, 3.0),
    "lean": (-30.0, 45.0),
    "loop": None,
    "name": None,
    "side": None,
    "replace": None,
}
#: Bone groups an edit can name instead of bones.
GROUPS = (
    "all",
    "root",
    "hips",
    "spine",
    "head",
    "legs",
    "arms",
    "left_leg",
    "right_leg",
    "left_arm",
    "right_arm",
)
#: edit_action operations and the parameters each takes.
EDIT_OPS: dict[str, frozenset[str]] = {
    "retime": frozenset({"speed", "length"}),
    "amplitude": frozenset({"bones", "channel", "factor"}),
    "bounce": frozenset({"factor"}),
    "offset": frozenset({"frames", "bones", "location", "rotation_degrees"}),
    "foot_lock": frozenset({"threshold"}),
    "loop_seam": frozenset(),
    "mirror": frozenset(),
    "rename": frozenset({"name"}),
    "delete": frozenset(),
    "copy": frozenset({"name"}),
}
#: Printed once per action an operation makes or measures, as JSON after the prefix.
ANIMATION_PREFIX = "JOINERY_ANIMATION:"
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]{0,62}$")


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def action_name(value: object, what: str = "name") -> str:
    """A clean action name: letters, digits, _ . - and spaces, 1 to 63 characters."""
    if not isinstance(value, str) or not _NAME.match(value.strip()):
        raise ValueError(
            f"{what} is 1 to 63 letters, digits, spaces, '_', '.' or '-' "
            "(clean names reach Godot unchanged)."
        )
    return value.strip()


def check_cycle(kind: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Refuse unknown kinds and parameters, and check each value, before Blender starts."""
    if kind not in KINDS:
        raise ValueError(f"Unknown motion {kind!r}. Use one of: {', '.join(KINDS)}.")
    given = dict(params or {})
    unknown = set(given) - set(CYCLE_PARAMS)
    if unknown:
        raise ValueError(
            f"make_cycle does not take {', '.join(sorted(unknown))}. "
            f"It takes: {', '.join(CYCLE_PARAMS)}."
        )
    checked: dict[str, Any] = {}
    for key, value in given.items():
        span = CYCLE_PARAMS[key]
        if span is not None:
            if not _finite(value) or not span[0] <= float(value) <= span[1]:
                raise ValueError(f"{key} is a number from {span[0]:g} to {span[1]:g}.")
            checked[key] = float(value)
        elif key in {"loop", "replace"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} is true or false.")
            checked[key] = value
        elif key == "side":
            if value not in {"left", "right"}:
                raise ValueError("side is left or right.")
            checked[key] = value
        else:
            checked[key] = action_name(value)
    if "name" in checked and len(KINDS[kind][0]) > 1:
        raise ValueError(
            f"A {kind} makes {', '.join(KINDS[kind][0])}; it takes no name."
        )
    return checked


def cycle_names(kind: str, params: dict[str, Any]) -> list[str]:
    """The action names make_cycle writes for a kind."""
    if "name" in params:
        return [str(params["name"])]
    return list(KINDS[kind][0])


def _bones(value: object) -> str | list[str]:
    if isinstance(value, str) and value in GROUPS:
        return value
    if (
        isinstance(value, list)
        and 0 < len(value) <= 64
        and all(isinstance(v, str) and 0 < len(v) <= 63 for v in value)
    ):
        return list(value)
    raise ValueError(f"bones is a group ({', '.join(GROUPS)}) or a list of bone names.")


def _vector(value: object, what: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or not all(map(_finite, value)):
        raise ValueError(f"{what} is three numbers.")
    return [float(v) for v in value]


def check_edit_ops(ops: object) -> list[dict[str, Any]]:
    """Each edit with checked values, refusing unknown operations and parameters."""
    if not isinstance(ops, list) or not 0 < len(ops) <= 16:
        raise ValueError("ops is a list of 1 to 16 edits.")
    checked: list[dict[str, Any]] = []
    for raw in ops:
        if not isinstance(raw, dict) or raw.get("op") not in EDIT_OPS:
            raise ValueError(
                f"Each edit is {{op, ...}} with op one of: {', '.join(EDIT_OPS)}."
            )
        op = str(raw["op"])
        params = {k: v for k, v in raw.items() if k != "op"}
        unknown = set(params) - EDIT_OPS[op]
        if unknown:
            raise ValueError(
                f"{op} does not take {', '.join(sorted(unknown))}. "
                f"It takes: {', '.join(sorted(EDIT_OPS[op])) or 'nothing'}."
            )
        row: dict[str, Any] = {"op": op}
        for key, value in params.items():
            if key == "bones":
                row[key] = _bones(value)
            elif key == "channel":
                if value not in {"rotation", "location", "all"}:
                    raise ValueError("channel is rotation, location or all.")
                row[key] = value
            elif key in {"location", "rotation_degrees"}:
                row[key] = _vector(value, key)
            elif key == "name":
                row[key] = action_name(value)
            elif key == "frames":
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or abs(value) > 10000
                ):
                    raise ValueError("frames is a whole number of frames.")
                row[key] = value
            elif not _finite(value):
                raise ValueError(f"{key} must be a finite number.")
            else:
                row[key] = float(value)
        if op == "retime" and ("speed" in row) == ("length" in row):
            raise ValueError(
                "retime takes speed (0.1 to 10) or length (seconds), not both."
            )
        if op == "retime" and not 0.1 <= row.get("speed", 1.0) <= 10:
            raise ValueError("speed is from 0.1 to 10.")
        if op == "retime" and not 0.05 <= row.get("length", 1.0) <= 60:
            raise ValueError("length is from 0.05 to 60 seconds.")
        if op in {"amplitude", "bounce"} and not 0 <= row.get("factor", -1) <= 5:
            raise ValueError(f"{op} needs a factor from 0 to 5 (1 keeps it).")
        if op in {"rename", "copy"} and "name" not in row:
            raise ValueError(f"{op} needs a name.")
        if op == "offset" and not (
            {"frames", "location", "rotation_degrees"} & set(row)
        ):
            raise ValueError("offset needs frames, location or rotation_degrees.")
        if (
            op == "offset"
            and ({"location", "rotation_degrees"} & set(row))
            and "bones" not in row
        ):
            raise ValueError("offset of a pose needs bones (a group or bone names).")
        if op == "foot_lock" and not 0.001 <= row.get("threshold", 0.02) <= 0.5:
            raise ValueError("threshold is from 0.001 to 0.5 metres.")
        checked.append(row)
    final = [row["op"] for row in checked]
    if "delete" in final and final != ["delete"]:
        raise ValueError("delete is the only edit in its call.")
    return checked


def animation_rows(output: str) -> list[dict[str, Any]]:
    """What each animation operation reported, from the worker's output."""
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        if line.startswith(ANIMATION_PREFIX):
            rows.append(json.loads(line.removeprefix(ANIMATION_PREFIX)))
    return rows


# --- Blender side ------------------------------------------------------------

# The library lives in blender_anim_pose and blender_anim_edit.
ANIM_CODE = POSE_CODE + EDIT_CODE


def library() -> str:
    """The Blender animation library, with the kinds table filled in."""
    loops = {kind: KINDS[kind][1] for kind in BASE_SECONDS}
    kinds = {kind: list(names) for kind, (names, _loop) in KINDS.items()}
    code = ANIM_CODE.replace("LOOPS_HERE", repr(loops)).replace(
        "SECONDS_HERE", repr(BASE_SECONDS)
    )
    return code + f"\n_J_KINDS = {kinds!r}\n"


def make_cycle_code(
    source: str, armature: str, kind: str, params: dict[str, Any]
) -> str:
    """Blender code that writes the action(s) of one procedural motion."""
    checked = check_cycle(kind, params)
    call = f"_joinery_make_cycle({armature!r}, {kind!r}, {checked!r})"
    return guarded(library() + "\n" + call, objects=(armature,) if armature else ())


def edit_action_code(
    source: str, armature: str, action: str, ops: list[dict[str, Any]]
) -> str:
    """Blender code that applies typed edits to one action."""
    checked = check_edit_ops(ops)
    call = f"_joinery_edit_action({armature!r}, {action!r}, {checked!r})"
    return guarded(library() + "\n" + call, objects=(armature,) if armature else ())


def pass_code(
    source: str, armature: str, makes: list[tuple[str, dict[str, Any]]]
) -> str:
    """Blender code that makes several motions in one edit (an animation pass)."""
    lines = [library()]
    for kind, params in makes:
        checked = check_cycle(kind, params)
        lines.append(f"_joinery_make_cycle({armature!r}, {kind!r}, {checked!r})")
    return guarded("\n".join(lines), objects=(armature,) if armature else ())


def measure_code(source: str, armature: str, actions: list[str]) -> str:
    """Read-only Blender code that prints one JOINERY_ANIMATION row per action."""
    call = f"_joinery_measure({armature!r}, {list(actions)!r})"
    return "import bpy\nimport json as _joinery_json\n" + library() + "\n" + call + "\n"


# Kind of motion each common clip name asks for, for an animation pass.
CLIP_KINDS = {
    "idle": "idle",
    "walk": "walk",
    "run": "run",
    "jump_start": "jump_start",
    "jump_air": "jump_air",
    "jump_land": "jump_land",
    "attack": "attack",
    "swing": "swing",
    "wave": "wave",
}
