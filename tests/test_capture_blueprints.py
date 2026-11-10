"""The new-project card screenshots: made by Godot, with honest provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from joinery import blueprints
from joinery.catalog import ROOT

SCRIPT = ROOT / "release" / "capture_blueprints.py"
IDS = [b.id for b in blueprints.list_blueprints()]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_blueprints", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "a PNG file"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_every_blueprint_has_a_capture() -> None:
    assert len(IDS) == 6
    for blueprint_id in IDS:
        directory = blueprints.BLUEPRINTS_DIR / blueprint_id
        assert (directory / blueprints.CAPTURE_IMAGE).is_file(), blueprint_id
        assert (directory / blueprints.CAPTURE_NOTE).is_file(), blueprint_id


@pytest.mark.parametrize("blueprint_id", IDS)
def test_capture_provenance(blueprint_id: str) -> None:
    directory = blueprints.BLUEPRINTS_DIR / blueprint_id
    data = (directory / blueprints.CAPTURE_IMAGE).read_bytes()
    note = tomllib.loads((directory / blueprints.CAPTURE_NOTE).read_text(encoding="utf-8"))
    for name in blueprints.CAPTURE_FIELDS:
        assert name in note, f"{blueprint_id}: capture.toml has {name}"
    assert note["origin"] == "tool-created"
    assert note["tool"] == "godot"
    assert note["engine_version"].startswith("4.7")
    assert note["sha256"] == hashlib.sha256(data).hexdigest()
    assert note["blueprint"] == blueprint_id
    command = note["command"]
    assert "--write-movie" in command and "--fixed-fps 30" in command
    assert f"--quit-after {note['frames']}" in command
    assert 0 <= note["frame"] < note["frames"]
    assert "Image.resize" in note["resize"]
    assert "<tmp>" in command and "/Users/" not in command and "/home/" not in command
    assert list(_png_size(data)) == note["size"]
    assert blueprints.check_capture(directory) == []


def test_catalogue_json_points_the_cards_at_their_captures() -> None:
    cards = {b["id"]: b["capture"] for b in blueprints.catalogue_json()["blueprints"]}
    for blueprint_id, path in cards.items():
        assert path == str(blueprints.BLUEPRINTS_DIR / blueprint_id / blueprints.CAPTURE_IMAGE)


def test_captures_never_reach_a_game() -> None:
    for blueprint_id in IDS:
        overlay = blueprints._overlay(blueprints.BLUEPRINTS_DIR / blueprint_id / "overlay")
        assert not any(rel.endswith(".png") for rel in overlay)


def test_note_text_is_valid_toml() -> None:
    script = _script()
    text = script.note_text(
        {
            "origin": "tool-created",
            "tool": "godot",
            "command": 'godot --path "<tmp>/a b"',
            "frame": 3,
            "size": [960, 540],
            "features": ["controller"],
        }
    )
    data = tomllib.loads(text)
    assert data["command"] == 'godot --path "<tmp>/a b"'
    assert data["size"] == [960, 540] and data["frame"] == 3


def test_recorded_command_hides_the_temporary_folder(tmp_path: Path) -> None:
    script = _script()
    godot = ROOT / "godot_patches/godot-src/bin/godot.macos.editor.arm64"
    shown = script._shown([str(godot), "--path", str(tmp_path / "game")], tmp_path, godot)
    assert shown == "godot_patches/godot-src/bin/godot.macos.editor.arm64 --path <tmp>/game"
