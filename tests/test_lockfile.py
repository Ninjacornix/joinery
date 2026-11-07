import hashlib
from pathlib import Path

import pytest

from joinery.catalog import load_catalog
from joinery.lockfile import check_assets


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "art").mkdir()
    (tmp_path / "audio").mkdir()
    (tmp_path / "recipes").mkdir()
    (tmp_path / "joinery.lock").write_text("version = 1\n")
    return tmp_path


def add_asset(project: Path) -> str:
    (project / "art/door.blend").write_bytes(b"door")
    (project / "recipes/door.toml").write_text("ops = []\n")
    return hashlib.sha256(b"door").hexdigest()


def lock(project: Path, sha: str, origin: str = "tool-created") -> None:
    extra = (
        'tool = "blender"\nrecipe = "recipes/door.toml"'
        if origin == "tool-created"
        else 'imported_by = "user"\nlicence = "CC0-1.0"'
    )
    (project / "joinery.lock").write_text(
        f'version = 1\n[[asset]]\npath = "art/door.blend"\n'
        f'sha256 = "{sha}"\norigin = "{origin}"\n{extra}\n'
    )


def errors(project: Path) -> list[str]:
    return check_assets(project, load_catalog())


def test_empty_project_passes(project: Path) -> None:
    (project / "art/bible.md").write_text("# bible")
    (project / "art/.gitkeep").touch()
    assert errors(project) == []


def test_unlocked_asset_fails(project: Path) -> None:
    add_asset(project)
    assert errors(project) == ["art/door.blend: no joinery.lock entry"]


@pytest.mark.parametrize("origin", ["tool-created", "human-import"])
def test_locked_asset_passes(project: Path, origin: str) -> None:
    lock(project, add_asset(project), origin)
    assert errors(project) == []


def test_changed_asset_fails(project: Path) -> None:
    lock(project, add_asset(project))
    (project / "art/door.blend").write_bytes(b"swapped")
    assert any("sha256" in e for e in errors(project))


def test_missing_recipe_fails(project: Path) -> None:
    lock(project, add_asset(project))
    (project / "recipes/door.toml").unlink()
    assert any("recipe" in e for e in errors(project))


def test_stale_entry_fails(project: Path) -> None:
    lock(project, add_asset(project))
    (project / "art/door.blend").unlink()
    assert any("missing on disk" in e for e in errors(project))


def test_unknown_origin_fails(project: Path) -> None:
    lock(project, add_asset(project), origin="generated")
    assert any("invalid" in e for e in errors(project))


def test_missing_lock_fails(tmp_path: Path) -> None:
    assert any("missing" in e for e in errors(tmp_path))


def custom_lock(project: Path, sha: str) -> None:
    (project / "joinery.lock").write_text(
        f'version = 1\n[[asset]]\npath = "art/door.blend"\nsha256 = "{sha}"\n'
        'origin = "custom-tool"\ntool = "sdxl"\n'
    )


def test_custom_output_needs_project_custom_tool(project: Path) -> None:
    custom_lock(project, add_asset(project))
    assert any("not a custom tool" in e for e in errors(project))
    (project / "joinery.toml").write_text(
        '[tools]\n[[tools.custom]]\nid = "sdxl"\nname = "S"\ndomain = "2d"\n'
        'server = "sdxl"\ncommand = "s"\n'
    )
    assert errors(project) == []


def test_custom_tool_cannot_claim_tool_created(project: Path) -> None:
    sha = add_asset(project)
    lock(project, sha)
    text = (project / "joinery.lock").read_text().replace('"blender"', '"sdxl"')
    (project / "joinery.lock").write_text(text)
    assert any("not a preferred tool" in e for e in errors(project))
