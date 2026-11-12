"""The user's own Geometry Nodes group as a kit, in the real offline worker.

A .blend outside the project holds "Spire", a plain Geometry Nodes group the
user made: a cone (Height, Sides), a random bottom radius from its Seed input,
studs instanced on three points behind a Studs switch, two materials, and an
Offset vector. Then:

* `joinery kits from-blend spire.blend --group Spire --yes` copies the .blend
  into the project as a human import, and writes a project kit whose typed
  parameters are Height (float, m), Sides (int) and Studs (bool); the Seed
  takes the kit's seed, the Offset stays fixed, and the two materials become
  roles; the kit shows in `joinery kits list` with its "As made" preset;
* a set of three spires from it are different (each its own seed), carry the
  kit's materials, include the realized studs, list the .blend as a recipe
  source, pass the provenance gate, and `joinery rebuild` replays them;
* kit_edit makes the set again taller; a missing group, a shader node group
  and a name already used are refused.

Technical fixtures only. Run: `uv run python tests/integration/own_kits.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import tempfile
from pathlib import Path

from joinery import kit_groups, kits, kits_cli
from joinery.authoring import run_offline
from joinery.bible_rules import measure_glb
from joinery.catalog import load_catalog
from joinery.lockfile import check_assets
from joinery.recipe import load_recipe, rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

MAKE_GROUP = """
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
stone = bpy.data.materials.new("Stone")
stone.use_nodes = True
shader = stone.node_tree.nodes.get("Principled BSDF")
shader.inputs["Base Color"].default_value = (0.3, 0.25, 0.2, 1.0)
shader.inputs["Roughness"].default_value = 0.8
moss = bpy.data.materials.new("Moss Green")
moss.diffuse_color = (0.1, 0.3, 0.05, 1.0)
group = bpy.data.node_groups.new("Spire", "GeometryNodeTree")
group.description = "A stone spire with studs."
face = group.interface
height = face.new_socket("Height", in_out="INPUT", socket_type="NodeSocketFloat")
height.default_value, height.min_value, height.max_value = 2.0, 0.5, 5.0
height.subtype = "DISTANCE"
height.description = "How tall the spire is."
sides = face.new_socket("Sides", in_out="INPUT", socket_type="NodeSocketInt")
sides.default_value, sides.min_value, sides.max_value = 8, 3, 32
face.new_socket("Studs", in_out="INPUT", socket_type="NodeSocketBool").default_value = True
face.new_socket("Seed", in_out="INPUT", socket_type="NodeSocketInt")
face.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketVector")
face.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
nodes, links = group.nodes, group.links
given, result = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
cone = nodes.new("GeometryNodeMeshCone")
paint = nodes.new("GeometryNodeSetMaterial")
paint.inputs["Material"].default_value = stone
cone.inputs["Radius Bottom"].default_value = 0.5
# The Seed jitters every vertex a little: each seed is another spire.
spread = nodes.new("FunctionNodeRandomValue")
spread.data_type = "FLOAT_VECTOR"
def pick(sockets, name, kind):
    return next(s for s in sockets if s.name == name and s.type == kind and s.enabled)
