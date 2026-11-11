"""Complex models are made in Blender: placeholder piles go back to their author."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import complex_models
from joinery.complex_models import (
    changed_findings,
    check_live,
    check_scenes,
    live_shapes,
    scene_findings,
    set_setting,
    setting,
)
from joinery.roles import reviewer_settings, set_reviewer

HEAD = """[gd_scene load_steps=4 format=3]

[sub_resource type="BoxMesh" id="seat"]
size = Vector3(0.5, 0.06, 0.5)

[sub_resource type="BoxMesh" id="back"]
size = Vector3(0.5, 0.5, 0.06)

[sub_resource type="BoxMesh" id="leg"]
size = Vector3(0.05, 0.45, 0.05)

[sub_resource type="BoxMesh" id="floor"]
size = Vector3(10, 0.2, 10)

[sub_resource type="Environment" id="env"]

[node name="Main" type="Node3D"]

[node name="Environment" type="WorldEnvironment" parent="."]
environment = SubResource("env")

[node name="Camera3D" type="Camera3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 6)

[node name="Floor" type="MeshInstance3D" parent="."]
mesh = SubResource("floor")
"""


def leg(name: str, parent: str, x: float, z: float) -> str:
    return f"""
[node name="{name}" type="MeshInstance3D" parent="{parent}"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {x}, 0.225, {z})
mesh = SubResource("leg")
"""


def chair(parent: str = ".", name: str = "Chair", meta: str = "") -> str:
    inner = name if parent == "." else f"{parent}/{name}"
    return (
        f"""
[node name="{name}" type="Node3D" parent="{parent}"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 0, 1)
{meta}
[node name="Seat" type="MeshInstance3D" parent="{inner}"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.45, 0)
mesh = SubResource("seat")

[node name="Back" type="MeshInstance3D" parent="{inner}"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.7, -0.22)
mesh = SubResource("back")
"""
        + leg("Leg1", inner, 0.2, 0.2)
        + leg("Leg2", inner, -0.2, 0.2)
        + leg("Leg3", inner, 0.2, -0.2)
        + leg("Leg4", inner, -0.2, -0.2)
    )


LAMP = """[gd_scene load_steps=4 format=3]

[sub_resource type="CylinderMesh" id="base"]
top_radius = 0.15
bottom_radius = 0.15
height = 0.05

[sub_resource type="CylinderMesh" id="pole"]
top_radius = 0.02
bottom_radius = 0.02
height = 1.2

[sub_resource type="SphereMesh" id="shade"]
radius = 0.2
height = 0.4

[node name="Lamp" type="Node3D"]

[node name="Base" type="MeshInstance3D" parent="."]
mesh = SubResource("base")

[node name="Pole" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.6, 0)
mesh = SubResource("pole")

[node name="Shade" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1.3, 0)
mesh = SubResource("shade")
"""

BLOCKOUT = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://addons/joinery/blockout_level.gd" id="1_level"]

[node name="Blockout" type="Node3D"]
script = ExtResource("1_level")
metadata/blockout_layout = "res://design/blockouts/level.toml"
metadata/blockout_sha256 = "abc"

[node name="Rooms" type="Node3D" parent="."]

[node name="A" type="Node3D" parent="Rooms"]

[node name="Pillar" type="CSGBox3D" parent="Rooms/A"]
size = Vector3(0.4, 2, 0.4)

[node name="Pillar2" type="CSGBox3D" parent="Rooms/A"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0.3, 0, 0)
size = Vector3(0.4, 2, 0.4)
"""


def test_a_chair_of_boxes_is_flagged() -> None:
    found = scene_findings(HEAD + chair(), "scenes/main.tscn")
    assert len(found) == 1
    assert found[0].name == "Chair" and len(found[0].shapes) == 6
    assert {s.path for s in found[0].shapes} >= {"Chair/Seat", "Chair/Leg4"}


def test_a_single_floor_box_and_level_pieces_are_not() -> None:
    walls = """
[node name="WallNorth" type="CSGBox3D" parent="."]
size = Vector3(10, 3, 0.2)

[node name="Crate" type="CSGBox3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, -3, 0.5, 0)

[node name="Ramp" type="CSGBox3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, -3, 0.5, 1)
size = Vector3(1, 0.2, 2)

[node name="Body" type="StaticBody3D" parent="."]

[node name="Shape" type="CollisionShape3D" parent="Body"]
"""
    assert scene_findings(HEAD + walls) == []


def test_two_stacked_crates_in_a_level_are_two_things() -> None:
    crates = """
[node name="CrateA" type="CSGBox3D" parent="."]

[node name="CrateB" type="CSGBox3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0)
"""
    assert scene_findings(HEAD + crates) == []


def test_loose_parts_in_the_level_are_one_object() -> None:
    loose = "".join(
        f"""
[node name="Chair{part}" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {x}, {y}, 0)
mesh = SubResource("leg")
"""
        for part, x, y in (("Seat", 0, 0.45), ("Leg1", 0.02, 0.2), ("Leg2", -0.02, 0.2))
    )
    found = scene_findings(HEAD + loose)
    assert [f.name for f in found] == ["Chair"]


def test_a_blockout_scene_is_not_flagged() -> None:
    assert scene_findings(BLOCKOUT) == []


def test_an_explicit_placeholder_is_not_flagged() -> None:
    marked = chair(meta="metadata/joinery_placeholder = true\n")
    assert scene_findings(HEAD + marked) == []
    hidden = chair(meta="visible = false\n")
    assert scene_findings(HEAD + hidden) == []


def test_a_prop_scene_of_primitives_is_flagged() -> None:
    found = scene_findings(LAMP, "scenes/props/lamp.tscn")
    assert [(f.name, len(f.shapes)) for f in found] == [("Lamp", 3)]


def test_a_sound_added_beside_old_shapes_does_not_make_them_new() -> None:
    # The sound pass adds a SoundCue to a greybox prop: the prop's shapes are as they
    # were, though the scene is no longer only shapes and so names them differently.
    cue = '\n[node name="UseSound" type="Node" parent="."]\ntrigger = &"switched"\n'
    assert changed_findings(LAMP, LAMP + cue, "scenes/props/lamp.tscn") == []


def test_csg_parts_count_and_combiners_group_them() -> None:
    table = """[gd_scene format=3]

[node name="Table" type="CSGCombiner3D"]

[node name="Top" type="CSGBox3D" parent="."]
size = Vector3(1.2, 0.05, 0.8)

[node name="Leg" type="CSGCylinder3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0.5, -0.4, 0.3)
radius = 0.03
height = 0.8
"""
    found = scene_findings(table)
    assert [(f.name, len(f.shapes)) for f in found] == [("Table", 2)]


def test_only_objects_the_change_adds_or_modifies_are_judged() -> None:
    before = HEAD + chair()
    # Moving the whole chair, or adding a floor beside it, leaves the old pile alone.
    moved = before.replace("0, 0, 1, 2, 0, 1)", "0, 0, 1, 4, 0, 1)")
    assert changed_findings(before, moved) == []
    assert changed_findings(before, before + chair(name="Stool")) != []
    # A modified part is the change's.
    widened = before.replace(
        "size = Vector3(0.5, 0.06, 0.5)", "size = Vector3(0.6, 0.06, 0.5)"
    )
    assert [f.name for f in changed_findings(before, widened)] == ["Chair"]


def test_the_problem_names_the_object_count_and_node_paths(tmp_path: Path) -> None:
    stage, original = tmp_path / "stage", tmp_path / "project"
    (stage / "scenes").mkdir(parents=True)
    original.mkdir()
    (stage / "scenes/main.tscn").write_text(HEAD + chair())
    problem = check_scenes(
        original,
        stage,
        original,
        [{"path": "scenes/main.tscn", "before": "", "after": "x"}],
    )
    assert problem is not None and problem.fixable
    assert problem.check == "complex_models"
    assert problem.paths == ["scenes/main.tscn"]
    assert problem.message.startswith(
        "Chair is built from 6 placeholder shapes. Make it in Blender (or with a kit) "
        "and place it as one model."
    )
    assert "scenes/main.tscn: Chair/Back, Chair/Leg1" in problem.message


def test_the_setting_off_skips_the_check(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "scenes").mkdir(parents=True)
    (stage / "scenes/main.tscn").write_text(HEAD + chair())
    changes = [{"path": "scenes/main.tscn", "before": "", "after": "x"}]
    assert check_scenes(tmp_path, stage, tmp_path, changes) is not None
    set_setting(tmp_path, "placeholders")
    assert setting(tmp_path) == "placeholders"
    assert check_scenes(tmp_path, stage, tmp_path, changes) is None


def test_the_setting_lives_in_the_agents_table_beside_the_reviewer(
    tmp_path: Path,
) -> None:
    assert setting(tmp_path) == "blender"
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    set_reviewer(tmp_path, enabled=False, model=None)
    set_setting(tmp_path, "placeholders")
    set_setting(tmp_path, "blender")
    text = (tmp_path / "joinery.toml").read_text()
    assert text.count("[agents]") == 1 and 'complex_models = "blender"' in text
    assert reviewer_settings(tmp_path).enabled is False
    with pytest.raises(ValueError):
        set_setting(tmp_path, "boxes")
    (tmp_path / "joinery.toml").write_text('[agents]\ncomplex_models = "cubes"\n')
    assert setting(tmp_path) == "blender"


def test_the_command_reads_and_saves_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(project=tmp_path, set="placeholders", json=True)
    assert complex_models.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "complex_models": "placeholders",
        "blender": False,
    }


