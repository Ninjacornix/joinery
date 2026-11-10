"""Parts of a model: Blender's inspection parsed into objects, materials and maps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import parts

FIXTURE = Path(__file__).parent / "fixtures/parts/parts.json"
RESOURCE = "res://art/models/hammer.glb"
SOURCE = "art/sources/hammer.blend"


@pytest.fixture
def inspection() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


@pytest.fixture
def report(inspection: dict[str, Any]) -> parts.PartsReport:
    return parts.build_report(inspection, "blender", SOURCE, RESOURCE, "abc")


def textures(report: parts.PartsReport) -> dict[str, parts.Part]:
    return {p.image: p for p in report.parts if p.part_kind == "texture"}


def test_the_recorded_hammer_has_two_objects_two_materials_and_five_maps(
    report: parts.PartsReport,
) -> None:
    counts = report.counts()
    assert counts["object"] == 2 and counts["material"] == 2
    assert counts["texture"] == 5 and counts["shape_key"] == 1
    objects = {p.object: p for p in report.parts if p.part_kind == "object"}
    assert set(objects) == {"Handle", "Head.001"}
    assert objects["Handle"].materials == ("Wood", "Steel")


def test_map_roles_come_from_the_socket_each_image_feeds(
    report: parts.PartsReport,
) -> None:
    maps = textures(report)
    assert maps["steel_base"].role == "base_color"
    assert maps["steel_rough"].role == "roughness"
    assert maps["steel_rough"].socket == "Roughness"
    assert maps["wood_base"].role == "base_color"
    assert maps["steel_rough"].texture == "art/textures/hammer/steel_rough.png"


def test_a_packed_orm_map_keeps_each_channel(report: parts.PartsReport) -> None:
    orm = textures(report)["wood_orm"]
    assert orm.roles == ("roughness", "metallic")
    # Two channels feed two sockets, so the map as a whole has no single channel.
    assert orm.channel == ""
    uses = next(
        image
        for material in json.loads(FIXTURE.read_text())["materials"]
        for image in material["images"]
        if image["image"] == "wood_orm"
    )["uses"]
    assert {(u["role"], u["channel"]) for u in uses} == {
        ("roughness", "Green"),
        ("metallic", "Blue"),
    }


def test_height_is_found_through_bump_and_displacement(
    report: parts.PartsReport, inspection: dict[str, Any]
) -> None:
    height = textures(report)["steel_height"]
    assert height.role == "height" and height.source_only
    uses = next(
        image
        for material in inspection["materials"]
        for image in material["images"]
        if image["image"] == "steel_height"
    )["uses"]
    assert {tuple(u["via"]) for u in uses} == {("BUMP",), ("DISPLACEMENT",)}


def test_a_single_channel_map_carries_its_channel() -> None:
    inspection = {
        "objects": [],
        "materials": [
            {
                "name": "Rock",
                "users": [],
                "images": [
                    {
                        "image": "rock_orm",
                        "node": "ORM",
                        "file": "rock_orm.png",
                        "uses": [
                            {
                                "role": "roughness",
                                "socket": "Roughness",
                                "channel": "Green",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    found = parts.build_report(inspection, "blender", SOURCE, RESOURCE, "x")
    assert textures(found)["rock_orm"].channel == "Green"


def test_shared_material_lists_every_object_using_it(report: parts.PartsReport) -> None:
    steel = next(
        p for p in report.parts if p.part_kind == "material" and p.material == "Steel"
    )
    assert set(steel.used_by) == {"Handle", "Head.001"}
    assert set(steel.maps) == {"steel_base", "steel_rough", "steel_height"}


@pytest.mark.parametrize(
    "fields",
    [
        {"material": "Steel", "image": "steel_rough"},
        {"object": "Head.001"},
        {"material": "Wood"},
        {"object": "Head.001", "shape_key": "Dent"},
        {"material": "A & B", "image": "x#y=z"},
    ],
)
def test_part_ids_round_trip(fields: dict[str, str]) -> None:
    identifier = parts.part_id("art/models/hammer.glb", **fields)
    assert identifier.startswith(RESOURCE)
    resource, parsed = parts.parse_part_id(identifier)
    assert resource == RESOURCE and parsed == fields


def test_part_ids_are_stable_and_name_the_part(report: parts.PartsReport) -> None:
    assert textures(report)["steel_rough"].id == (
        "res://art/models/hammer.glb#material=Steel&image=steel_rough"
    )
    with pytest.raises(ValueError, match="Unknown part id"):
        parts.parse_part_id(RESOURCE + "#node=Roughness")


def test_chip_labels_put_the_leaf_first(report: parts.PartsReport) -> None:
    maps = textures(report)
    assert maps["steel_rough"].label == "Roughness · Steel"
    assert maps["wood_orm"].label == "Roughness and metallic · Wood"
    picked = maps["steel_rough"].target() | {"object": "Head.001"}
    assert parts.chip_label(picked) == "Roughness · Head.001"
    steel = next(
        p for p in report.parts if p.part_kind == "material" and p.material == "Steel"
    )
    assert steel.label == "Steel · Hammer"
    tooltip = parts.chip_tooltip(maps["steel_rough"].target())
    assert tooltip.splitlines()[0] == "Hammer › Steel › Roughness"
    assert "Used by Handle, Head.001" in tooltip
    assert "In Blender: art/sources/hammer.blend" in tooltip


def test_targets_are_flat_strings(report: parts.PartsReport) -> None:
    for part in report.parts:
        assert all(isinstance(v, str) for v in part.target().values())


def test_find_matches_a_target_and_refuses_unknown_parts(
    report: parts.PartsReport,
) -> None:
    assert (
        report.find({"material": "Steel", "image": "steel_rough"}).role == "roughness"
    )
    assert (
        report.find({"object": "Head.001", "material": "Steel"}).part_kind == "material"
    )
    with pytest.raises(ValueError, match="no texture"):
        report.find({"material": "Steel", "image": "steel_gloss"})
    with pytest.raises(ValueError, match="no material"):
        report.find({"object": "Handle.002", "material": "Gold"})


def test_target_checks_keep_typed_edits_inside_the_selection(
    report: parts.PartsReport,
) -> None:
    rough = textures(report)["steel_rough"].target()
    parts.check_targets([rough], material="Steel", image="steel_rough")
    with pytest.raises(ValueError, match="'steel_base' is not part of the selection"):
        parts.check_targets([rough], material="Steel", image="steel_base")
    with pytest.raises(ValueError, match="'Wood' is not part of the selection"):
        parts.check_targets([rough], material="Wood")
    head = next(
        p for p in report.parts if p.object == "Head.001" and p.part_kind == "object"
    )
    # An object selection covers the materials on it and their maps.
    parts.check_targets([head.target()], material="Steel", image="steel_height")
    # No part selected: the whole model is in scope (the scope guard still applies).
    parts.check_targets([], material="Wood", image="wood_orm")


def test_prompt_targets_are_read_per_source(
    tmp_path: Path, report: parts.PartsReport
) -> None:
    rough = textures(report)["steel_rough"].target()
    parts.write_targets(
        tmp_path, [rough, {"id": "res://main.tscn::Door", "label": "Door"}]
    )
    assert parts.prompt_targets(tmp_path, SOURCE) == [rough]
    assert parts.prompt_targets(tmp_path, "art/sources/other.blend") == []


def write_recipe(project: Path) -> None:
    (project / "recipes").mkdir()
    (project / "recipes/hammer.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/hammer.py"\n'
        'outputs = ["art/sources/hammer.blend", "art/models/hammer.glb"]\n'
        'sources = []\nnote = ""\n'
    )
    (project / "art/sources").mkdir(parents=True)
    (project / "art/sources/hammer.blend").write_bytes(b"blend")


def test_cli_lists_parts_from_the_cache_without_a_worker(
    tmp_path: Path,
    inspection: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_recipe(tmp_path)
    digest = parts._sha256(tmp_path / SOURCE)
    cached = parts.cache_path(tmp_path, digest)
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps({"sha256": digest, "inspection": inspection}))

    async def no_worker(*_args: object) -> dict[str, Any]:
        raise AssertionError("A cached report needs no worker")

    monkeypatch.setattr("joinery.authoring.inspect_document", no_worker)
    for asset in ("res://art/models/hammer.glb", SOURCE):
        assert parts.main(["parts", asset, "--project", str(tmp_path), "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["resource"] == RESOURCE and data["source"] == SOURCE
        assert data["counts"]["texture"] == 5
        assert {"Roughness · Steel"} <= {p["label"] for p in data["parts"]}


def test_cli_inspects_once_then_caches(
    tmp_path: Path,
    inspection: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_recipe(tmp_path)
    calls: list[str] = []

    async def inspect(project: Path, tool: str, source: str) -> dict[str, Any]:
        calls.append(source)
        return inspection

    monkeypatch.setattr("joinery.authoring.inspect_document", inspect)
    args = argparse.Namespace(project=tmp_path, asset=SOURCE, json=True, refresh=False)
    assert parts.run(args) == 0 and parts.run(args) == 0
    assert calls == [SOURCE]
    capsys.readouterr()


def test_cli_explains_an_asset_with_no_parts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert parts.main(["parts", "art/door.png", "--project", str(tmp_path)]) == 1
    assert "Only models made in Blender" in capsys.readouterr().err
