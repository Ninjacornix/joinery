"""Terrain operations: validation, the recipe they write, reviewed edits, and a real bake.

Without Godot the bake is a stand-in that writes the two images; with a Godot
binary (JOINERY_TEST_GODOT) the feature's own `terrain_bake.gd` runs, twice, and
must write the same bytes, and `joinery rebuild` must accept the recipe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery import blueprints, terrain
from joinery.catalog import load_catalog
from joinery.lockfile import check_assets, read_assets
from joinery.recipe import load_recipe, rebuild_one
from joinery.runner import Result, _timed
from joinery.transactions import FileTransaction
from tests.blueprints.godot_run import godot_binary

OVERLAY = blueprints.FEATURES_DIR / "terrain" / "overlay"


def _stage(tmp_path: Path, feature: bool = True) -> FileTransaction:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot", "krita"]\n')
    (project / "joinery.lock").write_text("version=1\n")
    if feature:
        shutil.copytree(OVERLAY / "features", project / "features")
    tx = FileTransaction(project)
    tx.begin("terrain")
    return tx


def _png(width: int, height: int, depth: int, colour: int, body: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(body))
        + chunk(b"IEND", b"")
    )


def _fake_godot(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stands in for Godot: reads the recipe's document and writes two small PNGs."""
    docs: list[dict[str, Any]] = []

    async def run(
        argv: list[str], project: Path, timeout: int = 600, **_: Any
    ) -> Result:
        assert argv[:3] == ["godot", "--headless", "--script"]
        script = (project / argv[3]).read_text()
        document = script.split('"res://', 2)[2].split('"')[0]
        doc = json.loads((project / document).read_text())
        docs.append(doc)
        size = 3
        seed = len(doc["ops"])
        heights = b"".join(
            b"\x00" + struct.pack(">HHH", seed, seed * 2, seed * 3) for _ in range(size)
        )
        (project / doc["outputs"]["height"]).write_bytes(
            _png(size, size, 16, 0, heights)
        )
        (project / doc["outputs"]["mask"]).write_bytes(
            _png(size, size, 8, 6, (b"\x00" + bytes(4 * size)) * size)
        )
        report = {
            "ok": True,
            "low": 0.0,
            "high": 1.0,
            "problems": [],
            "resolution": doc["resolution"],
        }
        return Result(
            True, "noise\nJOINERY_TERRAIN " + json.dumps(report) + "\n", "", 0.5
        )

    monkeypatch.setattr(terrain, "run_offline", run)
    return docs


