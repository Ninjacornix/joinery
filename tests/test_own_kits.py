"""The user's own Geometry Nodes group as a kit: reading the group into a generator.

Blender's side is tests/integration/own_kits.py; here a group as Blender
describes it (READ_GROUP's JSON) becomes a kit, without the worker.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from joinery import kit_groups, kits, kits_cli
from joinery.kit_archive import read_spec
from joinery.kit_library import project_kits

FOUND: dict[str, Any] = {
    "name": "Spire",
    "description": "A stone spire.",
    "groups": ["Spire"],
    "problems": [],
    "inputs": [
        {
            "identifier": "Socket_0",
            "name": "Height",
            "socket_type": "NodeSocketFloat",
            "default": 2.0,
            "min_value": 0.5,
            "max_value": 5.0,
            "subtype": "DISTANCE",
            "description": "How tall.",
            "hide_value": False,
        },
        {
            "identifier": "Socket_1",
            "name": "Sides",
            "socket_type": "NodeSocketInt",
            "default": 8,
            "min_value": -2147483648,
            "max_value": 2147483647,
            "subtype": "NONE",
            "description": "",
            "hide_value": False,
        },
        {
            "identifier": "Socket_2",
            "name": "Has studs?",
            "socket_type": "NodeSocketBool",
            "default": True,
            "description": "",
            "hide_value": False,
        },
        {
            "identifier": "Socket_3",
            "name": "Seed",
            "socket_type": "NodeSocketInt",
            "default": 0,
            "min_value": 0,
            "max_value": 100,
            "description": "",
            "hide_value": False,
        },
        {
            "identifier": "Socket_4",
            "name": "Offset",
            "socket_type": "NodeSocketVector",
            "default": [0, 0, 0],
            "description": "",
            "hide_value": False,
        },
        {
            "identifier": "Socket_5",
            "name": "Tilt",
            "socket_type": "NodeSocketFloat",
            "default": -0.2,
            "min_value": -3.4e38,
            "max_value": 3.4e38,
            "subtype": "ANGLE",
            "description": "",
            "hide_value": False,
        },
        {
            "identifier": "Socket_6",
            "name": "Geometry",
            "socket_type": "NodeSocketGeometry",
            "default": None,
            "description": "",
            "hide_value": False,
        },
    ],
    "outputs": [
        {
            "identifier": "Socket_7",
            "name": "Geometry",
            "socket_type": "NodeSocketGeometry",
        }
    ],
    "materials": {
        "Stone": {"colour": "#958a7c", "roughness": 0.8, "metallic": 0.0},
        "Moss Green": {"colour": "#59953d", "roughness": 0.5, "metallic": 0.0},
    },
}


def _generator(**extra: Any) -> tuple[str, dict[str, Any]]:
    return kit_groups.generator(
        FOUND,
        kit="spire",
        label="Spire",
        source="art/sources/groups/spire.blend",
        blend_name="mine.blend",
        folder="art/props",
        base="none",
        **extra,
    )


def test_group_inputs_become_typed_parameters() -> None:
    params, sockets, seed, fixed = kit_groups.parameters(FOUND)
    assert list(params) == ["height", "sides", "has_studs", "tilt"]
    assert params["height"] == {
        "kind": "float",
        "label": "Height",
        "help": "How tall.",
        "default": 2.0,
        "min": 0.5,
        "max": 5.0,
        "step": 0.01,
        "vary": "scale",
        "unit": "m",
    }
    # Unbounded ends get a span around the default.
    assert params["sides"]["min"] == -80 and params["sides"]["max"] == 88
    assert params["tilt"]["min"] == -10.2 and params["tilt"]["max"] == 10.0
    assert params["tilt"]["vary"] == ""
    assert params["has_studs"] == {
        "kind": "bool",
        "label": "Has studs?",
        "help": "The group's Has studs? input.",
        "default": True,
    }
    assert sockets == {
        "height": "Socket_0",
        "sides": "Socket_1",
        "has_studs": "Socket_2",
        "tilt": "Socket_5",
    }
    assert seed == "Socket_3" and fixed == ["Offset"]


def test_materials_become_roles() -> None:
    looks, roles = kit_groups.materials(FOUND)
    assert roles == {"moss_green": "Moss Green", "stone": "Stone"}
    assert looks["stone"] == {"colour": "#958a7c", "roughness": 0.8, "metallic": 0.0}
    looks, roles = kit_groups.materials({**FOUND, "materials": {}})
    assert list(looks) == ["surface"] and roles == {}


def test_the_generator_is_a_project_kit(tmp_path: Path) -> None:
    text, summary = _generator()
    spec = read_spec(text, "spire.py")
    assert spec["sources"] == ["art/sources/groups/spire.blend"]
    assert spec["presets"][0]["params"]["height"] == 2.0
    compile(text, "spire.py", "exec")
    assert "SEED = 'Socket_3'" in text and "GROUP = 'Spire'" in text
    assert summary["params"] == ["height", "sides", "has_studs", "tilt"]
    folder = tmp_path / "kits/blender"
    folder.mkdir(parents=True)
    (folder / "spire.py").write_text(text)
    kit = project_kits(tmp_path)["spire"]
    assert kit.sources == ("art/sources/groups/spire.blend",)
    assert kit.presets[0]["name"] == "As made"
    assert kit.credit == "this project (spire.py)"
    # The recipe a set gets carries the generator, and it names its source.
    made = kits.Plan(
        kit=kit,
        params=kit.defaults(),
        seed=1,
        name="spire",
        count=1,
        vary=None,
        folder="art/props",
        lods=0,
        colours={},
        models=[],
        budget=None,
        pivot="bottom-centre",
        looks={},
    )
    assert "bpy.data.libraries.load" in kits.script(made)


def test_a_kit_source_must_be_a_project_art_file() -> None:
    text, _ = _generator()
    spec = read_spec(text, "spire.py")
    for bad in (["../x.blend"], ["/abs.blend"], ["kits/x.blend"], "art/x.blend"):
        with pytest.raises(ValueError, match="sources"):
            kits.from_spec({**spec, "sources": bad}, text)


@pytest.mark.parametrize(
    ("found", "message"),
    [
        ({"error": "missing", "groups": ["Rock", "Tree"]}, "Rock, Tree"),
        ({"error": "kind:ShaderNodeTree"}, "not a Geometry Nodes group"),
        (
            {
                "problems": [
                    "Spire: node X (SvGenNode) is not a node Blender itself defines."
                ]
            },
            "SvGenNode",
        ),
        ({"problems": [], "outputs": []}, "no Geometry output"),
    ],
)
def test_groups_that_cannot_be_kits_are_refused(
    found: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        kit_groups.check_group(found, "Spire")


def test_kit_ids() -> None:
    assert kit_groups.kit_id("My Spire 2") == "my_spire_2"
    assert kit_groups.kit_id("3 towers") == "p_3_towers"
    assert "GROUP_JSON = " in kit_groups.read_code('It\'s "odd"')


def test_import_group_writes_the_kit_the_blend_and_the_lock(tmp_path: Path) -> None:
    blend = tmp_path / "mine.blend"
    blend.write_bytes(b"BLENDER-fixture")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "joinery.lock").write_text("version = 1\n")
    with patch.object(kit_groups, "read_group", AsyncMock(return_value=FOUND)):
        got = asyncio.run(kit_groups.import_group(stage, blend, "Spire"))
        assert got["kit"] == "spire" and got["fixed"] == ["Offset"]
        assert (
            stage / "art/sources/groups/spire.blend"
        ).read_bytes() == b"BLENDER-fixture"
        assert (stage / "art/sources/groups/.gdignore").is_file()
        lock = (stage / "joinery.lock").read_text()
        assert 'origin = "human-import"' in lock and "joinery kits from-blend" in lock
        with pytest.raises(ValueError, match="already has a kit"):
            asyncio.run(kit_groups.import_group(stage, blend, "Spire"))
        with pytest.raises(ValueError, match="already has a kit 'rock'"):
            asyncio.run(kit_groups.import_group(stage, blend, "Spire", kit="rock"))
        with pytest.raises(ValueError, match="--base"):
            asyncio.run(
                kit_groups.import_group(stage, blend, "Spire", kit="b", base="torus")
            )
    with pytest.raises(ValueError, match="not a .blend"):
        asyncio.run(kit_groups.import_group(stage, tmp_path / "x.txt", "Spire"))


def test_the_cli_names_what_from_blend_needs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = argparse.ArgumentParser()
    kits_cli.add_arguments(parser)
    args = parser.parse_args(["from-blend", "--project", str(tmp_path), "--json"])
    assert kits_cli.main(args) == 1
    assert "Name your .blend" in capsys.readouterr().out
