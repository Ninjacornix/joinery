"""A character's shape keys and body regions, in the real worker.

Builds an MPFB2 human with a generated Rigify rig and two expression keys
("smile", "viseme_aa"), then:

- The default export bakes MPFB2's body-shape keys and keeps the expressions
  as the GLB's morph targets.
- set_body_shape (MPFB2's own API: a macro and a symmetric pair of detail
  targets) widens the shoulders. The .blend keeps every key editable; the GLB
  gets the new shape baked in (its measured width grows, as the art bible's
  checks measure it) and still only the expressions as morph targets.
- bake_shape_keys keeps only "smile" for the game, the choice is recorded, a
  later edit keeps it, and a name no mesh has is refused.
- The parts list has the body regions (by deform bone), the body settings,
  shape keys flagged in or out of the game, and the GLB carries the regions'
  bones (extras) for picking in the editor.
- Every recipe replays.

Technical fixtures in a temporary project; nothing here approves art direction
or touches a user's game. Run: `uv run python tests/integration/character_parts.py`.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery import parts
from joinery.authoring import IMAGE, edit_document, inspect_document, run_offline
from joinery.bible_rules import measure_glb
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.tools.blender_regions import REGION_TARGETS
from servers.blender_ops import character

SOURCE, EXPORT = "art/sources/hero.blend", "art/models/hero.glb"

# MPFB2 and Rigify (both named, so the recipe enables them), then two keys the
# game animates: moved on the head only, as a face rig's expressions would be.
BUILD = """
from bl_ext.system.mpfb.services.humanservice import HumanService
from bl_ext.system.mpfb.services.rigservice import RigService
human = HumanService.create_human()
human.name = 'Hero'
human.data.name = 'Hero'
meta = HumanService.add_builtin_rig(human, 'rigify.human_toes', import_weights=True)
rig = RigService.generate_rigify_rig(meta, name='HeroRig', meta_rig_action='delete')
assert rig is not None, 'Rigify rejected the MPFB2 meta-rig'
rig.name = 'HeroRig'
head = {g.index for g in human.vertex_groups if g.name in ('DEF-jaw', 'DEF-chin', 'DEF-lip.B.L', 'DEF-lip.B.R')}
mouth = [v.index for v in human.data.vertices if any(e.group in head and e.weight > 0.5 for e in v.groups)]
assert mouth, 'no mouth vertices'
for name, lift in (('smile', 0.004), ('viseme_aa', -0.006)):
    key = human.shape_key_add(name=name, from_mix=False)
    for index in mouth:
        key.data[index].co.z += lift
"""

# MPFB2's own check that the region hints name real targets.
CHECK_TARGETS = """
import addon_utils, json
addon_utils.enable('bl_ext.system.mpfb', default_set=True)
from bl_ext.system.mpfb.services.targetservice import TargetService
names = json.loads(NAMES)
print('JOINERY_MISSING:' + json.dumps([n for n in names if not TargetService.target_full_path(n)]))
"""


def glb_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    length, kind = struct.unpack("<I4s", data[12:20])
    assert data[:4] == b"glTF" and kind == b"JSON"
    found: dict[str, Any] = json.loads(data[20 : 20 + length])
    return found


def body_mesh(gltf: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(node, mesh) of the skinned body."""
    (node,) = [n for n in gltf["nodes"] if "skin" in n and "mesh" in n]
    return node, gltf["meshes"][node["mesh"]]


def morph_names(gltf: dict[str, Any]) -> list[str]:
    _node, mesh = body_mesh(gltf)
    names: list[str] = mesh.get("extras", {}).get("targetNames", [])
    targets = mesh["primitives"][0].get("targets", [])
    assert len(names) == len(targets), (names, len(targets))
    return names


def width(stage: Path) -> float:
    measured = measure_glb((stage / EXPORT).read_bytes())
    assert measured.bbox_min and measured.bbox_max
    return measured.bbox_max[0] - measured.bbox_min[0]


