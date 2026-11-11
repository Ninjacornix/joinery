"""Animation pass: parameter checks, generated code, parts, review clips and the audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from joinery import parts, review
from joinery.gateway import OP_APPS, op_apps
from joinery.read_access import read_only
from joinery.tools import blender_anim
from servers.blender_ops import animation

SOURCE = "art/sources/hero.blend"


def test_cycle_params_are_checked_before_blender_starts() -> None:
    assert blender_anim.check_cycle("walk", {"speed": 1.5, "loop": False}) == {
        "speed": 1.5,
        "loop": False,
    }
    for kind, params, words in [
        ("moonwalk", {}, "Unknown motion"),
        ("walk", {"sped": 1}, "does not take sped"),
        ("walk", {"speed": 9}, "speed is a number from 0.25 to 3"),
        ("walk", {"bounce": float("nan")}, "bounce is a number"),
        ("walk", {"loop": "yes"}, "loop is true or false"),
        ("wave", {"side": "up"}, "side is left or right"),
        ("walk", {"name": "a/b"}, "letters, digits"),
        ("jump", {"name": "hop"}, "takes no name"),
    ]:
        with pytest.raises(ValueError, match=words):
            blender_anim.check_cycle(kind, params)


def test_cycle_names() -> None:
    assert blender_anim.cycle_names("jump", {}) == [
        "jump_start",
        "jump_air",
        "jump_land",
    ]
    assert blender_anim.cycle_names("run", {"name": "sprint"}) == ["sprint"]
    assert blender_anim.cycle_names("wave", {}) == ["wave"]


def test_edit_ops_are_checked() -> None:
    ops = blender_anim.check_edit_ops(
        [
            {"op": "retime", "speed": 2},
            {"op": "amplitude", "bones": "arms", "factor": 1.3},
            {
                "op": "amplitude",
                "bones": ["DEF-spine"],
                "channel": "location",
                "factor": 0.5,
            },
            {"op": "offset", "frames": 3},
            {"op": "offset", "bones": "head", "rotation_degrees": [0, 10, 0]},
            {"op": "bounce", "factor": 1.3},
            {"op": "foot_lock"},
            {"op": "loop_seam"},
            {"op": "mirror"},
            {"op": "copy", "name": "run copy"},
            {"op": "rename", "name": "sprint"},
        ]
    )
    assert ops[0] == {"op": "retime", "speed": 2.0}
    assert ops[4]["rotation_degrees"] == [0.0, 10.0, 0.0]
    for given, words in [
        ([], "1 to 16 edits"),
        ([{"op": "twirl"}], "op one of"),
        ([{"op": "retime"}], "speed .* or length"),
        ([{"op": "retime", "speed": 2, "length": 1}], "not both"),
        ([{"op": "retime", "speed": 50}], "0.1 to 10"),
        ([{"op": "amplitude", "bones": "tail", "factor": 1}], "bones is a group"),
        ([{"op": "amplitude", "bones": "arms"}], "factor from 0 to 5"),
        (
            [{"op": "amplitude", "bones": "arms", "factor": 1, "channel": "scale"}],
            "channel",
        ),
        ([{"op": "offset"}], "offset needs"),
        ([{"op": "offset", "location": [0, 0, 1]}], "needs bones"),
        ([{"op": "foot_lock", "threshold": 2}], "threshold"),
        ([{"op": "rename"}], "rename needs a name"),
        ([{"op": "mirror", "axis": "x"}], "does not take axis"),
        ([{"op": "mirror"}, {"op": "delete"}], "delete is the only edit"),
    ]:
        with pytest.raises(ValueError, match=words):
            blender_anim.check_edit_ops(given)


def test_generated_code_is_scope_guarded_and_names_the_call() -> None:
    code = blender_anim.make_cycle_code(SOURCE, "HeroRig", "run", {"bounce": 1.3})
    assert "_joinery_scope_check(_joinery_before" in code
    assert "_joinery_make_cycle('HeroRig', 'run', {'bounce': 1.3})" in code
    assert "'object:HeroRig'" in code
    assert "LOOPS_HERE" not in code and "_J_KINDS = {" in code
    compile(code, "make_cycle", "exec")
    edit = blender_anim.edit_action_code(SOURCE, "", "run", [{"op": "mirror"}])
    assert "_joinery_edit_action('', 'run', [{'op': 'mirror'}])" in edit
    compile(edit, "edit_action", "exec")
    both = blender_anim.pass_code(SOURCE, "", [("idle", {}), ("jump", {})])
    assert both.count("\n_joinery_make_cycle(") == 2
    measure = blender_anim.measure_code(SOURCE, "", ["walk"])
    assert (
        "_joinery_scope_check" not in measure
        and "_joinery_measure('', ['walk'])" in measure
    )
    compile(measure, "measure", "exec")
    # Rigify needs its extension; the code names it, so the recipe enables it.
    assert "rigify" in code.lower()


def test_animation_rows_come_from_the_worker_output() -> None:
    output = 'noise\nJOINERY_ANIMATION:{"action": "run", "seam": 0.0}\n'
    assert blender_anim.animation_rows(output) == [{"action": "run", "seam": 0.0}]


INSPECTION: dict[str, Any] = {
    "objects": [
        {"name": "HeroRig", "type": "ARMATURE", "bones": []},
        {"name": "Hero", "type": "MESH", "slots": []},
    ],
    "materials": [],
    "actions": [
        {
            "name": "run",
            "frames": [0, 17],
            "fps": 24.0,
            "loop": True,
            "kind": "run",
            "armature": "HeroRig",
        },
        {
            "name": "wave_old",
            "frames": [1, 30],
            "fps": 24.0,
            "loop": False,
            "kind": "",
            "armature": "",
        },
    ],
}


def test_actions_are_parts() -> None:
    found = parts.blender_parts(INSPECTION, "art/models/hero.glb", SOURCE)
    anims = [p for p in found if p.part_kind == "animation"]
    assert [p.action for p in anims] == ["run", "wave_old"]
    run = anims[0].target()
    assert run["id"] == "res://art/models/hero.glb#object=HeroRig&action=run"
    assert run["kind"] == "Animation" and run["label"] == "run · Hero"
    assert (run["frames"], run["fps"], run["seconds"], run["loop"]) == (
        "0,17",
        "24",
        "0.708",
        "true",
    )
    assert run["ops"] == "edit_action,make_cycle" and run["animation_kind"] == "run"
    # An action nobody plays belongs to the model's only armature.
    assert anims[1].object == "HeroRig" and anims[1].target()["loop"] == ""
    assert "HeroRig › run" in parts.chip_tooltip(run)
    report = parts.PartsReport(
        resource="res://x", source=SOURCE, owner="blender", sha256="", parts=found
    )
    assert report.find({"action": "run"}).action == "run"
    assert report.counts()["animation"] == 2
    assert parts.parse_part_id(run["id"])[1] == {"object": "HeroRig", "action": "run"}


def _targets(tmp_path: Path, rows: list[dict[str, str]]) -> None:
    parts.write_targets(tmp_path, rows)


def test_selection_limits_which_actions_change(tmp_path: Path) -> None:
    animation.check_selection(tmp_path, SOURCE, "", ["walk"], False)
    run = {
        "part_kind": "animation",
        "action": "run",
        "object": "HeroRig",
        "owner_source": SOURCE,
        "label": "run · Hero",
    }
    _targets(tmp_path, [run])
    animation.check_selection(tmp_path, SOURCE, "", ["run"], False)
    with pytest.raises(ValueError, match="'walk' is not part of the selection"):
        animation.check_selection(tmp_path, SOURCE, "", ["walk"], False)
    # A new action may be made beside a picked one, or on a picked armature.
    animation.check_selection(tmp_path, SOURCE, "", ["run_copy"], True)
    rig = {"part_kind": "object", "object": "HeroRig", "owner_source": SOURCE}
    _targets(tmp_path, [rig])
    animation.check_selection(tmp_path, SOURCE, "HeroRig", ["wave"], True)
    with pytest.raises(ValueError, match="not part of the selection"):
        animation.check_selection(tmp_path, SOURCE, "", ["wave"], True)


def test_review_lists_picked_and_touched_actions(tmp_path: Path) -> None:
    original, stage = tmp_path / "original", tmp_path / "stage"
    for root in (original, stage):
        (root / "art/sources").mkdir(parents=True)
        (root / SOURCE).write_bytes(b"blend")
    (stage / "recipes").mkdir()
    (stage / "recipes/new.toml").write_text("")
    (original / "recipes").mkdir()
    (original / "recipes/old.toml").write_text("")
    result = {
        "recipe": "recipes/new.toml",
        "animations": [
            {"action": "sprint", "was": "run", "op": "edit_action"},
            {"action": "wave", "was": "", "op": "copy"},
            {"action": "idle_old", "was": "idle_old", "deleted": True, "op": "delete"},
        ],
    }
    animation.note_review(stage, SOURCE, result)
    # An older change's row (its recipe is in the original) is not this change.
    rows = json.loads((stage / animation.REVIEW_FILE).read_text())
    rows.append(
        {"source": SOURCE, "action": "old", "was": "old", "recipe": "recipes/old.toml"}
    )
    (stage / animation.REVIEW_FILE).write_text(json.dumps(rows))
    tx: Any = SimpleNamespace(original=original, stage=stage)
    picked = [
        {"part_kind": "animation", "action": "walk", "id": "w", "owner_source": SOURCE}
    ]
    media, renders = review.plan(tx, [{"path": SOURCE}], picked)
    clips = {m["label"]: m for m in media if m.get("media") == "clip"}
    assert sorted(clips) == ["idle_old", "sprint", "walk", "wave"], sorted(clips)
    sides = {(r.item["label"], r.side) for r in renders if r.optional}
    assert ("sprint", "before") in sides and ("sprint", "after") in sides
    assert ("wave", "after") in sides and ("wave", "before") not in sides
    assert ("idle_old", "before") in sides and ("idle_old", "after") not in sides
    script = next(
        r.script for r in renders if r.item["label"] == "sprint" and r.side == "before"
    )
    assert "ACTION='run'" in script
    compile("OUTPUT='x.png'\n" + script, "clip", "exec")
    # The model itself still renders as a whole.
    assert any(m.get("media") == "image" and m["path"] == SOURCE for m in media)


def test_optional_clip_sides_drop_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*_args: Any) -> Path:
        raise ValueError("Could not render")

    monkeypatch.setattr(review, "_render", fail)
    item: dict[str, Any] = {"before": "x", "after": "y"}
    job = review.Render(item, "before", tmp_path, SOURCE, "a.png", "", optional=True)
    asyncio.run(review.render([job], strict=True))
    assert "before" not in item and "error" not in item


def test_expected_clips_come_from_the_blueprint_and_scripts(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text(
        '[game]\nblueprint = "third-person-3d"\nblueprint_version = 1\n'
    )
    (tmp_path / "game/npc").mkdir(parents=True)
    (tmp_path / "game/npc/guard.gd").write_text(
        'const ANIMATION_NAMES: Array[StringName] = [&"idle", &"wave", &"salute"]\n'
    )
    names, where = animation.expected_clips(tmp_path)
    assert names[:6] == ["idle", "walk", "run", "jump_start", "jump_air", "jump_land"]
    assert names[6:] == ["wave", "salute"]
    assert where == ["the third-person-3d blueprint", "game/npc/guard.gd"]
    assert "salute" not in blender_anim.CLIP_KINDS


def test_gateway_slots_and_read_access() -> None:
    for op in ("make_cycle", "edit_action", "animation_pass"):
        assert OP_APPS[op] == ("blender",)
        assert op_apps(op, {}) == ("blender",)
    assert "audit_animations" not in OP_APPS
    assert read_only("joinery-blender-ops", "audit_animations")
    assert not read_only("joinery-blender-ops", "make_cycle")


def test_blender_tool_lists_action_details() -> None:
    from joinery.tools.blender import INSPECT_BLEND

    assert "'loop': bool(a.get('joinery_loop', a.use_cyclic))" in INSPECT_BLEND
    assert "'armature': action_owner(a)" in INSPECT_BLEND
