"""Typed Krita layer, Material Maker graph and Inkscape element operations, in the real worker.

Through the joinery-paint and joinery-materialmaker MCP servers exactly as an
agent calls them, in a disposable staged project:

* krita: operate_layer adds paint, group, fill and filter layers, renames,
  reorders, hides, sets opacity and blending, duplicates, merges and deletes
  named layers; the saved .kra has exactly that stack, a missing layer is
  refused with the layer list, and the recipe replays to the same fingerprint;
* material-maker: list_graph shows the nodes, their definitions from Material
  Maker's own library and the connections; set_node_parameters changes a
  colour, a float and a size and refuses an out-of-range value; wrap_image
  brings a recorded project image in and wires it into the material; both
  replay;
* inkscape: edit_vector_element changes one element's style, attribute,
  transform and text, keeps everything else, refuses an event handler and an
  outside link, and the recipe replays byte for byte.

Technical fixtures only.
Run: `uv run python tests/integration/typed_ops.py krita|material-maker|inkscape`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from joinery.authoring import edit_document, edit_graph, edit_vector, inspect_document
from joinery.catalog import load_catalog
from joinery.lockfile import check_assets
from joinery.recipe import load_recipe, rebuild_one
from joinery.runner import SandboxRunner
from joinery.transactions import FileTransaction

PAINTING, PAINTING_PNG = "art/painting.kra", "art/painting.png"
# A 32x32 painting: a blue Base layer and a red Ink stroke layer.
PAINTING_CODE = """
document = app.createDocument(32, 32, "Painting", "RGBA", "U8", "", 72.0)
root = document.rootNode()
for node in root.childNodes():
    node.remove()
