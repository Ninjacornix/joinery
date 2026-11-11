"""Character animation made by operating Blender, in the real worker.

Builds the MPFB2 + Rigify human of blender_rig.py and a hand-made humanoid
whose bones are named like a game rig (UpperLeg.L, LowerArm.R, ...). An
animation pass makes the clips the third-person blueprint plays; make_cycle
adds attack and wave, and walk, idle, jump and wave on the hand-made rig.
Every loop must meet itself (first pose equal to the last), feet must not slide
while planted, and edits must do what they say: retime, bounce (feet stay
planted), amplitude then foot_lock, mirror, loop_seam, offset, copy, rename,
delete. Godot imports each GLB with its actions by name, the review renders
before/after clips, and every recipe replays.

Technical fixtures in a temporary project; nothing here approves art direction
or touches a user's game. Run: `uv run python tests/integration/animation.py`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_rig import BUILD

from joinery import parts, review
from joinery.authoring import IMAGE, edit_document, run_offline
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from servers.blender_ops import animation

HERO, HERO_GLB = "art/sources/hero.blend", "art/models/hero.glb"
ROBOT, ROBOT_GLB = "art/sources/robot.blend", "art/models/robot.glb"
#: Loops meet within a tenth of a millimetre; planted feet move under 5 mm.
SEAM, SLIDE = 1e-4, 0.005

# A hand-made humanoid in a T-pose: bones named like a game rig, one box per
# bone, rigidly weighted, facing -Y.
ROBOT_CODE = """
import bmesh
from mathutils import Vector
data = bpy.data.armatures.new('BodyRig')
rig = bpy.data.objects.new('Body', data)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
def bone(name, head, tail, parent=None):
    b = data.edit_bones.new(name)
    b.head, b.tail = head, tail
    if parent:
        b.parent = data.edit_bones[parent]
        b.use_connect = (Vector(parent_tail[parent]) - Vector(head)).length < 1e-6
    parent_tail[name] = tail
parent_tail = {}
bone('Hips', (0, 0, 0.95), (0, 0, 1.05))
bone('Spine', (0, 0, 1.05), (0, 0, 1.25), 'Hips')
bone('Chest', (0, 0, 1.25), (0, 0, 1.45), 'Spine')
bone('Neck', (0, 0, 1.45), (0, 0, 1.55), 'Chest')
bone('Head', (0, 0, 1.55), (0, 0, 1.75), 'Neck')
for side, x in (('L', 1), ('R', -1)):
    bone('UpperLeg.' + side, (0.1 * x, 0, 0.95), (0.1 * x, 0, 0.52), 'Hips')
    bone('LowerLeg.' + side, (0.1 * x, 0, 0.52), (0.1 * x, 0, 0.1), 'UpperLeg.' + side)
    bone('Foot.' + side, (0.1 * x, 0, 0.1), (0.1 * x, -0.15, 0.02), 'LowerLeg.' + side)
    bone('UpperArm.' + side, (0.18 * x, 0, 1.42), (0.45 * x, 0, 1.42), 'Chest')
    bone('LowerArm.' + side, (0.45 * x, 0, 1.42), (0.7 * x, 0, 1.42), 'UpperArm.' + side)
    bone('Hand.' + side, (0.7 * x, 0, 1.42), (0.8 * x, 0, 1.42), 'LowerArm.' + side)
bpy.ops.object.mode_set(mode='OBJECT')
mesh = bpy.data.meshes.new('Robot')
body = bpy.data.objects.new('Robot', mesh)
bpy.context.scene.collection.objects.link(body)
built = bmesh.new()
groups = []
for b in data.bones:
    head, tail = b.head_local, b.tail_local
    centre = (head + tail) / 2
    size = Vector((0.08, 0.08, 0.08)) + Vector([abs(v) for v in (tail - head)])
    made = bmesh.ops.create_cube(built, size=1.0)['verts']
    for v in made:
        v.co = centre + Vector(v.co[i] * size[i] for i in range(3))
    groups.append((b.name, [v for v in made]))
built.verts.index_update()
built.to_mesh(mesh)
for name, verts in groups:
    group = body.vertex_groups.new(name=name)
    group.add([v.index for v in verts], 1.0, 'REPLACE')
