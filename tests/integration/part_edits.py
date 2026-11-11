"""Map sources, material copies, bones and shape keys, edited in the real offline workers.

On the part-selection hammer (tests/fixtures/parts/hammer_model.py) and a small
hand-made arm rig, through the joinery-blender-ops MCP server exactly as an
agent calls it:

* make_map_editable starts a Krita document and a Material Maker graph from a
  map's own pixels, and the model's map is unchanged by it;
* paint_map adds Krita fill, stroke and filter layers, and the game's map
  changes; replace_map puts a Krita export in another map;
* split_material gives Head.001 its own Steel, and editing the copy's map
  leaves the Handle's Steel alone;
* edit_shape_key and edit_bone change one key or bone each;
* every operation changes only its part (the scope guard's own hashes), and
  every current recipe replays (`joinery rebuild`), Krita and Blender alike.

Technical fixtures only. Run: `uv run python tests/integration/part_edits.py`.
"""

from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any

from native_documents import HAMMER, MODEL, SOURCE, _glb_material, _hashes

from joinery import parts
from joinery.authoring import edit_document, inspect_document, run_offline
from joinery.recipe import rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

ARM, ARM_MODEL = "art/sources/arm.blend", "art/models/arm.glb"
# A three-bone arm skinning a cylinder: each third of it follows one bone.
ARM_CODE = """
bpy.ops.object.armature_add(location=(0, 0, 0))
rig = bpy.context.object
rig.name = rig.data.name = 'Rig'
bpy.ops.object.mode_set(mode='EDIT')
bones = rig.data.edit_bones
root = bones[0]
root.name, root.head, root.tail = 'Root', (0, 0, 0), (0, 0, 0.4)
mid = bones.new('Mid'); mid.head, mid.tail, mid.parent, mid.use_connect = (0, 0, 0.4), (0, 0, 0.8), root, True
tip = bones.new('Tip'); tip.head, tip.tail, tip.parent, tip.use_connect = (0, 0, 0.8), (0, 0, 1.2), mid, True
bpy.ops.object.mode_set(mode='OBJECT')
import math
rings = [[(0.05 * math.cos(a * math.pi / 4), 0.05 * math.sin(a * math.pi / 4), level * 0.1) for a in range(8)] for level in range(13)]
faces = [(r * 8 + a, r * 8 + (a + 1) % 8, (r + 1) * 8 + (a + 1) % 8, (r + 1) * 8 + a) for r in range(12) for a in range(8)]
mesh = bpy.data.meshes.new('Arm')
mesh.from_pydata([v for ring in rings for v in ring], [], faces)
mesh.uv_layers.new(name='UVMap')
arm = bpy.data.objects.new('Arm', mesh)
bpy.context.scene.collection.objects.link(arm)
for name in ('Root', 'Mid', 'Tip'):
    arm.vertex_groups.new(name=name)
for vertex in arm.data.vertices:
    z = vertex.co.z
    arm.vertex_groups['Root' if z < 0.4 else 'Mid' if z < 0.8 else 'Tip'].add([vertex.index], 1.0, 'REPLACE')
arm.parent = rig
arm.modifiers.new('Armature', 'ARMATURE').object = rig
"""
# Bends the arm at its elbow (Mid, renamed), for a shape key made from the pose.
BEND = """
rig = bpy.data.objects['Rig']
rig.pose.bones['Elbow'].rotation_mode = 'XYZ'
rig.pose.bones['Elbow'].rotation_euler = (0.8, 0, 0)
"""


