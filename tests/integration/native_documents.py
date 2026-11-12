"""Real offline app fixtures. These are technical tests, never user art approval."""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import tempfile
from pathlib import Path
from typing import Any

from joinery.authoring import edit_document, edit_graph, inspect_document, run_offline
from joinery.recipe import rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction
from joinery.workspace import index

# Writes flat grey into the fixture's albedo image, creating the image (a
# GENERATED one, as a bake target would be) and its material on first use.
_FILL = """
image = bpy.data.images.get('FixtureAlbedo') or bpy.data.images.new('FixtureAlbedo', 8, 8)
image.pixels.foreach_set([{value}] * (8 * 8 * 4))
material = bpy.data.materials.get('FixtureMat')
if material is None:
    material = bpy.data.materials.new('FixtureMat')
    material.use_nodes = True
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    material.node_tree.links.new(texture.outputs['Color'], material.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
    bpy.data.objects['Fixture'].data.materials.append(material)
"""

# Reads the albedo back from the saved .blend and from its exported PNG.
_READ_ALBEDO = """
import bpy
packed = bpy.data.images['FixtureAlbedo']
png = bpy.data.images.load('/work/art/textures/fixture/FixtureAlbedo.png')
print('JOINERY_ALBEDO', packed.source, bool(packed.packed_file), packed.pixels[0], png.pixels[0])
"""


# A second bake as agent code often writes it: a new image under the same name,
# on a new texture node, while the old node and image stay in the material.
_REBAKE = """
image = bpy.data.images.new('FixtureAlbedo', 8, 8)
image.pixels.foreach_set([0.5] * (8 * 8 * 4))
material = bpy.data.materials['FixtureMat']
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
material.node_tree.links.new(texture.outputs['Color'], material.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
"""


async def _image_names(stage: Path) -> list[str]:
    code = "import bpy\nprint('JOINERY_IMAGES', ','.join(sorted(i.name for i in bpy.data.images)))"
    got = await run_offline(["blender", "--background", "art/sources/fixture.blend", "--python-expr", code], stage, readonly=True)
    line = next((line for line in got.stdout.splitlines() if line.startswith("JOINERY_IMAGES")), "")
    assert line, got.stdout[-800:] + got.stderr[-800:]
    return line.split(maxsplit=1)[1].split(",") if " " in line else []


async def _albedo(stage: Path) -> tuple[float, float]:
    """The albedo's first channel in the .blend and in the PNG, to two places."""
    got = await run_offline(["blender", "--background", "art/sources/fixture.blend", "--python-expr", _READ_ALBEDO], stage, readonly=True)
    line = next((line for line in got.stdout.splitlines() if line.startswith("JOINERY_ALBEDO")), "")
    assert line, got.stdout[-800:] + got.stderr[-800:]
    _, source, packed, blend, png = line.split()
    assert source == "FILE" and packed == "True", line
    return round(float(blend), 2), round(float(png), 2)


HAMMER = Path(__file__).resolve().parents[1] / "fixtures/parts/hammer_model.py"
SOURCE = "art/sources/hammer.blend"
MODEL = "art/models/hammer.glb"


async def _hashes(stage: Path) -> dict[str, str]:
    """The scope guard's own hashes of every object, material and image."""
    from joinery.tools.blender import PART_WALK, SCOPE_GUARD

    code = "import bpy, json\n" + PART_WALK + SCOPE_GUARD + "\nprint('JOINERY_HASHES:' + json.dumps(_joinery_scope_hashes()))"
    got = await run_offline(["blender", "--background", SOURCE, "--python-expr", code], stage, readonly=True)
    line = next((row for row in got.stdout.splitlines() if row.startswith("JOINERY_HASHES:")), "")
    assert line, got.output[-1500:]
    found: dict[str, str] = json.loads(line.removeprefix("JOINERY_HASHES:"))
    return found


async def _spread(stage: Path, png: str) -> float:
    """How much a PNG's pixels vary: a flat (unbaked) normal map is about 0."""
    code = f"import bpy\nimage = bpy.data.images.load('/work/{png}')\nimport numpy as np\np = np.empty(len(image.pixels), dtype=np.float32)\nimage.pixels.foreach_get(p)\nprint('JOINERY_SPREAD', float(p.reshape(-1, 4)[:, :3].std()))"
    got = await run_offline(["blender", "--background", "--factory-startup", "--python-expr", code], stage, readonly=True)
    line = next((row for row in got.stdout.splitlines() if row.startswith("JOINERY_SPREAD")), "")
    assert line, got.output[-1500:]
    return float(line.split()[1])


