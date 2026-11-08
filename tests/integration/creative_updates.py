"""Real painted layers, vector exports and audible synth edits in temporary projects.

These are technical fixtures; they never approve art direction or create content
in a user's game. Recipe replay runs in a separate copy, as the public CLI does.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import shutil
import tempfile
import wave
from pathlib import Path
from typing import Any

from joinery.authoring import (
    edit_document,
    edit_graph,
    edit_vector,
    inspect_document,
    run_offline,
)
from joinery.catalog import load_catalog
from joinery.lockfile import check_assets
from joinery.recipe import rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import EXCLUDED, FileTransaction, inventory


def project_at(directory: str, tool: str) -> Path:
    project = Path(directory)
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text(
        f'[tools]\nenabled=["godot",{json.dumps(tool)}]\n'
    )
    (project / "joinery.lock").write_text("version=1\n")
    return project


async def verify_replay(tx: FileTransaction, result: dict[str, Any]) -> None:
    problems = check_assets(tx.stage, load_catalog())
    assert not problems, problems
    with tempfile.TemporaryDirectory(prefix="joinery-creative-replay-") as directory:
        scratch = Path(directory)
        shutil.copytree(
            tx.stage,
            scratch,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*EXCLUDED),
        )
        rebuilt = await asyncio.to_thread(
            rebuild_one, scratch / result["recipe"], scratch, SandboxRunner()
        )
        assert rebuilt.ok, rebuilt.problems


async def pixel(project: Path, path: str) -> list[int]:
    code = (
        "from PyQt5.QtGui import QImage; import json; image=QImage("
        + repr(path)
        + "); print(json.dumps(list(image.pixelColor(8,8).getRgb())))"
    )
    got = await run_offline(["python3", "-c", code], project, readonly=True)
    assert got.ok, got.output
    return list(json.loads(got.stdout))


async def krita() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-painted-update-") as directory:
        project = project_at(directory, "krita")
        create = (
            "from PyQt5.QtCore import QByteArray\n"
            'document=app.createDocument(16,16,"TechnicalFixture","RGBA","U8","",120.0)\n'
            'layer=document.createNode("Base","paintlayer")\n'
            "document.rootNode().addChildNode(layer,None)\n"
            "layer.setPixelData(QByteArray(bytes([0,0,255,255])*256),0,0,16,16)\n"
            "document.refreshProjection()\n"
            "app.activeWindow().addView(document)"
        )
        tx = FileTransaction(project)
        tx.begin("Paint a technical layer fixture")
        result = await edit_document(
            tx.stage, "krita", "art/fixture.kra", create, ["art/fixture.png"]
        )
        first = await pixel(tx.stage, "art/fixture.png")
        assert first == [255, 0, 0, 255], first
        await verify_replay(tx, result)
        tx.review()
        tx.accept()
        before = inventory(project)
        update = (
            "from PyQt5.QtCore import QByteArray\n"
            'layer=document.createNode("Accent","paintlayer")\n'
            'document.rootNode().addChildNode(layer,document.nodeByName("Base"))\n'
            "layer.setPixelData(QByteArray(bytes([255,0,0,255])*256),0,0,16,16)\n"
            "document.refreshProjection()"
        )
        tx = FileTransaction(project)
        tx.begin("Edit an existing painted source")
        result = await edit_document(
            tx.stage, "krita", "art/fixture.kra", update, ["art/fixture.png"]
        )
        layers = (await inspect_document(tx.stage, "krita", "art/fixture.kra"))[
            "layers"
        ]
        names = [layer.get("name") for layer in layers]
        assert "Base" in names and "Accent" in names, names
        second = await pixel(tx.stage, "art/fixture.png")
        assert second == [0, 0, 255, 255], second
        assert not list((tx.stage / "art").rglob("*~")), "No unrecorded Krita backups"
        await verify_replay(tx, result)
        tx.review()
        tx.reject()
        assert inventory(project) == before
        tx = FileTransaction(project)
        tx.begin("Accept a painted source update")
        await edit_document(
            tx.stage, "krita", "art/fixture.kra", update, ["art/fixture.png"]
        )
        tx.review()
        tx.accept()
        assert await pixel(project, "art/fixture.png") == second
        tx.revert()
        assert inventory(project) == before
        print(
            "Krita painted pixels/layer retention/update replay/reject/accept/revert: PASS"
        )


def score(frequency: int) -> str:
    return (
        "// Synthèse: technical UTF-8 source fixture\n(\nvar definition=SynthDef(\\fixture,{ Out.ar(0,SinOsc.ar("
        + str(frequency)
        + ",0,0.2)*EnvGen.kr(Env.perc(0.001,0.04),doneAction:2)) });\n"
        "var score=Score([[0.0,[\\d_recv,definition.asBytes]],"
        "[0.001,[\\s_new,\\fixture,1000,0,0]],[0.08,[\\c_set,0,0]]]);\n"
        'score.recordNRT(outputFilePath:"audio/fixture.wav",sampleRate:48000,'
        'headerFormat:"WAV",sampleFormat:"int16",options:ServerOptions.new.'
        "numOutputBusChannels_(1),duration:0.08,action:{0.exit;});\n)\n"
    )


async def supercollider() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-synth-update-") as directory:
        project = project_at(directory, "supercollider")
        tx = FileTransaction(project)
        tx.begin("Create an audible technical synth fixture")
        result = await edit_document(
            tx.stage,
            "supercollider",
            "audio/fixture.scd",
            score(440),
            ["audio/fixture.wav"],
        )
        with wave.open(str(tx.stage / "audio/fixture.wav")) as output:
            assert output.getframerate() == 48000 and output.getnchannels() == 1
            samples = array.array("h", output.readframes(output.getnframes()))
        assert max(abs(sample) for sample in samples) > 1000
        await verify_replay(tx, result)
        tx.review()
        tx.accept()
        before = inventory(project)
        tx = FileTransaction(project)
        tx.begin("Change the native synth patch pitch")
        result = await edit_document(
            tx.stage,
            "supercollider",
            "audio/fixture.scd",
            score(660),
            ["audio/fixture.wav"],
        )
        assert (tx.stage / "audio/fixture.wav").read_bytes() != (
            project / "audio/fixture.wav"
        ).read_bytes()
        await verify_replay(tx, result)
        tx.review()
        tx.reject()
        assert inventory(project) == before
        tx = FileTransaction(project)
        tx.begin("Accept a native synth patch update")
        await edit_document(
            tx.stage,
            "supercollider",
            "audio/fixture.scd",
            score(660),
            ["audio/fixture.wav"],
        )
        tx.review()
        tx.accept()
        assert (project / "audio/fixture.scd").read_text() == score(660)
        tx.revert()
        assert inventory(project) == before
        print(
            "SuperCollider audible 440/660 Hz NRT/update replay/reject/accept/revert: PASS"
        )


async def inkscape() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-vector-update-") as directory:
        project = project_at(directory, "inkscape")

        def svg(color: str) -> str:
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="24">'
                f'<rect id="panel" x="2" y="2" width="28" height="20" fill="{color}"/>'
                "</svg>"
            )

        tx = FileTransaction(project)
        tx.begin("Create a technical vector source")
        result = await edit_vector(
            tx.stage, "art/fixture.svg", svg("#ff0000"), "art/fixture.png"
        )
        assert await pixel(tx.stage, "art/fixture.png") == [255, 0, 0, 255]
        await verify_replay(tx, result)
        tx.review()
        tx.accept()
        before = inventory(project)
        tx = FileTransaction(project)
        tx.begin("Edit the existing vector element")
        result = await edit_vector(
            tx.stage, "art/fixture.svg", svg("#0000ff"), "art/fixture.png"
        )
        assert 'id="panel"' in (tx.stage / "art/fixture.svg").read_text()
        assert await pixel(tx.stage, "art/fixture.png") == [0, 0, 255, 255]
        await verify_replay(tx, result)
        tx.review()
        tx.reject()
        assert inventory(project) == before
        tx = FileTransaction(project)
        tx.begin("Accept the vector source update")
        await edit_vector(
            tx.stage, "art/fixture.svg", svg("#0000ff"), "art/fixture.png"
        )
        tx.review()
        tx.accept()
        assert await pixel(project, "art/fixture.png") == [0, 0, 255, 255]
        tx.revert()
        assert inventory(project) == before
        print(
            "Inkscape SVG element/PNG pixels/update replay/reject/accept/revert: PASS"
        )


async def material_maker() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-material-update-") as directory:
        project = project_at(directory, "material-maker")
        graph = {
            "type": "graph",
            "name": "Fixture",
            "nodes": [
                {
                    "type": "material",
                    "name": "Material",
                    "parameters": {"size": 4, "roughness": 1.0},
                },
                {
                    "type": "uniform",
                    "name": "Color",
                    "parameters": {
                        "color": {"type": "Color", "r": 1, "g": 0, "b": 0, "a": 1}
                    },
                },
            ],
            "connections": [
                {"from": "Color", "from_port": 0, "to": "Material", "to_port": 0}
            ],
        }
        tx = FileTransaction(project)
        tx.begin("Create a technical native material")
        result = await edit_graph(tx.stage, "art/fixture.ptex", graph, "art/export")
        material = next(path for path in result["outputs"] if path.endswith(".tres"))
        texture = next(path for path in result["outputs"] if path.endswith(".png"))
        assert "roughness = 1.0" in (tx.stage / material).read_text()
        assert await pixel(tx.stage, texture) == [255, 0, 0, 255]
        await verify_replay(tx, result)
        tx.review()
        tx.accept()
        before = inventory(project)
        graph["nodes"][0]["parameters"]["roughness"] = 0.25
        graph["nodes"][1]["parameters"]["color"] = {
            "type": "Color",
            "r": 0,
            "g": 0,
            "b": 1,
            "a": 1,
        }
        tx = FileTransaction(project)
        tx.begin("Refresh an existing native material export")
        result = await edit_graph(tx.stage, "art/fixture.ptex", graph, "art/export")
        assert "roughness = 0.25" in (tx.stage / material).read_text()
        assert await pixel(tx.stage, texture) == [0, 0, 255, 255]
        assert (
            json.loads((tx.stage / "art/fixture.ptex").read_text())["nodes"][0]["name"]
            == "Material"
        )
        await verify_replay(tx, result)
        tx.review()
        tx.reject()
        assert inventory(project) == before
        tx = FileTransaction(project)
        tx.begin("Accept the material update")
        await edit_graph(tx.stage, "art/fixture.ptex", graph, "art/export")
        tx.review()
        tx.accept()
        assert "roughness = 0.25" in (project / material).read_text()
        assert await pixel(project, texture) == [0, 0, 255, 255]
        tx.revert()
        assert inventory(project) == before
        assert "roughness = 1.0" in (project / material).read_text()
        assert await pixel(project, texture) == [255, 0, 0, 255]
        print(
            "Material Maker red/blue texture + roughness/native export/clean replay/reject/accept/revert: PASS"
        )


async def main(app: str) -> None:
    for name, operation in (
        ("krita", krita),
        ("supercollider", supercollider),
        ("inkscape", inkscape),
        ("material-maker", material_maker),
    ):
        if app in {"all", name}:
            await operation()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "app",
        choices=["all", "krita", "supercollider", "inkscape", "material-maker"],
        default="all",
        nargs="?",
    )
    asyncio.run(main(parser.parse_args().app))
