"""Real offline app fixtures. These are technical tests, never user art approval."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from joinery.authoring import edit_document, edit_graph, inspect_document
from joinery.recipe import rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction
from joinery.workspace import index


async def main(app: str) -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-native-fixture-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender", "krita", "supercollider", "material-maker"]\n')
        (project / "joinery.lock").write_text("version=1\n")
        tx = FileTransaction(project)
        tx.begin("Automated native document fixture")
        if app == "blender":
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", 'bpy.ops.mesh.primitive_cube_add(size=1)\nbpy.context.object.name="Fixture"', ["art/models/fixture.glb"])
            first = got["recipe"]
            got = await edit_document(tx.stage, app, "art/sources/fixture.blend", 'bpy.data.objects["Fixture"].location.z += 0.5', ["art/models/fixture.glb"])
            assert (tx.stage / "recipes/history" / Path(first).name).is_file()
            native = await inspect_document(tx.stage, app, "art/sources/fixture.blend")
            assert native["objects"][0]["position"][2] == 0.5
            assets = index(tx.stage)["assets"]
            output = next(item for item in assets if item["path"] == "art/models/fixture.glb")
            assert output["sources"][0] == "art/sources/fixture.blend"
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
    parser.add_argument("app", choices=["blender", "krita", "supercollider", "material-maker"])
    asyncio.run(main(parser.parse_args().app))