def test_the_editor_context_carries_the_setting(tmp_path: Path) -> None:
    context = complex_models.with_setting(tmp_path, {"scene_path": "res://a.tscn"})
    assert context == {"scene_path": "res://a.tscn", "complex_models": "blender"}


def box(name: str, x: float, y: float, parent: str = "") -> dict[str, Any]:
    return {
        "operation": "create_node",
        "node_type": "MeshInstance3D",
        "name": name,
        "parent": parent,
        "properties": {
            "position": [x, y, 0],
            "mesh": {
                "resource_type": "BoxMesh",
                "properties": {"size": [0.05, 0.45, 0.05]},
            },
        },
    }


def test_live_edits_that_pile_shapes_are_flagged(tmp_path: Path) -> None:
    payload = {
        "summary": "Chair",
        "operations": [
            {
                "operation": "create_box",
                "name": "ChairSeat",
                "size": [0.5, 0.06, 0.5],
                "position": [0, 0.45, 0],
            },
            box("ChairLeg1", 0.2, 0.2),
            box("ChairLeg2", -0.2, 0.2),
            {"operation": "create_box", "name": "Floor", "size": [10, 0.2, 10]},
        ],
    }
    shapes = live_shapes(payload)
    assert [s.name for _p, s in shapes] == ["ChairSeat", "ChairLeg1", "ChairLeg2"]
    problem = check_live(tmp_path, shapes)
    assert problem is not None
    assert problem.message.startswith("Chair is built from 3 placeholder shapes.")
    set_setting(tmp_path, "placeholders")
    assert check_live(tmp_path, shapes) is None