def hero(document: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = next(o for o in document["objects"] if o["name"] == "Hero")
    return found


async def main(stage: Path) -> dict[str, Any]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    (stage / "project.godot").write_text("config_version=5\n")
    facts: dict[str, Any] = {}

    built = await edit_document(stage, "blender", SOURCE, BUILD, [EXPORT], "human")
    gltf = glb_json(stage / EXPORT)
    assert morph_names(gltf) == ["smile", "viseme_aa"], morph_names(gltf)
    (row,) = [r for r in built["shape_keys"] if r["object"] == "Hero"]
    assert row["kept_names"] == ["smile", "viseme_aa"] and row["baked"] >= 8, row
    node, _mesh = body_mesh(gltf)
    extras = node.get("extras", {})
    assert extras.get("joinery_role") == "basemesh", extras.keys()
    regions = json.loads(extras["joinery_regions"])
    assert "DEF-shoulder.L" in regions["shoulders"], regions.get("shoulders")
    assert "DEF-upper_arm.L" in regions["arms"] and "DEF-spine.006" in regions["head"]
    before_width = width(stage)
    facts.update(
        morph_targets=morph_names(gltf),
        regions=sorted(regions),
        baked_keys=row["baked"],
        width_m=round(before_width, 4),
    )

    # Bulkier shoulders, through MPFB2: a macro and a symmetric target pair.
    shaped = await character.set_body_shape(
        stage,
        SOURCE,
        "Hero",
        {"muscle": 0.8},
        {
            "l-upperarm-shoulder-muscle-incr": 1.0,
            "r-upperarm-shoulder-muscle-incr": 1.0,
            "measure-shoulder-dist-incr": 1.0,
        },
        None,
    )
    document = await inspect_document(stage, "blender", SOURCE)
    keys = hero(document)["shape_keys"]
    assert "l-upperarm-shoulder-muscle-incr" in keys, keys
    assert any(k.startswith("$md-") for k in keys), "the .blend lost its macro keys"
    assert hero(document)["mpfb_macros"]["muscle"] == 0.8, hero(document)["mpfb_macros"]
    gltf = glb_json(stage / EXPORT)
    assert morph_names(gltf) == ["smile", "viseme_aa"], morph_names(gltf)
    after_width = width(stage)
    assert after_width > before_width + 0.005, (before_width, after_width)
    (done,) = shaped["operation"]
    assert done["macros"]["muscle"][1] == 0.8 and done["refit"], done
    facts.update(width_after_m=round(after_width, 4), body_shape=done)

    # The game keeps only the smile; the .blend keeps everything.
    chosen = await character.bake_shape_keys(
        stage, SOURCE, ["smile"], {"viseme_aa": 0.0}, "", False, None
    )
    assert morph_names(glb_json(stage / EXPORT)) == ["smile"]
    script = (stage / chosen["recipe"]).with_suffix(".py").read_text()
    assert "_joinery_bake_shape_keys(keep_macros=False, keep=('smile',))" in script
    await edit_document(stage, "blender", SOURCE, "pass", [EXPORT], "touch")
    assert morph_names(glb_json(stage / EXPORT)) == ["smile"], (
        "a later edit dropped the choice"
    )
    document = await inspect_document(stage, "blender", SOURCE)
    assert "viseme_aa" in hero(document)["shape_keys"]
    try:
        await character.bake_shape_keys(stage, SOURCE, ["frown"], None, "", False, None)
    except RuntimeError as error:
        assert "frown" in str(error), error
    else:
        raise AssertionError("a shape key no mesh has was kept")
    assert morph_names(glb_json(stage / EXPORT)) == ["smile"]

    # Parts: regions, body settings, and which keys reach the game.
    report = await parts.report(stage, EXPORT, refresh=True)
    found = {
        (p.part_kind, p.region or p.parameter or p.shape_key): p for p in report.parts
    }
    shoulders = found[("region", "shoulders")]
    assert "DEF-shoulder.R" in shoulders.bones and shoulders.vertices > 100
    assert "l-upperarm-shoulder-muscle-incr" in shoulders.targets
    assert shoulders.ops[0] == "set_body_shape", shoulders.ops
    assert found[("body_shape", "muscle")].value == "0.8"
    assert found[("shape_key", "smile")].in_game
    assert found[("shape_key", "smile")].regions == ("head",), found[
        ("shape_key", "smile")
    ]
    assert not found[("shape_key", "viseme_aa")].in_game
    detail = found[("shape_key", "l-upperarm-shoulder-muscle-incr")]
    assert detail.shape_role == "body" and not detail.in_game
    vertices = sum(p.vertices for p in report.parts if p.part_kind == "region")
    assert vertices > 10000, vertices
    facts["region_vertices"] = {
        p.region: p.vertices for p in report.parts if p.part_kind == "region"
    }
    hints = sorted({t for names in REGION_TARGETS.values() for t in names})
    checked = await run_offline(
        [
            "blender",
            "--background",
            "--factory-startup",
            "--python-expr",
            CHECK_TARGETS.replace("NAMES", repr(json.dumps(hints))),
        ],
        stage,
        300,
        readonly=True,
    )
    line = next(
        x for x in checked.output.splitlines() if x.startswith("JOINERY_MISSING:")
    )
    assert json.loads(line.removeprefix("JOINERY_MISSING:")) == [], line

    replays = [
        rebuild_one(path, stage, SandboxRunner(image=IMAGE))
        for path in recipe_paths(stage)
    ]
    assert replays and all(replay.ok for replay in replays), replays
    facts["replayed"] = len(replays)
    return facts


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-character-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage)), indent=1))
        print("CHARACTER_PARTS_OK")
        sys.exit(0)
