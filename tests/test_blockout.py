"""Sketch to blockout (ADR 0043): the layout format, its checks, the walk, the build and
the tool every agent calls. Godot itself builds and walks the scenes in
tests/blueprints/test_blockout.py."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent

from bridge.instructions import BLOCKOUT_GUIDE, REVIEW_GUIDE, expand_command
from joinery import blockout
from joinery.blockout import stage
from joinery.blockout.layout import LayoutError, parse, to_toml
from joinery.blockout.world import profile_for
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from joinery.roles import registry_allowed
from servers.joinery_registry.proxy import make_server

FIXTURES = Path(__file__).parent / "fixtures" / "blockout"
NAMES = ("keep", "house", "cliffs", "dungeon")
BLUEPRINTS = {
    "keep": "third-person-3d",
    "house": "first-person-3d",
    "cliffs": "platformer-2d",
    "dungeon": "top-down",
}


def _layout(name: str) -> str:
    return (FIXTURES / f"{name}.toml").read_text(encoding="utf-8")


def _make(text: Any, blueprint: str = "", tmp: Path | None = None) -> blockout.Built:
    return blockout.make(tmp or FIXTURES, text, blueprint=blueprint or None)


def _rooms(extra: str = "", spawn: str = "[2, 2]") -> str:
    return (
        f'name = "test"\nmode = "3d"\n\n[spawn]\nat = {spawn}\n\n'
        '[[rooms]]\nid = "a"\nrect = [0, 0, 6, 6]\n\n'
        '[[rooms]]\nid = "b"\nrect = [6, 0, 6, 6]\n\n'
        '[[markers]]\nkind = "goal"\nid = "exit"\nat = [9, 3]\n' + extra
    )


DOOR = '\n[[openings]]\nroom = "a"\nwall = "east"\n'


def _errors(built: blockout.Built) -> list[str]:
    return [p.text for p in built.checked.errors]


# --- The format -----------------------------------------------------------------------


def test_fixture_layouts_are_canonical_and_round_trip() -> None:
    for name in NAMES:
        text = _layout(name)
        assert to_toml(parse(text)) == text, name
        assert parse(to_toml(parse(text))) == parse(text)


def test_unreadable_layouts_name_the_field() -> None:
    with pytest.raises(LayoutError) as found:
        parse({"rooms": [{"id": "a", "rect": [0, 0, 4]}], "mode": "4d"})
    text = "; ".join(found.value.problems)
    assert "rooms[0].rect" in text and "mode" in text
    with pytest.raises(LayoutError, match="not valid TOML"):
        parse("rooms = [")
    with pytest.raises(LayoutError, match="res:// .tscn"):
        blockout.make(FIXTURES, _rooms(DOOR), scene="res://addons/x.tscn")


# --- Checks and the walk ----------------------------------------------------------------


def test_every_fixture_is_sound_and_walkable_for_its_starting_point() -> None:
    for name in NAMES:
        built = _make(_layout(name), BLUEPRINTS[name])
        assert built.ok, (name, _errors(built))
        reach = built.checked.reach
        assert reach is not None
        total = len(reach.targets)
        assert reach.fact() == f"Walkable: spawn reaches {total} of {total} goals", name
        assert all(t.route for t in reach.targets)


def test_a_room_without_a_door_says_so() -> None:
    built = _make(_rooms())
    assert not built.ok
    (problem,) = _errors(built)
    assert "goal exit in room b can't be reached" in problem
    assert "Room b has no door" in problem
    assert _make(_rooms(DOOR)).ok


def test_shapes_and_sizes_are_checked() -> None:
    narrow = _make(_rooms(DOOR + "width = 0.5\n"))
    assert any("0.5 wide" in e and "needs at least" in e for e in _errors(narrow))
    low = _make(_rooms(DOOR + "height = 1.5\n"))
    assert any("1.5 m high" in e for e in _errors(low))
    overlap = _make(_rooms(DOOR + '\n[[rooms]]\nid = "c"\nrect = [4, 4, 4, 4]\n'))
    assert any("Rooms a and c overlap" in e for e in _errors(overlap))
    steep = _make(
        _rooms(
            DOOR
            + '\n[[stairs]]\nid = "s"\nrect = [0, 6, 2, 1]\nup = "south"\nbottom = 0\ntop = 3\n'
        )
    )
    assert any("Stairs s rise 3 m" in e and "at least" in e for e in _errors(steep))
    no_spawn = _make('name = "x"\n[[rooms]]\nid = "a"\nrect = [0, 0, 4, 4]\n')
    assert any("Add a spawn" in e for e in _errors(no_spawn))
    wrong = _make(_rooms(DOOR), "platformer-2d")
    assert any('mode = "2d-side"' in e for e in _errors(wrong))


def test_an_opening_on_a_missing_wall_or_too_wide_is_named() -> None:
    built = _make(_rooms('\n[[openings]]\nroom = "a"\nwall = "e2"\n'))
    assert any("has no wall e2" in e for e in _errors(built))
    built = _make(_rooms(DOOR + "at = 5.8\n"))
    assert any("doesn't fit in a wall 6 long" in e for e in _errors(built))


def test_the_walk_knows_what_each_player_can_jump() -> None:
    ledge = _rooms(
        DOOR
        + '\n[[platforms]]\nid = "shelf"\nrect = [8, 3, 3, 2]\ntop = 1.5\n'
        + '\n[[markers]]\nkind = "coin"\nat = [9.5, 4]\n'
    )
    assert _make(ledge, "third-person-3d").ok
    low_jump = _make(ledge, "first-person-3d")
    assert any("Platform shelf is 1.5 m up" in e for e in _errors(low_jump))


def test_routes_keep_off_hazards_when_there_is_another_way() -> None:
    reach = _make(_layout("cliffs"), "platformer-2d").checked.reach
    assert reach is not None
    goal = next(t for t in reach.targets if t.kind == "goal")
    # The pit holds the hazard: the route jumps over it instead of dropping in.
    assert all(y < 14 * 16 for _x, y, _h, _j in goal.route)
    assert any(jump for *_rest, jump in goal.route)


# --- Walls ----------------------------------------------------------------------------


def test_a_shared_wall_is_built_once_with_the_door_cut_in_it() -> None:
    world = _make(_rooms(DOOR)).checked.world
    shared = [
        p for p in world.pieces if abs(p.a[0] - 6) < 1e-6 and abs(p.b[0] - 6) < 1e-6
    ]
    owners = {p.owner for p in shared}
    assert owners == {"Rooms/a"}
    walls = sorted((p.a[1], p.b[1]) for p in shared if p.kind == "wall")
    assert walls == [(-0.1, 2.5), (3.5, 6.1)]
    (lintel,) = [p for p in shared if p.kind == "lintel"]
    assert lintel.base == pytest.approx(2.2) and lintel.top == pytest.approx(3.0)


def test_corridors_cut_room_walls_and_windows_keep_sill_and_lintel() -> None:
    world = _make(_layout("keep")).checked.world
    hall_south = [
        p
        for p in world.pieces
        if p.owner == "Rooms/hall" and p.name.startswith("south")
    ]
    gaps = sorted(round(p.a[0], 2) for p in hall_south)
    # The corridor link (x 2..4) opens the hall's south wall.
    assert [p for p in hall_south if 2 < p.centre[0] < 4] == []
    assert len(gaps) == 2
    house = _make(_layout("house")).checked.world
    kitchen = [
        p.kind for p in house.pieces if p.owner == "Rooms/kitchen" and "north" in p.name
    ]
    assert kitchen.count("window") == 1 and kitchen.count("lintel") == 1
    dungeon = _make(_layout("dungeon"), "top-down").checked.world
    assert all(p.top == 0 for p in dungeon.pieces)


# --- The build ------------------------------------------------------------------------


def test_the_same_layout_builds_the_same_bytes_in_3d_and_2d(tmp_path: Path) -> None:
    for name in NAMES:
        one = _make(_layout(name), BLUEPRINTS[name], tmp_path)
        two = _make(_layout(name), BLUEPRINTS[name], tmp_path)
        assert one.files == two.files, name
        assert one.plan_png == two.plan_png and one.plan_png.startswith(b"\x89PNG")
        assert set(one.files) == {
            f"design/blockouts/{name}.toml",
            f"game/{name}_blockout.tscn",
            "game/blockout/blockout_level.gd",
        }


def test_the_scene_names_every_piece_and_records_its_layout(tmp_path: Path) -> None:
    built = _make(_layout("keep"), "third-person-3d", tmp_path)
    scene = built.files["game/keep_blockout.tscn"].decode()
    assert scene.startswith("[gd_scene load_steps=")
    assert 'path="res://game/blockout/blockout_level.gd"' in scene
    assert '[node name="Floor" type="CSGBox3D" parent="Rooms/hall"]' in scene
    assert '[node name="Ramp" type="CSGBox3D" parent="Stairs/steps"]' in scene
    assert '[node name="exit" type="Area3D" parent="Markers" groups=["goal"]]' in scene
    assert (
        '[node name="Spawn" type="Marker3D" parent="Markers" groups=["player_spawn"]]'
        in scene
    )
    assert '[node name="Players" type="Node3D" parent="."]' in scene
    assert scene.count("use_collision = true") >= 20
    assert 'metadata/blockout_layout = "res://design/blockouts/keep.toml"' in scene
    side = _make(_layout("cliffs"), "platformer-2d", tmp_path).files[
        "game/cliffs_blockout.tscn"
    ]
    assert b'type="StaticBody2D"' in side and b'type="CollisionShape2D"' in side
    assert b'groups=["hazard"]' in side
    # A project's own (perhaps edited) level script is never replaced.
    (tmp_path / blockout.SCRIPT_FILE).parent.mkdir(parents=True)
    (tmp_path / blockout.SCRIPT_FILE).write_text("extends Node\n")
    assert blockout.SCRIPT_FILE not in _make(_layout("keep"), "", tmp_path).files


def test_the_cli_rebuilds_the_scene_from_its_layout(tmp_path: Path) -> None:
    from joinery.cli import main

    layout = tmp_path / "design/blockouts/keep.toml"
    layout.parent.mkdir(parents=True)
    layout.write_text(_layout("keep"))
    assert main(["blockout", "build", str(layout), "--project", str(tmp_path)]) == 0
    expected = _make(_layout("keep"), "", tmp_path)
    assert (tmp_path / "game/keep_blockout.tscn").read_bytes() == expected.files[
        "game/keep_blockout.tscn"
    ]
    assert (
        main(
            [
                "blockout",
                "check",
                str(FIXTURES / "keep.toml"),
                "--project",
                str(tmp_path),
            ]
        )
        == 0
    )


def test_the_sample_sketches_are_drawn_by_code_from_their_layouts() -> None:
    spec = importlib.util.spec_from_file_location(
        "make_sketches", FIXTURES / "make_sketches.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in NAMES:
        assert module.sketch(name) == (FIXTURES / f"{name}.png").read_bytes(), name


def test_accuracy_compares_an_agents_layout_with_the_expected_one() -> None:
    from joinery.blockout.accuracy import compare

    expected = parse(_layout("keep"))
    assert compare(expected, expected)["score"] == 1.0
    moved = parse(_layout("keep").replace("rect = [0, 0, 8, 6]", "rect = [0, 0, 7, 6]"))
    result = compare(expected, moved)
    assert 0.8 < result["score"] < 1.0 and result["rooms"] == "3 of 3"
    # Another origin and unit (sketch pixels at scale 0.05) is the same level.
    doubled = expected.model_copy(deep=True)
    doubled.scale = 0.05
    for room in doubled.rooms:
        assert room.rect is not None
        room.rect = [
            room.rect[0] * 20 + 100,
            room.rect[1] * 20,
            room.rect[2] * 20,
            room.rect[3] * 20,
        ]
    assert compare(expected, doubled)["rooms"] == "3 of 3"
    assert compare(expected, parse(_rooms()))["score"] < 0.6


# --- The tool, the same for every agent ---------------------------------------------------


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
    monkeypatch.setattr(
        "joinery.blockout.project_blueprint", lambda _p: "third-person-3d"
    )
    return tmp_path


def _walked(calls: list[Any], reached: bool = True) -> Any:
    async def run_async(options: Any, progress: Any = None) -> dict[str, Any]:
        calls.append(options)
        route = json.loads(Path(options.route).read_text())
        return {
            "status": "played",
            "items": [],
            "route": [
                {"id": t["id"], "kind": t["kind"], "reached": reached, "closest": 0.4}
                for t in route["targets"]
            ],
        }

    return run_async


def test_the_tool_stages_the_blockout_walks_it_and_feeds_the_review(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr("joinery.blockout.stage.playtest.run_async", _walked(calls))
    monkeypatch.setattr(
        "joinery.blockout.stage.playtest.godot_binary", lambda: Path("godot")
    )
    sketch = project / ".joinery/attachments/sketch.png"
    sketch.parent.mkdir(parents=True)
    sketch.write_bytes((FIXTURES / "keep.png").read_bytes())

    async def go() -> None:
        gateway = ProjectGateway(project)
        gateway.attachments = (str(sketch),)
        try:
            bad = await stage.tool(gateway, {"layout": _rooms()})
            assert bad["status"] == "problems" and gateway.transaction is None
            assert "Room b has no door" in bad["problems"][0]["text"]
            assert Path(bad["picture"]).is_file()
            result = await stage.tool(
                gateway, {"layout": parse(_layout("keep")).model_dump()}
            )
            assert result["status"] == "staged", result
            assert result["facts"] == ["Walkable: spawn reaches 3 of 3 goals"]
            assert result["walk"]["how"].startswith("walked in Godot")
            tx = gateway.transaction
            assert tx is not None
            assert (tx.stage / "game/keep_blockout.tscn").is_file()
            assert (tx.stage / "design/blockouts/keep.toml").read_text() == _layout(
                "keep"
            )
            assert not (project / "game/keep_blockout.tscn").exists()
            assert (
                calls[0].project == tx.stage
                and calls[0].scene == "res://game/keep_blockout.tscn"
            )
            preview = gateway.open_review("Build the keep")
            assert preview["joinery_facts"] == ["Walkable: spawn reaches 3 of 3 goals"]
            (item, *_) = preview["media"]
            assert item["reference"] == str(sketch.resolve())
            assert Path(item["after"]).is_file() and "before" not in item
        finally:
            await gateway.close()

    asyncio.run(go())


def test_a_walk_that_misses_in_godot_says_so(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.blockout.stage.playtest.run_async", _walked([], False))
    monkeypatch.setattr(
        "joinery.blockout.stage.playtest.godot_binary", lambda: Path("godot")
    )

    async def go() -> None:
        gateway = ProjectGateway(project)
        try:
            result = await stage.tool(gateway, {"layout": _layout("keep")})
            assert result["facts"] == ["Walkable: spawn reaches 0 of 3 goals"]
            assert any("didn't reach goal exit" in w for w in result["warnings"])
        finally:
            await gateway.close()

    asyncio.run(go())


def test_the_gateway_serves_blockout_to_changing_roles_only(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.blockout.stage.playtest.godot_binary", _no_godot)

    async def go() -> None:
        gateway = ProjectGateway(project)
        lead = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")
        await lead.start()
        await reviewer.start()
        try:
            with pytest.raises(RuntimeError, match="Specialists cannot build"):
                await rpc(reviewer.path, "blockout", {"layout": _layout("keep")})
            data = await rpc(lead.path, "blockout", {"layout": _layout("keep")})
            assert data["status"] == "staged"
            assert data["walk"]["how"].startswith("checked on the plan")
        finally:
            await reviewer.close()
            await lead.close()

    asyncio.run(go())


def _no_godot() -> Path:
    raise ValueError("no Godot")


def test_the_registry_tool_returns_the_answer_and_the_plan_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "plan.png"
    picture.write_bytes((FIXTURES / "keep.png").read_bytes())
    asked: list[dict[str, Any]] = []

    async def fake(
        _socket: Path, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        assert method == "blockout"
        asked.append(params)
        return {"status": "staged", "picture": str(picture)}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            tool = tools["blockout"]
            assert not (tool.annotations and tool.annotations.read_only_hint)
            assert "layout" in tool.input_schema["required"]
            assert "rooms" in json.dumps(tool.input_schema)
            result = await client.call_tool(
                "blockout",
                {"layout": parse(_layout("dungeon")).model_dump(exclude_none=True)},
            )
        text, image = result.content
        assert isinstance(text, TextContent) and isinstance(image, ImageContent)
        assert asked[0]["layout"]["mode"] == "2d-topdown"

    asyncio.run(go())


def test_every_provider_and_role_judges_blockout_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    for role in ("lead", "worker"):
        assert registry_allowed(role, "blockout")
    for role in ("reviewer", "debugger", "playtester"):
        assert not registry_allowed(role, "blockout")
        monkeypatch.setenv("JOINERY_AGENT_ROLE", role)
        event = {
            "tool_name": "mcp__joinery-registry__blockout",
            "tool_input": {},
            "cwd": str(tmp_path),
        }
        assert violation(event) is not None, role
    monkeypatch.delenv("JOINERY_AGENT_ROLE")
    # Codex asks before every MCP call unless the tool is pre-approved.
    source = Path("bridge/codex_session.py").read_text()
    assert 'tools.blockout.approval_mode="approve"' in source


def test_agents_are_told_and_slash_blockout_becomes_a_request() -> None:
    assert BLOCKOUT_GUIDE in REVIEW_GUIDE and "blockout(layout)" in BLOCKOUT_GUIDE
    request = expand_command("/blockout make the vault bigger")
    assert request.startswith("Build a greybox blockout") and request.endswith(
        "make the vault bigger"
    )
    assert expand_command("/blockoutish") == "/blockoutish"
    assert expand_command("build this level") == "build this level"


def test_profiles_fall_back_to_the_mode() -> None:
    assert profile_for("platformer-2d", "3d").blueprint == ""
    assert profile_for("top-down", "2d-topdown").radius == 18.0


def test_slash_blockout_in_the_chat_opens_a_review_with_the_walk_first(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import AsyncIterator

    from bridge.protocol import Done, Message
    from joinery.runner import Result
    from tests.test_crew import HostTest

    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

    async def offline(_argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    monkeypatch.setattr("joinery.blockout.stage.playtest.godot_binary", _no_godot)
    host = HostTest(project, monkeypatch)

    async def agent(lane: ProjectGateway) -> AsyncIterator[Any]:
        result = await stage.tool(lane, {"layout": _layout("keep")})
        assert lane.turn is not None
        submitted = await lane.turn.submit("Built the keep", "agent", ["3 rooms"])
        yield Message(text=f"{result['status']} {submitted.status}")
        yield Done(ok=True)

    host.scripts = {"Build a greybox blockout": agent}
    host.lines = [
        (
            lambda _e: True,
            {"kind": "prompt", "text": "/blockout keep the tower", "chat_id": ""},
        ),
        (lambda e: any(x.get("kind") == "file_preview" for x in e), {"kind": "cancel"}),
    ]
    host.run()
    preview = next(e for e in host.events if e.get("kind") == "file_preview")
    assert preview["facts"] == ["Walkable: spawn reaches 3 of 3 goals", "3 rooms"]
    assert "joinery_facts" not in preview
    assert preview["media"][0]["path"] == "design/blockouts/keep.toml"
    paths = {c["path"] for c in preview["changes"]}
    assert {"design/blockouts/keep.toml", "game/keep_blockout.tscn"} <= paths


def test_a_playtest_with_a_route_only_walks_it(tmp_path: Path) -> None:
    from joinery.playtest import Options, _bot_args
    from joinery.playtest_report import build

    route = tmp_path / "route.json"
    args = _bot_args(Options(project=tmp_path, route=route), tmp_path)
    assert f"--route={route}" in args
    bot = {
        "finished": True,
        "seconds_played": 9,
        "route": [{"id": "exit", "reached": True}],
    }
    report = build(bot, "", 0, options={"seed": 1, "seconds": 9})
    assert report["route"] == [{"id": "exit", "reached": True}]
    assert not [i for i in report["items"] if i["kind"] == "balance"]