def _glb_material(path: Path, name: str) -> tuple[dict[str, Any], list[str]]:
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20 : 20 + length])
    images = [str(image.get("name", "")) for image in gltf.get("images", [])]
    textures = [images[texture["source"]] for texture in gltf.get("textures", [])]
    material = next(m for m in gltf["materials"] if m["name"] == name)
    return material, textures


async def _op(stage: Path, tool: str, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(stage, "joinery-blender-ops")) as client:
        return await client.call_tool(tool, {"source": SOURCE, **arguments})


async def _parts(stage: Path) -> None:
    """Typed part edits on the hammer change only the selected part, in pixels."""
    from joinery import parts
    from joinery.tools.blender import guarded

    await edit_document(stage, "blender", SOURCE, HAMMER.read_text(), [MODEL])
    assert "embedded_image_handling=3" in (stage / (MODEL + ".import")).read_text()
    report = await parts.report(stage, MODEL)
    rough = report.find({"material": "Steel", "image": "steel_rough"}).target()
    height = report.find({"material": "Steel", "image": "steel_height"}).target()
    parts.write_targets(stage, [rough])
    before = await _hashes(stage)
    png = stage / "art/textures/hammer/steel_rough.png"
    png_before = png.read_bytes()
    result = await _op(stage, "adjust_map", {"material": "Steel", "image": "steel_rough", "op": "invert"})
    assert not result.is_error, result.content[0].text
    after = await _hashes(stage)
    changed = sorted(k for k in before if before[k] != after.get(k))
    assert changed == ["image:steel_rough"], changed
    assert png.read_bytes() != png_before, "The exported map must carry the new pixels"
    steel, textures = _glb_material(stage / MODEL, "Steel")
    rough_index = steel["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
    assert textures[rough_index] == "steel_rough", textures
    # Names outside the selection are refused before Blender starts.
    refused = await _op(stage, "adjust_map", {"material": "Wood", "image": "wood_orm", "op": "invert"})
    assert refused.is_error and "not part of the selection" in refused.content[0].text
    # The scope guard fails an edit that reaches past its part; nothing is saved.
    sneaky = guarded("bpy.data.materials['Wood'].node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = 0.1", images=("steel_rough",))
    from servers.authoring import _edit

    try:
        await _edit(stage, SOURCE, sneaky, [MODEL], "Reaches past its part")
    except RuntimeError as exc:
        assert "outside the selected part" in str(exc), exc
    else:
        raise AssertionError("The scope guard let a change to Wood through")
    assert await _hashes(stage) == after
    parts.write_targets(stage, [height])
    result = await _op(stage, "bake_height_to_normal", {"material": "Steel", "height_image": "steel_height", "strength": 1.0})
    assert not result.is_error, result.content[0].text
    baked = await _hashes(stage)
    changed = sorted(k for k in set(after) | set(baked) if after.get(k) != baked.get(k))
    assert changed == ["image:Steel_normal_baked", "material:Steel"], changed
    steel, textures = _glb_material(stage / MODEL, "Steel")
    assert textures[steel["normalTexture"]["index"]] == "Steel_normal_baked", textures
    spread = await _spread(stage, "art/textures/hammer/Steel_normal_baked.png")
    assert spread > 0.01, f"The baked normal map is flat ({spread})"
    print(f"parts: only steel_rough changed; bake spread {spread:.3f}")


async def main(app: str) -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-native-fixture-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender", "krita", "supercollider", "material-maker"]\n')
        (project / "joinery.lock").write_text("version=1\n")
        tx = FileTransaction(project)
        tx.begin("Automated native document fixture")
        if app == "blender-parts":
            await _parts(tx.stage)
            got = {"recipe": max((tx.stage / "recipes").glob("*.toml"), key=lambda path: path.stat().st_mtime).relative_to(tx.stage).as_posix()}
        elif app == "blender":
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", 'bpy.ops.mesh.primitive_cube_add(size=1)\nbpy.context.object.name="Fixture"\n' + _FILL.format(value=0.25), ["art/models/fixture.glb"])
            first = got["recipe"]
            # Pixels written into a new (GENERATED) image are kept in the
            # .blend and exported as a PNG.
            assert await _albedo(tx.stage) == (0.25, 0.25)
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", 'bpy.data.objects["Fixture"].location.z += 0.5\n' + _FILL.format(value=0.75), ["art/models/fixture.glb"])
            # Repainting the now-packed image in a later edit is kept too.
            assert await _albedo(tx.stage) == (0.75, 0.75)
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", 'bpy.data.objects["Fixture"].location.x += 0.25', ["art/models/fixture.glb"])
            # An edit that leaves the texture alone keeps it.
            assert await _albedo(tx.stage) == (0.75, 0.75)
            # Baking the same map again makes a new image under a taken name
            # (Blender calls it FixtureAlbedo.001). It replaces the original:
            # one image in the .blend, one PNG, never a _001 file.
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", _REBAKE, ["art/models/fixture.glb"])
            assert await _albedo(tx.stage) == (0.5, 0.5)
            names = sorted(p.name for p in (tx.stage / "art/textures/fixture").glob("*.png"))
            assert names == ["FixtureAlbedo.png"], names
            assert await _image_names(tx.stage) == ["FixtureAlbedo"]
            assert (tx.stage / "recipes/history" / Path(first).name).is_file()
            native = await inspect_document(tx.stage, app, "art/sources/fixture.blend")
            assert native["objects"][0]["position"][2] == 0.5
            assets = index(tx.stage)["assets"]
            output = next(item for item in assets if item["path"] == "art/models/fixture.glb")
            assert output["sources"][0] == "art/sources/fixture.blend"
        elif app == "blender-mpfb":
            # Needs a creative worker built with MPFB2 as a system extension
            # (sandbox/Dockerfile, BLENDER_SYSTEM_EXTENSIONS). The recipe enables
            # it because the code names it; an older image stops with the
            # rebuild hint rather than a traceback.
            code = "from bl_ext.system.mpfb.services.humanservice import HumanService\nhuman = HumanService.create_human()\nhuman.name = 'Fixture'"
            try:
                got = await edit_document(tx.stage, "blender", "art/sources/human.blend", code, ["art/models/human.glb"])
            except (RuntimeError, ValueError) as exc:
                if "MPFB2 is not available in this Blender worker" in str(exc):
                    raise SystemExit("blender-mpfb: the creative worker predates the MPFB2 fix; run Joinery setup to rebuild it") from exc
                raise
            native = await inspect_document(tx.stage, "blender", "art/sources/human.blend")
            human = next(item for item in native["objects"] if item["name"] == "Fixture")
            assert human["vertices"] > 10000, human
            assert (tx.stage / "art/models/human.glb").stat().st_size > 0
        elif app == "krita":
            code = 'document=app.createDocument(16,16,"Fixture","RGBA","U8","",120.0)\nlayer=document.createNode("Layer","paintlayer")\ndocument.rootNode().addChildNode(layer,None)\napp.activeWindow().addView(document)'
            got = await edit_document(tx.stage, app, "art/fixture.kra", code, ["art/fixture.png"])
            document = await inspect_document(tx.stage, app, "art/fixture.kra")
            assert any(layer.get("name") == "Layer" for layer in document["layers"])
        elif app == "supercollider":
            code = '(\nvar score = Score([[0.0, [\\c_set, 0, 0]], [0.02, [\\c_set, 0, 0]]]);\nscore.recordNRT(outputFilePath: "audio/fixture.wav", sampleRate: 48000, headerFormat: "WAV", sampleFormat: "int16", options: ServerOptions.new.numOutputBusChannels_(1), duration: 0.02, action: { 0.exit; });\n)\n'
            got = await edit_document(tx.stage, app, "audio/fixture.scd", code, ["audio/fixture.wav"])
        else:
            graph = {"type": "graph", "name": "Fixture", "nodes": [{"type": "material", "name": "Material", "parameters": {"size": 4, "roughness": 1.0}}], "connections": []}
            got = await edit_graph(tx.stage, "art/fixture.ptex", graph, "art/export")
        rebuilt = await asyncio.to_thread(rebuild_one, tx.stage / got["recipe"], tx.stage, SandboxRunner())
        assert rebuilt.ok, rebuilt.problems
        assert not (project / "art").exists() and not (project / "audio").exists()
        tx.reject()
        print(app + " native save/export/record/replay/reject: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=["blender", "blender-parts", "blender-mpfb", "krita", "supercollider", "material-maker"])
    asyncio.run(main(parser.parse_args().app))
