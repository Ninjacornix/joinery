"""Git LFS is set up before the first binary, not offered as a choice."""

from __future__ import annotations

from pathlib import Path

from joinery.vcs import ATTRIBUTES, MARKER, ensure_lfs, lfs_rules


def test_rules_cover_the_formats_projects_actually_store() -> None:
    rules = lfs_rules()
    # These are the native sources and rendered outputs a Joinery project keeps
    # in the repo. Any of them in plain git bloats history permanently.
    for suffix in ("blend", "wav", "png", "kra", "ogg"):
        assert f"*.{suffix} filter=lfs" in rules


def test_creates_the_file_when_absent(tmp_path: Path) -> None:
    assert ensure_lfs(tmp_path) is True
    assert MARKER in (tmp_path / ATTRIBUTES).read_text()


def test_appends_without_clobbering(tmp_path: Path) -> None:
    # Godot's own VCS metadata step may write this file first, and throwing
    # that away would undo whatever it configured.
    (tmp_path / ATTRIBUTES).write_text("# godot wrote this\n*.tmp -text\n")
    assert ensure_lfs(tmp_path) is True
    text = (tmp_path / ATTRIBUTES).read_text()
    assert "# godot wrote this" in text
    assert "*.tmp -text" in text
    assert "*.blend filter=lfs" in text


def test_is_idempotent(tmp_path: Path) -> None:
    assert ensure_lfs(tmp_path) is True
    assert ensure_lfs(tmp_path) is False
    text = (tmp_path / ATTRIBUTES).read_text()
    assert text.count("*.blend filter=lfs") == 1


def test_setup_leaves_a_project_ready_for_binaries(tmp_path: Path) -> None:
    # The whole point: a project is LFS-ready before any asset exists, because
    # converting after the fact means rewriting history.
    from joinery.installer import Selection, write_selection

    write_selection(Selection(["godot"], []), tmp_path / "joinery.toml")
    ensure_lfs(tmp_path)
    assert "filter=lfs" in (tmp_path / ATTRIBUTES).read_text()
