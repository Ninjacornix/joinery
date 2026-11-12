"""Levels grown from the user's own example (ADR 0069): the PNG reader, Wave Function
Collapse, reading samples, growing a checked layout, the recipe that grows it again,
the command and the grow_level tool. Godot builds and walks grown levels in
tests/blueprints/test_grow.py."""

from __future__ import annotations

import asyncio
import json
import random
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent

from bridge.instructions import BLOCKOUT_GUIDE
from joinery.blockout import grow, png, stage, wfc
from joinery.blockout.canvas import Canvas
from joinery.blockout.check import check as check_layout
from joinery.blockout.layout import parse, to_toml
from joinery.blockout.world import profile_for
from joinery.cli import main
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from joinery.roles import registry_allowed
from servers.joinery_registry.proxy import make_server
from tests.test_blockout import _walked

FIXTURES = Path(__file__).parent / "fixtures" / "blockout"
ROOMS = FIXTURES / "rooms_sample.toml"
LEDGES = FIXTURES / "ledges_sample.toml"
#: The painted sample: 4 px per cell. "#" wall, "." floor, then marker colours.
PAINTED = [
    "##############",
    "#S....##.....#",
    "#.....##.....#",
    "#............#",
    "###..####..###",
    "#.....##.....#",
    "#..c.........#",
    "#.....##...G.#",
    "##############",
]
COLOURS = {
    "#": (24, 24, 28),
    ".": (236, 236, 232),
    "S": (40, 90, 220),
    "G": (40, 180, 70),
    "c": (240, 200, 40),
}


def _painted(block: int = 4) -> bytes:
    """The user's painted example, drawn by code here (a test stands in for the user)."""
    canvas = Canvas(len(PAINTED[0]) * block, len(PAINTED) * block, (0, 0, 0))
    for y, row in enumerate(PAINTED):
        for x, char in enumerate(row):
            canvas.rect(
                x * block, y * block, (x + 1) * block, (y + 1) * block, COLOURS[char]
            )
    return canvas.png()


def _grow(
    sample: grow.Sample, seed: int, mode: str = "3d", **kw: Any
) -> grow.GrownLevel:
    blueprint = {"3d": "third-person-3d", "2d-topdown": "top-down"}.get(
        mode, "platformer-2d"
    )
    return grow.grow(
        sample,
        width=kw.pop("width", 24),
        height=kw.pop("height", 16),
        seed=seed,
        mode=mode,  # type: ignore[arg-type]
        profile=profile_for(blueprint, mode),  # type: ignore[arg-type]
        name="grown",
        recipe={"sample": "sample.toml", "sample_sha256": "0" * 64},
        **kw,
    )


def _read(path: Path, blueprint: str = "third-person-3d") -> grow.Sample:
    sample, _ = grow.read_sample(path, lambda m: profile_for(blueprint, m))
    return sample


# --- reading PNGs -----------------------------------------------------------------


