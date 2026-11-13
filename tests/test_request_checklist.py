"""submit_for_review's done: each part of the request and what was done, on the card."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery.turn import DONE_CHARS, DONE_ITEMS
from tests.test_submit_for_review import Session


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def _in_review(session: Session) -> dict[str, Any]:
    return next(e for e in session.events if e.get("state") == "in_review")


def test_the_list_rides_on_the_change_opening(session: Session) -> None:
    session.turn.begin("Make a mug and put it on the table")
    session.stage({"a.gd": "extends Node\n"})
    done = [
        "Made the mug in Blender",
        "  ",
        "Put it on Table in kitchen.tscn",
        "Couldn't  match the\nglaze: no reference",
    ]
    result = session.run(lambda: session.turn.submit("A mug", done=done))
    assert result.status == "in_review"
    assert _in_review(session)["done"] == [
        "Made the mug in Blender",
        "Put it on Table in kitchen.tscn",
        "Couldn't match the glaze: no reference",
    ]


def test_the_list_is_kept_short(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})
    long = ["x" * 500] * 20
    session.run(lambda: session.turn.submit("Ashtray", done=long))
    shown = _in_review(session)["done"]
    assert len(shown) == DONE_ITEMS and all(len(d) == DONE_CHARS for d in shown)


def test_no_done_means_no_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(tmp_path, monkeypatch)
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})
    session.run(lambda: session.turn.submit("Ashtray"))
    assert "done" not in _in_review(session)


def test_the_rpc_takes_done_and_refuses_anything_but_lines(session: Session) -> None:
    from joinery.gateway_rpc import GatewayRPC, rpc

    session.turn.begin("Make a mug and put it on the table")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> dict[str, Any]:
        server = GatewayRPC(session.gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="one per part of the request"):
                await rpc(server.path, "submit", {"summary": "A mug", "done": "all"})
            return await rpc(
                server.path,
                "submit",
                {"summary": "A mug", "done": ["Made the mug", "Put it on Table"]},
            )
        finally:
            await server.close()

    data = asyncio.run(go())
    assert data["status"] == "in_review"
    assert _in_review(session)["done"] == ["Made the mug", "Put it on Table"]


def test_the_proxy_offers_done() -> None:
    from servers.joinery_registry.proxy import make_server

    tools = asyncio.run(make_server(Path("gateway.sock")).list_tools())
    (tool,) = [t for t in tools if t.name == "submit_for_review"]
    assert tool.input_schema["required"] == ["summary"]
    assert "done" in tool.input_schema["properties"]
    assert "a part you couldn't do is listed too" in (tool.description or "")
