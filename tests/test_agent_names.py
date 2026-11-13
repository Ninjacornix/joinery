"""The chat host and the sidebar call each agent by its own name ("Claude Code")."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from bridge.protocol import Done
from joinery.agents import all_agents, display_name
from joinery.catalog import ROOT
from tests.test_chats import FakeBridge, FakeGateway, FakeRPC


def test_every_agent_has_its_display_name() -> None:
    for agent in all_agents():
        assert display_name(agent.id) == agent.name
    assert display_name("claude") == "Claude Code"
    assert display_name("someone-else") == "someone-else"


def test_the_sidebar_names_agents_as_the_host_does() -> None:
    source = (ROOT / "godot_addon/joinery/chat_bridge.gd").read_text()
    block = re.search(r"const AGENT_NAMES := \{(.*?)\}", source, re.DOTALL)
    assert block is not None
    names = dict(re.findall(r'"([a-z]+)": "([^"]+)"', block.group(1)))
    assert names == {agent.id: agent.name for agent in all_agents()}


class SlowBridge(FakeBridge):
    """Takes a moment over each request, so a second one arrives while it works."""

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(0.2)
        yield Done(ok=True)


class FailingBridge(FakeBridge):
    """Fails a request without saying why."""

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        raise ValueError("")
        yield Done(ok=True)  # pragma: no cover - makes this an async generator


def serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bridge: type[FakeBridge],
    prompts: int,
) -> list[dict[str, Any]]:
    """Run the chat host as Claude Code for ``prompts`` requests sent at once."""
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(stdio, "emit", events.append)
    monkeypatch.setattr(stdio, "Bridge", bridge)
    monkeypatch.setattr(stdio, "ProjectGateway", FakeGateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()

        async def feed() -> None:
            for _ in range(prompts):
                stream.feed_data(b'{"kind": "prompt", "text": "Door", "chat_id": ""}\n')
            while not any(e.get("kind") == "timing" for e in events):
                await asyncio.sleep(0.01)
            stream.feed_eof()

        asyncio.get_running_loop().create_task(feed())
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    asyncio.run(stdio.serve(tmp_path, "claude"))
    return events


def test_the_host_names_the_agent_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = serve(tmp_path, monkeypatch, SlowBridge, 2)
    connection = next(e for e in events if e.get("kind") == "connection")
    assert connection["agent"] == "claude" and connection["name"] == "Claude Code"
    texts = [str(e.get("text", "")) for e in events if e.get("kind") == "message"]
    assert "Claude Code is still responding." in texts
    assert not any("Codex" in text for text in texts)


def test_a_failed_request_names_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = serve(tmp_path, monkeypatch, FailingBridge, 1)
    texts = [str(e.get("text", "")) for e in events if e.get("kind") == "message"]
    assert "Claude Code could not complete this message." in texts
