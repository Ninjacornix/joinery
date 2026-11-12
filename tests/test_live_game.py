"""inspect_game and drive_game: look into the running game, and play it with timed input."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from joinery import live_game, roles
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from joinery.policy import Severity

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _text(value: Any, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value))], is_error=error
    )


class Game:
    """The runtime inspector as the Godot MCP answers it, recording each call."""

    def __init__(self, refuse: str = "") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refuse = refuse
        self.shot: CallToolResult = CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(PNG).decode(),
                    mime_type="image/png",
                )
            ]
        )

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((tool, arguments))
        if self.refuse and json.dumps(arguments).find(self.refuse) >= 0:
            return _text("no such action", error=True)
        if tool == "game_get_scene_tree":
            return _text({"name": "root", "children": [{"name": "Main"}]})
        if tool == "game_get_node_info":
            return _text({"path": arguments["nodePath"], "class": "CharacterBody3D"})
        if tool == "game_get_property":
            return _text({"value": [1, 2, 3]})
        if tool == "game_screenshot":
            return self.shot
        return _text({"ok": True})


class Clock:
    """A clock that moves only when the sequence sleeps: exact, repeatable timings."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


# --- Looking ---------------------------------------------------------------------------


def test_inspect_reads_the_tree_by_default(tmp_path: Path) -> None:
    game = Game()
    data = asyncio.run(live_game.inspect(game, tmp_path, {}))
    assert data["tree"]["children"][0]["name"] == "Main"
    assert [c[0] for c in game.calls] == ["game_get_scene_tree"]
    assert "never assets" in data["note"]


def test_inspect_reads_a_node_its_properties_and_a_screenshot(tmp_path: Path) -> None:
    game = Game()
    params = {
        "node": "/root/Main/Player",
        "properties": ["position", "health"],
        "screenshot": True,
    }
    data = asyncio.run(live_game.inspect(game, tmp_path, params))
    assert data["node"]["class"] == "CharacterBody3D"
    assert set(data["properties"]) == {"position", "health"}
    assert "tree" not in data
    shot = Path(data["screenshot"])
    assert shot.read_bytes() == PNG
    assert shot.is_relative_to(tmp_path / live_game.FOLDER)
    # Base64 in the text works too, as some inspector builds answer.
    game.shot = _text({"image": base64.b64encode(PNG).decode()})
    again = asyncio.run(live_game.inspect(game, tmp_path, {"screenshot": True}))
    assert Path(again["screenshot"]).read_bytes() == PNG


@pytest.mark.parametrize(
    ("params", "words"),
    [
        ({"properties": ["position"]}, "Name the node"),
        ({"node": "/root/x", "properties": ["bad name!"]}, "property names"),
        ({"node": "/root/x", "properties": ["p"] * 13}, "property names"),
    ],
)
def test_inspect_checks_its_arguments(
    tmp_path: Path, params: dict[str, Any], words: str
) -> None:
    with pytest.raises(ValueError, match=words):
        asyncio.run(live_game.inspect(Game(), tmp_path, params))


def test_a_refused_read_says_so(tmp_path: Path) -> None:
    game = Game(refuse="Ghost")
    with pytest.raises(ValueError, match="couldn't find /root/Ghost"):
        asyncio.run(live_game.inspect(game, tmp_path, {"node": "/root/Ghost"}))


# --- Playing ---------------------------------------------------------------------------


def _drive(game: Game, steps: list[dict[str, Any]]) -> dict[str, Any]:
    clock = Clock()
    return asyncio.run(
        live_game.drive(game, {"steps": steps}, sleep=clock.sleep, clock=clock)
    )


def test_a_tap_a_hold_and_a_wait_land_on_time() -> None:
    game = Game()
    result = _drive(
        game,
        [
            {"do": "tap", "action": "jump", "seconds": 0.2},
            {"do": "wait", "seconds": 0.5},
            {"do": "hold", "key": "D", "seconds": 1.5},
            {"do": "release", "key": "D"},
        ],
    )
    assert game.calls == [
        ("game_key_hold", {"action": "jump"}),
        ("game_key_release", {"action": "jump"}),
        ("game_key_hold", {"key": "D"}),
        ("game_key_release", {"key": "D"}),
    ]
    assert [(s["input"], s["down"], s["planned"]) for s in result["sent"]] == [
        ("action jump", True, 0.0),
        ("action jump", False, 0.2),
        ("key D", True, 0.7),
        ("key D", False, 2.2),
    ]
    assert result["planned_seconds"] == 2.2 and result["late_ms_max"] == 0.0
    assert result["released_at_end"] == []


def test_gamepad_buttons_and_sticks() -> None:
    game = Game()
    _drive(
        game,
        [
            {"do": "tap", "button": 0, "device": 1},
            {"do": "hold", "axis": 0, "value": -1.0, "seconds": 0.5},
        ],
    )
    assert game.calls == [
        ("game_gamepad", {"type": "button", "index": 0, "value": 1, "device": 1}),
        ("game_gamepad", {"type": "button", "index": 0, "value": 0, "device": 1}),
        ("game_gamepad", {"type": "axis", "index": 0, "value": -1.0, "device": 0}),
        # Still held at the end: the stick goes back to rest.
        ("game_gamepad", {"type": "axis", "index": 0, "value": 0.0, "device": 0}),
    ]


