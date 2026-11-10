"""Play test for the blank blueprint, run in stock Godot headless.

The GDScript play test (`res://tests/game/play_test.gd`) uses injected input
only: start from the main menu with ui_accept, pause pauses the tree, Resume
carries on, Restart builds a fresh level, Settings > Audio lowers the master volume and it is saved, Back
returns focus where it was, Main menu goes back to the menu, and every menu's
text is at least the minimum size.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot
from tests.blueprints.test_core import check_overlay

BLUEPRINT = "blank"
PLAY_TEST = "res://tests/game/play_test.gd"


def _supported_features() -> list[str]:
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def test_blank_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["name"] == "Blank"
    assert described["dimension"] == "2d"
    assert described["bible_preset"] == "none"
    assert described["suggested_tools"] == ["godot"]
    assert described["default_features"] == []
    assert described["main_scene"] == "res://core/main.tscn"
    assert described["hooks"]["level_scene"] == "res://game/level.tscn"
    assert described["hooks"]["player_scene"] == ""


def test_blank_overlay_is_tidy() -> None:
    root = blueprints.BLUEPRINTS_DIR / BLUEPRINT / "overlay"
    assert check_overlay(root, blueprints.BLUEPRINT_ROOTS) == []
    level = (root / "game/level.tscn").read_text()
    assert 'groups=["player_spawn"]' in level
    assert '[node name="Players"' in level
    assert 'text = "Your game starts here"' in level


def test_blank_applies_its_window_settings(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT)
    text = (project / "project.godot").read_text()
    assert 'blueprint="blank"' in text
    assert 'hooks/level_scene="res://game/level.tscn"' in text
    assert "window/size/viewport_height=720" in text
    assert 'window/stretch/mode="canvas_items"' in text


@needs_godot
def test_play_blank_alone(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    result = run_godot(project, PLAY_TEST, ("--autostart", settings), timeout=180, marker="blank")
    assert "blank: ok" in result.output


@needs_godot
def test_play_blank_with_all_features(tmp_path: Path) -> None:
    features = _supported_features()
    if not features:
        pytest.skip("no features in the catalogue yet")
    resolution = blueprints.resolve(BLUEPRINT, features)
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = make_project(tmp_path, BLUEPRINT, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    result = run_godot(project, PLAY_TEST, ("--autostart", settings), timeout=240, marker="blank")
    assert "blank: ok" in result.output
