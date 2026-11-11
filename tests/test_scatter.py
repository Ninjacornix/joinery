"""Scatter: seeded placement into a Godot scene file, on a surface and clear of paths."""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path
from typing import Any

import pytest

from joinery import scatter as scattering
from joinery.gateway import OP_APPS, op_apps
from joinery.read_access import read_only

LEVEL = """[gd_scene load_steps=3 format=3 uid="uid://level"]

[ext_resource type="PackedScene" path="res://art/terrain/slope.glb" id="1_slope"]

[sub_resource type="Curve3D" id="Curve3D_road"]
_data = {
"points": PackedVector3Array(0, 0, 0, 0, 0, 0, -10, 0, 0, 0, 0, 0, 0, 0, 0, 10, 0, 0),
"tilts": PackedFloat32Array(0, 0)
}
point_count = 2

[node name="Level" type="Node3D"]

[node name="World" type="Node3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0)

[node name="Slope" parent="World" instance=ExtResource("1_slope")]

[node name="Road" type="Path3D" parent="World"]
curve = SubResource("Curve3D_road")
"""


def _glb(triangles: list[tuple[float, ...]]) -> bytes:
    """A binary glTF with these triangles (9 floats each), no indices."""
    points = b"".join(struct.pack("<9f", *t) for t in triangles)
    flat = [v for t in triangles for v in t]
    low = [min(flat[i::3]) for i in range(3)]
    high = [max(flat[i::3]) for i in range(3)]
    document = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "Ground", "mesh": 0}],
        "meshes": [{"name": "Ground", "primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(triangles) * 3,
                "type": "VEC3",
                "min": low,
                "max": high,
            }
        ],
        "bufferViews": [{"buffer": 0, "byteLength": len(points)}],
        "buffers": [{"byteLength": len(points)}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    body += struct.pack("<II", len(points), 0x004E4942) + points
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _slope() -> bytes:
    """A 40 m square rising 0.1 m per metre towards +x (y = 0.1 x + 2)."""
    a, b, c, d = (-20, 0, -20), (20, 4, -20), (20, 4, 20), (-20, 0, 20)
    shift = [(x, y + 2, z) for x, y, z in (a, b, c, d)]
    a, b, c, d = shift
    # Counter-clockwise seen from above (+Y normals).
    return _glb([(*a, *d, *c), (*a, *c, *b)])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art/terrain").mkdir(parents=True)
    (tmp_path / "art/terrain/slope.glb").write_bytes(_slope())
    rocks = tmp_path / "art/environment"
    rocks.mkdir(parents=True)
    for i in (1, 2, 3):
        (rocks / f"rocks_0{i}.glb").write_bytes(_slope())
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes/level.tscn").write_text(LEVEL)
    return tmp_path


ROCKS = [f"res://art/environment/rocks_0{i}.glb" for i in (1, 2, 3)]


def _instances(text: str) -> list[tuple[str, list[float]]]:
    return [
        (ref, [float(v) for v in values.split(",")])
        for ref, values in re.findall(
            r'parent="World/Rocks" instance=ExtResource\("([^"]+)"\)\]\n'
            r"transform = Transform3D\(([^)]*)\)",
            text,
        )
    ]


def test_scatter_on_a_surface_beside_a_path(project: Path) -> None:
    result = scattering.scatter(
        project,
        "res://scenes/level.tscn",
        ROCKS,
        30,
        seed=5,
        surface="Slope",
        paths=["Road"],
        path_clearance=2.0,
        path_band=6.0,
        spacing=1.5,
        parent="World",
        name="Rocks",
    )
    assert result["placed"] == 30 and result["group"] == "World/Rocks"
    assert sum(result["per_model"].values()) == 30
    text = (project / "scenes/level.tscn").read_text()
    placed = _instances(text)
    assert len(placed) == 30
    points = []
    for _ref, values in placed:
        x, y, z = values[9:12]
        # The slope (y = 0.1 x + 4 in its file) sits under World, which is 2 m up;
        # the group is under World too, so in its space the slope is where it was.
        assert abs(y - (0.1 * x + 4.0)) < 1e-3, (x, y)
        # The road runs along x from -10 to 10, at z 0.
        gap = math.hypot(max(abs(x) - 10, 0.0), z)
        assert 2.0 - 1e-6 <= gap <= 8.0 + 1e-6, (x, z)
        scale = math.sqrt(sum(v * v for v in values[0:3]))
        assert 0.8 - 1e-6 <= scale <= 1.2 + 1e-6
        points.append((x, z))
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            assert math.hypot(a[0] - b[0], a[1] - b[1]) >= 1.5 - 1e-9
    # New resources are added once per model, after the existing ones.
    assert text.count('type="PackedScene"') == 4
    assert text.index('id="scatter_1"') < text.index("[sub_resource")
    assert "load_steps=6" in text  # four models, one curve, the scene


def test_scatter_is_repeatable_and_replaces_its_group(project: Path) -> None:
    arguments: dict[str, Any] = {"area": [-5, -5, 5, 5], "spacing": 1.0, "seed": 2}
    scattering.scatter(project, "scenes/level.tscn", ROCKS, 12, **arguments)
    first = (project / "scenes/level.tscn").read_text()
    scattering.scatter(project, "scenes/level.tscn", ROCKS, 12, **arguments)
    assert (project / "scenes/level.tscn").read_text() == first
    scattering.scatter(project, "scenes/level.tscn", ROCKS[:1], 5, **arguments)
    again = (project / "scenes/level.tscn").read_text()
    assert again.count('parent="Scatter"') == 5
    assert again.count('id="scatter_') == 1, "unused scatter resources stay behind"
    assert '[node name="Slope" parent="World"' in again
    assert again.count('[node name="Scatter"') == 1


def test_scatter_aligns_to_the_surface_normal(project: Path) -> None:
    scattering.scatter(
        project,
        "scenes/level.tscn",
        ROCKS[:1],
        3,
        surface="res://art/terrain/slope.glb",
        align="normal",
        scale=[1, 1],
        spacing=3,
    )
    text = (project / "scenes/level.tscn").read_text()
    rows = re.findall(r"transform = Transform3D\(([^)]*)\)", text)[1:]
    slope = 1 / math.sqrt(1.01)
    for row in rows:
        values = [float(v) for v in row.split(",")]
        up = values[3:6]
        assert abs(up[0] + 0.1 * slope) < 1e-4 and abs(up[1] - slope) < 1e-4, up


def test_scatter_says_when_fewer_fit(project: Path) -> None:
    result = scattering.scatter(
        project, "scenes/level.tscn", ROCKS, 50, area=[0, 0, 2, 2], spacing=1.5
    )
    assert result["placed"] < 50 and "Only" in result["note"]


def test_scatter_into_a_new_scene(project: Path) -> None:
    result = scattering.scatter(
        project,
        "scenes/props/rock_field.tscn",
        ROCKS,
        4,
        area=[0, 0, 10, 10],
        height=1.5,
    )
    text = (project / "scenes/props/rock_field.tscn").read_text()
    assert result["placed"] == 4
    assert text.startswith("[gd_scene format=3]")
    assert '[node name="RockField" type="Node3D"]' in text
    assert all(
        abs(v[10] - 1.5) < 1e-9
        for _r, v in [
            ("", [float(x) for x in row.split(",")])
            for row in re.findall(r"Transform3D\(([^)]*)\)", text)
        ]
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"scene": "scenes/level.scn"}, "into a .tscn"),
        ({"assets": []}, "1 to 32 models"),
        ({"assets": ["art/terrain/missing.glb"]}, "not a file"),
        ({"assets": ["project.godot"]}, "not a model"),
        ({"count": 0}, "1 to 2000"),
        ({"name": "my rocks"}, "group name"),
        ({"spacing": 0}, "spacing"),
        ({"scale": [2, 1]}, "scale is"),
        ({"align": "sideways"}, "align"),
        ({"area": None}, "Give an area"),
        ({"area": [5, 5, 0, 0]}, "max above min"),
        ({"paths": ["Slope"]}, "not a Path3D"),
        ({"paths": [[[0, 0], [1]]]}, "Each path"),
        ({"surface": "World"}, "not a model"),
        ({"surface": "Nowhere"}, "no node 'Nowhere'"),
        ({"parent": "Nowhere"}, "no node 'Nowhere'"),
        ({"path_band": 2.0}, "name the path"),
    ],
)
def test_scatter_refuses_bad_arguments(
    project: Path, arguments: dict[str, Any], message: str
) -> None:
    call: dict[str, Any] = {
        "scene": "scenes/level.tscn",
        "assets": ROCKS,
        "count": 5,
        "area": [0, 0, 10, 10],
    }
    call.update(arguments)
    scene, assets, count = call.pop("scene"), call.pop("assets"), call.pop("count")
    with pytest.raises(ValueError, match=message):
        scattering.scatter(project, scene, assets, count, **call)


def test_kit_operations_take_the_blender_slot() -> None:
    for tool in ("kit_make", "kit_batch", "kit_edit"):
        assert op_apps(tool, {}) == ("blender",)
    assert "scatter" not in OP_APPS  # a scene file edit: no app, no slot
    assert read_only("joinery-blender-ops", "kit_list")
    assert read_only("joinery-registry", "kit_list")
    assert not read_only("joinery-blender-ops", "kit_make")
