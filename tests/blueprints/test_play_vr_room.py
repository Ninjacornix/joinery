"""Play test for the vr-room blueprint, run in stock Godot headless.

The GDScript play test (`res://tests/game/play_test.gd`) plays the room on
the desktop with injected input: mouse and stick look, walking, the marks
that keep VR teleports off tables and walls, the closed glass case, picking
up, carrying and dropping the ball, the button opening the case, a
save_state/load_state round trip, pause, carrying the cube to the table to
win, and restart. The VR feature's own test plays it with the rig.
"""

from __future__ import annotations

from pathlib import Path

from joinery import blueprints
from tests.blueprints.godot_run import TEST_ARGS, make_project, needs_godot, run_godot

BLUEPRINT = "vr-room"
PLAY_TEST = "res://tests/game/play_test.gd"


def _supported_features() -> list[str]:
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def test_blueprint_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["dimension"] == "3d"
    assert described["name"] == "VR room"
    assert described["bible_preset"] == "vr-3d"
    assert described["default_features"] == ["vr", "controller", "save-load"]
    assert described["hooks"]["player_scene"] == "res://game/player/player.tscn"
    actions = set(described["input"])
    assert {"move_forward", "move_back", "move_left", "move_right", "interact"} <= actions
    assert {"look_left", "look_right", "look_up", "look_down"} <= actions
    assert "vr" in described["features_supported"]


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


def test_every_feature_with_per_blueprint_parts_covers_the_room() -> None:
    for feature in ("online-play", "save-load", "translations"):
        assert (blueprints.FEATURES_DIR / feature / "adapters" / BLUEPRINT).is_dir(), feature


@needs_godot
def test_play_blueprint_alone(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT, [])
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output


@needs_godot
def test_play_with_all_features(tmp_path: Path) -> None:
    features = _supported_features()
    assert blueprints.resolve(BLUEPRINT, features).problems == []
    project = make_project(tmp_path, BLUEPRINT, features)
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output