base = document.createNode("Base", "paintlayer")
root.addChildNode(base, None)
base.setPixelData(bytes([200, 80, 20, 255]) * (32 * 32), 0, 0, 32, 32)
ink = document.createNode("Ink", "paintlayer")
root.addChildNode(ink, None)
ink.setPixelData(bytes([0, 0, 220, 255]) * (8 * 32), 12, 0, 8, 32)
"""


async def _call(stage: Path, family: str, tool: str, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    from servers.authoring import make_server

    async with Client(make_server(stage, family)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text if result.content else ""
    return result.is_error, (json.loads(text) if not result.is_error else text)


def _png(pixels: list[tuple[int, int, int]], size: int = 8) -> bytes:
    rows = b"".join(
        b"\x00" + b"".join(bytes(pixels[(x + y) % len(pixels)]) for x in range(size))
        for y in range(size)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _replays(stage: Path, recipe: str) -> None:
    rebuilt = rebuild_one(stage / recipe, stage, SandboxRunner())
    assert rebuilt.ok, rebuilt.problems


async def krita(stage: Path) -> str:
    await edit_document(stage, "krita", PAINTING, PAINTING_CODE, [PAINTING_PNG])
    before = (stage / PAINTING_PNG).read_bytes()
    ops = [
        {"op": "add", "name": "Sketch"},
        {"op": "add", "name": "Shading", "kind": "group", "above": "Base"},
        {
            "op": "fill_layer",
            "name": "Tint",
            "color": [1.0, 0.8, 0.0],
            "opacity": 0.5,
            "blend": "multiply",
            "into": "Shading",
        },
        {
            "op": "filter_layer",
            "name": "Soften",
            "filter": "blur",
            "params": {"radius": 2},
            "above": "Ink",
        },
        {"op": "duplicate", "layer": "Ink", "name": "Ink copy"},
        {"op": "add", "name": "Scratch", "above": "Ink"},
        {"op": "merge", "layer": "Scratch"},
        {"op": "reorder", "layer": "Ink copy", "position": "bottom"},
        {"op": "reorder", "layer": "Sketch", "above": "Base"},
        {"op": "hide", "layer": "Sketch"},
        {"op": "opacity", "layer": "Ink", "opacity": 0.4},
        {"op": "blend", "layer": "Ink", "mode": "screen"},
        {"op": "rename", "layer": "Ink", "to": "Lines"},
        {"op": "delete", "layer": "Ink copy"},
    ]
    failed, got = await _call(
        stage, "joinery-paint", "operate_layer", {"source": PAINTING, "ops": ops}
    )
    assert not failed, got
    recipe = load_recipe(stage / got["recipe"])
    assert "rename 'Ink' to 'Lines'" in recipe.note and recipe.outputs == (
        PAINTING,
        PAINTING_PNG,
    ), recipe
    layers = (await inspect_document(stage, "krita", PAINTING))["layers"]
    # maindoc.xml lists each level top first; a group's children follow it.
    order = [layer["name"] for layer in layers]
    assert order == ["Soften", "Lines", "Shading", "Tint", "Sketch", "Base"], order
    by = {layer["name"]: layer for layer in layers}
    assert by["Sketch"]["visible"] == "0"
    assert by["Lines"]["compositeop"] == "screen" and by["Lines"]["opacity"] == str(
        round(0.4 * 255)
    )
    assert (
        by["Tint"]["nodetype"] == "generatorlayer"
        and by["Tint"]["compositeop"] == "multiply"
    )
    assert by["Soften"]["nodetype"] == "adjustmentlayer"
    assert (stage / PAINTING_PNG).read_bytes() != before, (
        "the export must show the new stack"
    )
    _replays(stage, got["recipe"])
    # A missing layer stops the edit before Krita saves anything.
    saved = (stage / PAINTING).read_bytes()
    failed, text = await _call(
        stage,
        "joinery-paint",
        "operate_layer",
        {"source": PAINTING, "ops": [{"op": "hide", "layer": "Nope"}]},
    )
    assert failed and "no layer 'Nope'" in text and "Lines" in text, text
    assert (stage / PAINTING).read_bytes() == saved
    failed, text = await _call(
        stage,
        "joinery-paint",
        "operate_layer",
        {
            "source": PAINTING,
            "ops": [{"op": "blend", "layer": "Lines", "mode": "glow"}],
        },
    )
    assert failed and "blend mode is one of" in text, text
    return f"krita: {len(ops)} layer operations, stack {order}"


GRAPH = "art/materials/tiles.ptex"
EXPORTS = "art/materials/tiles"
HEIGHT = "art/imported/tiles_height.png"


async def material_maker(stage: Path) -> str:
    data = _png([(0, 0, 0), (255, 255, 255), (128, 128, 128)])
    (stage / HEIGHT).parent.mkdir(parents=True)
    (stage / HEIGHT).write_bytes(data)
    (stage / "joinery.lock").write_text(
        "version = 1\n\n[[asset]]\n"
        f'path = "{HEIGHT}"\nsha256 = "{hashlib.sha256(data).hexdigest()}"\n'
        'origin = "human-import"\nimported_by = "integration fixture"\nlicence = "test fixture"\n'
    )
    graph = {
        "type": "graph",
        "name": "Tiles",
        "nodes": [
            {
                "type": "material",
                "name": "Material",
                "parameters": {"size": 5, "roughness": 1.0},
                "node_position": {"x": 0, "y": 0},
            },
            {
                "type": "uniform",
                "name": "Paint",
                "parameters": {
                    "color": {"r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0, "type": "Color"}
                },
                "node_position": {"x": -300, "y": 0},
            },
            {
                "type": "perlin",
                "name": "Noise",
                "parameters": {
                    "iterations": 3,
                    "persistence": 0.5,
                    "scale_x": 4,
                    "scale_y": 4,
                },
                "node_position": {"x": -300, "y": 200},
            },
        ],
        "connections": [
            {"from": "Paint", "from_port": 0, "to": "Material", "to_port": 0},
            {"from": "Noise", "from_port": 0, "to": "Material", "to_port": 2},
        ],
    }
    await edit_graph(stage, GRAPH, graph, EXPORTS)
    failed, listed = await _call(
        stage, "joinery-materialmaker", "list_graph", {"source": GRAPH}
    )
    assert not failed, listed
    nodes = {n["name"]: n for n in listed["nodes"]}
    assert nodes["Noise"]["definitions"]["scale_x"]["max"] == 32, nodes["Noise"]
    assert (
        nodes["Material"]["inputs"] == 9
        and "roughness" in nodes["Material"]["definitions"]
    )
    assert {"from": "Noise", "from_port": 0, "to": "Material", "to_port": 2} in listed[
        "connections"
    ]
    albedo = stage / EXPORTS / "tiles_albedo.png"
    before = albedo.read_bytes()
    failed, got = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {"source": GRAPH, "node": "Paint", "parameters": {"color": [0.8, 0.3, 0.1]}},
    )
    assert not failed, got
    assert albedo.read_bytes() != before, "the albedo must show the new colour"
    failed, got = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {
            "source": GRAPH,
            "node": "Material",
            "parameters": {"size": 6, "roughness": 0.7},
        },
    )
    assert not failed, got
    width = struct.unpack(">I", albedo.read_bytes()[16:20])[0]
    assert width == 64, width
    failed, text = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {"source": GRAPH, "node": "Material", "parameters": {"size": 6.5}},
    )
    assert failed and "whole number" in text, text
    failed, got = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {
            "source": GRAPH,
            "node": "Noise",
            "parameters": {"scale_x": 8, "iterations": 5},
        },
    )
    assert not failed, got
    stored = json.loads((stage / GRAPH).read_text())
    noise = next(n for n in stored["nodes"] if n["name"] == "Noise")
    assert (
        noise["parameters"]["scale_x"] == 8 and noise["parameters"]["iterations"] == 5
    )
    assert "scale_x" in load_recipe(stage / got["recipe"]).note
    _replays(stage, got["recipe"])
    failed, text = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {"source": GRAPH, "node": "Noise", "parameters": {"scale_x": 400}},
    )
    assert failed and "from 1 to 32" in text, text
    failed, text = await _call(
        stage,
        "joinery-materialmaker",
        "set_node_parameters",
        {"source": GRAPH, "node": "Noise", "parameters": {"wobble": 1}},
    )
    assert failed and "no parameter 'wobble'" in text, text
    failed, got = await _call(
        stage,
        "joinery-materialmaker",
        "wrap_image",
        {
            "source": GRAPH,
            "image": "res://" + HEIGHT,
            "name": "Height",
            "connect": [{"node": "Material", "port": 6}],
        },
    )
    assert not failed, got
    recipe = load_recipe(stage / got["recipe"])
    assert HEIGHT in recipe.sources, recipe.sources
    stored = json.loads((stage / GRAPH).read_text())
    height = next(n for n in stored["nodes"] if n["name"] == "Height")
    assert height["parameters"]["image"] == HEIGHT
    assert {"from": "Height", "from_port": 0, "to": "Material", "to_port": 6} in stored[
        "connections"
    ]
    assert not check_assets(stage, load_catalog())
    _replays(stage, got["recipe"])
    failed, text = await _call(
        stage,
        "joinery-materialmaker",
        "wrap_image",
        {"source": GRAPH, "image": "../outside.png"},
    )
    assert failed and "under art/" in text, text
    return "material-maker: list/set (colour, float, size)/wrap_image recorded and replayed"


SVG_SOURCE, SVG_PNG = "art/ui/badge.svg", "art/ui/badge.png"
SVG = """<?xml version="1.0" encoding="UTF-8"?>
<!-- A badge -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="64" height="64" viewBox="0 0 64 64">
  <rect id="plate" x="4" y="4" width="56" height="56" style="fill:#336699;stroke:#000000;stroke-width:2"/>
  <circle id="dot" cx="32" cy="32" r="8" fill="#ffffff" inkscape:label="Dot"/>
  <text id="label" x="32" y="58" font-size="8" text-anchor="middle">OLD</text>