built.free()
body.parent = rig
body.modifiers.new('Armature', 'ARMATURE').object = rig
"""

GODOT_CHECK = """extends SceneTree
func _init() -> void:
\tfor path: String in ["res://art/models/hero.glb", "res://art/models/robot.glb"]:
\t\tvar root: Node = (load(path) as PackedScene).instantiate()
\t\tvar names: PackedStringArray = []
\t\tfor player: Node in root.find_children("*", "AnimationPlayer", true, false):
\t\t\tfor name: StringName in (player as AnimationPlayer).get_animation_list():
\t\t\t\tvar clip: Animation = (player as AnimationPlayer).get_animation(name)
\t\t\t\tnames.append("%s=%.3f" % [name, clip.length])
\t\tprint("JOINERY_GODOT_ANIMS ", path, " ", ",".join(names))
\t\troot.free()
\tquit()
"""


def rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r["action"]): r for r in result.get("animations", [])}


def check_loop(row: dict[str, Any]) -> None:
    assert row["loop"], row
    assert row["seam"] <= SEAM, f"{row['action']} does not meet itself: {row['seam']}"


def check_feet(row: dict[str, Any]) -> None:
    assert row["contact_frames"] > 0, row
    assert row["foot_slide"] <= SLIDE, f"{row['action']} feet slide {row['foot_slide']}"


async def timed(facts: dict[str, Any], key: str, job: Any) -> dict[str, Any]:
    start = time.monotonic()
    result: dict[str, Any] = await job
    facts.setdefault("seconds", {})[key] = round(time.monotonic() - start, 1)
    return result


async def main(stage: Path) -> dict[str, Any]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled=["blender"]\n\n'
        '[game]\nblueprint = "third-person-3d"\nblueprint_version = 1\n'
    )
    (stage / "project.godot").write_text("config_version=5\n")
    facts: dict[str, Any] = {}

    await timed(
        facts,
        "build_hero",
        edit_document(stage, "blender", HERO, BUILD, [HERO_GLB], "hero"),
    )
    found = await animation.audit(stage, HERO_GLB)
    assert found["missing"] == [
        "idle",
        "walk",
        "run",
        "jump_start",
        "jump_air",
        "jump_land",
    ], found
    made = await timed(
        facts, "animation_pass", animation.animation_pass(stage, HERO_GLB, "", None)
    )
    hero = rows(made)
    assert sorted(hero) == sorted(found["missing"]), sorted(hero)
    for name in ("idle", "walk", "run", "jump_air"):
        check_loop(hero[name])
    for name in ("idle", "walk", "run", "jump_start", "jump_land"):
        check_feet(hero[name])
    assert (
        hero["walk"]["ground_speed"] > 0.3
        and hero["run"]["ground_speed"] > hero["walk"]["ground_speed"]
    )
    assert (await animation.audit(stage, HERO))["missing"] == []
    facts["hero"] = {
        n: {k: r[k] for k in ("seconds", "seam", "foot_slide", "bounce", "stride")}
        for n, r in hero.items()
    }
    for kind in ("attack", "wave"):
        row = rows(
            await timed(
                facts, kind, animation.make_cycle(stage, HERO, kind, None, "", None)
            )
        )[kind]
        check_feet(row)
        facts["hero"][kind] = {k: row[k] for k in ("seconds", "seam", "foot_slide")}
    check_loop(facts["hero"]["wave"] | {"loop": True, "action": "wave"})

    # The parts list carries every action as an animation part.
    report = await parts.report(stage, HERO_GLB, refresh=True)
    listed = {p.action: p for p in report.parts if p.part_kind == "animation"}
    assert set(listed) >= {"walk", "run", "idle", "attack", "wave"}, sorted(listed)
    assert listed["walk"].loop and listed["walk"].object == "HeroRig", listed["walk"]
    assert listed["walk"].target()["label"] == "walk · Hero", listed["walk"].target()

    # A selection holding run refuses an edit to walk.
    parts.write_targets(stage, [listed["run"].target()])
    try:
        await animation.edit_action(stage, HERO, "walk", [{"op": "mirror"}], "", None)
    except ValueError as refused:
        assert "not part of the selection" in str(refused), refused
    else:
        raise AssertionError("an action outside the selection was edited")
    parts.write_targets(stage, [])

    original = stage.parent / "original"
    shutil.copytree(stage, original, ignore=shutil.ignore_patterns(".joinery"))
    walk = hero["walk"]
    faster = rows(
        await timed(
            facts,
            "retime",
            animation.edit_action(
                stage, HERO, "walk", [{"op": "retime", "speed": 1.25}], "", None
            ),
        )
    )["walk"]
    assert abs(faster["seconds"] - walk["seconds"] / 1.25) < 1.5 / faster["fps"], faster
    check_loop(faster)
    bouncier = rows(
        await timed(
            facts,
            "bounce",
            animation.edit_action(
                stage, HERO, "run", [{"op": "bounce", "factor": 1.3}], "", None
            ),
        )
    )["run"]
    assert bouncier["bounce"] > hero["run"]["bounce"] * 1.15, (bouncier, hero["run"])
    check_loop(bouncier)
    check_feet(bouncier)
    facts["bouncier_run"] = {
        "before": hero["run"]["bounce"],
        "after": bouncier["bounce"],
    }

    # Bigger leg motion breaks the planted feet; foot_lock pins them again.
    sliding = rows(
        await animation.edit_action(
            stage,
            HERO,
            "idle",
            [
                {
                    "op": "amplitude",
                    "bones": "legs",
                    "channel": "rotation",
                    "factor": 3.0,
                }
            ],
            "",
            None,
        )
    )["idle"]
    locked = rows(
        await timed(
            facts,
            "foot_lock",
            animation.edit_action(stage, HERO, "idle", [{"op": "foot_lock"}], "", None),
        )
    )["idle"]
    assert sliding["foot_slide"] > locked["foot_slide"], (sliding, locked)
    check_feet(locked)
    check_loop(locked)
    facts["foot_lock"] = {
        "before": sliding["foot_slide"],
        "after": locked["foot_slide"],
    }

    edited = rows(
        await timed(
            facts,
            "mirror_seam_offset",
            animation.edit_action(
                stage,
                HERO,
                "wave",
                [{"op": "mirror"}, {"op": "offset", "frames": 5}, {"op": "loop_seam"}],
                "",
                None,
            ),
        )
    )["wave"]
    check_loop(edited)
    copied = rows(
        await animation.edit_action(
            stage, HERO, "walk", [{"op": "copy", "name": "stroll"}], "", None
        )
    )
    assert "stroll" in copied, copied
    renamed = rows(
        await animation.edit_action(
            stage, HERO, "stroll", [{"op": "rename", "name": "amble"}], "", None
        )
    )
    assert renamed["amble"]["was"] == "stroll", renamed
    gone = rows(
        await animation.edit_action(stage, HERO, "amble", [{"op": "delete"}], "", None)
    )
    assert gone["amble"]["deleted"], gone

    # The review shows run before and after as clips, with what each measured.
    tx = SimpleNamespace(original=original, stage=stage)
    media, renders = review.plan(tx, [{"path": HERO}], [listed["run"].target()])  # type: ignore[arg-type]
    clips = [m for m in media if m.get("media") == "clip"]
    start = time.monotonic()
    await review.render([r for r in renders if r.optional])
    facts.setdefault("seconds", {})["review_clips"] = round(time.monotonic() - start, 1)
    run_clip = next(c for c in clips if c["label"] == "run")
    assert Path(run_clip["before"]).is_file() and Path(run_clip["after"]).is_file(), (
        run_clip
    )
    assert run_clip["after_clip"]["bounce"] > run_clip["before_clip"]["bounce"], (
        run_clip
    )
    facts["review_clips"] = sorted(str(c["label"]) for c in clips)

    # A hand-made humanoid, driven through its own bones.
    await edit_document(stage, "blender", ROBOT, ROBOT_CODE, [ROBOT_GLB], "robot")
    for kind in ("walk", "idle", "jump", "wave"):
        made = rows(
            await timed(
                facts,
                "robot_" + kind,
                animation.make_cycle(stage, ROBOT, kind, None, "", None),
            )
        )
        for name, row in made.items():
            assert not row["rigify"], row
            if name in {"walk", "idle", "wave", "jump_air"}:
                check_loop(row)
            if name in {"walk", "idle", "jump_start", "jump_land"}:
                check_feet(row)
            facts.setdefault("robot", {})[name] = {
                k: row[k] for k in ("seconds", "seam", "foot_slide")
            }

    # Godot imports both models with their actions by name.
    (stage / "anim_check.gd").write_text(GODOT_CHECK)
    imported = await run_offline(
        ["godot", "--headless", "--path", "/work", "--editor", "--import"], stage, 600
    )
    assert imported.ok, imported.output[-2000:]
    listed_godot = await run_offline(
        ["godot", "--headless", "--path", "/work", "--script", "res://anim_check.gd"],
        stage,
        300,
    )
    lines = [
        l
        for l in listed_godot.output.splitlines()
        if l.startswith("JOINERY_GODOT_ANIMS")
    ]
    godot = {
        line.split()[1]: dict(x.split("=") for x in line.split()[2].split(","))
        for line in lines
    }
    assert {
        "walk",
        "run",
        "idle",
        "jump_start",
        "jump_air",
        "jump_land",
        "attack",
        "wave",
    } <= set(godot["res://art/models/hero.glb"]), godot
    assert {"walk", "idle", "wave", "jump_air"} <= set(
        godot["res://art/models/robot.glb"]
    ), godot
    walk_length = float(godot["res://art/models/hero.glb"]["walk"])
    assert abs(walk_length - faster["seconds"]) < 1e-3, (walk_length, faster)
    facts["godot"] = {k.rsplit("/", 1)[-1]: sorted(v) for k, v in godot.items()}

    start = time.monotonic()
    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    facts["replayed"] = len(replays)
    facts.setdefault("seconds", {})["rebuild"] = round(time.monotonic() - start, 1)
    return facts


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-anim-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("ANIMATION_OK")
        sys.exit(0)
