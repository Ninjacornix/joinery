"""Parametric kits in the real offline worker, through joinery-blender-ops as an agent calls it.

In a disposable project with an art bible (triangle budgets, pivots, palette,
material ranges):

* every kit makes a model with its defaults and with its parameters at their
  lowest and at their highest; each GLB is inside the bible's budget for its
  folder, bottom-centre, scale 1, and its colours are palette colours;
* kit_batch makes ten different rocks in one Blender run;
* kit_edit makes that set again, mossier and smaller, keeping the names;
* scatter places twenty of them on a hilly ground beside a Path3D in a scene;
* Godot imports every model and loads the scene without an error, the bible's
  own submit checks pass, and `joinery rebuild` replays every recipe to the
  same fingerprints (GLB bytes included).

Prints the triangle counts and timings as JSON. Technical fixtures only.
Run: `uv run python tests/integration/kits.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery import kits
from joinery.authoring import edit_document, run_offline
from joinery.bible_rules import check_rules, load_rules, measure_glb
from joinery.recipe import HISTORY_DIR, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

PALETTE = [
    "#2b2d42",
    "#8d99ae",
    "#c9c3b8",
    "#7d7a75",
    "#6b8f3a",
    "#4a6b2f",
    "#7a5c3e",
    "#a67c52",
    "#5a4632",
    "#4f5a63",
    "#d1c19a",
    "#8c4a3c",
]
BIBLE = f"""# Art bible

<!-- joinery-bible: 2 -->

## Checked rules

```toml
schema = 2

[models]
applied_scale = true
pivot_tolerance_m = 0.02

[models.max_triangles]
"art/props" = 800
"art/environment" = 6000

[models.pivot]
"art/props" = "bottom-centre"
"art/environment" = "bottom-centre"

[materials]
roughness = [0.6, 1.0]
metallic = [0.0, 1.0]
metallic_requires_name = "(?i)metal"
albedo_luminance = [0.03, 0.92]

[palette]
colours = {json.dumps(PALETTE)}
```
"""
# A gentle hill 30 m across, a Blender grid with a sine bump: the scatter surface.
GROUND = """
import bpy, math
bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=24, size=30)
ground = bpy.context.object
ground.name = ground.data.name = 'Ground'
for v in ground.data.vertices:
    v.co.z = 1.2 * math.sin(v.co.x / 6) * math.cos(v.co.y / 7)
"""
# A level: the ground, and a path that runs across it.
LEVEL = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://art/terrain/ground.glb" id="1_ground"]

[sub_resource type="Curve3D" id="Curve3D_path"]
_data = {
"points": PackedVector3Array(0, 0, 0, 0, 0, 0, -14, 0, -2, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 14, 0, 3),
"tilts": PackedFloat32Array(0, 0, 0)
}
point_count = 3

[node name="Level" type="Node3D"]

[node name="Ground" parent="." instance=ExtResource("1_ground")]

[node name="Path" type="Path3D" parent="."]
curve = SubResource("Curve3D_path")
"""
CHECK_SCENE = """extends SceneTree
func _init() -> void:
	var scene: PackedScene = load("res://scenes/level.tscn")
	var level := scene.instantiate()
	var rocks := level.get_node("Rocks")
	var meshes := 0
	for child in rocks.get_children():
		meshes += child.find_children("*", "MeshInstance3D", true, false).size()
	print("SCATTER_LOADED ", rocks.get_child_count(), " ", meshes)
	level.free()
	quit()
"""


async def op(stage: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(stage, "joinery-blender-ops")) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    assert not result.is_error, f"{tool}: {text}"
    found: dict[str, Any] = json.loads(text)
    return found


def edges(kit: kits.Kit) -> dict[str, dict[str, Any]]:
    """Every parameter at its lowest, and at its highest (where the kit allows)."""
    low: dict[str, Any] = {}
    high: dict[str, Any] = {}
    for param in kit.params:
        if param.kind == "bool":
            low[param.name], high[param.name] = False, True
        elif param.kind == "choice":
            low[param.name], high[param.name] = param.choices[0], param.choices[-1]
        else:
            low[param.name], high[param.name] = param.minimum, param.maximum
    for values in (low, high):
        # A limit (more broken posts than posts, more windows than walls) puts
        # that parameter back to its default.
        for _ in range(4):
            try:
                kits.check_params(kit, values)
                break
            except ValueError as exc:
                name = str(exc).split(":")[0]
                values[name] = kit.param(name).default
    return {"default": {}, "low": low, "high": high}


