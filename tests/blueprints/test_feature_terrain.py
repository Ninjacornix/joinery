"""The terrain feature: seeded heightmaps, chunked meshes with LODs, collision, scatter.

The Godot check runs `res://tests/features/terrain/terrain_test.gd` in stock Godot
on both 3D blueprints: the operations are seeded and do what they say, the PNG
codec keeps 16 bits, and a Terrain built beside the level collides where it is
drawn, carries the player as they land and walk, draws coarser chunks further
away and scatters only where its mask and slope allow. A real playtest then walks
the bot over a terrain that replaces the starting island.
"""

from __future__ import annotations

import json
import math
import re
import struct
import zlib
from pathlib import Path

import pytest

from joinery import blueprints, playtest
from tests.blueprints.godot_run import (
    import_project,
    make_project,
    needs_godot,
    run_godot,
)

FEATURE = "terrain"
TEST = "res://tests/features/terrain/terrain_test.gd"
BLUEPRINTS = ["third-person-3d", "first-person-3d"]


def test_the_feature_is_code_only_and_offered_to_3d_blueprints() -> None:
    feature = blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)
    overlay = blueprints.FEATURES_DIR / FEATURE / "overlay"
    suffixes = {p.suffix for p in overlay.rglob("*") if p.is_file()}
    assert suffixes <= {".gd", ".uid", ".md", ".gdshader"}
    assert (overlay / "features/terrain/GUIDE.md").is_file()
    for blueprint in blueprints.list_blueprints():
        problems = blueprints.resolve(blueprint.id, [FEATURE]).problems
        if blueprint.id in {"third-person-3d", "first-person-3d", "vr-room"}:
            assert problems == [], blueprint.id
        else:
            assert problems and "3D blueprint" in " ".join(problems), blueprint.id
    # Alternatives are named, never shipped or required.
    assert {e.name for e in feature.external} == {
        "Terrain3D",
        "ProtonScatter",
        "Spatial Gardener",
    }
    assert not any(e.required for e in feature.external)


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "first-person-3d")
    before = (project / "project.godot").read_text(encoding="utf-8")
    blueprints.add_feature(project, FEATURE)
    assert (project / "features/terrain/terrain.gd").is_file()
    blueprints.remove_feature(project, FEATURE)
    assert not (project / "features/terrain").exists()
    assert not (project / "tests/features/terrain").exists()
    assert (project / "project.godot").read_text(encoding="utf-8") == before


@needs_godot
@pytest.mark.parametrize("blueprint", BLUEPRINTS)
def test_terrain_builds_collides_and_carries_the_player(
    tmp_path: Path, blueprint: str
) -> None:
    features = sorted({*blueprints.describe(blueprint)["default_features"], FEATURE})
    project = make_project(tmp_path, blueprint, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    run_godot(project, TEST, ("--autostart", settings), timeout=300, marker=FEATURE)


# --- The playtest bot on a terrain ----------------------------------------------------


def _png16(path: Path, size: int, heights: list[float]) -> None:
    """A 16-bit grey PNG of 0-1 values, row by row."""
    rows = b"".join(
        b"\x00"
        + b"".join(
            struct.pack(">H", round(min(max(v, 0.0), 1.0) * 65535))
            for v in heights[y * size : (y + 1) * size]
        )
        for y in range(size)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 16, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _rolling_ground(size: int, extent: float, low: float, high: float) -> list[float]:
    """Flat at 0 m over the arena's footprint, rolling hills (up to 3 m) around it."""
    values = []
    for y in range(size):
        for x in range(size):
            wx = -extent / 2 + extent * x / (size - 1)
            wz = -extent / 2 + extent * y / (size - 1) - 12.0
            # The blueprint's start, walkway, arena and pen stay level.
            inside = max(0.0, max(abs(wx) - 13.0, abs(wz + 13.0) - 25.0))
            rise = min(inside / 8.0, 1.0) * (
                1.5 + 1.5 * math.sin(wx * 0.3) * math.cos(wz * 0.25)
            )
            values.append((rise - low) / (high - low))
    return values


def _put_terrain_in_level(project: Path) -> None:
    """Swap the starting island for a terrain node over the level's footprint."""
    _png16(
        project / "terrain_fixture/height.png", 65, _rolling_ground(65, 96.0, -1.0, 6.0)
    )
    (project / "terrain_fixture/height.png.import").write_text(
        '[remap]\n\nimporter="keep"\n', encoding="utf-8"
    )
    level = project / "game/level.tscn"
    text = level.read_text(encoding="utf-8")
    uid = (project / "features/terrain/terrain.gd.uid").read_text().strip()
    text = text.replace(
        "\n[sub_resource",
        f'\n[ext_resource type="Script" uid="{uid}" '
        'path="res://features/terrain/terrain.gd" id="99_terrain"]\n\n[sub_resource',
        1,
    )
    island = re.compile(
        r'\[node name="StartIsland" type="CSGBox3D" parent="Geometry"\].*?(?=\n\[node )',
        re.DOTALL,
    )
    assert island.search(text), "the blueprint's starting island moved"
    text = island.sub(
        '[node name="Terrain" type="Node3D" parent="Geometry"]\n'
        "transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -12)\n"
        'script = ExtResource("99_terrain")\n'
        'heightmap = "res://terrain_fixture/height.png"\n'
        "size = Vector2(96, 96)\n"
        "min_height = -1.0\n"
        "max_height = 6.0\n",
        text,
        count=1,
    )
    level.write_text(text, encoding="utf-8")


@needs_godot
def test_the_playtest_bot_walks_a_terrain(tmp_path: Path) -> None:
    features = sorted(
        {*blueprints.describe("third-person-3d")["default_features"], FEATURE}
    )
    project = make_project(tmp_path, "third-person-3d", features)
    _put_terrain_in_level(project)
    import_project(project)
    report = playtest.run(playtest.Options(project=project, seconds=25, seed=11))
    assert report["status"] == "played", report
    serious = [i for i in report["items"] if i["severity"] in {"critical", "high"}]
    assert serious == [], json.dumps(serious, indent=1)[:2000]
    assert report["goals"]["coverage_cells"] >= 5, report["goals"]
    states = report.get("states", {})
    terrain = next((s for s in states.values() if s.get("kind") == "terrain"), None)
    assert terrain is not None, states
    assert terrain["chunks"] == 4, terrain
    assert terrain["checks_on_surface"] > 20, terrain
    assert terrain["checks_below_surface"] == 0, terrain
