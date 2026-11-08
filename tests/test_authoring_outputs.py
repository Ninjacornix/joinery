"""Export ownership follows actual changes and the same native source only."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from joinery.authoring import _scripted_export
from joinery.runner import Result


def test_native_export_does_not_claim_unchanged_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exports = tmp_path / "art/export"
    exports.mkdir(parents=True)
    (exports / "own.tres").write_text("unchanged deterministic export")
    (exports / "other.tres").write_text("another material's export")
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes/own.toml").write_text(
        'version=1\ntool="material-maker"\nscript="recipes/own.py"\noutputs=["art/own.ptex","art/export/own.tres"]\n'
    )
    (tmp_path / "recipes/other.toml").write_text(
        'version=1\ntool="material-maker"\nscript="recipes/other.py"\noutputs=["art/other.ptex","art/export/other.tres"]\n'
    )

    async def execute(*_args: object, **_kwargs: object) -> Result:
        (tmp_path / "art/own.ptex").write_text("native graph")
        (exports / "new.png").write_bytes(b"new native export")
        return Result(True, "", "", 0.1)

    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr("joinery.authoring.run_offline", execute)
    monkeypatch.setattr("joinery.authoring._record", record)
    asyncio.run(
        _scripted_export(
            tmp_path,
            "material-maker",
            "art/own.ptex",
            "# logged operation",
            "art/export",
        )
    )
    recipe = record.await_args.args[1]
    assert set(recipe.outputs) == {
        "art/own.ptex",
        "art/export/own.tres",
        "art/export/new.png",
    }
    assert (exports / "other.tres").read_text() == "another material's export"