def check_model(stage: Path, glb: str, budget: int | None) -> dict[str, Any]:
    measured = measure_glb((stage / glb).read_bytes())
    assert budget is None or measured.triangles <= budget, (glb, measured.triangles)
    low, high = measured.bbox_min, measured.bbox_max
    assert low is not None and high is not None, glb
    assert abs(low[1]) < 0.02, (glb, "not on the ground", low)
    assert abs((low[0] + high[0]) / 2) < 0.02 and abs((low[2] + high[2]) / 2) < 0.02, (
        glb,
        "not centred",
        low,
        high,
    )
    for node in measured.nodes:
        assert all(abs(s - 1) < 1e-3 for s in node.scale), (glb, node)
    gltf = _gltf(stage / glb)
    palette = [_linear(c) for c in PALETTE]
    for material in gltf.get("materials", []):
        factor = material["pbrMetallicRoughness"].get("baseColorFactor", [1, 1, 1, 1])
        assert any(
            all(abs(a - b) < 2e-3 for a, b in zip(factor[:3], p, strict=True))
            for p in palette
        ), (glb, material["name"], factor)
    return {
        "triangles": measured.triangles,
        "size_m": [round(h - lo, 2) for lo, h in zip(low, high, strict=True)],
    }


def _linear(colour: str) -> list[float]:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return [channel(int(colour[i : i + 2], 16)) for i in (1, 3, 5)]


def _gltf(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    length = int.from_bytes(data[12:16], "little")
    found: dict[str, Any] = json.loads(data[20 : 20 + length])
    return found


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def every_kit(stage: Path, facts: dict[str, Any]) -> None:
    rules = load_rules(stage)
    for kit in kits.catalogue().values():
        for label, params in edges(kit).items():
            started = time.monotonic()
            arguments = {
                "kit": kit.id,
                "params": params,
                "seed": 5,
                "name": f"{kit.id}_{label}",
            }
            if label == "default":
                arguments["lods"] = 1
            made = await op(stage, "kit_make", arguments)
            seconds = time.monotonic() - started
            model = made["models"][0]
            for lod, triangles in zip(
                model["lods"], model["lod_triangles"], strict=True
            ):
                assert triangles < model["triangles"], (lod, triangles)
                check_model(stage, lod, None)
            budget = rules.models.max_triangles.get(kit.folder)
            measured = check_model(stage, model["glb"], budget)
            assert measured["triangles"] == model["triangles"], (model, measured)
            facts.setdefault(kit.id, {})[label] = {
                **measured,
                "budget": budget,
                "seconds": round(seconds, 1),
                **(
                    {"reduced_from": model["reduced_from"]}
                    if "reduced_from" in model
                    else {}
                ),
            }
            print(kit.id, label, facts[kit.id][label], flush=True)


async def batch_and_edit(stage: Path, facts: dict[str, Any]) -> list[str]:
    started = time.monotonic()
    made = await op(
        stage,
        "kit_batch",
        {
            "kit": "rock",
            "count": 10,
            "params": {"moss": 0.4},
            "seed": 11,
            "name": "rocks",
        },
    )
    seconds = time.monotonic() - started
    glbs = [m["glb"] for m in made["models"]]
    assert glbs == [f"art/environment/rocks_{i:02d}.glb" for i in range(1, 11)], glbs
    assert len({_sha(stage / g) for g in glbs}) == 10, (
        "the ten rocks are not all different"
    )
    sizes = [m["params"]["size"] for m in made["models"]]
    assert len(set(sizes)) > 5, sizes
    for glb in glbs:
        check_model(stage, glb, 6000)
    facts["batch"] = {
        "count": 10,
        "seconds": round(seconds, 1),
        "triangles": [m["triangles"] for m in made["models"]],
        "sizes": sizes,
    }
    before = {g: _sha(stage / g) for g in glbs}
    history = set((stage / HISTORY_DIR).glob("*.toml"))
    started = time.monotonic()
    edited = await op(
        stage,
        "kit_edit",
        {
            "asset": "res://art/environment/rocks_03.glb",
            "params": {"moss": 0.8, "size": 0.6},
        },
    )
    seconds = time.monotonic() - started
    assert [m["glb"] for m in edited["models"]] == glbs, "kit_edit renamed the models"
    assert all(_sha(stage / g) != before[g] for g in glbs), (
        "kit_edit left a model unchanged"
    )
    assert len(set((stage / HISTORY_DIR).glob("*.toml")) - history) == 1, (
        "old recipe kept"
    )
    _recipe, marker = kits.find(stage, glbs[2])
    assert marker["params"]["moss"] == 0.8 and marker["params"]["size"] == 0.6, marker
    unchanged = await op(stage, "kit_edit", {"asset": glbs[0], "params": {"moss": 0.8}})
    assert unchanged["status"] == "unchanged", unchanged
    facts["edit"] = {
        "seconds": round(seconds, 1),
        "sizes": [m["params"]["size"] for m in edited["models"]],
    }
    return glbs


async def scatter(stage: Path, glbs: list[str], facts: dict[str, Any]) -> None:
    await edit_document(
        stage,
        "blender",
        "art/sources/ground.blend",
        GROUND,
        ["art/terrain/ground.glb"],
        "Hilly ground for the scatter check",
    )
    level = stage / "scenes/level.tscn"
    level.parent.mkdir(parents=True, exist_ok=True)
    level.write_text(LEVEL)
    arguments = {
        "scene": "res://scenes/level.tscn",
        "assets": glbs,
        "count": 20,
        "seed": 4,
        "surface": "Ground",
        "paths": ["Path"],
        "path_clearance": 1.5,
        "path_band": 4.0,
        "spacing": 1.2,
        "scale": [0.7, 1.3],
        "align": "normal",
        "name": "Rocks",
    }
    started = time.monotonic()
    placed = await op(stage, "scatter", arguments)
    seconds = time.monotonic() - started
    assert placed["placed"] == 20, placed
    first = level.read_text()
    again = await op(stage, "scatter", arguments)
    assert again["placed"] == 20 and level.read_text() == first, (
        "scatter is not repeatable"
    )
    instances = re.findall(
        r'parent="Rocks" instance=ExtResource\("[^"]+"\)\]\ntransform = Transform3D\(([^)]*)\)',
        first,
    )
    assert len(instances) == 20, len(instances)
    path = [(-14.0, -2.0), (0.0, 1.0), (14.0, 3.0)]
    for row in instances:
        x, y, z = (float(v) for v in row.split(",")[9:12])
        gap = min(_segment_distance((x, z), a, b) for a, b in itertools.pairwise(path))
        assert 1.5 - 1e-6 <= gap <= 5.5 + 1e-6, ("not beside the path", x, z, gap)
        expected = 1.2 * math.sin(x / 6) * math.cos(-z / 7)
        assert abs(y - expected) < 0.15, ("not on the ground", x, y, z, expected)
    facts["scatter"] = {"placed": 20, "seconds": round(seconds, 2)}


def _segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    t = max(
        0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / (dx * dx + dz * dz))
    )
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