def _png(
    width: int, height: int, colour: int, depth: int, rows: list[bytes], **chunks: bytes
) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    extra = b"".join(chunk(k.encode(), v) for k, v in chunks.items())
    return (
        png.SIGNATURE
        + chunk(b"IHDR", header)
        + extra
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def _filtered(raw: list[bytes], bpp: int) -> list[bytes]:
    """Each row with a different PNG filter (sub, up, average, Paeth, none)."""
    out = []
    previous = bytes(len(raw[0]))
    for index, row in enumerate(raw):
        kind = (index % 4) + 1 if index < 4 else 0
        encoded = bytearray()
        for i, value in enumerate(row):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            corner = previous[i - bpp] if i >= bpp else 0
            guess = {
                0: 0,
                1: left,
                2: up,
                3: (left + up) >> 1,
                4: png._paeth(left, up, corner),
            }[kind]
            encoded.append((value - guess) & 0xFF)
        out.append(bytes([kind]) + bytes(encoded))
        previous = row
    return out


def test_png_reads_what_the_canvas_writes() -> None:
    width, height, pixels = png.read(_painted(block=2))
    assert (width, height) == (28, 18)
    assert pixels[2][2] == (*COLOURS["S"], 255)
    assert pixels[0][0] == (*COLOURS["#"], 255)


def test_png_reads_every_filter_and_colour_type() -> None:
    raw = [
        bytes(v for x in range(3) for v in (x * 40, y * 50, 90, 200 + x))
        for y in range(5)
    ]
    _, _, pixels = png.read(_png(3, 5, 6, 8, _filtered(raw, 4)))
    assert pixels[4][2] == (80, 200, 90, 202)
    # A 4-bit palette with transparency.
    rows = [b"\x00" + bytes([0x01, 0x20]), b"\x00" + bytes([0x21, 0x00])]
    _, _, pixels = png.read(
        _png(
            3,
            2,
            3,
            4,
            rows,
            PLTE=bytes([0, 0, 0, 255, 255, 255, 255, 0, 0]),
            tRNS=b"\x00",
        )
    )
    assert pixels[0] == [(0, 0, 0, 0), (255, 255, 255, 255), (255, 0, 0, 255)]
    # 1-bit grey.
    _, _, pixels = png.read(_png(8, 1, 0, 1, [b"\x00\xa0"]))
    assert [p[0] for p in pixels[0]] == [255, 0, 255, 0, 0, 0, 0, 0]
    with pytest.raises(png.PngError, match="Not a PNG"):
        png.read(b"GIF89a")


# --- Wave Function Collapse -------------------------------------------------------


def test_wfc_is_deterministic_per_seed_and_only_uses_the_samples_patterns() -> None:
    sample = _read(ROOMS)
    edges = grow._edges(sample.tiles)
    model = wfc.learn(sample.tiles, 3, 8, [e is not None for e in edges])
    one = wfc.run(model, 20, 14, random.Random(5), edges)
    assert one == wfc.run(model, 20, 14, random.Random(5), edges)
    assert one != wfc.run(model, 20, 14, random.Random(6), edges)
    patterns = set(model.patterns)
    for y in range(14 - 2):
        for x in range(20 - 2):
            window = tuple(one[y + j][x + i] for j in range(3) for i in range(3))
            assert window in patterns, (x, y)
    # The sample's walled sides stay walled.
    assert all(t == grow.WALL_TILE for t in one[0] + one[-1])
    assert all(row[0] == row[-1] == grow.WALL_TILE for row in one)


# --- samples ----------------------------------------------------------------------


def test_a_painted_sample_is_read_in_its_blocks_with_its_markers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "my_map.png"
    path.write_bytes(_painted())
    sample = _read(path)
    assert (sample.width, sample.height) == (14, 9)
    assert sorted(sample.markers) == [("coin", 3, 6), ("goal", 11, 7), ("spawn", 1, 1)]
    assert sample.tiles[0] == [grow.WALL_TILE] * 14
    assert sample.tiles[1][1] == grow.FLOOR_TILE  # under the spawn


def test_text_and_layout_samples_are_read() -> None:
    rooms = _read(ROOMS)
    assert rooms.mode is None and rooms.count("goal") == 1 and rooms.count("coin") == 1
    ledges = _read(LEDGES, "platformer-2d")
    assert ledges.mode == "2d-side"
    dungeon = _read(FIXTURES / "dungeon.toml", "top-down")
    assert dungeon.mode == "2d-topdown" and dungeon.cell == 1.0
    assert dungeon.count("goal") == 1 and dungeon.count("spawn") == 1
    assert any(grow.FLOOR_TILE in row for row in dungeon.tiles)


def test_links_and_unknown_samples_are_refused(tmp_path: Path) -> None:
    with pytest.raises(grow.GrowError, match="not a link"):
        grow.make(tmp_path, "https://example.com/map.png", width=20, height=12, seed=1)
    bad = tmp_path / "bad.toml"
    bad.write_text("grid = '''\n#?#\n'''\n")
    with pytest.raises(grow.GrowError, match="not in the legend"):
        _read(bad)


# --- growing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "mode"),
    [(ROOMS, "3d"), (ROOMS, "2d-topdown"), (LEDGES, "2d-side")],
)
def test_a_grown_level_is_deterministic_and_its_goals_are_reachable(
    path: Path, mode: str
) -> None:
    sample = _read(path, "platformer-2d" if mode == "2d-side" else "top-down")
    first = _grow(sample, 3, mode)
    assert to_toml(first.layout) == to_toml(_grow(sample, 3, mode).layout)
    assert to_toml(first.layout) != to_toml(_grow(sample, 4, mode).layout)
    assert first.checked.ok
    reach = first.checked.reach
    assert reach is not None and reach.targets
    assert all(t.reached for t in reach.targets)
    assert first.layout.spawn is not None
    assert sum(1 for m in first.layout.markers if m.kind == "goal") == 1
    assert first.layout.grown is not None and first.layout.grown.seed == 3
    # Plain blockout TOML: it parses back to the same layout.
    assert to_toml(parse(to_toml(first.layout))) == to_toml(first.layout)


