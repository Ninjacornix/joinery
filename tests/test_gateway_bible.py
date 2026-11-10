"""The gateway: reference boards come before approval; UI files meet the bible."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from joinery.art_approval import approve
from joinery.gateway import ProjectGateway, _reference_only
from joinery.runner import Result
from joinery.transactions import FileTransaction

BIBLE = """# Art Bible

## 18. Checked rules

```toml
schema = 2

[accessibility]
min_ui_contrast = 4.5
min_text_px = 18
ui_folders = ["ui"]
```
"""

LOW_CONTRAST = """[gd_resource type="Theme" load_steps=2 format=3 uid="uid://bgatewaytheme"]

[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_a"]
bg_color = Color(0.2, 0.2, 0.2, 1)

[resource]
Button/colors/font_color = Color(0.3, 0.3, 0.3, 1)
Button/styles/normal = SubResource("StyleBoxFlat_a")
"""


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "joinery.lock").write_text("version = 1\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.evidence", AsyncMock(return_value=[]))

    async def offline(_argv: list[str], _root: Path, *_args: Any, **_kw: Any) -> Result:
        # Godot's import check and any worker run; the bible checks here are Python.
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    return tmp_path


def lock_entry(root: Path, path: str) -> str:
    digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
    return (
        f'[[asset]]\npath = "{path}"\nsha256 = "{digest}"\norigin = "human-import"\n'
        'imported_by = "tester"\nlicence = "own work, reference only"\n'
    )


def begin(root: Path) -> tuple[ProjectGateway, FileTransaction]:
    """A gateway first (it clears stale stages), then the change under review."""
    gateway = ProjectGateway(root)
    tx = FileTransaction(root)
    tx.begin("Change under review")
    gateway.transaction = tx
    return gateway, tx


def finish(gateway: ProjectGateway, _tx: FileTransaction) -> dict[str, Any] | None:
    async def run() -> dict[str, Any] | None:
        try:
            return await gateway.finish()
        finally:
            await gateway.close()

    return asyncio.run(run())


def test_reference_paths_are_recognised() -> None:
    assert _reference_only({"document": "art/reference/dusk_board.kra"})
    assert _reference_only({"files": ["res://art/reference/a.kra", "README.md"]})
    assert not _reference_only({"document": "art/props/crate.blend"})
    assert not _reference_only(
        {"a": "art/reference/board.kra", "b": {"out": "art/props/crate.png"}}
    )
    assert not _reference_only({"text": "hello"})
    assert not _reference_only({"document": "art/reference/../props/crate.blend"})
    assert not _reference_only({"document": "art/reference_old/crate.blend"})


def test_reference_board_needs_no_approval_but_does_need_a_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / "art/reference").mkdir(parents=True)
    (tx.stage / "art/reference/quarry.png").write_bytes(b"my photo")
    with pytest.raises(ValueError, match="no joinery.lock entry"):
        finish(gateway, tx)


def test_locked_reference_board_reaches_review_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / "art/reference").mkdir(parents=True)
    (tx.stage / "art/reference/quarry.png").write_bytes(b"my photo")
    (tx.stage / "joinery.lock").write_text(
        "version = 1\n\n" + lock_entry(tx.stage, "art/reference/quarry.png")
    )
    preview = finish(gateway, tx)
    assert preview is not None and preview["kind"] == "file_preview"


def test_other_assets_still_need_an_approved_bible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / "art/reference").mkdir(parents=True)
    (tx.stage / "art/props").mkdir(parents=True)
    (tx.stage / "art/reference/quarry.png").write_bytes(b"my photo")
    (tx.stage / "art/props/crate.png").write_bytes(b"texture")
    with pytest.raises(PermissionError, match="Approve the project art bible"):
        finish(gateway, tx)


@pytest.mark.parametrize("path", ["ui/fonts/body.ttf", "sounds/click.mp3", "ui/icons/gem.webp"])
def test_asset_files_outside_art_still_need_an_approved_bible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    root = project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / path).parent.mkdir(parents=True)
    (tx.stage / path).write_bytes(b"not code")
    with pytest.raises(PermissionError, match="Approve the project art bible"):
        finish(gateway, tx)


def approved_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = project(tmp_path, monkeypatch)
    (root / "art").mkdir()
    (root / "art/bible.md").write_text(BIBLE)
    approve(root, "tester")
    return root


def test_ui_theme_changes_are_checked_against_the_bible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = approved_project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / "ui").mkdir()
    (tx.stage / "ui/theme.tres").write_text(LOW_CONTRAST)
    with pytest.raises(
        ValueError, match="Art bible check failed: ui/theme.tres: Button"
    ):
        finish(gateway, tx)


def test_readable_ui_and_ui_outside_the_folders_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = approved_project(tmp_path, monkeypatch)
    gateway, tx = begin(root)
    (tx.stage / "ui").mkdir()
    (tx.stage / "levels").mkdir()
    (tx.stage / "ui/theme.tres").write_text(
        LOW_CONTRAST.replace("Color(0.3, 0.3, 0.3, 1)", "Color(1, 1, 1, 1)")
    )
    (tx.stage / "levels/theme.tres").write_text(LOW_CONTRAST)
    preview = finish(gateway, tx)
    assert preview is not None
    assert json.dumps(preview["diff"])  # reached the review


def test_ui_is_not_checked_without_an_approved_bible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    (root / "art").mkdir()
    (root / "art/bible.md").write_text(BIBLE)  # written, not approved
    gateway, tx = begin(root)
    (tx.stage / "ui").mkdir()
    (tx.stage / "ui/theme.tres").write_text(LOW_CONTRAST)
    assert finish(gateway, tx) is not None
