from pathlib import Path

import pytest

from joinery.transactions import FileTransaction


def test_accept_refuses_changes_added_after_review(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Review")
    (tx.stage / "file.txt").write_text("reviewed")
    tx.review()
    (tx.stage / "file.txt").write_text("not reviewed")
    with pytest.raises(ValueError, match="changed after review"):
        tx.accept()
    assert not (tmp_path / "file.txt").exists()


@pytest.fixture
def game(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "script.gd").write_text("original unsaved-to-git work")
    return tmp_path


def test_review_accept_and_revert(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Edit a script")
    (tx.stage / "script.gd").write_text("changed")
    (tx.stage / "new.gd").write_text("new")
    assert (game / "script.gd").read_text() == "original unsaved-to-git work"
    assert len(tx.review()) == 2
    tx.accept()
    assert (game / "script.gd").read_text() == "changed"
    tx = FileTransaction.load(game, tx.id)
    tx.revert()
    assert (game / "script.gd").read_text() == "original unsaved-to-git work"
    assert not (game / "new.gd").exists()


def test_delete_reject_and_conflict(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Delete")
    (tx.stage / "script.gd").unlink()
    tx.review()
    tx.reject()
    assert (game / "script.gd").exists()
    tx = FileTransaction(game)
    tx.begin("Edit")
    (tx.stage / "script.gd").write_text("changed")
    tx.review()
    (game / "script.gd").write_text("new manual edit")
    with pytest.raises(ValueError, match="preserved"):
        tx.accept()
    assert (game / "script.gd").read_text() == "new manual edit"


def test_unrelated_manual_work_preserved(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Edit")
    (tx.stage / "script.gd").write_text("changed")
    tx.review()
    (game / "other.gd").write_text("manual")
    tx.accept()
    tx.revert()
    assert (game / "other.gd").read_text() == "manual"


def test_recovery_after_partial_commit(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Edit")
    (tx.stage / "script.gd").write_text("changed")
    changes = tx.review()
    tx.state = "committing"
    tx._record(changes=changes)
    (game / "script.gd").write_text("changed")
    loaded = FileTransaction.load(game, tx.id)
    loaded.recover()
    assert loaded.state == "recovered"
    assert (game / "script.gd").read_text() == "original unsaved-to-git work"


def test_symlink_never_copies_external_source(game: Path, tmp_path: Path) -> None:
    (game / "escape").symlink_to("/etc")
    with pytest.raises(ValueError, match="symbolic"):
        FileTransaction(game).begin("Bad")


def test_empty_directories_are_reviewed_committed_and_reverted(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Create folders")
    (tx.stage / "assets/empty").mkdir(parents=True)
    assert len(tx.review()) == 2
    tx.accept()
    assert (game / "assets/empty").is_dir()
    tx.revert()
    assert not (game / "assets").exists()


def test_directory_deletion_restores_contents_and_empty_children(game: Path) -> None:
    (game / "folder/empty").mkdir(parents=True)
    (game / "folder/file.txt").write_text("original")
    import shutil

    tx = FileTransaction(game)
    tx.begin("Remove folder")
    shutil.rmtree(tx.stage / "folder")
    tx.review()
    tx.accept()
    assert not (game / "folder").exists()
    tx.revert()
    assert (game / "folder/empty").is_dir()
    assert (game / "folder/file.txt").read_text() == "original"


def test_revert_refuses_new_folder_contents_before_changing_files(game: Path) -> None:
    tx = FileTransaction(game)
    tx.begin("Create assets")
    (tx.stage / "new").mkdir()
    (tx.stage / "new/accepted.txt").write_text("accepted")
    (tx.stage / "script.gd").write_text("accepted script")
    tx.review()
    tx.accept()
    (game / "new/manual.txt").write_text("manual")
    with pytest.raises(ValueError, match="newer manual"):
        tx.revert()
    assert (game / "new/accepted.txt").read_text() == "accepted"
    assert (game / "script.gd").read_text() == "accepted script"
    assert (game / "new/manual.txt").read_text() == "manual"