pick(spread.inputs, "Min", "VECTOR").default_value = (-0.05, -0.05, 0.0)
pick(spread.inputs, "Max", "VECTOR").default_value = (0.05, 0.05, 0.0)
links.new(given.outputs["Seed"], pick(spread.inputs, "Seed", "INT"))
jitter = nodes.new("GeometryNodeSetPosition")
links.new(pick(spread.outputs, "Value", "VECTOR"), jitter.inputs["Offset"])
links.new(given.outputs["Sides"], cone.inputs["Vertices"])
links.new(given.outputs["Height"], cone.inputs["Depth"])
links.new(cone.outputs["Mesh"], jitter.inputs["Geometry"])
links.new(jitter.outputs["Geometry"], paint.inputs["Geometry"])
line = nodes.new("GeometryNodeMeshLine")
line.inputs["Count"].default_value = 3
cube = nodes.new("GeometryNodeMeshCube")
cube.inputs["Size"].default_value = (0.1, 0.1, 0.1)
studs = nodes.new("GeometryNodeSetMaterial")
studs.inputs["Material"].default_value = moss
links.new(cube.outputs["Mesh"], studs.inputs["Geometry"])
place = nodes.new("GeometryNodeInstanceOnPoints")
links.new(line.outputs["Mesh"], place.inputs["Points"])
links.new(studs.outputs["Geometry"], place.inputs["Instance"])
links.new(given.outputs["Studs"], place.inputs["Selection"])
join = nodes.new("GeometryNodeJoinGeometry")
links.new(paint.outputs["Geometry"], join.inputs["Geometry"])
links.new(place.outputs["Instances"], join.inputs["Geometry"])
links.new(join.outputs["Geometry"], result.inputs["Geometry"])
group.use_fake_user = True
bpy.data.node_groups.new("ShaderBits", "ShaderNodeTree").use_fake_user = True
bpy.ops.wm.save_as_mainfile(filepath="/work/spire.blend")
"""


def _cli(*argv: str) -> tuple[int, dict]:
    parser = argparse.ArgumentParser()
    kits_cli.add_arguments(parser)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = kits_cli.main(parser.parse_args(list(argv)))
    return code, json.loads(out.getvalue())


async def main() -> None:
    with (
        tempfile.TemporaryDirectory(prefix="joinery-own-blend-") as files,
        tempfile.TemporaryDirectory(prefix="joinery-own-kit-") as directory,
    ):
        made = await run_offline(
            [
                "blender",
                "--background",
                "--factory-startup",
                "--python-expr",
                MAKE_GROUP,
            ],
            Path(files),
        )
        blend = Path(files) / "spire.blend"
        assert made.ok and blend.is_file(), made.output[-1500:]
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
        (project / "joinery.lock").write_text("version = 1\n")
        code, got = await asyncio.to_thread(
            _cli,
            "from-blend",
            str(blend),
            "--group",
            "Spire",
            "--project",
            str(project),
            "--yes",
            "--json",
        )
        assert code == 0 and got["state"] == "accepted", got
        assert got["params"] == ["height", "sides", "studs"] and got["fixed"] == [
            "Offset"
        ], got
        assert got["seed_input"] is True and set(got["materials"]) == {
            "stone",
            "moss_green",
        }, got
        source = "art/sources/groups/spire.blend"
        assert (project / source).read_bytes() == blend.read_bytes()
        lock = (project / "joinery.lock").read_text()
        assert source in lock and "human-import" in lock, lock
        listed = {row["id"]: row for row in kits.describe(project)["kits"]}
        spire = listed["spire"]
        params = {p["name"]: p for p in spire["params"]}
        assert params["height"]["min"] == 0.5 and params["height"]["unit"] == "m", (
            params
        )
        assert params["sides"]["kind"] == "int" and params["studs"]["kind"] == "bool"
        assert (
            spire["presets"][0]["name"] == "As made"
            and spire["presets"][0]["params"]["height"] == 2.0
        )
        assert "this project" in spire["from"]
        for argv, message in [
            (("--group", "Nope"), "no node group 'Nope'"),
            (("--group", "ShaderBits", "--id", "bits"), "not a Geometry Nodes group"),
            (("--group", "Spire"), "already has a kit 'spire'"),
        ]:
            code, refused = await asyncio.to_thread(
                _cli,
                "from-blend",
                str(blend),
                *argv,
                "--project",
                str(project),
                "--yes",
                "--json",
            )
            assert code == 1 and message in refused["error"], refused
        tx = FileTransaction(project)
        tx.begin("Spires from the user's own node group")
        stage = tx.stage
        plan = kits.plan(
            stage,
            "spire",
            {"height": 3.0, "sides": 6},
            seed=7,
            name="spire",
            count=3,
            vary=0.0,
            folder="art/props",
        )
        result = await kits.make(stage, plan)
        recipe = load_recipe(stage / result["recipe"])
        assert recipe.sources == (source,), recipe.sources
        glbs = [stage / m["glb"] for m in result["models"]]
        digests = {path.read_bytes() for path in glbs}
        assert len(digests) == 3, "each spire has its own seed"
        for model in result["models"]:
            # A six-sided cone plus three realized studs.
            assert model["triangles"] == 6 + 4 + 3 * 12, model
            assert abs(model["size_m"][2] - 3.0) < 0.2, model
        gltf = (stage / result["models"][0]["glb"]).read_bytes()
        assert b"Spire_Stone" in gltf and b"Spire_MossGreen" in gltf
        assert measure_glb(gltf).triangles == result["models"][0]["triangles"]
        problems = check_assets(stage, load_catalog())
        assert not problems, problems
        rebuilt = await asyncio.to_thread(
            rebuild_one, stage / result["recipe"], stage, SandboxRunner()
        )
        assert rebuilt.ok, rebuilt.problems
        edited, _marker = kits.edit_plan(
            stage, result["models"][0]["glb"], {"height": 4.0}
        )
        again = await kits.make(stage, edited, replacing=result["models"][0]["glb"])
        assert all(abs(m["size_m"][2] - 4.0) < 0.2 for m in again["models"]), again[
            "models"
        ]
        tx.reject()
        print(
            "own kit from a Geometry Nodes group: from-blend/list/batch of 3/edit/"
            f"lock/rebuild/refusals: PASS (triangles {[m['triangles'] for m in result['models']]})"
        )
        assert kit_groups.GROUP_DIR in source


if __name__ == "__main__":
    asyncio.run(main())
