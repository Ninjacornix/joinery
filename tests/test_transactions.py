import json
import os
import shutil
from pathlib import Path

import pytest

from joinery import transactions
from joinery.transactions import (
    NOT_REVERTIBLE,
    FileTransaction,
    apply_retention,
    clone_file,
    copy_file,
    inventory,
    tree_bytes,
)


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


# --- Storage: clones, sparse originals, retention -------------------------


BIG = 2_000_000


def large_game(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art").mkdir()
    (tmp_path / "art/big.bin").write_bytes(os.urandom(BIG))
    (tmp_path / "script.gd").write_text("v0")
    return tmp_path


def edit(project: Path, name: str, text: str) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin("Edit " + name)
    (tx.stage / name).write_text(text)
    tx.review()
    tx.accept()
    return tx


def test_original_keeps_only_changed_files_and_stage_goes_after_accept(
    tmp_path: Path,
) -> None:
    project = large_game(tmp_path)
    tx = FileTransaction(project)
    tx.begin("Edit")
    assert (tx.stage / "art/big.bin").is_file(), "Tools work on a full tree"
    (tx.stage / "script.gd").write_text("v1")
    tx.review()
    assert inventory(tx.snapshot) == {"script.gd": inventory(project)["script.gd"]}
    tx.accept()
    assert not tx.stage.exists()
    assert (tx.snapshot / "script.gd").read_text() == "v0"
    assert not (tx.snapshot / "art").exists()
    FileTransaction.load(project, tx.id).revert()
    assert (project / "script.gd").read_text() == "v0"
    assert not tx.snapshot.exists(), "A reverted change keeps no restore data"


def test_bytes_stay_flat_over_many_transactions(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    store = project / ".joinery/transactions"
    for index in range(8):
        edit(project, "script.gd", f"v{index + 1}")
    # Before: each request kept two full copies of the project (~4 MB each).
    assert tree_bytes(store) < 64_000
    for tx_dir in store.iterdir():
        assert not (tx_dir / "stage").exists()


def test_reject_restores_exactly_and_keeps_only_the_journal(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    before = inventory(project, include_dirs=True)
    tx = FileTransaction(project)
    tx.begin("Rejected")
    (tx.stage / "script.gd").write_text("never")
    (tx.stage / "art/big.bin").unlink()
    (tx.stage / "new").mkdir()
    (tx.stage / "new/file.txt").write_text("new")
    tx.review()
    tx.reject()
    assert inventory(project, include_dirs=True) == before
    assert sorted(path.name for path in tx.directory.iterdir()) == ["journal.json"]
    journal = json.loads(tx.journal.read_text())
    assert journal["state"] == "rejected"
    assert "base" not in journal, "The full inventory is dropped once decided"


def test_original_reads_the_project_until_review(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    tx = FileTransaction(project)
    tx.begin("Edit")
    assert tx.original == project
    (tx.stage / "script.gd").write_text("v1")
    tx.review()
    assert tx.original == tx.snapshot
    assert (tx.original / "script.gd").read_text() == "v0"


def test_accept_captures_an_original_that_review_could_not(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    tx = FileTransaction(project)
    tx.begin("Edit")
    (tx.stage / "script.gd").write_text("v1")
    (project / "script.gd").write_text("manual, briefly")
    tx.review()
    assert not (tx.snapshot / "script.gd").exists()
    (project / "script.gd").write_text("v0")
    tx.accept()
    tx.revert()
    assert (project / "script.gd").read_text() == "v0"


def test_revert_works_after_older_transactions_are_pruned(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    (project / "joinery.toml").write_text("[history]\nkeep = 2\n")
    first = edit(project, "a.gd", "a")
    second = edit(project, "b.gd", "b")
    third = edit(project, "c.gd", "c")
    states = {
        tx.id: FileTransaction.load(project, tx.id).revertible
        for tx in (first, second, third)
    }
    assert states == {first.id: False, second.id: True, third.id: True}
    with pytest.raises(ValueError, match="can no longer be reverted"):
        FileTransaction.load(project, first.id).revert()
    FileTransaction.load(project, third.id).revert()
    FileTransaction.load(project, second.id).revert()
    assert not (project / "b.gd").exists()
    assert not (project / "c.gd").exists()
    assert (project / "a.gd").read_text() == "a"
    journal = json.loads(first.journal.read_text())
    assert journal["revertible"] is False and journal["pruned"] == "retention"


def test_a_stale_copy_cannot_revert_twice(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    stale = FileTransaction.load(project, tx.id)
    FileTransaction.load(project, tx.id).revert()
    (project / "script.gd").write_text("v1")  # Same bytes as the reverted change.
    with pytest.raises(ValueError, match="Only an accepted"):
        stale.revert()
    assert (project / "script.gd").read_text() == "v1"
    assert json.loads(tx.journal.read_text())["state"] == "reverted"


def test_revert_waits_for_history_tidying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    monkeypatch.setattr(transactions.time, "sleep", lambda _: None)
    with transactions.HistoryLock(project), pytest.raises(ValueError, match="tidying"):
        transactions._wait_for_history(project, wait=0.0)
    FileTransaction.load(project, tx.id).revert()
    assert (project / "script.gd").read_text() == "v0"


def test_size_budget_prunes_older_but_never_the_newest(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    (project / "joinery.toml").write_text("[history]\nmax_mb = 1\n")
    edit(project, "art/big.bin", "x" * 10)
    first = FileTransaction.load(
        project,
        next(path.name for path in (project / ".joinery/transactions").iterdir()),
    )
    assert first.revertible, "The newest change stays revertible over budget"
    second = edit(project, "script.gd", "v1")
    assert not FileTransaction.load(project, first.id).revertible
    assert FileTransaction.load(project, second.id).revertible
    result = apply_retention(project)
    assert result["kept"] == 1


def test_in_memory_transaction_sees_pruning_by_another_process(
    tmp_path: Path,
) -> None:
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    FileTransaction.load(project, tx.id).drop_restore_data("compacted")
    with pytest.raises(ValueError, match="can no longer be reverted"):
        tx.revert()
    assert NOT_REVERTIBLE.startswith("This change")


def test_copy_fallback_when_cloning_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transactions, "clone_file", lambda _s, _t: False)
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    assert (project / "script.gd").read_text() == "v1"
    tx.revert()
    assert (project / "script.gd").read_text() == "v0"


def test_clone_is_independent_of_its_source(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"shared bytes" * 1000)
    target = tmp_path / "nested/target.bin"
    copy_file(source, target)
    assert target.read_bytes() == source.read_bytes()
    with target.open("r+b") as handle:
        handle.write(b"CHANGED")
    assert source.read_bytes().startswith(b"shared bytes")
    other = tmp_path / "other.bin"
    if clone_file(source, other):
        assert other.read_bytes() == source.read_bytes()
    else:
        assert not other.exists()


def test_no_clone_environment_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOINERY_NO_CLONE", "1")
    source = tmp_path / "a.txt"
    source.write_text("a")
    assert not clone_file(source, tmp_path / "b.txt")


def test_old_full_copy_journal_is_tidied_and_still_reverts(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    # Recreate what the old storage left: full original and stage, version 1.
    shutil.copytree(
        project,
        tx.snapshot,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".joinery"),
    )
    (tx.snapshot / "script.gd").write_text("v0")
    shutil.copytree(project, tx.stage, ignore=shutil.ignore_patterns(".joinery"))
    raw = json.loads(tx.journal.read_text())
    raw["version"] = 1
    raw.pop("decided")
    raw.pop("created")
    tx.journal.write_text(json.dumps(raw))
    assert FileTransaction.load(project, tx.id).leftover_bytes() > BIG
    apply_retention(project)
    assert not tx.stage.exists()
    assert inventory(tx.snapshot) == {"script.gd": inventory(tx.snapshot)["script.gd"]}
    FileTransaction.load(project, tx.id).revert()
    assert (project / "script.gd").read_text() == "v0"


def test_load_refuses_unsafe_change_paths(tmp_path: Path) -> None:
    project = large_game(tmp_path)
    tx = edit(project, "script.gd", "v1")
    raw = json.loads(tx.journal.read_text())
    raw["changes"][0]["path"] = "../outside.txt"
    tx.journal.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Unsafe"):
        FileTransaction.load(project, tx.id)


# --- Game features as reviewed changes ------------------------------------------


@pytest.fixture
def blueprint_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from joinery import blueprints
    from joinery.catalog import load_catalog
    from joinery.installer import Selection
    from joinery.project import create_project
    from tests.blueprints.conftest import build_catalogue

    built = build_catalogue(tmp_path / "catalogue")
    monkeypatch.setattr(blueprints, "BLUEPRINTS_DIR", built.blueprints)
    monkeypatch.setattr(blueprints, "FEATURES_DIR", built.features)
    project = tmp_path / "game"
    create_project(
        project, "G", Selection(["godot"], []), load_catalog(), blueprint="alpha"
    )
    return project


def _tree(root: Path) -> dict[str, bytes]:
    """Every project file with its bytes, the game manifest included.

    Joinery's own bookkeeping under .joinery (journals, locks) is left out.
    """
    from joinery.blueprints import MANIFEST

    return {
        rel: p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and (
            not (rel := p.relative_to(root).as_posix()).startswith(".joinery/")
            or rel == MANIFEST
        )
    }


def test_feature_add_accepted_carries_the_game_manifest(blueprint_game: Path) -> None:
    from joinery import blueprints, game_setup

    manifest = blueprint_game / blueprints.MANIFEST
    before = manifest.read_bytes()
    tx = game_setup.stage_feature(blueprint_game, "add", "net")
    # The stage holds the new manifest; the project keeps the old one until accept.
    assert (tx.stage / blueprints.MANIFEST).is_file()
    assert manifest.read_bytes() == before
    assert not any(c["path"].startswith(".joinery") for c in tx.changes)
    tx.accept()
    assert manifest.read_bytes() != before
    data = json.loads(manifest.read_text())
    assert "net" in data["units"] and data["features"] == ["net"]
    journal = json.loads(tx.journal.read_text())
    assert [c["path"] for c in journal["carried"]] == [blueprints.MANIFEST]
    # The manifest now matches the files: removing the feature cleans up fully.
    tx2 = game_setup.stage_feature(blueprint_game, "remove", "net")
    tx2.accept()
    assert not (blueprint_game / "features/net").exists()


def test_feature_add_rejected_leaves_the_project_byte_identical(
    blueprint_game: Path,
) -> None:
    from joinery import game_setup

    before = _tree(blueprint_game)
    tx = game_setup.stage_feature(blueprint_game, "add", "net")
    tx.reject()
    assert _tree(blueprint_game) == before
    assert not tx.stage.exists()


def test_revert_puts_the_earlier_manifest_back(blueprint_game: Path) -> None:
    from joinery import blueprints, game_setup

    manifest = blueprint_game / blueprints.MANIFEST
    before = manifest.read_bytes()
    tx = game_setup.stage_feature(blueprint_game, "add", "words")
    tx.accept()
    assert manifest.read_bytes() != before
    reloaded = FileTransaction.load(blueprint_game, tx.id)
    assert reloaded.carried and reloaded.carried[0]["before"]
    reloaded.revert()
    assert manifest.read_bytes() == before
    assert not (blueprint_game / "features/words").exists()


def test_revert_refuses_when_the_manifest_moved_on(blueprint_game: Path) -> None:
    from joinery import blueprints, game_setup

    first = game_setup.stage_feature(blueprint_game, "add", "net")
    first.accept()
    manifest = blueprint_game / blueprints.MANIFEST
    moved = manifest.read_text().replace('"net"', '"net" ', 1)
    manifest.write_text(moved)
    with pytest.raises(ValueError, match="Revert that one first"):
        FileTransaction.load(blueprint_game, first.id).revert()
    # Nothing was restored: the feature's files and the newer manifest stay.
    assert (blueprint_game / "features/net/net.gd").is_file()
    assert manifest.read_text() == moved
    # A later feature change also blocks the earlier revert.
    game_setup.stage_feature(blueprint_game, "add", "words").accept()
    with pytest.raises(ValueError, match="Cannot revert|Revert that one first"):
        FileTransaction.load(blueprint_game, first.id).revert()


def test_retention_keeps_the_saved_manifest(blueprint_game: Path) -> None:
    from joinery import blueprints, game_setup

    tx = game_setup.stage_feature(blueprint_game, "add", "net")
    tx.accept()
    apply_retention(blueprint_game)
    assert (tx.snapshot / blueprints.MANIFEST).is_file()
    assert tx.restore_bytes() > 0


def test_recovery_restores_a_half_carried_manifest(blueprint_game: Path) -> None:
    from joinery import blueprints, game_setup

    manifest = blueprint_game / blueprints.MANIFEST
    before = manifest.read_bytes()
    project_before = (blueprint_game / "project.godot").read_bytes()
    tx = game_setup.stage_feature(blueprint_game, "add", "net")
    tx.accept()
    # Simulate a crash after the copy: the journal still says committing.
    raw = json.loads(tx.journal.read_text())
    raw["state"] = "committing"
    tx.journal.write_text(json.dumps(raw))
    loaded = FileTransaction.load(blueprint_game, tx.id)
    loaded.recover()
    assert manifest.read_bytes() == before
    assert (blueprint_game / "project.godot").read_bytes() == project_before