</svg>
"""


async def inkscape(stage: Path) -> str:
    await edit_vector(stage, SVG_SOURCE, SVG, SVG_PNG)
    before = (stage / SVG_PNG).read_bytes()
    failed, got = await _call(
        stage,
        "joinery-paint",
        "edit_vector_element",
        {
            "source": SVG_SOURCE,
            "element_id": "plate",
            "style": {"fill": "#aa3311", "stroke": None},
            "attributes": {"rx": 6},
        },
    )
    assert not failed, got
    failed, got = await _call(
        stage,
        "joinery-paint",
        "edit_vector_element",
        {
            "source": SVG_SOURCE,
            "element_id": "dot",
            "transform": "translate(4, -2) scale(1.5)",
            "attributes": {"inkscape:label": "Big dot"},
        },
    )
    assert not failed, got
    failed, got = await _call(
        stage,
        "joinery-paint",
        "edit_vector_element",
        {"source": SVG_SOURCE, "element_id": "label", "text": "NEW"},
    )
    assert not failed, got
    root = ElementTree.fromstring((stage / SVG_SOURCE).read_bytes())
    ns = {"svg": "http://www.w3.org/2000/svg"}
    plate = root.find("svg:rect", ns)
    dot = root.find("svg:circle", ns)
    label = root.find("svg:text", ns)
    assert plate is not None and dot is not None and label is not None
    assert (
        plate.get("style") == "fill:#aa3311;stroke-width:2" and plate.get("rx") == "6"
    ), plate.attrib
    assert dot.get("transform") == "translate(4, -2) scale(1.5)"
    assert dot.get("{http://www.inkscape.org/namespaces/inkscape}label") == "Big dot"
    assert label.text == "NEW" and label.get("font-size") == "8"
    text = (stage / SVG_SOURCE).read_text()
    assert text.startswith(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- A badge -->\n<svg '
    )
    assert "ns0:" not in text and "xmlns:inkscape=" in text, text
    assert (stage / SVG_PNG).read_bytes() != before
    assert "NEW" in load_recipe(stage / got["recipe"]).note
    _replays(stage, got["recipe"])
    failed, text = await _call(
        stage,
        "joinery-paint",
        "edit_vector_element",
        {
            "source": SVG_SOURCE,
            "element_id": "dot",
            "attributes": {"onclick": "alert(1)"},
        },
    )
    assert failed and "event handlers" in text, text
    failed, text = await _call(
        stage,
        "joinery-paint",
        "edit_vector_element",
        {
            "source": SVG_SOURCE,
            "element_id": "plate",
            "style": {"fill": "url(https://example.com/x)"},
        },
    )
    assert failed, text
    return "inkscape: style/attribute/transform/text on one element each, recorded and replayed"


async def main(app: str) -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-typed-ops-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text(
            '[tools]\nenabled=["godot", "krita", "inkscape", "material-maker"]\n'
        )
        (project / "joinery.lock").write_text("version = 1\n")
        tx = FileTransaction(project)
        tx.begin("Typed operation fixture")
        run = {"krita": krita, "material-maker": material_maker, "inkscape": inkscape}[
            app
        ]
        line = await run(tx.stage)
        tx.reject()
        print(line + ": PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=["krita", "material-maker", "inkscape"])
    asyncio.run(main(parser.parse_args().app))