def png_pixels(path: Path) -> list[bytes]:
    """Rows of a PNG's pixels (8-bit RGB or RGBA), decoded with the standard library."""
    data = path.read_bytes()
    offset, chunks = 8, b""
    width = height = kind = 0
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        name, body = (
            data[offset + 4 : offset + 8],
            data[offset + 8 : offset + 8 + length],
        )
        if name == b"IHDR":
            width, height, _depth, kind = struct.unpack(">IIBB", body[:10])
        elif name == b"IDAT":
            chunks += body
        offset += 12 + length
    raw, size = zlib.decompress(chunks), {2: 3, 6: 4}[kind]
    stride, rows, previous = width * size, [], bytearray(width * size)
    for y in range(height):
        start = y * (stride + 1)
        kind_of_filter, line = (
            raw[start],
            bytearray(raw[start + 1 : start + 1 + stride]),
        )
        for x in range(stride):
            a = line[x - size] if x >= size else 0
            b, c = previous[x], previous[x - size] if x >= size else 0
            if kind_of_filter == 1:
                line[x] = (line[x] + a) & 255
            elif kind_of_filter == 2:
                line[x] = (line[x] + b) & 255
            elif kind_of_filter == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif kind_of_filter == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (
                    line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)
                ) & 255
        rows.append(bytes(b for i, b in enumerate(line) if size == 3 or i % 4 != 3))
        previous = line
    return rows


async def op(
    stage: Path, tool: str, arguments: dict[str, Any], source: str = SOURCE
) -> dict[str, Any]:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(stage, "joinery-blender-ops")) as client:
        result = await client.call_tool(tool, {"source": source, **arguments})
    text = result.content[0].text
    assert not result.is_error, f"{tool}: {text}"
    found: dict[str, Any] = json.loads(text)
    return found


async def changed(
    stage: Path, before: dict[str, str], source: str = SOURCE
) -> list[str]:
    after = await _hashes_of(stage, source)
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


async def _hashes_of(stage: Path, source: str) -> dict[str, str]:
    if source == SOURCE:
        return await _hashes(stage)
    from joinery.tools.blender import PART_WALK, SCOPE_GUARD

    code = (
        "import bpy, json\n"
        + PART_WALK
        + SCOPE_GUARD
        + "\nprint('JOINERY_HASHES:' + json.dumps(_joinery_scope_hashes()))"
    )
    got = await run_offline(
        ["blender", "--background", source, "--python-expr", code], stage, readonly=True
    )
    line = next(
        row for row in got.stdout.splitlines() if row.startswith("JOINERY_HASHES:")
    )
    found: dict[str, str] = json.loads(line.removeprefix("JOINERY_HASHES:"))
    return found


async def target(stage: Path, model: str, **names: str) -> dict[str, str]:
    report = await parts.report(stage, model, refresh=True)
    picked = report.find(names).target()
    parts.write_targets(stage, [picked])
    return picked