def test_unreachable_tries_are_dropped_and_the_tries_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _read(ROOMS)
    real = check_layout
    seen: list[int] = []

    def flaky(layout: Any, profile: Any, **kw: Any) -> Any:
        """The first two tries' goals can't be reached."""
        seen.append(1)
        return (
            _failing(real, layout, profile, **kw)
            if len(seen) < 3
            else real(layout, profile, **kw)
        )

    monkeypatch.setattr(grow, "check", flaky)
    grown = _grow(sample, 2)
    assert grown.tries == 3 and grown.layout.grown is not None
    assert grown.layout.grown.tries == 3
    assert len(grown.dropped) == 2 and "can't be reached" in grown.dropped[0]
    monkeypatch.setattr(grow, "check", lambda *a, **k: _failing(real, *a, **k))
    with pytest.raises(grow.GrowError, match="in 4 tries"):
        _grow(sample, 2, tries=4)


def _problem() -> Any:
    from joinery.blockout.check import Problem

    return Problem("error", "goal can't be reached")


def _failing(real: Any, *a: Any, **k: Any) -> Any:
    checked = real(*a, **k)
    checked.problems.append(_problem())
    return checked


def test_sizes_and_seeds_are_bounded() -> None:
    sample = _read(ROOMS)
    with pytest.raises(grow.GrowError, match="6 to 64"):
        _grow(sample, 1, width=4)
    with pytest.raises(grow.GrowError, match="seed"):
        _grow(sample, -1)


# --- the recipe and the command ---------------------------------------------------


