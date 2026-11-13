"""The editor restarting its chat (another agent, Reconnect) keeps changes in review."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from joinery.gateway import ProjectGateway
from joinery.transactions import HELD, FileTransaction
from tests.test_chats import FakeBridge, FakeGateway, FakeRPC


def test_shutdown_keeps_reviews_and_ends_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    gateways: list[Any] = []

    def gateway(project: Path) -> FakeGateway:
        made = FakeGateway(project)
        made.keep_reviews = False  # type: ignore[attr-defined]
        gateways.append(made)
        return made

    monkeypatch.setattr(stdio, "emit", events.append)
    monkeypatch.setattr(stdio, "Bridge", FakeBridge)
    monkeypatch.setattr(stdio, "ProjectGateway", gateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()
        stream.feed_data(b'{"kind": "shutdown", "keep_reviews": true}\n')
        # Never read: the host has gone by then.
        stream.feed_data(b'{"kind": "prompt", "text": "Door", "chat_id": ""}\n')
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(stdio.serve(tmp_path, "claude")) == 0
    assert gateways[0].keep_reviews is True
    assert gateways[0].held_by == "chat restart"
    assert not any(e.get("kind") == "done" for e in events)


def test_a_change_kept_by_a_restart_says_who_kept_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def go() -> str:
        gateway = ProjectGateway(tmp_path)
        tx = FileTransaction(tmp_path)
        tx.begin("Note")
        (tx.stage / "note.txt").write_text("staged")
        tx.review()
        gateway.transaction = tx
        gateway.keep_reviews = True
        gateway.held_by = "chat restart"
        await gateway.close()
        assert FileTransaction.load(tmp_path, tx.id).state == "review"
        return str(json.loads((tx.directory / HELD).read_text())["by"])

    assert asyncio.run(go()) == "chat restart"