def test_one_live_box_or_a_marked_placeholder_is_not(tmp_path: Path) -> None:
    marked = box("Leg", 0, 0, "1234")
    marked["properties"]["metadata/joinery_placeholder"] = True
    shapes = live_shapes(
        {"operations": [box("Crate", 0, 0), marked, box("Leg2", 0, 0.1, "1234")]}
    )
    assert check_live(tmp_path, shapes) is None


def test_validate_judges_staged_scenes_for_every_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check is in the shared submit: the same stage fails the same way."""
    from joinery import submission
    from joinery.transactions import FileTransaction

    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr(submission, "check_deps", lambda *_: [])

    async def none(*_a: Any, **_k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(submission, "checks", none)
    gateway = type(
        "G",
        (),
        {
            "project": tmp_path,
            "editor_context": {},
            "import_current": lambda self, tx: True,
            "span": None,
        },
    )()
    tx = FileTransaction(tmp_path)
    tx.begin("Chair")
    (tx.stage / "scenes").mkdir(parents=True, exist_ok=True)
    (tx.stage / "scenes/main.tscn").write_text(HEAD + chair())
    found = asyncio.run(submission.validate(gateway, tx, set()))  # type: ignore[arg-type]
    assert [p.check for p in found.problems] == ["complex_models"]


def test_every_agent_is_told_to_make_complex_models_in_blender() -> None:
    from bridge.instructions import GAME_GUIDE, OBJECT_GUIDE, REVIEW_GUIDE

    assert OBJECT_GUIDE in GAME_GUIDE
    assert "editor_context.complex_models" in OBJECT_GUIDE
    assert "kit" in OBJECT_GUIDE and "Blender" in OBJECT_GUIDE
    assert "metadata/joinery_placeholder = true" in OBJECT_GUIDE
    assert "placeholder shapes" in REVIEW_GUIDE
