"""Play test for the first-person-3d blueprint, run in stock Godot headless.

The GDScript play test (`res://tests/game/play_test.gd`) drives the game with
injected input: mouse and stick look with clamped pitch, walking, jumping onto
the ledge, using switches only when in range and looked at, the door blocking
until all three switches are on, the exit completing the level and showing a
win screen that stops the player, pause, the Camera settings page, restart and
a save_state/load_state round trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import TEST_ARGS, make_project, needs_godot, run_godot

BLUEPRINT = "first-person-3d"
PLAY_TEST = "res://tests/game/play_test.gd"


def _supported_features() -> list[str]:
    """Every catalogue feature that supports this blueprint."""
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def test_blueprint_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["dimension"] == "3d"
    assert described["hooks"]["player_scene"] == "res://game/player/player.tscn"
    actions = set(described["input"])
    assert {"move_forward", "move_back", "move_left", "move_right", "jump", "interact"} <= actions
    assert {"look_left", "look_right", "look_up", "look_down"} <= actions


def test_overlay_is_code_only_greybox() -> None:
    root = blueprints.BLUEPRINTS_DIR / BLUEPRINT
    banned = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb", ".gltf", ".blend",
              ".wav", ".ogg", ".mp3", ".ttf", ".otf"}
    found = [
        p for p in root.rglob("*")
        if p.suffix.lower() in banned and p.relative_to(root).as_posix() not in blueprints.CAPTURE_FILES
    ]
    assert found == []
    for script in root.rglob("*.gd"):
        uid = script.with_name(script.name + ".uid")
        assert uid.is_file(), f"{script} has no .uid sidecar"
        assert uid.read_text().startswith("uid://")


@needs_godot
def test_play_blueprint_alone(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT, [])
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output


@needs_godot
def test_play_with_all_features(tmp_path: Path) -> None:
    features = _supported_features()
    if not features:
        pytest.skip("no features in the catalogue yet")
    assert blueprints.resolve(BLUEPRINT, features).problems == []
    project = make_project(tmp_path, BLUEPRINT, features)
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output