async def godot(stage: Path, facts: dict[str, Any]) -> None:
    started = time.monotonic()
    got = await run_offline(
        ["godot", "--headless", "--path", "/work", "--editor", "--import"], stage, 600
    )
    assert got.ok and "ERROR" not in got.output, got.output[-3000:]
    (stage / "check_scatter.gd").write_text(CHECK_SCENE)
    loaded = await run_offline(
        [
            "godot",
            "--headless",
            "--path",
            "/work",
            "--script",
            "res://check_scatter.gd",
        ],
        stage,
        300,
    )
    (stage / "check_scatter.gd").unlink()
    line = next(
        (r for r in loaded.output.splitlines() if r.startswith("SCATTER_LOADED")), ""
    )
    assert line == "SCATTER_LOADED 20 20", loaded.output[-3000:]
    assert "ERROR" not in loaded.output, loaded.output[-3000:]
    facts["godot_import_seconds"] = round(time.monotonic() - started, 1)


async def main() -> dict[str, Any]:
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-kits-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
        (project / "joinery.lock").write_text("version=1\n")
        (project / "art").mkdir()
        (project / "art/bible.md").write_text(BIBLE)
        tx = FileTransaction(project)
        tx.begin("Kits fixture")
        stage = tx.stage
        listed = await op(stage, "kit_list", {})
        assert [k["id"] for k in listed["kits"]] == list(kits.ORDER), listed
        facts["kits"] = {}
        await every_kit(stage, facts["kits"])
        glbs = await batch_and_edit(stage, facts)
        await scatter(stage, glbs, facts)
        await godot(stage, facts)
        problems = await check_rules(
            stage,
            [
                p.relative_to(stage).as_posix()
                for p in (stage / "art").rglob("*")
                if p.is_file()
            ],
            lambda argv, root: run_offline(argv, root, 600, readonly=True),
        )
        assert not problems, problems
        started = time.monotonic()
        replays = [
            await asyncio.to_thread(rebuild_one, path, stage, SandboxRunner())
            for path in recipe_paths(stage)
        ]
        failed = [(r.recipe, r.problems) for r in replays if not r.ok]
        assert not failed, failed
        facts["rebuild"] = {
            "recipes": len(replays),
            "seconds": round(time.monotonic() - started, 1),
            "batch_recipe_seconds": max(round(r.seconds, 1) for r in replays),
        }
        tx.reject()
    return facts


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), indent=1))
    print("KITS_OK")
