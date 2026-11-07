"""joinery.lock is derived from the recipes, never hand-written."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from joinery.catalog import load_catalog
from joinery.lockfile import check_assets
from joinery.locking import claimed_outputs, record_lock, write_lock


def project_with(tmp_path: Path, *, asset: str, claimed: bool) -> Path:
    (tmp_path / "recipes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "recipes" / "make.py").write_text("# bpy")
    outputs = f'["{asset}"]' if claimed else '["art/other.blend"]'
    (tmp_path / "recipes" / "make.toml").write_text(
        f'version = 1\ntool = "blender"\nscript = "recipes/make.py"\n'
        f"outputs = {outputs}\n"
    )
    target = tmp_path / asset
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"asset bytes")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["blender"]\n')
    return tmp_path


def test_lock_records_measured_hash(tmp_path: Path) -> None:
    project = project_with(tmp_path, asset="art/door.blend", claimed=True)
    assert write_lock(project) == []
    lock = tomllib.loads((project / "joinery.lock").read_text())
    entry = lock["asset"][0]
    assert entry["path"] == "art/door.blend"
    assert entry["sha256"] == hashlib.sha256(b"asset bytes").hexdigest()
    assert entry["origin"] == "tool-created"
    assert entry["tool"] == "blender"
    assert entry["recipe"] == "recipes/make.toml"


def test_locked_project_passes_the_gate(tmp_path: Path) -> None:
    project = project_with(tmp_path, asset="art/door.blend", claimed=True)
    write_lock(project)
    assert check_assets(project, load_catalog()) == []


def test_unclaimed_asset_is_reported_not_guessed(tmp_path: Path) -> None:
    # Nothing may invent an origin: an unexplained asset needs a person.
    project = project_with(tmp_path, asset="art/mystery.png", claimed=False)
    problems = write_lock(project)
    assert any("no recipe produces this file" in p for p in problems)
    # Nothing is written when the result would be wrong.
    assert not (project / "joinery.lock").exists()


def test_recipe_output_missing_on_disk_is_reported(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir(parents=True)
    (tmp_path / "recipes" / "make.py").write_text("# bpy")
    (tmp_path / "recipes" / "make.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/make.py"\n'
        'outputs = ["art/never_built.blend"]\n'
    )
    (tmp_path / "art").mkdir()
    _, problems = record_lock(tmp_path)
    assert any("not on disk" in p for p in problems)


def test_claims_map_covers_every_output(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir(parents=True)
    (tmp_path / "recipes" / "m.py").write_text("")
    (tmp_path / "recipes" / "m.toml").write_text(
        'version = 1\ntool = "blender"\nscript = "recipes/m.py"\n'
        'outputs = ["art/a.blend", "art/b.png"]\n'
    )
    claims = claimed_outputs(tmp_path)
    assert claims["art/a.blend"] == ("blender", "recipes/m.toml")
    assert claims["art/b.png"] == ("blender", "recipes/m.toml")


def test_godot_plumbing_is_not_an_asset(tmp_path: Path) -> None:
    # .gdignore and .import are Godot's own files, not things to lock.
    project = project_with(tmp_path, asset="art/door.blend", claimed=True)
    (project / "art" / ".gdignore").write_text("")
    (project / "art" / "door.blend.import").write_text("[remap]")
    assert write_lock(project) == []
    assert check_assets(project, load_catalog()) == []
