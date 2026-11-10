"""The checks a submitted change runs: GUT tests where a project has them."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import submission
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction

SCRIPT = "extends Node\n\nfunc _ready() -> void:\n\tpass\n"


def staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, gut: bool, failing: bool = False
) -> tuple[ProjectGateway, FileTransaction, list[list[str]]]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    if gut:
        (tmp_path / "addons/gut").mkdir(parents=True)
        (tmp_path / "addons/gut/gut_cmdln.gd").write_text("extends SceneTree\n")
        (tmp_path / "test").mkdir()
        (tmp_path / "test/test_player.gd").write_text("extends GutTest\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    runs: list[list[str]] = []

    async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        runs.append(argv)
        if failing and "-gexit" in argv:
            return Result(False, "1 test failed: test_jump", "", 0.0)
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Player")
    gateway.transaction = tx
    (tx.stage / "player.gd").write_text(SCRIPT)
    return gateway, tx, runs


def validate(gateway: ProjectGateway, tx: FileTransaction) -> submission.Validation:
    async def go() -> submission.Validation:
        try:
            return await submission.validate(gateway, tx, set())
        finally:
            await gateway.close()

    return asyncio.run(go())


def test_gut_tests_run_after_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx, runs = staged(tmp_path, monkeypatch, gut=True)
    found = validate(gateway, tx)
    assert found.problems == [] and found.skipped == []
    assert "--import" in runs[0]
    assert "res://addons/gut/gut_cmdln.gd" in runs[1]
    assert "-gdir=res://test" in runs[1]


def test_a_failing_test_is_a_problem_to_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx, _runs = staged(tmp_path, monkeypatch, gut=True, failing=True)
    found = validate(gateway, tx)
    assert [p.check for p in found.problems] == ["tests"]
    assert found.problems[0].fixable
    assert "test_jump" in found.problems[0].message


def test_without_gut_the_tests_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, tx, runs = staged(tmp_path, monkeypatch, gut=False)
    found = validate(gateway, tx)
    assert found.problems == [] and found.skipped == ["tests"]
    assert len(runs) == 1 and "--import" in runs[0]
