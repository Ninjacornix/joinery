"""The save-load feature: versioned slots, autosave, migration steps and a
per-blueprint adapter, checked in stock Godot.

`res://tests/features/save-load/save_load_test.gd` changes each starting point
the way playing does (through the adapter's `steps.gd`), saves, restarts,
loads and compares; feeds damaged, newer, foreign and old files; autosaves at
a checkpoint; drives the Save and Load pages with the ui_* actions; and checks
an online client may neither save nor load. A second pair of runs saves in one
process and continues from the main menu in another.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import (
    boot,
    make_project,
    needs_godot,
    run_godot,
    run_pair,
)
from tests.blueprints.test_core import check_overlay

FEATURE = "save-load"
TEST = "res://tests/features/save-load/save_load_test.gd"
ONLINE_TEST = "res://tests/features/save-load/online_test.gd"
SAVE_GAME = "res://features/save-load/save_game.gd"
ROOTS = ("features/save-load/", "tests/features/save-load/")
ADAPTER_FILES = ("features/save-load/blueprint_adapter.gd", "tests/features/save-load/steps.gd")
SETTINGS = {
    "save/directory": "user://saves",
    "save/data_version": 1,
    "save/autosave_on_checkpoint": True,
    "save/autosave_min_interval": 10.0,
}
_DIR_LINE = re.compile(r"^save-load dir: (.+)$", re.MULTILINE)
_SUMMARY_LINE = re.compile(r"^save-load summary: (.*)$", re.MULTILINE)


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


def _adapter_ids() -> list[str]:
    adapters = blueprints.FEATURES_DIR / FEATURE / "adapters"
    return sorted(p.name for p in adapters.iterdir() if p.is_dir())


def _feature_complete(feature_id: str) -> bool:
    """In the catalogue, with every autoload it declares shipped."""
    found = [f for f in blueprints.list_features() if f.id == feature_id]
    if not found:
        return False
    overlay = found[0].dir / "overlay"
    return all(
        (overlay / path.removeprefix("res://")).is_file()
        for path in found[0].merge.autoload.values()
    )


def _settings(tmp_path: Path) -> str:
    return f"--settings-path={tmp_path / 'settings.cfg'}"


# --- static -----------------------------------------------------------------------


def test_feature_declares_slots_autosave_and_versions() -> None:
    feature = _feature()
    assert feature.name == "Save and load"
    assert feature.blueprints == ["*"]
    assert feature.requires == [] and feature.conflicts == []
    assert feature.enhances == ["online-play"]
    assert feature.extra_roots == []
    assert feature.external == []
    assert feature.merge.autoload == {"SaveGame": SAVE_GAME}
    assert feature.merge.settings == SETTINGS
    assert feature.merge.input == {}


def test_save_load_overlays_are_code_only() -> None:
    feature_dir = blueprints.FEATURES_DIR / FEATURE
    assert check_overlay(feature_dir / "overlay", ROOTS) == []
    for blueprint in _adapter_ids():
        overlay = feature_dir / "adapters" / blueprint / "overlay"
        assert check_overlay(overlay, ROOTS) == [], blueprint
        for rel in ADAPTER_FILES:
            assert (overlay / rel).is_file(), (blueprint, rel)


def test_every_blueprint_with_saveable_world_has_an_adapter() -> None:
    """Each starting point with world objects ships a summary and test steps."""
    expected = {bp for bp in _blueprint_ids() if bp != "blank"}
    assert set(_adapter_ids()) == expected


def test_save_game_follows_the_contract() -> None:
    source = (
        blueprints.FEATURES_DIR / FEATURE / "overlay/features/save-load/save_game.gd"
    ).read_text()
    for needle in (
        "const SLOTS := 3",
        "func save_slot(slot: int) -> Error:",
        "func load_slot(slot: int) -> Error:",
        "func has_slot(slot: int) -> bool:",
        "func slot_info(slot: int) -> Dictionary:",
        "func delete_slot(slot: int) -> Error:",
        "func latest_slot() -> int:",
        "func autosave() -> Error:",
        "func add_migration(from_version: int, step: Callable) -> void:",
        "signal saved(slot: int)",
        "signal loaded(slot: int)",
        "JSON.from_native",
        '+ ".tmp"',
        "DirAccess.rename_absolute",
        "GameEvents.checkpoint_reached.connect",
    ):
        assert needle in source, needle


def test_save_load_passes_catalogue_checks() -> None:
    problems = [
        p
        for p in blueprints.validate_catalogue()
        if FEATURE in p.split(":", 1)[0] or "save-load" in p
    ]
    assert problems == []


def test_save_load_combines_with_every_blueprint() -> None:
    for blueprint in _blueprint_ids():
        resolution = blueprints.resolve(blueprint, [FEATURE])
        assert resolution.problems == [], (blueprint, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]
        assert resolution.added == {}


def test_adapter_files_come_only_with_their_blueprint(tmp_path: Path) -> None:
    blank = make_project(tmp_path / "a", "blank", [FEATURE])
    assert (blank / "features/save-load/save_game.gd").is_file()
    for rel in ADAPTER_FILES:
        assert not (blank / rel).exists(), rel
    cards = make_project(tmp_path / "b", "puzzle-cards", [FEATURE])
    for rel in ADAPTER_FILES:
        assert (cards / rel).is_file(), rel
    adapter = (cards / ADAPTER_FILES[0]).read_text()
    assert adapter.startswith('extends "res://features/save-load/save_adapter.gd"')


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "platformer-2d")
    before = (project / "project.godot").read_text()
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text()
    assert 'SaveGame="*res://features/save-load/save_game.gd"' in text
    assert "[save]" in text
    assert 'directory="user://saves"' in text and "data_version=1" in text
    for rel in ADAPTER_FILES:
        assert (project / rel).is_file(), rel
    assert (project / "tests/features/save-load/save_load_test.gd").is_file()
    game = blueprints.project_game(project)
    assert game is not None and FEATURE in game["features"]
    again = blueprints.plan(project, "platformer-2d", [FEATURE])
    assert again.conflicts == [] and again.changes == []

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_text() == before
    assert not (project / "features/save-load").exists()
    assert not (project / "tests/features/save-load").exists()


# --- in Godot -----------------------------------------------------------------------


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_save_restart_load_per_blueprint(tmp_path: Path, blueprint: str) -> None:
    """Change, save, restart, load; refusals; migration; autosave; pages; client."""
    project = make_project(tmp_path, blueprint, [FEATURE])
    result = run_godot(
        project, TEST, ("--autostart", _settings(tmp_path)), timeout=240, marker="save-load"
    )
    found = _DIR_LINE.search(result.output)
    assert found is not None
    assert not Path(found.group(1).strip()).exists(), "the test's save folder is cleaned up"
    summary = _SUMMARY_LINE.search(result.output)
    assert summary is not None
    if blueprint in _adapter_ids():
        assert summary.group(1).strip() != "", "the adapter sums up the slot"


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_saves_survive_a_new_process(tmp_path: Path, blueprint: str) -> None:
    """Save in one process; Continue from the main menu in the next."""
    project = make_project(tmp_path, blueprint, [FEATURE])
    saves = tmp_path / "saves"
    args = ("--autostart", _settings(tmp_path), f"--saves-dir={saves}")
    run_godot(project, TEST, (*args, "--save-phase=save"), timeout=180, marker="save-load")
    slot = saves / "slot_1.json"
    assert slot.is_file()
    names = {p.name for p in saves.iterdir()}
    # Adapters with their own autosave moments may also have written slot 0.
    assert names - {"slot_0.json"} == {"expect.json", "slot_1.json"}
    data = json.loads(slot.read_text())
    assert data["format"] == 1 and data["version"] == 1
    assert data["game"] == "Blueprint Test"
    assert data["blueprint"] == blueprint
    assert data["level"].startswith("res://game/")
    assert isinstance(data["nodes"], dict) and isinstance(data["extra"], dict)
    assert "save_load_test" in data["extra"]
    run_godot(project, TEST, (*args, "--save-phase=load"), timeout=180, marker="save-load")
    assert slot.is_file(), "loading never deletes the slot"


@needs_godot
def test_damaged_saves_on_disk_never_crash_the_game(tmp_path: Path) -> None:
    project = make_project(tmp_path, "platformer-2d", [FEATURE])
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "slot_0.json").write_text("{not json")
    (saves / "slot_1.json").write_bytes(b"\x00\xff\xfe garbage")
    (saves / "slot_2.json").write_text(json.dumps({"format": 99}))
    (saves / "slot_1.json.tmp").write_text("{half")
    boot(project, frames=90, user_args=("--autostart", _settings(tmp_path), f"--saves-dir={saves}"))
    boot(project, frames=60, user_args=(_settings(tmp_path), f"--saves-dir={saves}"))
    assert (saves / "slot_0.json").read_text() == "{not json", "damaged saves are left alone"


@needs_godot
@pytest.mark.parametrize("blueprint", ["platformer-2d", "puzzle-cards"])
def test_only_the_host_saves_online(tmp_path: Path, blueprint: str) -> None:
    """Two processes: the host saves with a client in; the client is refused."""
    if not _feature_complete("online-play"):
        pytest.skip("online play is not in the catalogue yet")
    if blueprints.resolve(blueprint, ["online-play", FEATURE]).problems:
        pytest.skip("online play and save-load do not combine yet")
    project = make_project(tmp_path, blueprint, ["online-play", FEATURE])
    host, client = run_pair(
        project,
        ("--host", "--port={port}", "--autostart", f"--settings-path={tmp_path / 'host.cfg'}"),
        ("--join=127.0.0.1:{port}", "--autostart", f"--settings-path={tmp_path / 'client.cfg'}"),
        timeout=120,
        script=ONLINE_TEST,
        host_marker="save-load host",
        client_marker="save-load client",
    )
    assert "FAIL" not in host.output + client.output


@needs_godot
def test_save_load_with_every_feature(tmp_path: Path) -> None:
    """The full check on a starting point with every feature applied."""
    features = sorted(f.id for f in blueprints.list_features())
    if not all(_feature_complete(f) for f in features):
        pytest.skip("not every feature is complete in the catalogue yet")
    resolution = blueprints.resolve("platformer-2d", features)
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = make_project(tmp_path, "platformer-2d", features)
    run_godot(project, TEST, ("--autostart", _settings(tmp_path)), timeout=240, marker="save-load")
