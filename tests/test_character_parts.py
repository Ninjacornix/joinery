"""A character's parts (body regions, body settings, shape keys) and its typed operations.

Blender is replaced at its boundary; tests/integration/character_parts.py runs
the same on a real MPFB2 human with a Rigify rig.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import parts
from joinery.bible_rules import BibleRules, _glb_problems, measure_glb
from joinery.tools import blender, blender_character, blender_regions
from joinery.tools.blender_export import ExportShapeKeys
from servers.blender_ops import character

SOURCE = "art/sources/hero.blend"
RESOURCE = "res://art/models/hero.glb"


@pytest.mark.parametrize(
    ("bone", "region"),
    [
        # Rigify (MPFB2's rigify.human_toes), its spine numbered hips to head.
        ("DEF-spine", "hips"),
        ("DEF-spine.002", "torso"),
        ("DEF-spine.004", "neck"),
        ("DEF-spine.006", "head"),
        ("DEF-shoulder.L", "shoulders"),
        ("DEF-upper_arm.L.001", "arms"),
        ("DEF-forearm.R", "arms"),
        ("DEF-f_index.02.L", "hands"),
        ("DEF-thumb.01.R", "hands"),
        ("DEF-thigh.L.001", "legs"),
        ("DEF-toe2-1.L", "feet"),
        ("DEF-ear.L.003", "head"),
        ("DEF-lid.B.R", "head"),
        ("DEF-pelvis.L", "hips"),
        ("DEF-breast.R", "torso"),
        # MPFB2's own rig and a Mixamo rig.
        ("upperarm01.L", "arms"),
        ("clavicle.R", "shoulders"),
        ("spine03", "torso"),
        ("neck02", "neck"),
        ("mixamorig:LeftHandIndex1", "hands"),
        ("mixamorig:LeftUpLeg", "legs"),
        ("mixamorig:Hips", "hips"),
        # MPFB2's helper and joint groups are not the body.
        ("joint-head", ""),
        ("helper-tongue", ""),
        ("HelperGeometry", ""),
        ("root", ""),
        ("Socket", ""),
    ],
)
def test_bones_name_their_region(bone: str, region: str) -> None:
    assert blender_regions.region_of(bone) == region


def test_the_blender_scripts_name_regions_the_same_way() -> None:
    scope: dict[str, Any] = {"bpy": None}
    # The very source the Blender scripts embed (definitions only).
    exec(blender_regions.REGION_CODE, scope)  # noqa: S102
    for bone in ("DEF-spine.004", "upperarm01.L", "joint-head", "mixamorig:LeftFoot"):
        assert scope["region_of"](bone) == blender_regions.region_of(bone)
    compile(blender.INSPECT_BLEND, "inspect.py", "exec")
    script = blender.MODULE.script(SOURCE, "", "pass", ["art/models/hero.glb"])
    compile(script, "recipe.py", "exec")
    # The GLB carries each mesh's regions and MPFB2 role for picking.
    assert "obj['joinery_regions']" in script and "obj['joinery_role']" in script


def _inspection() -> dict[str, Any]:
    regions = {
        "head": {"bones": ["DEF-spine.006", "DEF-jaw"], "vertices": 3000},
        "shoulders": {"bones": ["DEF-shoulder.L", "DEF-shoulder.R"], "vertices": 400},
        "arms": {"bones": ["DEF-upper_arm.L"], "vertices": 1200},
    }
    return {
        "objects": [
            {
                "name": "Hero",
                "type": "MESH",
                "role": "basemesh",
                "shape_keys": [
                    "Basis",
                    "$md-$as-$fe-$yn",
                    "l-hand-scale-incr",
                    "smile",
                ],
                "shape_key_values": {
                    "$md-$as-$fe-$yn": 0.165,
                    "l-hand-scale-incr": 0.5,
                    "smile": 0.0,
                },
                "shape_key_regions": {
                    "smile": ["head"],
                    "l-hand-scale-incr": ["hands"],
                },
                "regions": regions,
                "mpfb_macros": {"muscle": 0.8, "weight": 0.5, "african": 0.33},
                "slots": [],
            },
            {
                "name": "Shirt",
                "type": "MESH",
                "role": "clothes",
                "regions": {"torso": {"bones": ["DEF-spine.002"], "vertices": 900}},
                "slots": [],
            },
            {
                "name": "HeroRig",
                "type": "ARMATURE",
                "role": "skeleton",
                "bones": [{"name": "DEF-shoulder.L"}, {"name": "root"}],
            },
        ],
        "materials": [],
    }


def _report(chosen: ExportShapeKeys | None = None) -> parts.PartsReport:
    return parts.build_report(
        _inspection(), "blender", SOURCE, RESOURCE, "0" * 64, chosen
    )


def test_a_character_lists_its_regions_and_body_settings() -> None:
    report = _report()
    regions = [p for p in report.parts if p.part_kind == "region"]
    # Head to toe; clothes are picked whole, never by region.
    assert [(p.object, p.region) for p in regions] == [
        ("Hero", "head"),
        ("Hero", "shoulders"),
        ("Hero", "arms"),
    ]
    shoulders = report.find(
        {"part_kind": "region", "object": "Hero", "region": "shoulders"}
    )
    assert shoulders.id == RESOURCE + "#object=Hero&region=shoulders"
    assert shoulders.bones == ("DEF-shoulder.L", "DEF-shoulder.R")
    assert "l-upperarm-shoulder-muscle-incr" in shoulders.targets
    assert shoulders.ops == ("set_body_shape", "edit_bone")
    target = shoulders.target()
    assert target["label"] == "Shoulders · Hero"
    assert target["kind"] == "Body region" and target["vertices"] == "400"
    assert parts.chip_tooltip(target).startswith("Hero › Hero › Shoulders")
    muscle = report.find({"object": "Hero", "parameter": "muscle"})
    assert muscle.part_kind == "body_shape" and muscle.value == "0.8"
    assert muscle.label == "Muscle · Hero" and muscle.ops == ("set_body_shape",)
    assert (
        report.find({"object": "Hero", "parameter": "african"}).label
        == "African · Hero"
    )
    shirt = report.find({"object": "Shirt"})
    assert shirt.object_role == "clothes"
    bone = report.find({"object": "HeroRig", "bone": "DEF-shoulder.L"})
    assert bone.regions == ("shoulders",)
    assert report.find({"object": "HeroRig", "bone": "root"}).regions == ()
    counts = report.counts()
    assert counts["region"] == 3 and counts["body_shape"] == 3


def test_a_rigged_model_without_mpfb2_routes_regions_to_its_bones() -> None:
    inspection = _inspection()
    hero = inspection["objects"][0]
    del hero["mpfb_macros"], hero["role"]
    report = parts.build_report(inspection, "blender", SOURCE, RESOURCE, "0" * 64)
    arms = report.find({"object": "Hero", "region": "arms"})
    assert arms.targets == () and arms.ops == ("edit_bone",)
    assert not [p for p in report.parts if p.part_kind == "body_shape"]


def test_shape_keys_say_what_they_are_and_whether_the_game_has_them() -> None:
    report = _report()
    smile = report.find({"object": "Hero", "shape_key": "smile"})
    macro = report.find({"object": "Hero", "shape_key": "$md-$as-$fe-$yn"})
    detail = report.find({"object": "Hero", "shape_key": "l-hand-scale-incr"})
    assert smile.shape_role == "expression" and smile.in_game
    assert smile.regions == ("head",) and smile.value == "0"
    assert macro.shape_role == "body" and not macro.in_game
    assert detail.shape_role == "body" and not detail.in_game
    assert detail.target()["in_game"] == ""
    chosen = _report(ExportShapeKeys(keep=("l-hand-scale-incr",)))
    assert not chosen.find({"object": "Hero", "shape_key": "smile"}).in_game
    assert chosen.find({"object": "Hero", "shape_key": "l-hand-scale-incr"}).in_game
    kept = _report(ExportShapeKeys(keep_macros=True))
    assert kept.find({"object": "Hero", "shape_key": "$md-$as-$fe-$yn"}).in_game
    # A plain model's key is an expression even when named like a target.
    assert not parts.is_body_key("brow-up", False)
    assert parts.is_body_key("$md-universal", False)


def test_the_parts_list_reads_the_recorded_game_keys(tmp_path: Path) -> None:
    (tmp_path / "art/sources").mkdir(parents=True)
    (tmp_path / SOURCE).write_bytes(b"blend")
    (tmp_path / "recipes").mkdir()
    chosen = ExportShapeKeys(keep=("l-hand-scale-incr",))
    (tmp_path / "recipes/a.py").write_text(
        blender.MODULE.script(
            SOURCE, "", "pass", ["art/models/hero.glb"], shape_keys=chosen
        )
    )
    (tmp_path / "recipes/a.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/a.py"\n'
        f'outputs = ["{SOURCE}", "art/models/hero.glb"]\n'
    )
    digest = parts._sha256(tmp_path / SOURCE)
    cached = parts.cache_path(tmp_path, digest)
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps({"sha256": digest, "inspection": _inspection()}))
    report = parts.cached_report(tmp_path, "art/models/hero.glb")
    assert report is not None
    assert report.find({"object": "Hero", "shape_key": "l-hand-scale-incr"}).in_game
    assert not report.find({"object": "Hero", "shape_key": "smile"}).in_game


def test_body_shape_is_checked_before_blender_starts(tmp_path: Path) -> None:
    step = character.plan_set_body_shape(
        tmp_path,
        SOURCE,
        "Hero",
        {"muscle": 0.8},
        {"l-upperarm-shoulder-muscle-incr": 1, "r-upperarm-shoulder-muscle-incr": 1},
    )
    assert "bl_ext.system.mpfb" in step.code and "HumanService.refit(body)" in step.code
    assert "_joinery_scope_check" in step.code and step.shape_keys is None
    assert step.note.startswith("Body shape of Hero: muscle 0.8")
    compile(blender.MODULE.script(SOURCE, "", step.code, []), "recipe.py", "exec")
    for macros, targets, message in [
        ({}, {}, "Give macros"),
        ({"strength": 1}, {}, "Unknown MPFB2 macro"),
        ({"muscle": 1.5}, {}, "from 0 to 1"),
        ({}, {"Shoulders": 1}, "not an MPFB2 target name"),
        ({}, {"x": True}, "from 0 to 1"),
    ]:
        with pytest.raises(ValueError, match=message):
            character.plan_set_body_shape(tmp_path, SOURCE, "Hero", macros, targets)


def test_body_shape_stays_inside_the_selection(tmp_path: Path) -> None:
    report = _report()
    picked = report.find({"object": "Hero", "region": "shoulders"}).target()
    parts.write_targets(tmp_path, [picked])
    character.plan_set_body_shape(tmp_path, SOURCE, "Hero", {"muscle": 0.6})
    with pytest.raises(ValueError, match="not part of the selection"):
        character.plan_set_body_shape(tmp_path, SOURCE, "Villain", {"muscle": 0.6})


def test_bake_shape_keys_records_the_game_keys(tmp_path: Path) -> None:
    step = character.plan_bake_shape_keys(
        tmp_path, SOURCE, ["smile", "smile", "viseme_aa"], {"viseme_aa": 0}
    )
    assert step.shape_keys == ExportShapeKeys(keep=("smile", "viseme_aa"))
    assert step.note == "Game shape keys: keep smile, viseme_aa; values viseme_aa 0"
    assert (
        "_joinery_choose_shape_keys('', {'viseme_aa': 0.0}, ['smile', 'viseme_aa'], False)"
        in step.code
    )
    default = character.plan_bake_shape_keys(tmp_path, SOURCE, None, in_source=True)
    assert default.shape_keys == ExportShapeKeys()
    assert default.note.endswith("baked in the .blend too")
    assert blender_character.SOURCE_SHAPES_PREFIX in default.code
    none = character.plan_bake_shape_keys(tmp_path, SOURCE, [])
    assert none.note == "Game shape keys: keep no shape keys"
    with pytest.raises(ValueError, match="named"):
        character.plan_bake_shape_keys(tmp_path, SOURCE, [""])


def _glb(targets: int) -> bytes:
    document = {
        "accessors": [
            {"count": 3, "min": [0, 0, 0], "max": [1, 1, 0]},
            {"count": 3},
        ],
        "meshes": [
            {
                "name": "Hero",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "targets": [{"POSITION": 1}] * targets,
                    }
                ],
            }
        ],
        "nodes": [{"name": "Hero", "mesh": 0}],
        "scenes": [{"nodes": [0]}],
    }
    body = json.dumps(document).encode()
    body += b" " * (-len(body) % 4)
    import struct

    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
    )


def test_the_art_bible_budgets_morph_targets_on_the_exported_model() -> None:
    assert measure_glb(_glb(3)).morph_targets == 3
    rules = BibleRules.model_validate(
        {"models": {"max_morph_targets": {"art/characters": 2}}}
    )
    path = "art/characters/hero.glb"
    (problem,) = _glb_problems(path, _glb(3), rules)
    assert "3 morph targets" in problem and "bake_shape_keys" in problem
    assert _glb_problems(path, _glb(2), rules) == []
    assert _glb_problems("art/props/crate.glb", _glb(9), rules) == []