def test_grow_records_its_recipe_and_grows_again_to_the_same_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = tmp_path / "art/reference/my_map.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(_painted())
    code = main(
        [
            "blockout", "grow", "--project", str(tmp_path),
            "--sample", "art/reference/my_map.png",
            "--size", "20x14", "--seed", "9", "--mode", "2d-topdown", "--name", "Cave",
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0, out
    layout_file = tmp_path / "design/blockouts/cave.toml"
    text = layout_file.read_text()
    layout = parse(text)
    assert layout.grown is not None
    assert layout.grown.sample == "art/reference/my_map.png"
    assert layout.grown.sample_sha256 == grow.sha256(reference.read_bytes())
    assert layout.grown.seed == 9 and layout.grown.size == [20, 14]
    assert "Walkable: spawn reaches" in out
    # Growing again from the recipe gives the same bytes; check and build use it.
    assert main(["blockout", "grow", str(layout_file), "--project", str(tmp_path)]) == 0
    assert "Same bytes as before." in capsys.readouterr().out
    assert layout_file.read_text() == text
    assert (
        main(["blockout", "build", str(layout_file), "--project", str(tmp_path)]) == 0
    )
    assert (tmp_path / "game/cave_blockout.tscn").is_file()
    # A changed sample would grow something else: say so rather than overwrite.
    reference.write_bytes(_painted(block=3))
    assert main(["blockout", "grow", str(layout_file), "--project", str(tmp_path)]) == 2
    assert "changed since" in capsys.readouterr().err


# --- the tool ---------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.test_crew import FakeConnection

    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[game]\n\nhooks/player_scene="res://game/player.tscn"\n'
    )
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.blockout.project_blueprint", lambda _p: "top-down")
    sample = tmp_path / ".joinery/attachments/map.png"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(_painted())
    return tmp_path


def test_grow_level_stages_the_level_with_the_sample_beside_the_plan(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr("joinery.blockout.stage.playtest.run_async", _walked(calls))
    monkeypatch.setattr(
        "joinery.blockout.stage.playtest.godot_binary", lambda: Path("godot")
    )

    async def go() -> None:
        gateway = ProjectGateway(project)
        try:
            refused = await stage.grow_tool(
                gateway, {"sample": "/etc/hosts", "size": [20, 14], "seed": 1}
            )
            raise AssertionError(refused)
        except ValueError as exc:
            assert "must be in the project" in str(exc)
        try:
            bad = await stage.grow_tool(
                gateway,
                {"sample": ".joinery/attachments/map.png", "size": [2, 2], "seed": 1},
            )
            assert bad["status"] == "problems" and gateway.transaction is None
            result = await stage.grow_tool(
                gateway,
                {
                    "sample": ".joinery/attachments/map.png",
                    "size": [20, 14],
                    "seed": 5,
                    "name": "cave",
                },
            )
            assert result["status"] == "staged", result
            assert result["facts"][0].startswith("Walkable: spawn reaches")
            assert result["facts"][1] == (
                "Grown from .joinery/attachments/map.png: 20x14 cells, seed 5"
            )
            assert result["grown"]["seed"] == 5
            tx = gateway.transaction
            assert tx is not None
            staged = parse((tx.stage / "design/blockouts/cave.toml").read_text())
            assert staged.mode == "2d-topdown" and staged.grown is not None
            assert (tx.stage / "game/cave_blockout.tscn").is_file()
            assert calls and calls[0].scene == "res://game/cave_blockout.tscn"
            preview = gateway.open_review("Grow a cave like my map")
            (item, *_) = preview["media"]
            assert item["label"] == "Grown level"
            assert item["reference"].endswith("cave-sample.png")
            assert Path(item["reference"]).read_bytes().startswith(png.SIGNATURE)
            assert Path(item["after"]).is_file()
        finally:
            await gateway.close()

    asyncio.run(go())


def test_the_gateway_serves_grow_level_to_changing_roles_only(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_godot() -> Path:
        raise ValueError("no Godot")

    monkeypatch.setattr("joinery.blockout.stage.playtest.godot_binary", no_godot)
    params = {"sample": ".joinery/attachments/map.png", "size": [20, 14], "seed": 2}

    async def go() -> None:
        gateway = ProjectGateway(project)
        lead = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")
        await lead.start()
        await reviewer.start()
        try:
            with pytest.raises(RuntimeError, match="Specialists cannot grow"):
                await rpc(reviewer.path, "grow_level", params)
            data = await rpc(lead.path, "grow_level", params)
            assert data["status"] == "staged"
        finally:
            await reviewer.close()
            await lead.close()

    asyncio.run(go())


def test_the_registry_tool_and_every_provider_judge_grow_level_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "plan.png"
    picture.write_bytes(_painted())
    asked: list[dict[str, Any]] = []

    async def fake(
        _socket: Path, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        assert method == "grow_level"
        asked.append(params)
        return {"status": "staged", "picture": str(picture)}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            tool = tools["grow_level"]
            assert not (tool.annotations and tool.annotations.read_only_hint)
            assert {"sample", "size"} <= set(tool.input_schema["required"])
            result = await client.call_tool(
                "grow_level", {"sample": "art/reference/map.png", "size": [24, 16]}
            )
        text, image = result.content
        assert isinstance(text, TextContent) and isinstance(image, ImageContent)
        assert asked[0]["seed"] == 1 and json.loads(text.text)["status"] == "staged"

    asyncio.run(go())
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    for role in ("lead", "worker"):
        assert registry_allowed(role, "grow_level")
    for role in ("reviewer", "debugger", "playtester"):
        assert not registry_allowed(role, "grow_level")
        monkeypatch.setenv("JOINERY_AGENT_ROLE", role)
        event = {
            "tool_name": "mcp__joinery-registry__grow_level",
            "tool_input": {},
            "cwd": str(tmp_path),
        }
        assert violation(event) is not None, role
    monkeypatch.delenv("JOINERY_AGENT_ROLE")
    from bridge.codex_session import registry_config

    argv = registry_config(tmp_path / "gateway.sock", "lead")
    assert (
        'mcp_servers.joinery-registry.tools.grow_level.approval_mode="approve"' in argv
    )
    assert "grow_level(sample, size, seed)" in BLOCKOUT_GUIDE
