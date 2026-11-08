"""Lock diagnostics are bounded metadata queries, never credential readers."""

import os
import sys
from pathlib import Path

import pytest

from release.windows_lock_diagnostics import _resource_files, diagnose_locks


def test_resource_enumeration_is_bounded_and_skips_links(tmp_path: Path) -> None:
    resource = tmp_path / "private"
    resource.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "never-register.txt").write_text("not provider data")
    (resource / "outside-link").symlink_to(outside, target_is_directory=True)
    for index in range(40):
        (resource / f"fixture-{index:02d}").touch()
    paths = _resource_files(resource)
    assert len(paths) == 32
    assert all(path.parent == resource for path in paths)
    assert _resource_files(resource / "outside-link") == []


@pytest.mark.skipif(sys.platform == "win32", reason="other-platform fallback")
def test_other_platforms_do_not_run_windows_diagnostics(tmp_path: Path) -> None:
    assert diagnose_locks(tmp_path, []) == {
        "supported": False,
        "platform": sys.platform,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows Restart Manager")
def test_native_diagnostics_identify_only_the_scoped_file_holder(
    tmp_path: Path,
) -> None:
    target = tmp_path / "held-technical-fixture.txt"
    with target.open("w") as held:
        held.write("ordinary technical fixture")
        held.flush()
        report = diagnose_locks(tmp_path, [os.getpid()])
    assert report.get("diagnostic_error") is None, report
    query = report["file_lock_owners"]
    assert query["status"] == "ok", report
    assert os.getpid() in {owner["pid"] for owner in query["owners"]}, report
    assert all(
        set(owner) == {"pid", "application_type", "session_id"}
        for owner in query["owners"]
    )
    assert report["directory_owner_detection"] == "unsupported_by_restart_manager"
