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


def test_a_model_names_the_glb_each_side_exports_for_live_3d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway, tx = staged(tmp_path, monkeypatch)
    recipe = (
        'version=1\ntool="blender"\nscript="recipes/crate.py"\n'
        'outputs=["art/crate.blend","art/models/crate.glb"]\n'
    )
    for root, body in ((tx.original, b"old glb"), (tx.stage, b"new glb")):
        (root / "recipes").mkdir(exist_ok=True)
        (root / "recipes/crate.toml").write_text(recipe)
        (root / "art/models").mkdir(parents=True, exist_ok=True)
        (root / "art/models/crate.glb").write_bytes(body)
    media, _renders = review.plan(
        tx, [{"path": "art/crate.blend", "change": "modified"}]
    )
    (item,) = media
    assert item["before_model"] == str(tx.original / "art/models/crate.glb")
    assert item["after_model"] == str(tx.stage / "art/models/crate.glb")
    (tx.original / "art/models/crate.glb").unlink()
    media, _renders = review.plan(
        tx, [{"path": "art/crate.blend", "change": "modified"}]
    )
    assert "before_model" not in media[0], "A side without the file has no live model"


def test_a_model_without_an_exported_glb_keeps_its_renders_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway, tx = staged(tmp_path, monkeypatch)
    media, _renders = review.plan(
        tx, [{"path": "art/crate.blend", "change": "modified"}]
    )
    assert not {"before_model", "after_model"} & media[0].keys()


def test_an_unchanged_side_is_never_rendered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renders are kept by the document's bytes and the script: a second review of
    the same before (a send-back, a second round) takes it from the cache."""
    _gateway, tx = staged(tmp_path, monkeypatch)
    drawn: list[str] = []

    async def render(root: Path, _relative: str, name: str, _script: str) -> Path:
        drawn.append("after" if root == tx.stage else "before")
        output = root / ".joinery/previews" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png of " + (root / "art/crate.blend").read_bytes())
        output.with_name(output.stem + review.BACK_SUFFIX).write_bytes(b"back")
        return output

    monkeypatch.setattr(review, "_render", render)
    changes = [{"path": "art/crate.blend", "change": "modified"}]

    def once() -> dict[str, Any]:
        media, renders = review.plan(tx, changes)
        asyncio.run(review.render(renders))
        return media[0]

    first = once()
    assert sorted(drawn) == ["after", "before"]
    drawn.clear()
    (tx.stage / "art/crate.blend").write_bytes(b"after, fixed")
    second = once()
    # Only the side that changed was drawn; the before came from the cache.
    assert drawn == ["after"]
    assert Path(second["before"]).read_bytes() == b"png of before"
    assert Path(second["before_back"]).read_bytes() == b"back"
    assert Path(second["after"]).read_bytes() == b"png of after, fixed"
    assert first["before"] == second["before"]
    assert list((tmp_path / review.CACHE_DIR).glob("*.png"))
