"""Real offline app fixtures. These are technical tests, never user art approval."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

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


async def _albedo(stage: Path) -> tuple[float, float]:
    """The albedo's first channel in the .blend and in the PNG, to two places."""
    got = await run_offline(["blender", "--background", "art/sources/fixture.blend", "--python-expr", _READ_ALBEDO], stage, readonly=True)
    line = next((line for line in got.stdout.splitlines() if line.startswith("JOINERY_ALBEDO")), "")
    assert line, got.stdout[-800:] + got.stderr[-800:]
    _, source, packed, blend, png = line.split()
    assert source == "FILE" and packed == "True", line
    return round(float(blend), 2), round(float(png), 2)


async def main(app: str) -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-native-fixture-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender", "krita", "supercollider", "material-maker"]\n')
        (project / "joinery.lock").write_text("version=1\n")
        tx = FileTransaction(project)
        tx.begin("Automated native document fixture")
        if app == "blender":
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
    parser.add_argument("app", choices=["blender", "blender-mpfb", "krita", "supercollider", "material-maker"])
    asyncio.run(main(parser.parse_args().app))
