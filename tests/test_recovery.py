"""Approval version binding, exclusive recovery ownership and source resolution."""

import asyncio
import threading
import time
import tomllib
from pathlib import Path

import pytest

from joinery.art_approval import approve, approved, review
from joinery.bootstrap import update_addon
from joinery.catalog import load_catalog
from joinery.gateway import ProjectGateway
from joinery.lockfile import check_assets
from joinery.locking import record_custom_outputs
from joinery.transactions import FileTransaction, ProjectLease


def test_art_approval_tracks_content_and_rejects_unfinished_template(
    tmp_path: Path,
) -> None:
    (tmp_path / "art").mkdir()
    bible = tmp_path / "art/bible.md"
    bible.write_text("Not set yet")
    with pytest.raises(ValueError, match="Finish"):
        approve(tmp_path, "Automated test fixture")
    bible.write_text("Technical test fixture; no game artwork.")
    approve(tmp_path, "Automated test fixture")
    assert approved(tmp_path)
    bible.write_text("Changed test direction")
    assert not approved(tmp_path)


def test_one_authoring_host_owns_project_recovery(tmp_path: Path) -> None:
    first = ProjectLease(tmp_path)
    try:
        with pytest.raises(ValueError, match="active Joinery chat"):
            ProjectLease(tmp_path, wait=0.3)
    finally:
        first.close()
    next_lease = ProjectLease(tmp_path)
    next_lease.close()


def test_a_reconnect_waits_for_the_previous_host_to_let_go(tmp_path: Path) -> None:
    """Reconnect starts the new host before the old one has seen its pipes close."""
    old = ProjectLease(tmp_path)
    releaser = threading.Timer(0.5, old.close)
    releaser.start()
    try:
        started = time.monotonic()
        new = ProjectLease(tmp_path, wait=5.0)
        assert 0.3 < time.monotonic() - started < 5.0
        new.close()
    finally:
        releaser.join()


def test_approval_cannot_approve_content_other_than_the_displayed_review(
    tmp_path: Path,
) -> None:
    (tmp_path / "art").mkdir()
    bible = tmp_path / "art/bible.md"
    bible.write_text("Reviewed technical fixture.")
    shown = review(tmp_path)
    bible.write_text("New technical fixture content.")
    with pytest.raises(ValueError, match="changed while being reviewed"):
        approve(tmp_path, "Automated fixture", str(shown["sha256"]))
    assert not approved(tmp_path)


def test_custom_outputs_keep_the_custom_provenance_label(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/custom.txt").write_text("technical fixture")
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled=[]\n[[tools.custom]]\nid="user-tool"\nname="User tool"\ndomain="2d"\nserver="user-tool"\ncommand="user-tool"\n'
    )
    record_custom_outputs(tmp_path, "user-tool", ["art/custom.txt"])
    entry = tomllib.loads((tmp_path / "joinery.lock").read_text())["asset"][0]
    assert entry["origin"] == "custom-tool"
    assert check_assets(tmp_path, load_catalog()) == []


def test_gateway_restores_interrupted_commit_before_new_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "code.gd").write_text("original")
    tx = FileTransaction(tmp_path)
    tx.begin("Interrupted change")
    (tx.stage / "code.gd").write_text("partial")
    changes = tx.review()
    tx.state = "committing"
    tx._record(changes=changes)
    (tmp_path / "code.gd").write_text("partial")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    assert (tmp_path / "code.gd").read_text() == "original"
    assert FileTransaction.load(tmp_path, tx.id).state == "recovered"
    asyncio.run(gateway.close())


def test_addon_update_restores_uid_even_when_script_version_matches(
    tmp_path: Path,
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    update_addon(tmp_path)
    uid = tmp_path / "addons/joinery/chat_bridge.gd.uid"
    assert uid.is_file()
    uid.unlink()
    assert update_addon(tmp_path)["state"] == "updated"
    assert uid.is_file()