def test_nothing_stays_held_when_the_game_refuses_a_step() -> None:
    game = Game(refuse="dash")
    with pytest.raises(ValueError, match="refused action dash"):
        _drive(
            game,
            [
                {"do": "hold", "action": "move_right", "seconds": 1.0},
                {"do": "tap", "action": "dash"},
            ],
        )
    assert game.calls[-1] == ("game_key_release", {"action": "move_right"})


@pytest.mark.parametrize(
    ("steps", "words"),
    [
        ([], "1 to 64"),
        ([{"do": "tap"}], "one of action, key, button or axis"),
        ([{"do": "tap", "action": "jump", "key": "W"}], "one of action"),
        ([{"do": "spin", "action": "jump"}], "tap, hold, release or wait"),
        ([{"do": "wait"}], "lasts some seconds"),
        ([{"do": "tap", "key": "Space; rm"}], "key name"),
        ([{"do": "hold", "axis": 1, "value": 3}], "from -1 to 1"),
        ([{"do": "wait", "seconds": 9}] * 4, "at most 30 seconds"),
    ],
)
def test_sequences_are_checked_before_anything_is_sent(
    steps: list[dict[str, Any]], words: str
) -> None:
    game = Game()
    with pytest.raises(ValueError, match=words):
        _drive(game, steps)
    assert game.calls == []


# --- Who may --------------------------------------------------------------------------


def test_every_role_may_look_and_only_writers_drive_by_default(tmp_path: Path) -> None:
    for role in roles.ROLES:
        assert roles.registry_allowed(role, roles.INSPECT_GAME), role
    assert roles.may_drive("lead", tmp_path) and roles.may_drive("worker", None)
    assert not roles.may_drive("playtester", tmp_path)
    (tmp_path / "joinery.toml").write_text(
        '[agents]\ngame_input_roles = ["playtester", "reviewer"]\n'
    )
    assert roles.may_drive("playtester", tmp_path)
    # Only the playtester and the debugger can ever be let drive.
    assert not roles.may_drive("reviewer", tmp_path)
    assert not roles.may_drive("debugger", tmp_path)


def test_the_hook_judges_drive_game_for_every_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv(roles.ROLE_ENV, "playtester")
    # Claude and Codex spell MCP tools one way, Gemini another: the same verdict.
    for name in (
        "mcp__joinery-registry__drive_game",
        "mcp_joinery-registry_drive_game",
    ):
        verdict = violation({"tool_name": name, "tool_input": {"steps": []}})
        assert verdict is not None and verdict.severity == Severity.DENY
        assert "game_input_roles" in verdict.reason
        assert violation({"tool_name": name.replace("drive", "inspect")}) is None
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n[agents]\ngame_input_roles = ["playtester"]\n'
    )
    assert violation({"tool_name": "mcp__joinery-registry__drive_game"}) is None
    monkeypatch.setenv(roles.ROLE_ENV, "reviewer")
    assert violation({"tool_name": "mcp__joinery-registry__drive_game"}) is not None


def test_the_gateway_needs_a_game_it_started_and_judges_the_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def run() -> None:
        gateway = ProjectGateway(tmp_path)
        lead = GatewayRPC(gateway)
        tester = GatewayRPC(gateway, role="playtester")
        await lead.start()
        await tester.start()
        try:
            steps = {"steps": [{"do": "tap", "action": "jump", "seconds": 0}]}
            with pytest.raises(RuntimeError, match="No game is running"):
                await rpc(lead.path, "inspect_game", {})
            game = Game()
            gateway.runtime_owned = True
            monkeypatch.setattr(gateway, "game_call", game)
            assert (await rpc(tester.path, "inspect_game", {}))["tree"]
            with pytest.raises(RuntimeError, match="game_input_roles"):
                await rpc(tester.path, "drive_game", steps)
            sent = await rpc(lead.path, "drive_game", steps)
            assert [s["input"] for s in sent["sent"]] == ["action jump"] * 2
            (tmp_path / "joinery.toml").write_text(
                '[tools]\nenabled=["godot"]\n[agents]\ngame_input_roles=["playtester"]\n'
            )
            assert (await rpc(tester.path, "drive_game", steps))["sent"]
        finally:
            await tester.close()
            await lead.close()

    asyncio.run(run())


def test_only_reads_and_input_reach_the_game(tmp_path: Path) -> None:
    gateway = ProjectGateway.__new__(ProjectGateway)
    gateway.runtime_owned = True
    for tool in ("game_set_property", "game_eval", "game_http_request", "run_project"):
        with pytest.raises(ValueError, match="not a running-game read or input"):
            asyncio.run(gateway.game_call(tool, {}))
