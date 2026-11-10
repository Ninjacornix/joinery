"""Play test for the top-down blueprint, run in stock Godot headless.

The GDScript play test (`res://tests/game/play_test.gd`, checks in
`play_checks.gd`) drives the game with injected input: eight-direction
movement with normalised diagonals, the dash and its cooldown, the attack arc
hitting in front and not behind, enemy contact damage with invulnerable
frames, gems and hearts, three waves spawned through `Game.spawn`, the win and
lose screens, pause, the screen shake and reduced motion settings, text sizes
and a save_state/load_state round trip.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import (
    TEST_ARGS,
    boot,
    make_project,
    needs_godot,
    run_godot,
)

BLUEPRINT = "top-down"
PLAY_TEST = "res://tests/game/play_test.gd"
ROOT = blueprints.BLUEPRINTS_DIR / BLUEPRINT


def _supported_features() -> list[str]:
    """Every catalogue feature that supports this blueprint."""
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def test_blueprint_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["dimension"] == "2d"
    assert described["bible_preset"] == "flat-2d"
    assert described["hooks"]["level_scene"] == "res://game/level.tscn"
    assert described["hooks"]["player_scene"] == "res://game/player/player.tscn"
    assert described["hooks"]["spawnable_scenes"] == ["res://game/enemies/chaser.tscn"]
    actions = set(described["input"])
    assert {"move_left", "move_right", "move_up", "move_down", "attack", "dash"} <= actions
    assert "crouch" not in actions  # shares a pad button with dash


def test_catalogue_has_no_problems_for_this_blueprint() -> None:
    # Missing default features belong to their own packages, not this one.
    problems = [
        p for p in blueprints.validate_catalogue()
        if f"blueprint {BLUEPRINT}:" in p and "default feature" not in p
    ]
    assert problems == []


def test_overlay_is_code_only_greybox() -> None:
    banned = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb", ".gltf", ".blend",
              ".wav", ".ogg", ".mp3", ".ttf", ".otf"}
    found = [
        p for p in ROOT.rglob("*")
        if p.suffix.lower() in banned and p.relative_to(ROOT).as_posix() not in blueprints.CAPTURE_FILES
    ]
    assert found == []
    for script in ROOT.rglob("*.gd"):
        uid = script.with_name(script.name + ".uid")
        assert uid.is_file(), f"{script} has no .uid sidecar"
        assert uid.read_text().startswith("uid://")
    for scene in ROOT.rglob("*.tscn"):
        assert 'uid="uid://' in scene.read_text().split("\n", 1)[0], f"{scene} header has no uid"


def test_overlay_writes_only_game_folders() -> None:
    overlay = ROOT / "overlay"
    for path in overlay.rglob("*"):
        if path.is_file():
            rel = path.relative_to(overlay).as_posix()
            assert rel.startswith(("game/", "tests/game/")), rel


def test_brief_defaults_are_complete() -> None:
    data = tomllib.loads((ROOT / "blueprint.toml").read_text())
    brief = data["brief"]
    for key in ("camera", "core_loop", "progression"):
        assert brief[key].strip()
    assert brief["genre"]
    assert all(len(row) == 3 for row in brief["verbs"])


@needs_godot
def test_play_blueprint_alone(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT, [])
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output


@needs_godot
def test_unattended_run_has_no_errors(tmp_path: Path) -> None:
    """Twenty seconds untouched: waves arrive, the idle player goes down, and
    the lose screen shows, all without a script error."""
    project = make_project(tmp_path, BLUEPRINT, [])
    boot(project, frames=1200, timeout=180)


@needs_godot
def test_play_with_all_features(tmp_path: Path) -> None:
    features = _supported_features()
    if not features:
        pytest.skip("no features in the catalogue yet")
    assert blueprints.resolve(BLUEPRINT, features).problems == []
    project = make_project(tmp_path, BLUEPRINT, features)
    result = run_godot(project, PLAY_TEST, TEST_ARGS, timeout=240, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output
