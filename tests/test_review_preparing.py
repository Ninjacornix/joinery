"""The review gets there fast: no repeated import, checks and renders side by side."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge.instructions import REVIEW_GUIDE
from joinery import submission
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction


def test_agents_hand_off_with_submit_for_review() -> None:
    assert "call submit_for_review with one short line" in REVIEW_GUIDE
    assert "Only after it returns status in_review" in REVIEW_GUIDE
    assert "never ask the user to accept before that" in REVIEW_GUIDE
    assert "needs_user or final" in REVIEW_GUIDE


def staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ProjectGateway, list[str]]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.render", AsyncMock())
    runs: list[str] = []

    async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        runs.append(argv[0])
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    monkeypatch.setattr("joinery.submission.run_offline", offline)
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

    monkeypatch.setattr("joinery.submission.check_rules", slow_rules)
    monkeypatch.setattr("joinery.submission.run_offline", slow_import)
    start = time.monotonic()
    asyncio.run(submission.checks(tx, [], rules=True, godot=True))
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

    monkeypatch.setattr("joinery.submission.check_rules", rules)
    problems = asyncio.run(submission.checks(tx, [], rules=True, godot=True))
    assert [p.check for p in problems] == ["art_bible"]
    assert problems[0].message.startswith("Art bible check failed: art/models/a.glb")
    assert problems[0].paths == ["art/models/a.glb"]
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
