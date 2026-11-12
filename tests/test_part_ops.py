"""Map sources, material copies, bones and shape keys as typed part operations.

The applications are replaced at their boundary: these tests check argument
validation, the part selection, the code each operation hands Blender or Krita,
and what the recipe records. tests/integration/part_edits.py runs the
same operations in the real workers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from joinery import parts
from joinery.recipe import store_preimage
from joinery.tools import (
    blender,
    blender_parts,
    krita_ops,
    material_maker,
)

FIXTURE = Path(__file__).parent / "fixtures/parts/parts.json"
SOURCE = "art/sources/hammer.blend"
MODEL = "art/models/hammer.glb"


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "tx" / "stage"
    stage.mkdir(parents=True)
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled=["blender","krita","material-maker"]\n'
    )
    return stage


# --- typed Krita layers ----------------------------------------------------


def test_paint_ops_become_krita_layers() -> None:
    ops = krita_ops.check_ops(
        [
            {"op": "fill", "color": [1, 0, 0], "rect": [0, 0, 8, 4], "name": "Rust"},
            {
                "op": "stroke",
                "points": [[0, 0], [10, 10]],
                "color": [0, 0, 0, 0.5],
                "width": 3,
            },
            {
                "op": "adjust",
                "filter": "levels",
                "params": {"in_low": 0.1, "gamma": 1.2},
            },
        ]
    )
    assert [row["name"] for row in ops] == ["Rust", "Stroke 2", "Adjust 3"]
    code = krita_ops.paint_code(ops)
    assert "_joinery_fill(document, 'Rust', [1.0, 0.0, 0.0], 1.0, [0, 0, 8, 4])" in code
    assert "createVectorLayer" in code and 'stroke-linecap="round"' in code
    assert "createFilterLayer" in code and "'levels'" in code
    assert code.index("_joinery_fill(document") < code.index(
        "_joinery_stroke(document, "
    )
    seed = krita_ops.seed_code("recipes/inputs/ab.png")
    assert "openDocument(os.path.abspath('recipes/inputs/ab.png'))" in seed
    assert "setName('Map')" in seed


@pytest.mark.parametrize(
    ("ops", "reason"),
    [
        ([], "1 to 32"),
        ([{"op": "smudge"}], "op is one of"),
        ([{"op": "fill"}], "needs a color"),
        ([{"op": "fill", "color": [2, 0, 0]}], "0 to 1"),
        ([{"op": "fill", "color": [1, 0, 0], "rect": [0, 0, 0, 4]}], "whole pixels"),
        ([{"op": "stroke", "color": [1, 0, 0], "points": [[0, 0]]}], "2 to 512"),
        (
            [
                {
                    "op": "stroke",
                    "color": [1, 0, 0],
                    "points": [[0, 0], [1, 1]],
                    "width": 0,
                }
            ],
            "width",
        ),
        ([{"op": "adjust", "filter": "emboss"}], "filter is one of"),
        (
            [{"op": "adjust", "filter": "blur", "params": {"amount": 2}}],
            "does not take amount",
        ),
        ([{"op": "adjust", "filter": "levels", "params": {"gamma": 0}}], "above 0"),
        ([{"op": "fill", "color": [1, 0, 0], "brush": "x"}], "does not take brush"),
    ],
)
def test_paint_ops_are_refused_before_krita_starts(ops: list[Any], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        krita_ops.check_ops(ops)


# --- Blender part operation code -------------------------------------------


def test_each_blender_part_op_is_scope_guarded_to_its_part() -> None:
    link = blender_parts.link_map_code("Steel", "steel_rough", "art/x.png", "art/x.kra")
    assert "set(['image:steel_rough']) | _joinery_also" in link
    assert "_joinery_link_map('Steel', 'steel_rough', 'art/x.png', 'art/x.kra')" in link
    body = link[link.index("def _joinery_link_map") :]
    assert body.index("colorspace_settings.name = space") < body.index(
        "pixels.foreach_set"
    )
    name, images = blender_parts.split_names("Steel", ["Head.001"], ["steel_rough"])
    assert (name, images) == (
        "Steel_Head.001",
        {"steel_rough": "steel_rough_Steel_Head.001"},
    )
    split = blender_parts.split_material_code("Steel", ["Head.001"], name, images)
    assert (
        "set(['image:steel_rough_Steel_Head.001', 'material:Steel_Head.001', "
        "'object:Head.001'])" in split
    )
    shared = blender_parts.split_material_code("Steel", ["Head.001"], name, None)
    assert "set(['material:Steel_Head.001', 'object:Head.001'])" in shared
    bone = blender_parts.edit_bone_code("Rig", "Mid", "rename", {"name": "Elbow"})
    assert "set(['object:Rig'])" in bone and "_joinery_also.add('object:'" in bone
    key = blender_parts.edit_shape_key_code(
        "Head.001", "Dent", "set_value", {"value": 0.5}
    )
    assert "set(['object:Head.001'])" in key
    # Armature bones are part of the scope guard's object hash.
    assert "obj.type == 'ARMATURE'" in blender.SCOPE_GUARD


@pytest.mark.parametrize(
    ("ops", "op", "params", "reason"),
    [
        (blender_parts.BONE_OPS, "rename", {}, "needs a name"),
        (blender_parts.BONE_OPS, "rename", {"name": ""}, "1 to 63"),
        (blender_parts.BONE_OPS, "set_rest", {}, "head, tail or roll"),
        (blender_parts.BONE_OPS, "set_rest", {"head": [0, 0]}, "three numbers"),
        (blender_parts.BONE_OPS, "set_parent", {}, "needs a parent"),
        (blender_parts.BONE_OPS, "scale_chain", {"factor": 0}, "0.01 to 100"),
        (blender_parts.BONE_OPS, "twist", {}, "Unknown bone operation"),
        (blender_parts.SHAPE_KEY_OPS, "set_value", {}, "needs a value"),
        (blender_parts.SHAPE_KEY_OPS, "delete", {"value": 1}, "does not take value"),
    ],
)
def test_bone_and_shape_key_params_are_checked(
    ops: dict[str, frozenset[str]], op: str, params: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        blender_parts.check_params(ops, op, params, "bone" if "rename" in ops else "x")


def test_bone_params_come_back_as_plain_values() -> None:
    checked = blender_parts.check_params(
        blender_parts.BONE_OPS,
        "set_rest",
        {"head": [0, 0, 1], "roll_degrees": 90},
        "bone",
    )
    assert checked == {"head": [0.0, 0.0, 1.0], "roll_degrees": 90.0}
    assert blender_parts.check_params(
        blender_parts.BONE_OPS, "set_parent", {"parent": None}, "bone"
    ) == {"parent": None}


# --- the parts list routes each part to its operations ---------------------


def _report(editable: str = "") -> parts.PartsReport:
    data = json.loads(FIXTURE.read_text())
    for material in data["materials"]:
        for image in material["images"]:
            if image["image"] == "steel_rough":
                image["editable"] = editable
    return parts.build_report(data, "blender", SOURCE, "res://" + MODEL, "abc")


def test_each_part_lists_the_operations_that_change_it() -> None:
    report = _report()
    rough = report.find({"material": "Steel", "image": "steel_rough"})
    assert (
        rough.target()["ops"]
        == "adjust_map,make_map_editable,replace_map,split_material"
    )
    height = report.find({"material": "Steel", "image": "steel_height"})
    assert "bake_height_to_normal" in height.ops
    assert report.find({"material": "Steel"}).ops == (
        "set_material_value",
        "split_material",
    )
    assert report.find({"object": "Head.001", "shape_key": "Dent"}).ops == (
        "edit_shape_key",
    )
    painted = _report("art/sources/maps/hammer/steel_rough.kra")
    target = painted.find({"material": "Steel", "image": "steel_rough"}).target()
    assert target["editable"] == "art/sources/maps/hammer/steel_rough.kra"
    assert "paint_map" in target["ops"] and "make_map_editable" not in target["ops"]


# --- the MCP operations ----------------------------------------------------


def _project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editable: str = ""
) -> tuple[Path, AsyncMock]:
    stage = _stage(tmp_path)
    (stage / "recipes").mkdir()
    (stage / "recipes/hammer.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/hammer.py"\n'
        f'outputs = ["{SOURCE}", "{MODEL}", "art/textures/hammer/steel_rough.png"]\n'
    )
    (stage / "art/textures/hammer").mkdir(parents=True)
    (stage / "art/textures/hammer/steel_rough.png").write_bytes(b"\x89PNGpixels")

    async def report(_project: Path, _asset: str, **_kw: Any) -> parts.PartsReport:
        return _report(editable)

    monkeypatch.setattr(parts, "report", report)
    edit = AsyncMock(
        side_effect=lambda *a, **k: {
            "status": "staged",
            "recipe": "recipes/x.toml",
            "outputs": list(a[4]),
        }
    )
    monkeypatch.setattr("servers.blender_ops.ops.edit_document", edit)
    monkeypatch.setattr("servers.authoring.edit_document", edit)
    return stage, edit


def _call(stage: Path, tool: str, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    from servers.authoring import make_server

    async def run() -> Any:
        async with Client(make_server(stage, "joinery-blender-ops")) as client:
            return await client.call_tool(tool, {"source": SOURCE, **arguments})

    return asyncio.run(run())


def _pick(stage: Path, **fields: str) -> None:
    parts.write_targets(
        stage,
        [
            {
                "part_kind": fields.pop("part_kind"),
                "owner_source": SOURCE,
                "label": "picked",
                **fields,
            }
        ],
    )


def test_make_map_editable_seeds_krita_from_the_map_and_links_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    result = _call(
        stage, "make_map_editable", {"material": "Steel", "image": "steel_rough"}
    )
    assert not result.is_error, result.content[0].text
    krita, linked = edit.await_args_list
    seed = store_preimage(stage, stage / "art/textures/hammer/steel_rough.png")
    assert krita.args[1:3] == ("krita", "art/sources/maps/hammer/steel_rough.kra")
    assert krita.args[4] == ["art/sources/maps/hammer/steel_rough.png"]
    assert krita.kwargs["inputs"] == (seed,) and seed in krita.args[3]
    assert linked.args[1:3] == ("blender", SOURCE)
    assert linked.kwargs["inputs"] == ("art/sources/maps/hammer/steel_rough.png",)
    assert (
        "_joinery_link_map('Steel', 'steel_rough', 'art/sources/maps/hammer/steel_rough.png', 'art/sources/maps/hammer/steel_rough.kra')"
        in linked.args[3]
    )
    assert linked.args[4] == [MODEL]


def test_paint_map_needs_a_krita_source_and_records_both_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    ops = [{"op": "fill", "color": [1, 1, 1]}]
    refused = _call(
        stage, "paint_map", {"material": "Steel", "image": "steel_rough", "ops": ops}
    )
    assert refused.is_error and "make_map_editable first" in refused.content[0].text
    kra = "art/sources/maps/hammer/steel_rough.kra"
    stage, edit = _project(tmp_path / "b", monkeypatch, kra)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    (stage / kra).parent.mkdir(parents=True)
    (stage / kra).write_bytes(b"kra")
    result = _call(
        stage, "paint_map", {"material": "Steel", "image": "steel_rough", "ops": ops}
    )
    assert not result.is_error, result.content[0].text
    painted, linked = edit.await_args_list
    assert (
        painted.args[1:3] == ("krita", kra)
        and "_joinery_fill(document" in painted.args[3]
    )
    assert "inputs" not in painted.kwargs
    assert linked.kwargs["inputs"] == ("art/sources/maps/hammer/steel_rough.png",)


def test_a_failed_link_puts_the_krita_step_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    kra = stage / "art/sources/maps/hammer/steel_rough.kra"

    async def fake(*args: Any, **_kw: Any) -> dict[str, Any]:
        if args[1] == "krita":
            kra.parent.mkdir(parents=True)
            kra.write_bytes(b"kra")
            (stage / "recipes/krita.toml").write_text("made")
            return {"status": "staged"}
        raise RuntimeError("Blender stopped")

    edit.side_effect = fake
    result = _call(
        stage, "make_map_editable", {"material": "Steel", "image": "steel_rough"}
    )
    assert result.is_error and "Blender stopped" in result.content[0].text
    assert not kra.exists() and not (stage / "recipes/krita.toml").exists()
    assert (stage / "recipes/hammer.toml").is_file()


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({"replacement": "art/textures/hammer/steel_rough.png"}, "own exported maps"),
        ({"replacement": "art/found.png"}, "not made by a project tool"),
        ({"replacement": "audio/x.png"}, "under art/"),
        ({"replacement": "art/missing.png"}, "not a file"),
    ],
)
def test_replace_map_takes_only_tool_made_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, str],
    reason: str,
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    (stage / "art/found.png").write_bytes(b"\x89PNG")
    (stage / "audio").mkdir()
    (stage / "audio/x.png").write_bytes(b"\x89PNG")
    result = _call(
        stage, "replace_map", {"material": "Steel", "image": "steel_rough", **arguments}
    )
    assert result.is_error and reason in result.content[0].text, result.content[0].text
    edit.assert_not_awaited()


def test_replace_map_links_a_recipe_made_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="texture", material="Steel", image="steel_rough")
    (stage / "art/paint").mkdir(parents=True)
    (stage / "art/paint/rust.png").write_bytes(b"\x89PNG")
    (stage / "recipes/rust.toml").write_text(
        'version = 1\ntool = "krita"\nscript = "recipes/rust.py"\n'
        'outputs = ["art/paint/rust.kra", "art/paint/rust.png"]\n'
    )
    result = _call(
        stage,
        "replace_map",
        {
            "material": "Steel",
            "image": "steel_rough",
            "replacement": "res://art/paint/rust.png",
        },
    )
    assert not result.is_error, result.content[0].text
    assert json.loads(result.content[0].text)["made_by"] == "krita"
    assert edit.await_args.kwargs["inputs"] == ("art/paint/rust.png",)
    assert "'art/paint/rust.png', '')" in edit.await_args.args[3]


def test_split_material_copies_for_the_named_objects_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(
        stage,
        part_kind="material",
        material="Steel",
        object="Head.001",
        used_by="Handle,Head.001",
    )
    refused = _call(
        stage, "split_material", {"material": "Wood", "objects": ["Handle"]}
    )
    assert refused.is_error and "not part of the selection" in refused.content[0].text
    wrong = _call(
        stage, "split_material", {"material": "Steel", "objects": ["Head.002"]}
    )
    assert wrong.is_error and "not part of the selection" in wrong.content[0].text
    result = _call(
        stage, "split_material", {"material": "Steel", "objects": ["Head.001"]}
    )
    assert not result.is_error, result.content[0].text
    found = json.loads(result.content[0].text)
    assert found["material"] == "Steel_Head.001"
    assert set(found["maps"]) == {"steel_base", "steel_rough", "steel_height"}
    assert (
        "_joinery_split_material('Steel', ['Head.001'], 'Steel_Head.001'"
        in edit.await_args.args[3]
    )


def test_bone_and_shape_key_ops_respect_the_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, edit = _project(tmp_path, monkeypatch)
    _pick(stage, part_kind="shape_key", object="Head.001", shape_key="Dent")
    other = _call(
        stage,
        "edit_shape_key",
        {"object": "Head.001", "shape_key": "Smile", "op": "delete"},
    )
    assert other.is_error and "not part of the selection" in other.content[0].text
    ok = _call(
        stage,
        "edit_shape_key",
        {
            "object": "Head.001",
            "shape_key": "Dent",
            "op": "set_value",
            "params": {"value": 0.5},
        },
    )
    assert not ok.is_error, ok.content[0].text
    assert (
        "_joinery_edit_shape_key('Head.001', 'Dent', 'set_value', {'value': 0.5})"
        in edit.await_args.args[3]
    )
    _pick(stage, part_kind="bone", object="Rig", bone="Mid")
    refused = _call(
        stage,
        "edit_bone",
        {
            "armature": "Rig",
            "bone": "Tip",
            "op": "scale_chain",
            "params": {"factor": 2},
        },
    )
    assert refused.is_error and "not part of the selection" in refused.content[0].text
    ok = _call(
        stage,
        "edit_bone",
        {
            "armature": "Rig",
            "bone": "Mid",
            "op": "scale_chain",
            "params": {"factor": 2},
        },
    )
    assert not ok.is_error, ok.content[0].text
    assert (
        "_joinery_edit_bone('Rig', 'Mid', 'scale_chain', {'factor': 2.0})"
        in edit.await_args.args[3]
    )


# --- Material Maker reads a map's preimage ---------------------------------


def test_material_maker_takes_a_preimage_by_its_content(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    picture = tmp_path / "art/map.png"
    picture.write_bytes(b"\x89PNGmap")
    seed = store_preimage(tmp_path, picture)
    graph = {"nodes": [{"type": "image", "name": "Map", "parameters": {"image": seed}}]}
    _stored, runtime, inputs = material_maker.image_inputs(
        tmp_path, graph, "art/m.ptex", "art/out"
    )
    assert (
        inputs == (seed,)
        and runtime["nodes"][0]["parameters"]["image"] == "/work/" + seed
    )
    (tmp_path / seed).write_bytes(b"changed")
    with pytest.raises(ValueError, match="no joinery.lock entry"):
        material_maker.image_inputs(tmp_path, graph, "art/m.ptex", "art/out")