async def maps(stage: Path, facts: dict[str, Any]) -> None:
    texture = stage / "art/textures/hammer/steel_rough.png"
    pixels = png_pixels(texture)
    await target(stage, MODEL, material="Steel", image="steel_rough")
    before = await _hashes(stage)
    made = await op(
        stage, "make_map_editable", {"material": "Steel", "image": "steel_rough"}
    )
    assert made["editable"] == "art/sources/maps/hammer/steel_rough.kra", made
    assert png_pixels(stage / made["map"]) == pixels, "Krita changed the map's pixels"
    assert png_pixels(texture) == pixels, "making the map editable changed it"
    assert await changed(stage, before) == [], "only a link was added"
    picked = await target(stage, MODEL, material="Steel", image="steel_rough")
    assert picked["editable"] == made["editable"] and "paint_map" in picked["ops"], (
        picked
    )
    before = await _hashes(stage)
    painted = await op(
        stage,
        "paint_map",
        {
            "material": "Steel",
            "image": "steel_rough",
            "ops": [
                {
                    "op": "fill",
                    "color": [1, 1, 1],
                    "rect": [0, 0, 8, 32],
                    "name": "Worn edge",
                },
                {
                    "op": "stroke",
                    "points": [[4, 28], [28, 4]],
                    "color": [0, 0, 0],
                    "width": 3,
                },
                {
                    "op": "adjust",
                    "filter": "levels",
                    "params": {"in_low": 0.1, "gamma": 1.3},
                },
            ],
        },
    )
    assert await changed(stage, before) == ["image:steel_rough"]
    after = png_pixels(texture)
    assert after != pixels and after[16][:3] == b"\xff\xff\xff", after[16][:6]
    layers = (await inspect_document(stage, "krita", made["editable"]))["layers"]
    names = [layer.get("name") for layer in layers]
    assert {"Map", "Worn edge", "Stroke 2", "Adjust 3"} <= set(names), names
    steel, textures = _glb_material(stage / MODEL, "Steel")
    assert (
        textures[steel["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]]
        == "steel_rough"
    )
    facts["paint"] = {"layers": names, "recipe": painted["recipe"]}

    await target(stage, MODEL, material="Wood", image="wood_base")
    before = await _hashes(stage)
    await op(
        stage,
        "replace_map",
        {"material": "Wood", "image": "wood_base", "replacement": made["map"]},
    )
    assert await changed(stage, before) == ["image:wood_base"]
    assert png_pixels(stage / "art/textures/hammer/wood_base.png") == png_pixels(
        stage / made["map"]
    )

    await target(stage, MODEL, material="Steel", image="steel_base")
    before = await _hashes(stage)
    graph = await op(
        stage,
        "make_map_editable",
        {"material": "Steel", "image": "steel_base", "editor": "material-maker"},
    )
    assert graph["editable"].endswith("steel_base.ptex"), graph
    assert await changed(stage, before) in ([], ["image:steel_base"])
    facts["material_maker"] = {"graph": graph["editable"], "map": graph["map"]}


async def split(stage: Path, facts: dict[str, Any]) -> None:
    await target(stage, MODEL, material="Steel", object="Head.001")
    before = await _hashes(stage)
    result = await op(
        stage, "split_material", {"material": "Steel", "objects": ["Head.001"]}
    )
    copy = result["material"]
    assert copy == "Steel_Head.001", result
    changes = await changed(stage, before)
    assert changes == sorted(
        [
            "object:Head.001",
            f"material:{copy}",
            *[f"image:{n}" for n in result["maps"].values()],
        ]
    ), changes
    report = await parts.report(stage, MODEL)
    assert report.find({"material": "Steel"}).used_by == ("Handle",)
    assert report.find({"material": copy}).used_by == ("Head.001",)
    rough = result["maps"]["steel_rough"]
    await target(stage, MODEL, material=copy, image=rough)
    before = await _hashes(stage)
    await op(stage, "adjust_map", {"material": copy, "image": rough, "op": "invert"})
    assert await changed(stage, before) == [f"image:{rough}"], (
        "the Handle's Steel changed too"
    )
    facts["split"] = {"material": copy, "maps": result["maps"]}


async def shape_keys(stage: Path, facts: dict[str, Any]) -> None:
    await target(stage, MODEL, object="Head.001", shape_key="Dent")
    steps = [
        ("Dent", "set_value", {"value": 0.5}),
        ("Dent", "rename", {"name": "Dented"}),
    ]
    for key, operation, params in steps:
        before = await _hashes(stage)
        await op(
            stage,
            "edit_shape_key",
            {"object": "Head.001", "shape_key": key, "op": operation, "params": params},
        )
        assert await changed(stage, before) == ["object:Head.001"], operation
    await target(stage, MODEL, object="Head.001")
    await op(
        stage,
        "edit_shape_key",
        {"object": "Head.001", "shape_key": "Mix", "op": "new_from_mix"},
    )
    head = next(
        o
        for o in (await inspect_document(stage, "blender", SOURCE))["objects"]
        if o["name"] == "Head.001"
    )
    assert head["shape_keys"] == ["Basis", "Dented", "Mix"], head["shape_keys"]
    await op(
        stage,
        "edit_shape_key",
        {"object": "Head.001", "shape_key": "Mix", "op": "delete"},
    )
    gltf_names = _target_names(stage / MODEL)
    assert gltf_names == [["Dented"]], gltf_names
    facts["shape_keys"] = gltf_names


def _target_names(path: Path) -> list[list[str]]:
    from joinery.tools.blender_export import glb_meshes

    return [m["target_names"] for m in glb_meshes(path) if m["morph_targets"]]


async def bones(stage: Path, facts: dict[str, Any]) -> None:
    await edit_document(stage, "blender", ARM, ARM_CODE, [ARM_MODEL], "arm rig")
    await target(stage, ARM_MODEL, object="Rig", bone="Mid")
    before = await _hashes_of(stage, ARM)
    await op(
        stage,
        "edit_bone",
        {"armature": "Rig", "bone": "Mid", "op": "rename", "params": {"name": "Elbow"}},
        ARM,
    )
    assert await changed(stage, before, ARM) == ["object:Arm", "object:Rig"]
    document = await inspect_document(stage, "blender", ARM)
    rig = next(o for o in document["objects"] if o["name"] == "Rig")
    assert [b["name"] for b in rig["bones"]] == ["Root", "Elbow", "Tip"], rig["bones"]
    await target(stage, ARM_MODEL, object="Rig")
    for params in (
        ("Tip", "set_rest", {"tail": [0.0, 0.2, 1.2]}),
        ("Tip", "set_parent", {"parent": "Root"}),
        ("Root", "scale_chain", {"factor": 0.5}),
    ):
        before = await _hashes_of(stage, ARM)
        await op(
            stage,
            "edit_bone",
            {
                "armature": "Rig",
                "bone": params[0],
                "op": params[1],
                "params": params[2],
            },
            ARM,
        )
        assert await changed(stage, before, ARM) == ["object:Rig"], params
    rig = next(
        o
        for o in (await inspect_document(stage, "blender", ARM))["objects"]
        if o["name"] == "Rig"
    )
    parents = {b["name"]: b["parent"] for b in rig["bones"]}
    assert parents == {"Root": None, "Elbow": "Root", "Tip": "Root"}, parents
    gltf = json.loads(_glb_json(stage / ARM_MODEL))
    joints = sorted(gltf["nodes"][i]["name"] for i in gltf["skins"][0]["joints"])
    assert joints == ["Elbow", "Root", "Tip"], joints
    await edit_document(stage, "blender", ARM, BEND, [ARM_MODEL], "bend the arm")
    await target(stage, ARM_MODEL, object="Arm")
    await op(
        stage,
        "edit_shape_key",
        {"object": "Arm", "shape_key": "Bent", "op": "new_from_pose"},
        ARM,
    )
    assert _target_names(stage / ARM_MODEL) == [["Bent"]], _target_names(
        stage / ARM_MODEL
    )
    facts["bones"] = {"joints": joints, "parents": parents}


def _glb_json(path: Path) -> bytes:
    data = path.read_bytes()
    (length,) = struct.unpack("<I", data[12:16])
    return data[20 : 20 + length]


async def godot_import(stage: Path) -> None:
    """Godot imports every exported model without an error."""
    got = await run_offline(
        ["godot", "--headless", "--path", "/work", "--editor", "--import"], stage, 300
    )
    assert got.ok and "ERROR" not in got.output, got.output[-2000:]


async def main() -> dict[str, Any]:
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-part-edits-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text(
            '[tools]\nenabled=["godot", "blender", "krita", "material-maker"]\n'
        )
        (project / "joinery.lock").write_text("version=1\n")
        tx = FileTransaction(project)
        tx.begin("Part edits fixture")
        stage = tx.stage
        await edit_document(stage, "blender", SOURCE, HAMMER.read_text(), [MODEL])
        await maps(stage, facts)
        await split(stage, facts)
        await shape_keys(stage, facts)
        await bones(stage, facts)
        await godot_import(stage)
        replays = [
            await asyncio.to_thread(rebuild_one, path, stage, SandboxRunner())
            for path in recipe_paths(stage)
        ]
        failed = [(r.recipe, r.problems) for r in replays if not r.ok]
        assert not failed, failed
        facts["replayed"] = len(replays)
        tx.reject()
    return facts


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), indent=1))
    print("PART_EDITS_OK")
