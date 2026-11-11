"""Rigged and modified Blender models exported for Godot, in the real worker.

Builds a human with MPFB2, adds MPFB2's Rigify meta-rig with its weights,
generates the Rigify rig and exports a GLB. The game gets only the deform
bones: the GLB skin must list exactly the rig's deform bones, every one a
`DEF-` bone, and the mesh must be skinned to it. MPFB2's helper geometry
(hidden by a Mask modifier) must not reach the GLB, while the human's shape
keys are body-shape macros, baked into the mesh by default and kept as morph
targets when the recipe says so. A second recipe poses an FK control in an action; the exported
animation must drive deform bones only. A hand-made crate with a Subdivision
modifier and a shape key checks that the GLB shows what Blender shows. Every
recipe replays (Blender recipes are compared as Blender writes them; see
joinery.recipe.fingerprint).

Technical fixtures in a temporary project; nothing here approves art direction
or touches a user's game. Run: `uv run python tests/integration/blender_rig.py`.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery.authoring import IMAGE, edit_document, inspect_document, run_offline
from joinery.bible_blend import measure_argv, parse_measures
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner

SOURCE, EXPORT = "art/sources/hero.blend", "art/models/hero.glb"

# The code names MPFB2 and Rigify, so the recipe enables both first.
BUILD = """
from bl_ext.system.mpfb.services.humanservice import HumanService
from bl_ext.system.mpfb.services.rigservice import RigService
human = HumanService.create_human()
human.name = 'Hero'
human.data.name = 'Hero'  # MPFB2 calls it 'base'; art bibles name meshes
meta = HumanService.add_builtin_rig(human, 'rigify.human_toes', import_weights=True)
rig = RigService.generate_rigify_rig(meta, name='HeroRig', meta_rig_action='delete')
assert rig is not None, 'Rigify rejected the MPFB2 meta-rig'
rig.name = 'HeroRig'
"""

# Raise the left arm over 20 frames through its FK control (not a DEF bone).
WAVE = """
rig = bpy.data.objects['HeroRig']
control = rig.pose.bones['upper_arm_fk.L']
control.rotation_mode = 'XYZ'
rig.animation_data_create()
rig.animation_data.action = bpy.data.actions.new('Wave')
for frame, angle in ((1, 0.0), (20, 1.2)):
    control.rotation_euler = (0.0, 0.0, angle)
    control.keyframe_insert('rotation_euler', frame=frame)
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 20
"""


CRATE, CRATE_EXPORT = "art/sources/crate.blend", "art/models/crate.glb"
CRATE_CODE = """
bpy.ops.mesh.primitive_cube_add(size=1)
crate = bpy.context.object
crate.name = 'Crate'
crate.modifiers.new('Smooth', 'SUBSURF').levels = 2
crate.shape_key_add(name='Basis')
dent = crate.shape_key_add(name='Dent')
dent.data[0].co.z -= 0.2
"""


def glb_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    assert data[:4] == b"glTF", "not a GLB"
    length, kind = struct.unpack("<I4s", data[12:20])
    assert kind == b"JSON", kind
    found: dict[str, Any] = json.loads(data[20 : 20 + length])
    return found


def rig_row(document: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        o for o in document["objects"] if o["type"] == "ARMATURE"
    ]
    assert len(rows) == 1, [row["name"] for row in rows]
    return rows[0]


async def main(stage: Path) -> dict[str, Any]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    (stage / "project.godot").write_text("config_version=5\n")
    facts: dict[str, Any] = {}

    built = await edit_document(
        stage, "blender", SOURCE, BUILD, [EXPORT], "rigged human"
    )
    document = await inspect_document(stage, "blender", SOURCE)
    rig = rig_row(document)
    assert rig["name"] == "HeroRig", rig
    assert rig["bone_count"] > rig["deform_bone_count"] > 50, rig
    hero = next(o for o in document["objects"] if o["name"] == "Hero")
    assert hero["parent"] == "HeroRig" and hero["vertices"] > 10000, hero
    assert any(m["type"] == "ARMATURE" for m in hero["modifiers"]), hero

    gltf = glb_json(stage / EXPORT)
    (skin,) = gltf["skins"]
    joints = [gltf["nodes"][i]["name"] for i in skin["joints"]]
    deform = {b["name"] for b in rig["bones"] if b["deform"]}
    assert len(deform) == rig["deform_bone_count"]
    assert sorted(set(joints) ^ deform) == [], sorted(set(joints) ^ deform)
    assert len(joints) == len(set(joints)), "a joint is listed twice"
    skinned = [n for n in gltf["nodes"] if "skin" in n and "mesh" in n]
    assert skinned, "the human mesh is not skinned to the rig"
    # Rigify's control shapes stay in the .blend to draw the rig, flagged as
    # widgets for the art bible's checks, and never reach the GLB.
    measured = await run_offline(measure_argv([SOURCE]), stage, 300, readonly=True)
    widgets = [
        o.name for o in parse_measures(measured.output)[SOURCE].objects if o.widget
    ]
    assert len(widgets) > 100 and all(w.startswith("WGT-") for w in widgets), widgets[
        :5
    ]
    stray = [n["name"] for n in gltf["nodes"] if n["name"].startswith("WGT-")]
    assert not stray, stray[:5]
    assert len(gltf["nodes"]) == len(joints) + 2, len(gltf["nodes"])  # + body, rig
    # MPFB2 hides its helper geometry with a Mask modifier. The game gets the
    # body only (no helpers, so no unweighted vertices and no "neutral_bone").
    # Its shape keys are all body-shape macros ($md-…): by default the export
    # bakes their mix into the mesh, so the GLB has no morph targets.
    (primitive,) = gltf["meshes"][skinned[0]["mesh"]]["primitives"]
    position = gltf["accessors"][primitive["attributes"]["POSITION"]]
    exported = position["count"]
    assert exported < hero["vertices"], (exported, hero["vertices"])
    macros = [k for k in hero["shape_keys"][1:] if k.startswith("$md-")]
    assert macros and len(macros) == len(hero["shape_keys"]) - 1, hero["shape_keys"]
    assert "targets" not in primitive, "the macro shape keys reached the GLB"
    (row,) = [r for r in built["shape_keys"] if r["object"] == "Hero"]
    assert row["baked"] == len(macros) and row["kept"] == 0, row
    facts.update(
        bones=rig["bone_count"],
        deform_bones=len(joints),
        source_vertices=hero["vertices"],
        rig_widgets_left_out=len(widgets),
        glb_nodes=len(gltf["nodes"]),
        exported_vertices=exported,
        source_shape_keys=len(hero["shape_keys"]) - 1,
        morph_targets_baked=len(primitive.get("targets", [])),
        glb_bytes_baked=(stage / EXPORT).stat().st_size,
    )
    # Kept on request: the macros reach the GLB as morph targets at their
    # values, over the unmixed base, and the recipe records the choice.
    kept = await edit_document(
        stage,
        "blender",
        SOURCE,
        "pass",
        [EXPORT],
        "keep the body shapes",
        keep_macro_shape_keys=True,
    )
    gltf = glb_json(stage / EXPORT)
    (mesh,) = [
        gltf["meshes"][n["mesh"]] for n in gltf["nodes"] if "skin" in n and "mesh" in n
    ]
    (primitive,) = mesh["primitives"]
    assert len(primitive.get("targets", [])) == len(macros), primitive.keys()
    assert mesh["extras"]["targetNames"] == macros, mesh["extras"]
    assert (
        gltf["accessors"][primitive["attributes"]["POSITION"]]["min"] != position["min"]
    )
    assert (
        "_joinery_bake_macro_keys(keep=True)"
        in (stage / kept["recipe"]).with_suffix(".py").read_text()
    )
    facts.update(
        morph_targets_kept=len(primitive["targets"]),
        glb_bytes_kept=(stage / EXPORT).stat().st_size,
    )

    # A later edit keeps the model's choice without being told again.
    await edit_document(stage, "blender", SOURCE, WAVE, [EXPORT], "wave")
    gltf = glb_json(stage / EXPORT)
    assert any(
        len(p.get("targets", [])) == len(macros)
        for m in gltf["meshes"]
        for p in m["primitives"]
    ), "the kept macro shape keys were dropped by a later edit"
    (skin,) = gltf["skins"]
    skinned_bones = {gltf["nodes"][i]["name"] for i in skin["joints"]}
    (wave,) = [a for a in gltf.get("animations", []) if a["name"] == "Wave"]
    targets = {gltf["nodes"][c["target"]["node"]]["name"] for c in wave["channels"]}
    assert targets and targets <= skinned_bones, sorted(targets - skinned_bones)[:10]
    assert "DEF-upper_arm.L" in targets, sorted(targets)[:20]
    document = await inspect_document(stage, "blender", SOURCE)
    assert rig_row(document).get("action") == "Wave", rig_row(document)
    facts["animated_bones"] = len(targets)

    # A hand-made model: what Godot shows is what Blender shows. The
    # Subdivision modifier reaches the GLB, and so does its shape key, which
    # is not a macro and so is never baked.
    await edit_document(stage, "blender", CRATE, CRATE_CODE, [CRATE_EXPORT], "crate")
    gltf = glb_json(stage / CRATE_EXPORT)
    (mesh,) = gltf["meshes"]
    (primitive,) = mesh["primitives"]
    count = gltf["accessors"][primitive["attributes"]["POSITION"]]["count"]
    assert count > 24, f"the Subdivision modifier was not applied ({count} vertices)"
    assert len(primitive.get("targets", [])) == 1, primitive
    assert mesh.get("extras", {}).get("targetNames") == ["Dent"], mesh
    facts["crate_vertices"] = count

    # Godot imports both models.
    imported = await run_offline(
        ["godot", "--headless", "--path", "/work", "--editor", "--import"], stage, 300
    )
    assert imported.ok, imported.output[-2000:]
    scenes = list((stage / ".godot/imported").glob("*.scn"))
    assert len(scenes) >= 2, sorted(
        p.name for p in (stage / ".godot/imported").iterdir()
    )

    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    facts["replayed"] = len(replays)
    return facts


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-rig-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("BLENDER_RIG_OK")
        sys.exit(0)
