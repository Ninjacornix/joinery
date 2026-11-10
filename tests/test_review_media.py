"""A review opens as soon as its checks pass; the before/after renders follow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import review
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction


def staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ProjectGateway, FileTransaction]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art").mkdir()
    (tmp_path / "art/crate.blend").write_bytes(b"before")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Crate")
    gateway.transaction = tx
    (tx.stage / "art/crate.blend").write_bytes(b"after")
    return gateway, tx


def test_the_review_names_renders_that_are_still_coming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx = staged(tmp_path, monkeypatch)
    started = asyncio.Event()
    events: list[dict[str, Any]] = []

    async def render(root: Path, _relative: str, name: str, _script: str) -> Path:
        started.set()
        output = root / ".joinery/previews" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png")
        return output

    monkeypatch.setattr(review, "_render", render)

    async def go() -> dict[str, Any]:
        try:
            preview = gateway.open_review("  Weathered the crate  ")
            assert not started.is_set(), "The review does not wait for renders"
            gateway.start_media(events.append)
            assert gateway._media_task is not None
            await gateway._media_task
            return preview
        finally:
            await gateway.close()

    preview = asyncio.run(go())
    assert preview["summary"] == "Weathered the crate" and preview["media_pending"]
    item = preview["media"][0]
    assert item["before"].startswith(str(tx.snapshot))
    assert item["after"].startswith(str(tx.stage))
    assert events == [
        {"kind": "review_media", "transaction_id": tx.id, "media": preview["media"]}
    ]


def test_a_failed_render_marks_the_item_and_the_review_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx = staged(tmp_path, monkeypatch)
    events: list[dict[str, Any]] = []

    async def render(*_a: Any) -> Path:
        raise ValueError("Could not render the native document for review: x\nlog")

    monkeypatch.setattr(review, "_render", render)

    async def go() -> None:
        try:
            gateway.open_review("Crate")
            gateway.start_media(events.append)
            assert gateway._media_task is not None
            await gateway._media_task
        finally:
            await gateway.close()

    asyncio.run(go())
    (item,) = events[0]["media"]
    assert item["error"] == "Could not render the native document for review: x"
    assert "before" not in item and "after" not in item
    assert tx.state == "rejected", "Closing the session drops the open review"


def test_deciding_stops_a_render_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx = staged(tmp_path, monkeypatch)
    cancelled = asyncio.Event()
    events: list[dict[str, Any]] = []

    async def render(*_a: Any) -> Path:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError

    monkeypatch.setattr(review, "_render", render)

    async def go() -> dict[str, Any]:
        try:
            gateway.open_review("Crate")
            gateway.start_media(events.append)
            await asyncio.sleep(0.05)
            return await gateway.decide(tx.id, True)
        finally:
            await gateway.close()

    decision = asyncio.run(go())
    assert decision["accepted"] and cancelled.is_set() and events == []
    assert (tmp_path / "art/crate.blend").read_bytes() == b"after"
