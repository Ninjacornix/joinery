"""The playtester role and the playtest tool, with a fake provider and a fake engine.

The engine itself is tested in tests/test_playtest.py and, in real Godot, in
tests/blueprints/test_playtest.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent

from bridge.instructions import PLAYTEST_PROMPT, REVIEW_GUIDE
from bridge.playtester import (
    BUSY,
    Playtester,
    gameplay_change,
    split_followups,
    tool_params,
)
from bridge.protocol import Done, MessageDelta, Prompt
from joinery import playtest_report
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from servers.joinery_registry.proxy import make_server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WRITE_UP = (
    "Bugs: the lever script calls a freed node (high), res://game/props/lever.gd:40, "
    "seed 42 at 0:12.\n"
    "Fix: In res://game/props/lever.gd line 40, check the door still exists before "
    "opening it; replay with joinery playtest --seed 42.\n"
    "- Fix: Make the first coin easier to reach.\n"
)


def _report(options: Any) -> dict[str, Any]:
    bot = {
        "finished": True,
        "fps": 60,
        "seconds_played": options.seconds,
        "core": True,
        "interactive": True,
        "style": "3d",
        "goals": {"completed": [], "failed": [], "score_changes": 1},
        "findings": [{"kind": "softlock", "detail": "Stuck.", "frame": 720, "t": 12.0}],
        "perf": {"spikes": [], "samples": [], "respawns": 0},
        "log": [],
    }
    public = {**options.public(), "seed": options.seed or 42}
    report = playtest_report.build(bot, "", 0, options=public)
    report["screenshots"] = [str(options.project / "shot.png")]
    return report


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self, options: Any, progress: Any) -> dict[str, Any]:
        self.calls.append(options)
        for t in (1.0, 2.0, 2.5):
            progress(t, options.seconds)
        await self.release.wait()
        return _report(options)


class FakeEndpoint:
    def __init__(self) -> None:
        self.path = Path("gateway.sock")
        self.playtest: Any = None
        self.closed = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeAgent:
    """A provider that calls playtest through its endpoint, then writes ``text``."""

    def __init__(self, endpoint: FakeEndpoint, text: str, play: bool = True) -> None:
        self.endpoint = endpoint
        self.text = text
        self.play = play
        self.prompts: list[Prompt] = []
        self.closed = False

    def bind_gateway(self, path: Any) -> None:
        assert path == self.endpoint.path

    async def connect(self, _project: Any, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        # Narration before the run is not the report.
        yield MessageDelta(text="I'll play the game now.", new_message=True)
        if self.play:
            result = await self.endpoint.playtest({"minutes": 0.5, "focus": "coins"})
            assert result["counts"]["high"] == 1
        yield MessageDelta(text=self.text, new_message=True)
        yield Done(ok=True)

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def _playtester(
    tmp_path: Path, text: str = WRITE_UP, play: bool = True
) -> tuple[Playtester, list[dict[str, Any]], FakeEngine, list[FakeAgent]]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    engine = FakeEngine()
    endpoint = FakeEndpoint()
    agents: list[FakeAgent] = []

    def bridge(_agent: str) -> FakeAgent:
        agents.append(FakeAgent(endpoint, text, play))
        return agents[-1]

    tester = Playtester(
        tmp_path,
        events.append,
        agent="codex",
        profile="",
        bridge=bridge,
        endpoint=lambda: endpoint,
        runner=engine,
    )
    return tester, events, engine, agents


def test_followups_are_the_fix_lines() -> None:
    summary, followups = split_followups(WRITE_UP)
    assert summary.startswith("Bugs: the lever script")
    assert "Fix:" not in summary
    assert followups == [
        (
            "In res://game/props/lever.gd line 40, check the door still exists before "
            "opening it; replay with joinery playtest --seed 42."
        ),
        "Make the first coin easier to reach.",
    ]


def test_gameplay_changes_are_code_and_scenes() -> None:
    assert gameplay_change([{"path": "game/player/player.gd"}])
    assert gameplay_change([{"path": "game/level.tscn"}, {"path": "art/a.png"}])
    assert gameplay_change([{"path": "project.godot"}])
    assert not gameplay_change([{"path": "art/bible.md"}, {"path": "audio/hit.wav"}])


def test_tool_arguments_are_checked() -> None:
    assert tool_params({}, 2.0) == {"minutes": 2.0, "seed": 0, "focus": ""}
    assert tool_params({"minutes": 1, "seed": 7, "focus": " lever "}, 2.0) == {
        "minutes": 1.0,
        "seed": 7,
        "focus": "lever",
    }
    for bad in ({"minutes": 60}, {"minutes": True}, {"seed": -1}, {"focus": "x" * 300}):
        with pytest.raises(ValueError):
            tool_params(bad, 2.0)


def test_the_playtester_plays_then_writes_a_report_with_followups(
    tmp_path: Path,
) -> None:
    async def go() -> None:
        tester, events, engine, agents = _playtester(tmp_path)
        run = tester.start("the lever")
        assert run.job is not None
        await run.job
        states = [e["state"] for e in events]
        assert states[0] == "starting"
        assert states[1:4] == ["playing", "playing", "playing"]
        assert states[-2:] == ["writing", "done"]
        assert [e["elapsed"] for e in events if e["state"] == "playing"] == [
            0.0,
            1.0,
            2.0,
        ]
        done = events[-1]
        assert done["playtest_id"] == run.id and done["origin"] == "user"
        assert done["report"]["counts"]["high"] == 1
        assert done["report"]["items"][0]["repro"].startswith("Run `joinery playtest")
        assert done["summary"].startswith("Bugs:")
        assert len(done["followups"]) == 2
        # The agent's tool call played with its own arguments, screenshots on.
        options = engine.calls[0]
        assert (
            options.seconds == 30 and options.focus == "coins" and options.screenshots
        )
        prompt = agents[0].prompts[0].text
        assert "minutes=2" in prompt and "the lever" in prompt and "Fix: " in prompt
        assert agents[0].closed and not tester.busy()

    asyncio.run(go())


def test_a_playtester_that_never_plays_still_leaves_a_report(tmp_path: Path) -> None:
    async def go() -> None:
        tester, events, engine, _ = _playtester(tmp_path, "All good.", play=False)
        run = tester.start()
        assert run.job is not None
        await run.job
        assert len(engine.calls) == 1
        assert events[-1]["state"] == "done"
        assert events[-1]["report"]["counts"]["high"] == 1
        assert events[-1]["summary"] == "All good."

    asyncio.run(go())


def test_one_playtest_at_a_time_and_stop(tmp_path: Path) -> None:
    async def go() -> None:
        tester, events, engine, _ = _playtester(tmp_path)
        engine.release.clear()
        run = tester.start()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if engine.calls:
                break
        with pytest.raises(ValueError, match="already running"):
            tester.start()
        with pytest.raises(ValueError, match="already running"):
            await tester.tool({})
        await tester.stop(run.id)
        assert events[-1]["state"] == "stopped"
        assert not tester.busy()
        assert BUSY.startswith("A playtest")

    asyncio.run(go())


def test_another_agent_calling_playtest_gets_its_own_card(tmp_path: Path) -> None:
    async def go() -> None:
        tester, events, engine, agents = _playtester(tmp_path)
        result = await tester.tool({"minutes": 1, "seed": 9})
        assert result["seed"] == 9 and result["summary"].startswith("Played")
        assert "never assets" in result["note_for_you"]
        assert engine.calls[0].seed == 9
        assert {e["origin"] for e in events} == {"agent"}
        assert events[-1]["state"] == "done" and "summary" not in events[-1]
        assert agents == []

    asyncio.run(go())


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return tmp_path


def test_every_endpoint_serves_playtest_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def go() -> None:
        gateway = ProjectGateway(_project(tmp_path, monkeypatch))
        main = GatewayRPC(gateway)
        inspector = GatewayRPC(gateway, role="playtester")
        await main.start()
        await inspector.start()
        try:
            with pytest.raises(RuntimeError, match="not available"):
                await rpc(main.path, "playtest", {})
            seen: list[dict[str, Any]] = []

            async def play(params: dict[str, Any]) -> dict[str, Any]:
                seen.append(params)
                return {"summary": "Played 0:30"}

            main.playtest = inspector.playtest = play
            assert (await rpc(main.path, "playtest", {"seed": 1}))["summary"]
            assert (await rpc(inspector.path, "playtest", {}))["summary"]
            assert seen == [{"seed": 1}, {}]
            assert inspector.inspection_only
            with pytest.raises(RuntimeError, match="Only the reviewer"):
                await rpc(inspector.path, "report_review", {})
        finally:
            await inspector.close()
            await main.close()

    asyncio.run(go())


def test_the_read_only_playtester_may_call_playtest_and_nothing_that_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch)
    monkeypatch.setenv("JOINERY_AGENT_ROLE", "playtester")
    for tool, allowed in (
        ("playtest", True),
        ("view_editor", True),
        ("submit_for_review", False),
        ("start_task", False),
        ("report_review", False),
    ):
        event = {
            "tool_name": f"mcp__joinery-registry__{tool}",
            "tool_input": {},
            "cwd": str(tmp_path),
        }
        assert (violation(event) is None) is allowed, tool


def test_the_tool_returns_the_report_and_screenshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    data = {"summary": "Played 0:30 (seed 4): 1 high.", "screenshots": [str(shot)]}
    asked: list[dict[str, Any]] = []

    async def fake(
        _socket: Path, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        assert method == "playtest"
        asked.append(params)
        return data

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["playtest"].annotations is not None
            assert tools["playtest"].annotations.read_only_hint is True
            result = await client.call_tool("playtest", {"minutes": 0.5, "seed": 4})
        text, image = result.content
        assert isinstance(text, TextContent) and isinstance(image, ImageContent)
        assert json.loads(text.text)["summary"].startswith("Played")
        assert base64.b64decode(image.data) == PNG
        assert asked == [{"minutes": 0.5, "seed": 4, "focus": ""}]

    asyncio.run(go())


def test_agents_are_told_about_playtest() -> None:
    assert "playtest(" in REVIEW_GUIDE
    assert "{minutes}" in PLAYTEST_PROMPT and "{focus}" in PLAYTEST_PROMPT
    assert "never change it" in PLAYTEST_PROMPT


# --- The chat host ---------------------------------------------------------------


@pytest.fixture
def host_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The chat host's project, as tests/test_crew.py sets it up."""
    from joinery.runner import Result
    from tests.test_crew import FakeConnection

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

    async def offline(_argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    return tmp_path


def _host(project: Path, monkeypatch: pytest.MonkeyPatch, engine: FakeEngine) -> Any:
    from bridge import stdio
    from tests.test_crew import HostTest

    host = HostTest(project, monkeypatch)
    lead_bridge, lead_rpc = stdio.Bridge, stdio.GatewayRPC
    endpoint = FakeEndpoint()

    def bridge(agent: str, **kwargs: Any) -> Any:
        if kwargs.get("role") == "playtester":
            return FakeAgent(endpoint, WRITE_UP)
        return lead_bridge(agent, **kwargs)

    def rpc_factory(gateway: Any, stop: Any = None, **kwargs: Any) -> Any:
        if kwargs.get("role") == "playtester":
            return endpoint
        return lead_rpc(gateway, stop, **kwargs)

    monkeypatch.setattr(stdio, "Bridge", bridge)
    monkeypatch.setattr(stdio, "GatewayRPC", rpc_factory)
    monkeypatch.setattr("bridge.playtester.playtest.run_async", engine)
    return host


def _playtest(events: list[dict[str, Any]], state: str) -> dict[str, Any] | None:
    return next(
        (e for e in events if e.get("kind") == "playtest" and e["state"] == state), None
    )


def test_slash_playtest_runs_the_playtester_from_the_host(
    host_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = host_project
    engine = FakeEngine()
    host = _host(project, monkeypatch, engine)
    host.lines = [
        (lambda _e: True, {"kind": "playtest_start", "focus": "the lever"}),
        (lambda e: _playtest(e, "done") is not None, {"kind": "cancel_tasks"}),
    ]
    host.run()
    done = _playtest(host.events, "done")
    assert done is not None and done["focus"] == "the lever"
    assert done["followups"] and done["report"]["counts"]["high"] == 1
    assert len(engine.calls) == 1


def test_an_accepted_gameplay_change_starts_a_playtest_when_asked(
    host_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = host_project
    from tests.test_crew import makes

    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n\n[agents]\nplaytest_after_change = true\n'
    )
    engine = FakeEngine()
    host = _host(project, monkeypatch, engine)
    host.scripts = {"Make the player faster": makes("game/player.gd", "extends Node\n")}

    def decide(events: list[dict[str, Any]]) -> bool:
        preview = next((e for e in events if e.get("kind") == "file_preview"), None)
        if preview is not None:
            host.lines.append(
                (
                    lambda _e: True,
                    {
                        "kind": "decision",
                        "transaction_id": preview["transaction_id"],
                        "accept": True,
                    },
                )
            )
        return False

    host.lines = [
        (
            lambda _e: True,
            {"kind": "prompt", "text": "Make the player faster", "chat_id": ""},
        ),
        (decide, {}),
        (lambda e: _playtest(e, "done") is not None, {"kind": "cancel_tasks"}),
    ]
    host.run()
    done = _playtest(host.events, "done")
    assert done is not None and done["origin"] == "after_change"
    assert (project / "game/player.gd").is_file()


def test_the_playtester_is_a_read_only_role_and_playtest_a_shared_read() -> None:
    from joinery.roles import READ_ONLY_ROLES, registry_allowed, role_violation, spec

    assert "playtester" in READ_ONLY_ROLES and spec("playtester").read_only
    for role in ("lead", "worker", "playtester"):
        assert registry_allowed(role, "playtest"), role
    assert not registry_allowed("reviewer", "playtest")
    refused = role_violation("playtester", "bash", "", "", command="rm -rf game")
    assert refused is not None and "only plays" in refused.reason
