import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery.catalog import load_catalog
from joinery.godotsteam import load_release
from joinery.lockfile import (
    Extension,
    check_assets,
    forget_extension,
    read_extensions,
    record_extension,
    tree_sha256,
)


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


# --- [[extensions]] -------------------------------------------------------------


def extension(project: Path, folder: str = "addons/godotsteam") -> Extension:
    root = project / folder
    (root / "osx").mkdir(parents=True)
    (root / "godotsteam.gdextension").write_text("[configuration]\n")
    (root / "osx/libgodotsteam.dylib").write_bytes(b"\x00binary")
    (root / "LICENSE").write_text("MIT License\n")
    pinned = load_release()
    return Extension(
        name=pinned.name,
        version=pinned.version,
        url=pinned.url,
        sha256=pinned.sha256,
        licence="MIT",
        origin="pinned-download",
        path=folder,
        files_sha256=tree_sha256(root),
    )


def test_extensions_round_trip(project: Path) -> None:
    lock(project, add_asset(project))
    before = (project / "joinery.lock").read_text()
    entry = extension(project)
    record_extension(project, entry)
    assert read_extensions(project) == [entry]
    assert errors(project) == []
    record_extension(project, entry)
    text = (project / "joinery.lock").read_text()
    assert text.count("[[extensions]]") == 1
    assert text.startswith(before)
    assert forget_extension(project, entry.name)
    assert (project / "joinery.lock").read_text() == before
    assert not forget_extension(project, entry.name)


def test_tampered_extension_file_fails(project: Path) -> None:
    record_extension(project, extension(project))
    assert errors(project) == []
    (project / "addons/godotsteam/osx/libgodotsteam.dylib").write_bytes(b"swapped")
    assert errors(project) == [
        (
            "addons/godotsteam: files differ from GodotSteam GDExtension 4.23 in "
            "joinery.lock; install it again"
        )
    ]


def test_added_or_removed_extension_files_fail(project: Path) -> None:
    record_extension(project, extension(project))
    (project / "addons/godotsteam/extra.gd").write_text("extends Node\n")
    assert any("files differ" in e for e in errors(project))
    (project / "addons/godotsteam/extra.gd").unlink()
    (project / "addons/godotsteam/LICENSE").unlink()
    assert any("files differ" in e for e in errors(project))


def test_godot_import_files_do_not_change_the_hash(project: Path) -> None:
    record_extension(project, extension(project))
    (project / "addons/godotsteam/godotsteam.gdextension.uid").write_text("uid://abc\n")
    (project / "addons/godotsteam/icon.svg.import").write_text("[remap]\n")
    assert errors(project) == []


def test_missing_extension_folder_fails(project: Path) -> None:
    record_extension(project, extension(project))
    shutil.rmtree(project / "addons/godotsteam")
    assert any("missing on disk" in e for e in errors(project))


def test_extensions_never_hide_assets(project: Path) -> None:
    """An entry under art/ or audio/ is refused, so it cannot exempt assets."""
    for folder in ("audio/ext", "art", "Art/x"):
        with pytest.raises(ValidationError):
            extension(project, folder)
    (project / "art").mkdir(exist_ok=True)
    (project / "art/door.blend").write_bytes(b"door")
    entry = extension(project).model_dump() | {"path": "art"}
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in entry.items())
    (project / "joinery.lock").write_text(f"version = 1\n\n[[extensions]]\n{body}\n")
    assert any("invalid" in e for e in errors(project))


def test_unpinned_extension_fails(project: Path) -> None:
    entry = extension(project).model_copy(update={"sha256": "c" * 64})
    record_extension(project, entry)
    assert errors(project) == [
        (
            "addons/godotsteam: GodotSteam GDExtension 4.23 is not a release Joinery "
            "pins; install it again"
        )
    ]


@pytest.mark.parametrize(
    "field, value",
    [
        ("origin", "tool-created"),
        ("sha256", "abc"),
        ("path", "../outside"),
        ("path", "/abs"),
        ("url", "http://example.com/x.zip"),
    ],
)
def test_invalid_extension_entries_fail(project: Path, field: str, value: str) -> None:
    entry = extension(project).model_dump() | {field: value}
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in entry.items())
    (project / "joinery.lock").write_text(f"version = 1\n\n[[extensions]]\n{body}\n")
    assert any("invalid" in e for e in errors(project))


def test_game_steam_table_does_not_break_the_check(project: Path) -> None:
    (project / "joinery.toml").write_text(
        '[game]\nblueprint = "blank"\nblueprint_version = 1\nfeatures = ["steam"]\n\n'
        '[game.steam]\napp_id = 480\nconnect = "steam"\n'
    )
    lock(project, add_asset(project))
    record_extension(project, extension(project))
    assert errors(project) == []