# --- Validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"resolution": 100}, "one of 33, 65, 129"),
        ({"min_height": 5, "max_height": 5}, "max_height must be above"),
        ({"size": [0, 10]}, "size"),
        ({"colour": "red"}, "colour"),
    ],
)
def test_settings_are_checked(settings: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        terrain.check_spec(settings)


def test_operations_are_typed_bounded_and_kept_as_written() -> None:
    spec = terrain.check_spec({"size": [40, 40], "min_height": -5, "max_height": 10})
    rows = terrain.check_ops(
        [
            {"op": "noise", "amplitude": 3},
            {"op": "flatten", "at": [0, 0], "radius": 6, "height": 2},
            {"op": "path", "points": [[-15, 0], [15, 4]], "paint": "r"},
            {"op": "paint", "channel": "a", "at": [10, 10], "radius": 4, "value": 0},
            {"op": "smooth"},
        ],
        spec,
    )
    assert rows[0] == {
        "op": "noise",
        "amplitude": 3.0,
        "scale": 32.0,
        "octaves": 4,
        "ridged": False,
        "offset": 0.0,
    }
    assert rows[1]["falloff"] == 0.5 and rows[1]["height"] == 2.0
    assert rows[2]["points"] == [(-15.0, 0.0), (15.0, 4.0)]
    assert "at" not in rows[4]


@pytest.mark.parametrize(
    ("op", "message"),
    [
        ({"op": "bulldoze"}, "does not match any of the expected tags"),
        ({"op": "raise", "at": [0, 0], "amount": -2}, "greater than 0"),
        ({"op": "raise", "at": [90, 0], "radius": 5}, "outside the terrain"),
        ({"op": "flatten", "at": [0, 0], "height": 50}, "outside the height range"),
        ({"op": "paint", "at": [0, 0], "channel": "x"}, "channel"),
        ({"op": "path", "points": [[0, 0]]}, "at least 2"),
        ({"op": "erode", "iterations": 500}, "less than or equal to 50"),
        ({"op": "raise", "at": [0, 0], "amount": 1, "colour": 2}, "colour"),
    ],
)
def test_bad_operations_are_refused_with_the_reason(
    op: dict[str, Any], message: str
) -> None:
    spec = terrain.check_spec({"size": [40, 40]})
    with pytest.raises(ValueError, match=message):
        terrain.check_ops([op], spec)


def test_names_are_plain() -> None:
    assert terrain.check_name("valley_2") == "valley_2"
    for bad in ("Valley", "2valley", "../x", "a b", ""):
        with pytest.raises(ValueError, match="lower case"):
            terrain.check_name(bad)


# --- Reviewed changes with recipes -----------------------------------------------------


def test_new_and_edit_write_a_recipe_and_lock_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _fake_godot(monkeypatch)
    stage = _stage(tmp_path).stage
    made = asyncio.run(
        terrain.create(
            stage,
            "valley",
            {
                "size": [80, 60],
                "resolution": 65,
                "min_height": -2,
                "max_height": 18,
                "seed": 4,
            },
            [{"op": "noise", "amplitude": 5}],
        )
    )
    assert made["status"] == "staged" and made["requires_review"]
    assert made["node"] == {
        "heightmap": "res://art/terrain/valley/height.png",
        "mask": "res://art/terrain/valley/mask.png",
        "size": [80.0, 60.0],
        "min_height": -2.0,
        "max_height": 18.0,
    }
    recipe = load_recipe(stage / made["recipe"])
    assert recipe.tool == "godot"
    assert recipe.outputs == (
        "art/terrain/valley/height.png",
        "art/terrain/valley/mask.png",
    )
    assert recipe.sources[0].endswith(".json") and recipe.script.endswith(".gd")
    assert "bake" in (stage / recipe.script).read_text()
    assert all(v.startswith("sha256:") for v in recipe.expect.values())
    # Godot keeps both images as they are, so all 16 bits reach the game.
    for name in ("height", "mask"):
        settings = stage / f"art/terrain/valley/{name}.png.import"
        assert 'importer="keep"' in settings.read_text()
    locked = {e.path: e for e in read_assets(stage)}
    assert locked["art/terrain/valley/height.png"].origin == "tool-created"
    assert check_assets(stage, load_catalog()) == []

    edited = asyncio.run(
        terrain.edit(
            stage,
            "valley",
            [
                {"op": "flatten", "at": [10, -5], "radius": 6, "height": 3},
                {"op": "paint", "channel": "a", "at": [-20, 10], "radius": 8},
            ],
        )
    )
    assert [op["op"] for op in docs[-1]["ops"]] == ["noise", "flatten", "paint"]
    assert docs[-1]["seed"] == 4 and docs[-1]["size"] == [80.0, 60.0]
    assert edited["operations"] == 3
    assert load_recipe(stage / edited["recipe"]).note.startswith(
        "Terrain valley: flatten at [10, -5], paint at [-20, 10]"
    )
    # The old recipe is history; one recipe makes the terrain now.
    assert (stage / "recipes/history" / Path(made["recipe"]).name).is_file()
    assert terrain.current(stage, "valley")[0].name == Path(edited["recipe"]).name  # type: ignore[index]
    assert check_assets(stage, load_catalog()) == []
    info = terrain.describe(stage)
    assert info["terrains"][0]["operations"] == [
        "noise",
        "flatten at [10, -5]",
        "paint at [-20, 10]",
    ]
    with pytest.raises(ValueError, match="exists; change it with terrain_edit"):
        asyncio.run(terrain.create(stage, "valley", {}, []))
    with pytest.raises(ValueError, match="No terrain called hills"):
        asyncio.run(terrain.edit(stage, "hills", [{"op": "smooth"}]))


def test_a_failed_bake_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(
        argv: list[str], project: Path, timeout: int = 600, **_: Any
    ) -> Result:
        return Result(False, "", "Parse Error: something", 0.1)

    monkeypatch.setattr(terrain, "run_offline", broken)
    stage = _stage(tmp_path).stage
    with pytest.raises(RuntimeError, match="Godot did not make the terrain"):
        asyncio.run(terrain.create(stage, "dunes", {}, [{"op": "noise"}]))
    assert not list((stage / "recipes").glob("*"))
    assert not (stage / "art/terrain/dunes/height.png").exists()


def test_terrain_needs_a_transaction_and_the_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_godot(monkeypatch)
    loose = tmp_path / "loose"
    loose.mkdir()
    with pytest.raises(PermissionError, match="transaction"):
        asyncio.run(terrain.create(loose, "valley", {}, []))
    stage = _stage(tmp_path, feature=False).stage
    with pytest.raises(ValueError, match="feature add terrain"):
        asyncio.run(terrain.create(stage, "valley", {}, []))


def test_alternatives_are_listed_unpinned_and_refused() -> None:
    names = {a["name"]: a for a in terrain.alternatives()}
    assert set(names) == {"Terrain3D", "ProtonScatter", "Spatial Gardener"}
    assert all(a["sha256"] == "" and a["url"] == "" for a in names.values())
    for name in names:
        with pytest.raises(PermissionError, match="isn't pinned"):
            terrain.install_alternative(name)
    with pytest.raises(ValueError, match="not a terrain alternative"):
        terrain.install_alternative("SomethingElse")


def test_the_paint_server_offers_the_terrain_tools(tmp_path: Path) -> None:
    from servers.authoring import make_server

    server = make_server(tmp_path, "joinery-paint")
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert {"terrain_new", "terrain_edit", "terrain_info"} <= set(tools)
    assert "flatten" in (tools["terrain_edit"].description or "")
    assert tools["terrain_info"].annotations.read_only_hint  # type: ignore[union-attr]
    from joinery.read_access import read_only

    assert read_only("joinery-paint", "terrain_info")
    assert not read_only("joinery-paint", "terrain_edit")


# --- The real bake ---------------------------------------------------------------------


class _LocalGodot:
    name = "local"

    def __init__(self, binary: Path) -> None:
        self.binary = binary

    def available(self) -> bool:
        return True

    def run(self, argv: list[str], project: Path, timeout: int = 600) -> Result:
        return _timed([str(self.binary), *argv[1:]], timeout, cwd=project)


@pytest.mark.skipif(
    godot_binary() is None, reason="needs a Godot 4.7 binary (JOINERY_TEST_GODOT)"
)
def test_godot_bakes_the_same_bytes_and_rebuild_accepts_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = godot_binary()
    assert binary is not None
    local = _LocalGodot(binary)

    async def run(
        argv: list[str], project: Path, timeout: int = 600, **_: Any
    ) -> Result:
        return local.run(argv, project, timeout)

    monkeypatch.setattr(terrain, "run_offline", run)
    stage = _stage(tmp_path).stage
    made = asyncio.run(
        terrain.create(
            stage,
            "hills",
            {
                "size": [64, 64],
                "resolution": 65,
                "min_height": -2,
                "max_height": 14,
                "seed": 9,
            },
            [
                {"op": "noise", "amplitude": 5, "scale": 20},
                {"op": "flatten", "at": [0, 0], "radius": 8, "height": 2},
                {
                    "op": "path",
                    "points": [[-30, 10], [30, 6]],
                    "width": 3,
                    "paint": "r",
                },
            ],
        )
    )
    assert made["problems"] == [] and made["heights_m"][0] >= -2
    height = stage / "art/terrain/hills/height.png"
    data = height.read_bytes()
    assert data[12:16] == b"IHDR" and struct.unpack(">IIBB", data[16:26])[:4] == (
        65,
        65,
        16,
        0,
    )
    first = hashlib.sha256(data).hexdigest()
    # The recipe replays to the same bytes.
    rebuilt = rebuild_one(stage / made["recipe"], stage, local)
    assert rebuilt.ok, rebuilt.problems
    # An edit changes the ground and keeps every earlier operation.
    asyncio.run(
        terrain.edit(
            stage, "hills", [{"op": "raise", "at": [20, -20], "radius": 6, "amount": 4}]
        )
    )
    assert hashlib.sha256(height.read_bytes()).hexdigest() != first
    # Stock Godot reads the 16-bit file back through the feature's own reader.
    probe = stage / "probe.gd"
    probe.write_text(
        "extends SceneTree\n\n\nfunc _initialize() -> void:\n"
        '\tvar map: RefCounted = load("res://features/terrain/terrain_map.gd").load_file('
        '"res://art/terrain/hills/height.png", 1)\n'
        '\tprint("PROBE %d %s" % [map.width, map.value_range()])\n'
        "\tquit(0)\n"
    )
    out = subprocess.run(
        [str(binary), "--headless", "--script", "probe.gd"],
        cwd=stage,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "PROBE 65" in out.stdout, out.stdout[-2000:] + out.stderr[-2000:]
