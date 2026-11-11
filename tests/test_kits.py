"""Parametric kits without Blender: parameters, variation, the art bible, recipes."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from joinery import kits
from joinery.bible_rules import BibleRules, parse_rules
from joinery.recipe import load_recipe
from joinery.runner import Result
from joinery.transactions import FileTransaction
from joinery.workspace import index

RULES = parse_rules(
    """
schema = 2
[models.max_triangles]
"art/props" = 800
[models.pivot]
"art/props" = "centre"
[materials]
roughness = [0.6, 0.9]
metallic = [0.0, 0.5]
metallic_requires_name = "(?i)steel"
[palette]
colours = ["#2b2d42", "#8d99ae", "#6b8f3a", "#a67c52"]
"""
)


def test_catalogue_lists_every_kit_with_valid_defaults() -> None:
    catalogue = kits.catalogue()
    assert list(catalogue) == list(kits.ORDER)
    for kit in catalogue.values():
        assert kit.params and kit.looks and kit.summary
        values = kits.check_params(kit, {})
        assert values == kit.defaults()
        for param in kit.params:
            assert param.label and param.help, (kit.id, param.name)
            if param.kind in {"float", "int"}:
                assert param.minimum is not None and param.maximum is not None
                assert param.minimum <= param.default <= param.maximum
            assert param.vary in {"", "scale", "range"}
        assert "def build(" in kit.source and "SPEC" in kit.source


@pytest.mark.parametrize(
    ("kit", "params", "message"),
    [
        ("rock", {"bulk": 1}, "has no 'bulk'"),
        ("rock", {"size": 9}, "size is from 0.1 to 6 m; got 9"),
        ("rock", {"size": "big"}, "size is a number"),
        ("rock", {"size": True}, "size is a number"),
        ("rock", {"facets": 2.5}, "facets is a whole number"),
        ("rock", {"moss": float("nan")}, "finite"),
        ("hut", {"roof": "dome"}, "roof is one of: gable, hip, shed, flat"),
        ("hut", {"chimney": 1}, "chimney is true or false"),
        ("hut", {"windows": 24}, "room for"),
        ("fence", {"length": 2, "post_spacing": 2, "broken": 3}, "at most 2"),
    ],
)
def test_parameters_are_checked(kit: str, params: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("(", r"\(")):
        kits.check_params(kits.get(kit), params)


def test_whole_numbers_given_as_floats_are_accepted() -> None:
    values = kits.check_params(kits.get("rock"), {"facets": 3.0, "size": 2})
    assert values["facets"] == 3 and isinstance(values["facets"], int)
    assert values["size"] == 2.0 and isinstance(values["size"], float)


def test_unknown_kit_names_the_kits() -> None:
    with pytest.raises(ValueError, match="Kits: rock, crate"):
        kits.get("statue")


def test_variants_are_deterministic_and_inside_the_ranges() -> None:
    kit = kits.get("rock")
    base = kits.check_params(kit, {"moss": 0.5})
    first = kits.variants(kit, base, 20, seed=7)
    assert first == kits.variants(kit, base, 20, seed=7)
    assert first != kits.variants(kit, base, 20, seed=8)
    assert len({seed for seed, _ in first}) == 20
    for _seed, values in first:
        for param in kit.params:
            if param.kind == "float":
                assert param.minimum <= values[param.name] <= param.maximum  # type: ignore[operator]
        assert values["facets"] == base["facets"]  # facets never vary
    sizes = [values["size"] for _seed, values in first]
    assert min(sizes) >= 0.75 and max(sizes) <= 1.25 and len(set(sizes)) == 20
    assert kits.variants(kit, base, 1, seed=7) == [(7, base)]


def test_vary_can_name_parameters() -> None:
    kit = kits.get("rock")
    base = kit.defaults()
    made = kits.variants(kit, base, 5, seed=1, vary={"moss": 1.0})
    assert {v["size"] for _s, v in made} == {base["size"]}
    assert len({v["moss"] for _s, v in made}) > 1
    with pytest.raises(ValueError, match="facets does not vary"):
        kits.variants(kit, base, 5, seed=1, vary={"facets": 0.5})
    with pytest.raises(ValueError, match="from 0 to 1"):
        kits.variants(kit, base, 5, seed=1, vary=1.5)


def test_a_variation_the_kit_cannot_build_keeps_the_base() -> None:
    kit = kits.get("hut")
    base = kits.check_params(kit, {"width": 3.2, "depth": 3.2, "windows": 6})
    for _seed, values in kits.variants(kit, base, 30, seed=3, vary=1.0):
        assert not kit.limits or not kit.limits(values)


def test_looks_follow_the_palette_and_material_ranges() -> None:
    crate = kits.get("crate")
    looks = kits.looks(crate, RULES, "Crate")
    palette = {"#2b2d42", "#8d99ae", "#6b8f3a", "#a67c52"}
    assert {look["colour"] for look in looks.values()} <= palette
    assert looks["wood"]["colour"] == "#a67c52"
    assert all(0.6 <= look["roughness"] <= 0.9 for look in looks.values())
    # Crate_Metal does not say steel: no metallic, as the bible asks.
    assert looks["metal"]["metallic"] == 0.0
    steel = kits.looks(
        crate, parse_rules('[materials]\nmetallic_requires_name = "(?i)metal"'), "Crate"
    )
    assert steel["metal"]["metallic"] == 0.7
    assert kits.looks(crate, BibleRules(), "Crate")["wood"]["colour"] == "#a87b4f"
    chosen = kits.looks(crate, RULES, "Crate", {"wood": "#6b8f3a"})
    assert chosen["wood"]["colour"] == "#6b8f3a"
    with pytest.raises(ValueError, match="not in the art bible's palette"):
        kits.looks(crate, RULES, "Crate", {"wood": "#ff00ff"})
    with pytest.raises(ValueError, match="has no material 'glass'"):
        kits.looks(crate, RULES, "Crate", {"glass": "#2b2d42"})
    leaves = kits.looks(kits.get("tree"), RULES, "Tree")
    assert leaves["leaf_card"]["double_sided"] and not leaves["bark"]["double_sided"]


def test_plan_names_files_and_reads_budget_and_pivot(tmp_path: Path) -> None:
    made = kits.plan(
        tmp_path,
        "crate",
        {"planks": 5},
        seed=4,
        name="Supply-Crate",
        count=3,
        folder="res://art/props/",
        lods=1,
        rules=RULES,
    )
    assert made.name == "supply_crate"
    assert [m["glb"] for m in made.models] == [
        f"art/props/supply_crate_0{i}.glb" for i in (1, 2, 3)
    ]
    assert made.models[0]["blend"] == "art/sources/kits/supply_crate_01.blend"
    assert made.models[0]["lods"] == ["art/props/supply_crate_01_lod1.glb"]
    assert made.models[2]["object"] == "SupplyCrate_03"
    assert made.budget == 800 and made.pivot == "centre"
    assert len(made.outputs) == 9
    one = kits.plan(tmp_path, "rock", rules=RULES)
    assert one.models[0]["glb"] == "art/environment/rock.glb"
    assert one.budget is None and one.pivot == "bottom-centre"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"name": "9 lives"}, "The name is"),
        ({"folder": "scenes"}, "under art/"),
        ({"folder": "art/sources/x"}, "not art/sources/"),
        ({"count": 0}, "1 to 100"),
        ({"count": 101}, "1 to 100"),
        ({"lods": 3}, "lods is 0 to 2"),
        ({"seed": -1}, "seed"),
    ],
)
def test_plan_refuses_bad_arguments(
    tmp_path: Path, arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        kits.plan(tmp_path, "rock", rules=RULES, **arguments)


def test_plan_follows_the_bible_naming(tmp_path: Path) -> None:
    rules = parse_rules('[naming.nodes]\n"art" = "^[a-z_0-9]+$"')
    with pytest.raises(ValueError, match="naming pattern"):
        kits.plan(tmp_path, "rock", rules=rules)
    files = parse_rules('[naming.files]\n"art" = "^prop_[a-z_0-9]+$"')
    with pytest.raises(ValueError, match="file naming pattern"):
        kits.plan(tmp_path, "rock", rules=files)
    assert (
        kits.plan(tmp_path, "rock", name="prop_rock", rules=files).name == "prop_rock"
    )


def test_script_is_self_contained_deterministic_and_marked(tmp_path: Path) -> None:
    made = kits.plan(tmp_path, "rock", {"moss": 0.3}, seed=9, count=2, rules=RULES)
    text = kits.script(made)
    assert text == kits.script(
        kits.plan(tmp_path, "rock", {"moss": 0.3}, seed=9, count=2, rules=RULES)
    )
    assert "def make_variant(" in text and "def build(" in text
    assert "kits/blender/rock.py, MIT" in text and "no models" in text
    marker = kits.read_marker(text)
    assert marker is not None
    assert marker["kit"] == "rock" and marker["seed"] == 9 and marker["count"] == 2
    assert marker["params"]["moss"] == 0.3
    assert [m["glb"] for m in marker["models"]] == [m["glb"] for m in made.models]
    compile(text, "recipe.py", "exec")


def _glb(low: tuple[float, float, float], high: tuple[float, float, float]) -> bytes:
    """A one-triangle binary glTF whose bounds are low..high."""
    points = struct.pack("<9f", *low, high[0], low[1], high[2], *high)
    document = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "Rock", "mesh": 0}],
        "meshes": [{"name": "Rock", "primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": list(low),
                "max": list(high),
            }
        ],
        "bufferViews": [{"buffer": 0, "byteLength": len(points)}],
        "buffers": [{"byteLength": len(points)}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    body += struct.pack("<II", len(points), 0x004E4942) + points
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _stage(tmp_path: Path) -> FileTransaction:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    (project / "joinery.lock").write_text("version=1\n")
    tx = FileTransaction(project)
    tx.begin("kits")
    return tx


def _fake_blender(
    monkeypatch: pytest.MonkeyPatch, low_y: float = 0.0
) -> list[list[str]]:
    calls: list[list[str]] = []

    async def run(
        argv: list[str], project: Path, timeout: int = 600, **_: Any
    ) -> Result:
        calls.append(argv)
        script = (project / argv[-1]).read_text()
        marker = kits.read_marker(script)
        assert marker is not None
        rows = []
        for model in marker["models"]:
            for path in (model["blend"], model["glb"]):
                (project / path).parent.mkdir(parents=True, exist_ok=True)
            (project / model["blend"]).write_bytes(
                b"BLENDER-fake" + model["glb"].encode()
            )
            (project / model["glb"]).write_bytes(
                _glb((-0.5, low_y, -0.5), (0.5, 1.0, 0.5))
            )
            rows.append(
                "JOINERY_KIT:"
                + json.dumps(
                    {
                        "triangles": 1,
                        "built_triangles": 1,
                        "lod_triangles": [],
                        "size": [1, 1, 1],
                    }
                )
            )
        return Result(True, "\n".join(rows), "", 1.5)

    async def record(project: Path, recipe: Any, path: Path) -> dict[str, Any]:
        lines = [
            "version = 1",
            f"tool = {json.dumps(recipe.tool)}",
            f"script = {json.dumps(recipe.script)}",
            f"outputs = {json.dumps(list(recipe.outputs))}",
            f"note = {json.dumps(recipe.note)}",
        ]
        path.write_text("\n".join(lines) + "\n")
        return {"recipe": path.relative_to(project).as_posix(), "status": "staged"}

    monkeypatch.setattr(kits, "run_offline", run)
    monkeypatch.setattr(kits, "_record", record)
    return calls


def test_make_records_one_recipe_for_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_blender(monkeypatch)
    tx = _stage(tmp_path)
    stage = tx.stage
    made = kits.plan(stage, "rock", {"moss": 0.4}, seed=3, name="rocks", count=3)
    result = asyncio.run(kits.make(stage, made))
    assert calls and calls[0][:3] == ["blender", "--background", "--factory-startup"]
    recipe = load_recipe(stage / result["recipe"])
    assert recipe.tool == "blender"
    assert recipe.outputs == tuple(made.outputs)
    assert recipe.note.startswith("Rock kit, 3 models: size 1, roughness 0.45")
    assert (stage / "art/sources/kits/.gdignore").is_file()
    assert [m["glb"] for m in result["models"]] == [
        f"art/environment/rocks_0{i}.glb" for i in (1, 2, 3)
    ]
    found = kits.kit_records(stage)
    assert found["art/environment/rocks_02.glb"]["kit"] == "rock"
    assert (
        found["art/sources/kits/rocks_02.blend"]["params"] == made.models[1]["params"]
    )
    assert found["art/environment/rocks_02.glb"]["base"]["moss"] == 0.4
    # A second set cannot take the same names.
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(kits.make(stage, made))
    assert kits.free_name(stage, "rock", 3) == "rocks_2"
    assert kits.free_name(stage, "rock", 1) == "rock"
    tx.reject()


def test_make_puts_everything_back_when_blender_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_blender(monkeypatch, low_y=0.3)  # a model floating off its pivot
    tx = _stage(tmp_path)
    stage = tx.stage
    made = kits.plan(stage, "rock", name="rock")
    with pytest.raises(ValueError, match="off bottom-centre"):
        asyncio.run(kits.make(stage, made))
    assert not (stage / "art/environment/rock.glb").exists()
    assert not list((stage / "recipes").glob("*"))
    tx.reject()


def test_edit_plan_keeps_the_set_and_changes_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_blender(monkeypatch)
    tx = _stage(tmp_path)
    stage = tx.stage
    asyncio.run(
        kits.make(stage, kits.plan(stage, "rock", seed=2, name="rocks", count=4))
    )
    made, marker = kits.edit_plan(
        stage, "res://art/environment/rocks_02.glb", {"moss": 0.9}
    )
    assert made.params["moss"] == 0.9 and marker["params"]["moss"] == 0.0
    assert made.seed == 2 and made.count == 4 and made.name == "rocks"
    assert made.outputs == [p for m in marker["models"] for p in (m["blend"], m["glb"])]
    with pytest.raises(ValueError, match="was not made with a kit"):
        kits.edit_plan(stage, "art/models/other.glb")
    tx.reject()


def test_workspace_shows_kit_and_folds_each_model_under_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_blender(monkeypatch)
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "blender"]\n')
    tx = FileTransaction(project)
    tx.begin("kits")
    asyncio.run(
        kits.make(tx.stage, kits.plan(tx.stage, "crate", name="crates", count=2))
    )
    tx.review()
    data = index(project)
    by_path = {a["path"]: a for a in data["assets"]}
    second = by_path["art/sources/kits/crates_02.blend"]
    assert second["parts"] == ["art/props/crates_02.glb"]
    assert second["pending"] == tx.id
    assert second["kit"]["kit"] == "crate" and second["kit"]["count"] == 2
    assert (
        by_path["art/props/crates_01.glb"]["part_of"]
        == "art/sources/kits/crates_01.blend"
    )
    tx.reject()


def test_describe_uses_the_project_bible(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(
        '## Checked rules\n\n```toml\n[models.max_triangles]\n"art/props" = 500\n```\n'
    )
    data = kits.describe(tmp_path)
    crate = next(k for k in data["kits"] if k["id"] == "crate")
    assert crate["budget"] == 500 and crate["pivot"] == "bottom-centre"
    assert {p["name"] for p in crate["params"]} >= {"size", "planks", "bands", "wear"}
    assert data["batch"]["max_count"] == kits.MAX_COUNT


def test_object_names() -> None:
    assert kits.object_name("rock") == "Rock"
    assert kits.object_name("mossy_rock_03") == "MossyRock_03"
    assert kits.material_name("MossyRock", "leaf_card") == "MossyRock_LeafCard"
