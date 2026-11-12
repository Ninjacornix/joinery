"""Typed Krita layer, Material Maker graph and Inkscape element operations: the checks.

The real runs are tests/integration/typed_ops.py; these cover the checking and
the code each operation records, without the worker.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from joinery.tools import inkscape, krita_ops, mm_graph

# --- Krita layers ---------------------------------------------------------------------


def test_layer_ops_are_checked_and_normalised() -> None:
    ops = krita_ops.check_layer_ops(
        [
            {"op": "add", "name": " Sketch ", "kind": "group", "blend": "soft_light"},
            {"op": "fill_layer", "name": "Tint", "color": [1, 0.5, 0], "opacity": 0.5},
            {
                "op": "filter_layer",
                "name": "Soft",
                "filter": "blur",
                "params": {"radius": 2},
                "above": "Base",
            },
            {"op": "reorder", "layer": "Sketch", "position": "bottom"},
            {"op": "hide", "layer": "Sketch"},
            {"op": "blend", "layer": "Tint", "mode": "difference"},
            {"op": "duplicate", "layer": "Tint"},
        ]
    )
    assert ops[0] == {
        "op": "add",
        "name": "Sketch",
        "kind": "group",
        "mode": "soft_light",
    }
    assert ops[1]["color"] == [1.0, 0.5, 0.0] and ops[1]["opacity"] == 0.5
    assert ops[2]["filter"] == "blur" and ops[2]["params"] == {"radius": 2.0}
    assert ops[4]["hidden"] is True
    assert ops[5]["mode"] == "diff"  # Krita's own id
    assert ops[6]["name"] == "Tint copy"
    code = krita_ops.layer_code(ops)
    compile(code, "layer_code", "exec")
    assert "_joinery_layer_op(document, _row)" in code
    notes = [krita_ops.describe_layer_op(row) for row in ops]
    assert (
        notes[0] == "add group layer 'Sketch'"
        and notes[3] == "move 'Sketch' to the bottom"
    )


@pytest.mark.parametrize(
    ("op", "message"),
    [
        ({"op": "paint"}, "op is one of"),
        ({"op": "rename", "layer": "A"}, "needs to"),
        ({"op": "hide", "name": "A"}, "does not take name"),
        ({"op": "reorder", "layer": "A"}, "give above"),
        (
            {"op": "reorder", "layer": "A", "above": "B", "position": "top"},
            "give above",
        ),
        ({"op": "reorder", "layer": "A", "above": "A"}, "above itself"),
        ({"op": "blend", "layer": "A", "mode": "glow"}, "blend mode is one of"),
        ({"op": "opacity", "layer": "A"}, "needs an opacity"),
        ({"op": "opacity", "layer": "A", "opacity": 2}, "from 0 to 1"),
        ({"op": "add", "name": "A", "kind": "vector"}, "paint or group"),
        ({"op": "add", "name": "A", "above": "B", "into": "C"}, "not both"),
        ({"op": "fill_layer", "name": "A", "color": [1, 0, 0, 1]}, "three numbers"),
        ({"op": "filter_layer", "name": "A", "filter": "sharpen"}, "filter is one of"),
        ({"op": "delete", "layer": "x" * 64}, "1 to 63"),
    ],
)
def test_bad_layer_ops_are_refused(op: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        krita_ops.check_layer_ops([op])


def test_operate_layer_refuses_anything_but_a_krita_painting(tmp_path: Path) -> None:
    from servers.paint import ops

    with pytest.raises(ValueError, match="under art/"):
        asyncio.run(
            ops.operate_layer(tmp_path, "art/a.png", [{"op": "delete", "layer": "A"}])
        )
    with pytest.raises(ValueError, match="missing"):
        asyncio.run(
            ops.operate_layer(tmp_path, "art/a.kra", [{"op": "delete", "layer": "A"}])
        )


# --- Inkscape elements -----------------------------------------------------------------

SVG = """<?xml version="1.0"?>
<!-- kept -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10">
  <defs><linearGradient id="g"/></defs>
  <rect id="r" style="fill:#000000;stroke:#ffffff" width="5" height="5"/>
  <!-- a note -->
  <text id="t"><tspan id="s">Hi</tspan></text>
  <use id="u" xlink:href="#r"/>
