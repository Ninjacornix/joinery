"""The review is announced the moment the agent finishes, and gets there fast."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from bridge.instructions import REVIEW_GUIDE
from bridge.protocol import Done, Message
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction


class FakeBridge:
    def __init__(self, _agent: str) -> None:
        self.model_choices: list[Any] = []

    def bind_gateway(self, _path: Path) -> None:
        pass

    async def connect(self, _project: Path, _profile: str) -> None:
        pass

    async def send(self, _prompt: Any) -> AsyncIterator[Any]:
        yield Message(text="The ashtray will open for review.")
        yield Done(ok=True)

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeGateway:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.transaction: Any = type("Tx", (), {"id": "a" * 32, "summary": "Ashtray"})()
        self.finished = asyncio.Event()

    async def finish(self) -> dict[str, Any]:
        self.finished.set()
        return {"kind": "file_preview", "transaction_id": "a" * 32}

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeRPC:
    def __init__(self, gateway: Any, _stop: Any) -> None:
        self.path = Path("gateway.sock")
        self.stop_reason = ""

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


def test_review_preparing_comes_before_the_review_and_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(stdio, "emit", events.append)
    monkeypatch.setattr(stdio, "Bridge", FakeBridge)
    monkeypatch.setattr(stdio, "ProjectGateway", FakeGateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()
        stream.feed_data(
            json.dumps({"kind": "prompt", "text": "ashtray"}).encode() + b"\n"
        )

        async def end() -> None:
            # Close the input once the turn is over.
            while not any(e.get("kind") == "done" for e in events):
                await asyncio.sleep(0.01)
            stream.feed_eof()

        asyncio.get_running_loop().create_task(end())
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(stdio.serve(tmp_path, "codex")) == 0
    kinds = [event.get("kind") for event in events]
    assert kinds.index("message") < kinds.index("review_preparing")
    assert (
        kinds.index("review_preparing")
        < kinds.index("file_preview")
        < kinds.index("done")
    )
    preparing = events[kinds.index("review_preparing")]
    assert preparing["transaction_id"] == "a" * 32 and preparing["summary"] == "Ashtray"


def test_agents_say_the_change_will_open_for_review() -> None:
    assert "say the change will open for review" in REVIEW_GUIDE
    assert (
        "never tell the user to accept something that is not on screen yet"
        in REVIEW_GUIDE
    )


def staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ProjectGateway, list[str]]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.evidence", AsyncMock(return_value=[]))
    runs: list[str] = []

    async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        runs.append(argv[0])
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Room")
    gateway.transaction = tx
    (tx.stage / "room.tscn").write_text(
        '[gd_scene format=3]\n\n[node name="Room" type="Node3D"]\n'
    )
    return gateway, runs


def test_the_review_skips_an_import_godot_just_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, runs = staged(tmp_path, monkeypatch)
    tx = gateway.transaction
    assert tx is not None
    from joinery.transactions import inventory

    gateway._imported = (tx.id, inventory(tx.stage, include_dirs=True))

    async def go() -> dict[str, Any] | None:
        try:
            return await gateway.finish()
        finally:
            await gateway.close()

    assert asyncio.run(go()) is not None
    assert runs == [], "Nothing changed since the native export's import"


def test_the_review_imports_again_after_a_later_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, runs = staged(tmp_path, monkeypatch)
    tx = gateway.transaction
    assert tx is not None
    from joinery.transactions import inventory

    gateway._imported = (tx.id, inventory(tx.stage, include_dirs=True))
    (tx.stage / "room.tscn").write_text(
        '[gd_scene format=3]\n\n[node name="Hall" type="Node3D"]\n'
    )

    async def go() -> dict[str, Any] | None:
        try:
            return await gateway.finish()
        finally:
            await gateway.close()

    assert asyncio.run(go()) is not None
    assert runs == ["godot"]


def test_rules_and_import_run_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, _runs = staged(tmp_path, monkeypatch)
    tx = gateway.transaction
    assert tx is not None

    async def slow_rules(*_a: Any) -> list[str]:
        await asyncio.sleep(0.3)
        return []

    async def slow_import(*_a: Any, **_k: Any) -> Result:
        await asyncio.sleep(0.3)
        return Result(True, "", "", 0.3)

    monkeypatch.setattr("joinery.gateway.check_rules", slow_rules)
    monkeypatch.setattr("joinery.gateway.run_offline", slow_import)
    start = time.monotonic()
    asyncio.run(gateway._checks(tx, [], rules=True, godot=True))
    assert time.monotonic() - start < 0.55
    asyncio.run(gateway.close())


def test_a_failed_rule_still_wins_over_a_clean_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, _runs = staged(tmp_path, monkeypatch)
    tx = gateway.transaction
    assert tx is not None

    async def rules(*_a: Any) -> list[str]:
        return ["art/models/a.glb: 9000 triangles, the bible allows 600"]

    monkeypatch.setattr("joinery.gateway.check_rules", rules)
    with pytest.raises(ValueError, match="Art bible check failed: art/models/a.glb"):
        asyncio.run(gateway._checks(tx, [], rules=True, godot=True))
    asyncio.run(gateway.close())


def test_before_and_after_renders_run_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import review

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art").mkdir()
    (tmp_path / "art/crate.blend").write_bytes(b"before")
    tx = FileTransaction(tmp_path)
    tx.begin("Crate")
    (tx.stage / "art/crate.blend").write_bytes(b"after")
    changes = tx.review()

    async def render(root: Path, _relative: str, name: str, _script: str) -> Path:
        await asyncio.sleep(0.3)
        return root / name

    monkeypatch.setattr(review, "_render", render)
    start = time.monotonic()
    media = asyncio.run(review.evidence(tx, changes))
    assert time.monotonic() - start < 0.55
    assert media[0]["before"].startswith(str(tx.snapshot))
    assert media[0]["after"].startswith(str(tx.stage))
