"""User review applies native documents and exports together for every adapter.

File bytes are deliberately opaque fixtures: these are transaction tests, not
claims that a real application has produced a valid model/image/sound.
"""

from pathlib import Path

import pytest

from joinery.transactions import FileTransaction, inventory

ASSETS = [
    pytest.param("art/sources/hammer.blend", "art/models/hammer.glb", id="blender"),
    pytest.param("art/icon.kra", "art/icon.png", id="krita"),
    pytest.param("art/walk.aseprite", "art/walk.png", id="aseprite"),
    pytest.param("art/steel.ptex", "art/steel.tres", id="material-maker"),
    pytest.param("art/ui.svg", "art/ui.png", id="inkscape"),
    pytest.param("audio/impact.scd", "audio/impact.wav", id="supercollider"),
]


@pytest.fixture
def game(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    return tmp_path


def source_pair(game: Path, source: str, export: str) -> None:
    for relative, content in (
        (source, b"original editable source"),
        (export, b"original game export"),
    ):
        path = game / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def change_pair(tx: FileTransaction, source: str, export: str) -> None:
    (tx.stage / source).write_bytes(b"changed editable source")
    (tx.stage / export).write_bytes(b"changed game export")


@pytest.mark.parametrize("source,export", ASSETS)
def test_rejecting_an_asset_edit_restores_both_source_and_game_export(
    game: Path, source: str, export: str
) -> None:
    source_pair(game, source, export)
    original = inventory(game)
    tx = FileTransaction(game)
    tx.begin("Change only the selected asset")
    change_pair(tx, source, export)
    tx.review()
    assert inventory(game) == original  # still only a preview
    tx.reject()
    assert inventory(game) == original
    assert tx.state == "rejected"


@pytest.mark.parametrize("source,export", ASSETS)
def test_accept_then_revert_updates_and_restores_the_complete_asset_pair(
    game: Path, source: str, export: str
) -> None:
    source_pair(game, source, export)
    original = inventory(game)
    tx = FileTransaction(game)
    tx.begin("Update the selected source and its export")
    change_pair(tx, source, export)
    tx.review()
    tx.accept()
    assert (game / source).read_bytes() == b"changed editable source"
    assert (game / export).read_bytes() == b"changed game export"
    # An unrelated manual script remains untouched by asset undo.
    (game / "player.gd").write_text("extends Node\n")
    FileTransaction.load(game, tx.id).revert()
    assert (game / source).read_bytes() == b"original editable source"
    assert (game / export).read_bytes() == b"original game export"
    assert (game / "player.gd").read_text() == "extends Node\n"
    assert {k: v for k, v in inventory(game).items() if k != "player.gd"} == original


@pytest.mark.parametrize("source,export", ASSETS)
def test_manual_export_edit_before_accept_prevents_partial_native_source_overwrite(
    game: Path, source: str, export: str
) -> None:
    source_pair(game, source, export)
    tx = FileTransaction(game)
    tx.begin("Re-export selected asset")
    change_pair(tx, source, export)
    tx.review()
    (game / export).write_bytes(b"newer manual export")
    with pytest.raises(ValueError, match="preserved"):
        tx.accept()
    assert (game / source).read_bytes() == b"original editable source"
    assert (game / export).read_bytes() == b"newer manual export"
    tx.reject()


@pytest.mark.parametrize("source,export", ASSETS)
def test_unreviewed_staged_change_cannot_be_accepted_with_old_preview(
    game: Path, source: str, export: str
) -> None:
    source_pair(game, source, export)
    original = inventory(game)
    tx = FileTransaction(game)
    tx.begin("Review this source/export pair")
    change_pair(tx, source, export)
    tx.review()
    (tx.stage / source).write_bytes(b"not the source the user reviewed")
    with pytest.raises(ValueError, match="changed after review"):
        tx.accept()
    assert inventory(game) == original
    tx.reject()