</svg>
"""


def _root(svg: str) -> ElementTree.Element:
    return ElementTree.fromstring(svg.split("\n", 2)[2])


def test_edit_element_changes_one_element_and_keeps_the_rest() -> None:
    svg, changed = inkscape.edit_element(
        SVG,
        "r",
        attributes={"width": 7, "inkscape:label": "Plate", "height": None},
        style={"fill": "url(#g)", "stroke": None, "opacity": 0.5},
        transform="rotate(45, 5 5) translate(1)",
    )
    assert svg.startswith('<?xml version="1.0"?>\n<!-- kept -->\n<svg ')
    assert "<!-- a note -->" in svg and "ns0" not in svg
    rect = _root(svg).find("{http://www.w3.org/2000/svg}rect")
    assert rect is not None
    assert rect.get("width") == "7" and rect.get("height") is None
    assert rect.get("{http://www.inkscape.org/namespaces/inkscape}label") == "Plate"
    assert rect.get("style") == "fill:url(#g);opacity:0.5"
    assert rect.get("transform") == "rotate(45, 5 5) translate(1)"
    assert changed["height"] is None
    svg, _ = inkscape.edit_element(svg, "s", text="Hello & <bye>")
    tspan = next(e for e in _root(svg).iter() if e.get("id") == "s")
    assert tspan.text == "Hello & <bye>"
    svg, _ = inkscape.edit_element(svg, "r", clear_transform=True)
    assert "transform" not in svg


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"element_id": "nope", "text": "x"}, "no element with id"),
        ({"element_id": "r"}, "Give attributes"),
        ({"element_id": "r", "attributes": {"onload": "x"}}, "event handlers"),
        ({"element_id": "r", "attributes": {"id": "q"}}, "keeps its id"),
        ({"element_id": "r", "attributes": {"style": "x"}}, "style field"),
        ({"element_id": "r", "attributes": {"sodipodi:type": "x"}}, "no namespace"),
        (
            {"element_id": "u", "attributes": {"xlink:href": "other.svg#a"}},
            "inside this document",
        ),
        ({"element_id": "r", "attributes": {"fill": "url(file.png)"}}, "url\\(#id\\)"),
        ({"element_id": "r", "attributes": {"fill": "javascript:x"}}, "script"),
        ({"element_id": "r", "style": {"fill": "red;stroke:blue"}}, "cannot contain"),
        ({"element_id": "r", "style": {"bad prop": "x"}}, "not a style property"),
        ({"element_id": "r", "transform": "rotate(1, 2)"}, "rotate\\(\\) takes 1 or 3"),
        ({"element_id": "r", "transform": "perspective(3)"}, "transform is a list"),
        ({"element_id": "r", "text": "x"}, "is a <rect>"),
        ({"element_id": "t", "text": "x"}, "holds other elements"),
    ],
)
def test_edit_element_refusals(kwargs: dict[str, Any], message: str) -> None:
    element_id = kwargs.pop("element_id")
    with pytest.raises(ValueError, match=message):
        inkscape.edit_element(SVG, element_id, **kwargs)


def test_edit_element_refuses_external_documents() -> None:
    with pytest.raises(ValueError, match="self-contained"):
        inkscape.edit_element(
            SVG.replace("<defs>", '<!DOCTYPE x [<!ENTITY e "x">]><defs>'),
            "r",
            attributes={"x": 1},
        )


# --- Material Maker graphs -------------------------------------------------------------

LIBRARY: dict[str, Any] = {
    "perlin": {
        "label": "Value Noise",
        "inputs": 0,
        "outputs": 1,
        "parameters": {
            "scale_x": {"type": "float", "min": 1, "max": 32, "step": 1},
            "iterations": {"type": "float", "min": 1, "max": 10},
        },
    },
    "material": {
        "label": "Material",
        "inputs": 9,
        "outputs": 0,
        "parameters": {
            "size": {"type": "size", "first": 4, "last": 13},
            "albedo_color": {"type": "color"},
            "flags_transparent": {"type": "boolean"},
        },
    },
}
GRAPH: dict[str, Any] = {
    "type": "graph",
    "nodes": [
        {
            "name": "Material",
            "type": "material",
            "parameters": {"size": 9},
            "node_position": {"x": 0, "y": 0},
        },
        {
            "name": "Noise",
            "type": "perlin",
            "parameters": {"scale_x": 4},
            "node_position": {"x": -300, "y": 50},
        },
        {
            "name": "Custom",
            "type": "shader",
            "parameters": {"mode": 0},
            "shader_model": {
                "name": "Custom",
                "inputs": [{"name": "in"}],
                "outputs": [{"type": "f"}],
                "parameters": [
                    {
                        "name": "mode",
                        "type": "enum",
                        "values": [
                            {"name": "A", "value": "a"},
                            {"name": "B", "value": "b"},
                        ],
                    }
                ],
            },
        },
        {"name": "Img", "type": "image", "parameters": {"image": "art/a.png"}},
    ],
    "connections": [{"from": "Noise", "from_port": 0, "to": "Material", "to_port": 2}],
}


def test_set_parameters_checks_against_definitions() -> None:
    changed, checked = mm_graph.set_parameters(
        GRAPH,
        "Material",
        {"size": 10, "albedo_color": [0.1, 0.2, 0.3], "flags_transparent": True},
        LIBRARY,
    )
    assert checked["albedo_color"] == {
        "a": 1.0,
        "b": 0.3,
        "g": 0.2,
        "r": 0.1,
        "type": "Color",
    }
    assert changed["nodes"][0]["parameters"]["size"] == 10
    assert GRAPH["nodes"][0]["parameters"]["size"] == 9, "the original is untouched"
    # A node's own shader_model is its definition; enums take an index or a name.
    _, checked = mm_graph.set_parameters(GRAPH, "Custom", {"mode": "B"}, {})
    assert checked == {"mode": 1}
    for node, given, message in [
        ("Noise", {"scale_x": 64}, "from 1 to 32"),
        ("Noise", {"scale_x": "4"}, "is a number"),
        ("Material", {"size": 2}, "from 4 to 13"),
        ("Material", {"flags_transparent": 1}, "true or false"),
        ("Material", {"albedo_color": [2, 0, 0]}, "from 0 to 1"),
        ("Custom", {"mode": 5}, "one of"),
        ("Img", {"image": "art/b.png"}, "wrap_image"),
        ("Nope", {"x": 1}, "no node 'Nope'"),
        ("Noise", {"seed": 1}, "no parameter 'seed'"),
    ]:
        with pytest.raises(ValueError, match=message):
            mm_graph.set_parameters(GRAPH, node, given, LIBRARY)


def test_wrap_image_adds_a_node_and_rewires_a_port() -> None:
    changed, name = mm_graph.wrap_image(
        GRAPH,
        "art/textures/rock height.png",
        LIBRARY,
        connect=[{"node": "Material", "port": 2}],
    )
    assert name == "rock_height"
    added = changed["nodes"][-1]
    assert added["type"] == "image" and added["parameters"] == {
        "image": "art/textures/rock height.png"
    }
    assert changed["connections"] == [
        {"from": "rock_height", "from_port": 0, "to": "Material", "to_port": 2}
    ]
    with pytest.raises(ValueError, match="input ports 0 to 8"):
        mm_graph.wrap_image(
            GRAPH, "art/a.png", LIBRARY, connect=[{"node": "Material", "port": 9}]
        )
    with pytest.raises(ValueError, match="already has a node"):
        mm_graph.wrap_image(GRAPH, "art/a.png", LIBRARY, name="Noise")
    with pytest.raises(ValueError, match="file"):
        mm_graph.wrap_image(GRAPH, "art/a.txt", LIBRARY)


def test_library_definitions_are_read_like_the_worker_reads_them(
    tmp_path: Path,
) -> None:
    """The worker script normalises .mmg files, subgraph controls included."""
    (tmp_path / "perlin.mmg").write_text(
        json.dumps(
            {
                "shader_model": {
                    "name": "Value Noise",
                    "inputs": [],
                    "outputs": [{}],
                    "parameters": [
                        {"name": "scale_x", "type": "float", "min": 1, "max": 32}
                    ],
                }
            }
        ).replace("]}}", "],}}")  # Material Maker allows trailing commas
    )
    (tmp_path / "normal_map2.mmg").write_text(
        json.dumps(
            {
                "type": "graph",
                "nodes": [
                    {"name": "gen_inputs", "type": "ios", "ports": [{}]},
                    {"name": "gen_outputs", "type": "ios", "ports": [{}]},
                    {
                        "name": "edge",
                        "type": "shader",
                        "shader_model": {
                            "parameters": [
                                {"name": "amount", "type": "float", "min": 0, "max": 2}
                            ]
                        },
                    },
                    {"name": "noise", "type": "perlin"},
                    {
                        "name": "gen_parameters",
                        "type": "remote",
                        "widgets": [
                            {
                                "name": "strength",
                                "type": "linked_control",
                                "linked_widgets": [
                                    {"node": "edge", "widget": "amount"}
                                ],
                            },
                            {
                                "name": "scale",
                                "type": "linked_control",
                                "linked_widgets": [
                                    {"node": "noise", "widget": "scale_x"}
                                ],
                            },
                            {
                                "name": "buffer",
                                "type": "config_control",
                                "configurations": {"False": [], "True": []},
                            },
                        ],
                    },
                ],
            }
        )
    )
    script = mm_graph.WORKER_SCRIPT.replace(
        repr(mm_graph.NODES_DIR), repr(str(tmp_path))
    )
    import contextlib
    import io
    import sys

    out = io.StringIO()
    argv = sys.argv
    sys.argv = ["-c", json.dumps(["normal_map2", "perlin", "missing", "../etc"])]
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(script, "worker", "exec"), {"__name__": "__main__"})  # noqa: S102 - our own script
    finally:
        sys.argv = argv
    found = mm_graph.read_worker_output(out.getvalue())
    assert set(found) == {"normal_map2", "perlin"}
    normal = found["normal_map2"]
    assert normal["inputs"] == 1 and normal["outputs"] == 1
    assert normal["parameters"]["strength"]["max"] == 2
    assert normal["parameters"]["scale"]["max"] == 32
    assert normal["parameters"]["buffer"] == {
        "type": "enum",
        "label": "buffer",
        "values": ["False", "True"],
    }


def test_export_directory_is_where_the_exports_sit() -> None:
    outputs = ("art/m/a.ptex", "art/m/export/a_albedo.png", "art/m/export/a.tres")
    assert mm_graph.export_directory(outputs, "art/m/a.ptex") == "art/m/export"
    with pytest.raises(ValueError, match="output_directory"):
        mm_graph.export_directory(("art/m/a.ptex",), "art/m/a.ptex")
