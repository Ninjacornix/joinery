"""The art bible's model checks on rigged characters (MPFB2 + Rigify).

A generated Rigify rig keeps hundreds of control shapes (face-less WGT- meshes
at odd scales) and ORG-/MCH- bones in the source; the game gets the body and the
DEF- bones. The checks judge what ships, and stay strict for real problems.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_bible_rules import GlbBuilder, blend_line, run_checks, v2_stage

from joinery.bible_blend import MEASURE_BLEND, BlendMeasure
from joinery.bible_rules import blend_problems, parse_rules
from joinery.tools.blender import MODULE

HERO = "art/characters/hero.blend"
PATTERN = "^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"

#: Every model rule on: scale, pivots, names, triangles and deform bones.
RIG_BLOCK = f"""schema = 2

[models]
applied_scale = true
pivot_tolerance_m = 0.02

[models.max_triangles]
"art/characters" = 3000

[models.max_bones]
"art/characters" = 4

[models.pivot]
"art/characters" = "bottom-centre"

[naming.nodes]
"art" = "{PATTERN}"
"""


def obj(name: str, kind: str = "MESH", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "type": kind,
        "parent": None,
        "scale": [1.0, 1.0, 1.0],
        "bbox_min": [-0.3, 0.0, -0.2],
        "bbox_max": [0.3, 1.8, 0.2],
        "triangles": 0,
        "mesh": name if kind == "MESH" else None,
        "texel_density": None,
    }
    row.update(extra)
    return row


def widget(name: str, **extra: Any) -> dict[str, Any]:
    # As Rigify leaves them: unparented, scaled to the bone, off the origin.
    return obj(
        name,
        scale=[0.1045, 0.1045, 0.1045],
        bbox_min=[0.1, 1.2, -0.05],
        bbox_max=[0.2, 1.3, 0.05],
        widget=True,
        **extra,
    )


def rigged(**body: Any) -> dict[str, Any]:
    """An MPFB2 human on a generated Rigify rig, as the measure script reports it."""
    return {
        "path": "/work/" + HERO,
        "objects": [
            obj("Hero", **{"parent": "Hero_Rig", "triangles": 2800, **body}),
            obj("Hero_Rig", "ARMATURE", bbox_min=[-0.5, 0, -0.5]),
            widget("WGT-Hero_Rig_hand_ik.L"),
            widget("WGT-Hero_Rig_root"),
            widget("WGT-Hero_Rig_torso"),
        ],
        # ORG-, MCH- and control bones do not deform: only DEF- bones count.
        "armatures": [{"name": "Hero_Rig", "deform_bones": 4}],
        "materials": [],
    }


def check(measure: dict[str, Any], tmp_path: Path) -> list[str]:
    root = v2_stage(tmp_path, {HERO: b"BLENDER"}, RIG_BLOCK)
    return run_checks(root, [HERO], blend_line(HERO, measure))


def test_a_rigify_character_passes_with_its_control_shapes(tmp_path: Path) -> None:
    assert check(rigged(), tmp_path) == []


def test_the_body_is_still_judged_on_a_rigged_character(tmp_path: Path) -> None:
    hero = rigged(scale=[2.0, 2.0, 2.0], triangles=4000)
    hero["objects"][0]["name"] = "hero.body"
    hero["armatures"][0]["deform_bones"] = 5
    assert check(hero, tmp_path) == [
        f"{HERO}: 4000 triangles, over the art bible's 3000 budget",
        f"{HERO}: armature 'Hero_Rig' has 5 deform bones, over the art bible's 4 budget",
        f"{HERO}: scale is not applied on 'hero.body'; the art bible asks for scale 1",
        (
            f"{HERO}: object names 'hero.body' do not follow the art bible's "
            f"naming pattern {PATTERN}"
        ),
    ]


def test_a_mesh_named_like_a_widget_is_checked_like_any_mesh(tmp_path: Path) -> None:
    # Only a bone's face-less custom shape is a rig internal; the name proves nothing.
    hero = rigged()
    hero["objects"].append(obj("WGT-Cape", scale=[0.5, 0.5, 0.5], triangles=10))
    problems = check(hero, tmp_path)
    assert (
        f"{HERO}: scale is not applied on 'WGT-Cape'; the art bible asks for scale 1"
        in problems
    )
    assert any("object names 'WGT-Cape'" in line for line in problems), problems


def test_mesh_data_names_are_still_checked(tmp_path: Path) -> None:
    # MPFB2 names its body mesh "base": the bible names meshes too.
    hero = rigged(mesh="base")
    assert check(hero, tmp_path) == [
        (
            f"{HERO}: mesh data names 'base' do not follow the art bible's "
            f"naming pattern {PATTERN}"
        )
    ]


def test_widget_flag_comes_from_the_measure_script() -> None:
    assert "def rig_widgets()" in MEASURE_BLEND
    assert "custom_shape" in MEASURE_BLEND and "'widget'" in MEASURE_BLEND
    row = BlendMeasure.model_validate(rigged())
    assert [o.name for o in row.objects if o.widget] == [
        "WGT-Hero_Rig_hand_ik.L",
        "WGT-Hero_Rig_root",
        "WGT-Hero_Rig_torso",
    ]
    rules = parse_rules(RIG_BLOCK)
    assert blend_problems(HERO, row, rules) == []


def deform_glb(joints: list[str], *extra_nodes: str) -> bytes:
    builder = GlbBuilder()
    builder.quad("Hero")
    start = len(builder.doc["nodes"])
    builder.doc["nodes"] += [{"name": name} for name in joints]
    builder.doc["skins"] = [{"joints": list(range(start, start + len(joints)))}]
    builder.doc["nodes"] += [{"name": name} for name in extra_nodes]
    return builder.build()


def test_the_glb_counts_and_names_only_what_ships(tmp_path: Path) -> None:
    glb = "art/characters/hero.glb"
    bones = ["DEF-spine", "DEF-spine.001", "DEF-thigh.L", "DEF-thigh.R"]
    root = v2_stage(tmp_path, {glb: deform_glb(bones, "Hero_Rig")}, RIG_BLOCK)
    assert run_checks(root, [glb]) == []
    # A widget that did reach the GLB ships as a stray node: still a problem.
    root = v2_stage(
        tmp_path,
        {glb: deform_glb([*bones, "DEF-neck"], "WGT-Hero_Rig_root")},
        RIG_BLOCK,
    )
    assert run_checks(root, [glb]) == [
        f"{glb}: 5 bones in a skin, over the art bible's 4 budget",
        (
            f"{glb}: node names 'WGT-Hero_Rig_root' do not follow the art bible's "
            f"naming pattern {PATTERN}"
        ),
    ]


def test_the_export_leaves_rig_control_shapes_out() -> None:
    script = MODULE.script(HERO, "", "pass", ["art/models/hero.glb"])
    drop = script.index("_joinery_drop_rig_widgets()\n")
    assert script.index("save_as_mainfile") < drop < script.index("export_scene.gltf")
    assert "not obj.data.polygons" in script
    assert json.dumps("WGT") not in script  # judged by use, not by name
