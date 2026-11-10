"""Play test for the platformer-2d blueprint, run in stock Godot headless.

The GDScript play test (`res://tests/game/play_test.gd`, checks in
`play_checks.gd`) drives the game with injected input: running and stopping,
tap versus held jump height, coyote time after a ledge, the jump buffer before
landing, dropping through one-way platforms, coins and the score HUD,
checkpoints, spikes and pits sending the player back, the restart action, a
save_state/load_state round trip, pause, the Gameplay settings page, the
minimum text size and the flag completing the level.
"""

from __future__ import annotations

import re
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

BLUEPRINT = "platformer-2d"
PLAY_TEST = "res://tests/game/play_test.gd"
ROOT = blueprints.BLUEPRINTS_DIR / BLUEPRINT
OVERLAY = ROOT / "overlay"
LEGEND = set(".#-^oCFP")


def _supported_features() -> list[str]:
    """Every catalogue feature that supports this blueprint."""
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def _map_rows() -> list[str]:
    text = (OVERLAY / "game/level.gd").read_text(encoding="utf-8")
    block = re.search(r"const MAP: Array\[String\] = \[(.*?)\n\]", text, re.DOTALL)
    assert block is not None, "level.gd keeps its map in const MAP"
    return re.findall(r'"([^"]*)"', block.group(1))


def test_blueprint_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["dimension"] == "2d"
    assert described["bible_preset"] == "pixel-2d"
    assert described["hooks"]["level_scene"] == "res://game/level.tscn"
    assert described["hooks"]["player_scene"] == "res://game/player/player.tscn"
    assert {"move_left", "move_right", "move_down", "jump", "restart"} <= set(described["input"])
    assert set(described["input"]) <= blueprints.ACTIONS
    assert described["brief"]["verbs"], "the brief has player verbs"


def test_catalogue_has_no_problems_with_this_blueprint() -> None:
    # Default features are checked once the feature packages exist.
    pending = re.compile(r"default feature \S+ does not exist|default features: ")
    problems = [
        p for p in blueprints.validate_catalogue()
        if BLUEPRINT in p and not pending.search(p)
    ]
    assert problems == []


def test_overlay_is_code_only_greybox() -> None:
    found = [p for p in ROOT.rglob("*") if p.suffix.lower() in blueprints.BANNED_SUFFIXES]
    assert found == []
    for script in ROOT.rglob("*.gd"):
        uid = script.with_name(script.name + ".uid")
        assert uid.is_file(), f"{script} has no .uid sidecar"
        assert re.fullmatch(r"uid://[a-y0-9]+", uid.read_text(encoding="utf-8").strip())


def test_scenes_point_at_the_scripts_own_uids() -> None:
    uids = {
        "res://" + p.relative_to(OVERLAY).as_posix().removesuffix(".uid"): p.read_text().strip()
        for p in OVERLAY.rglob("*.gd.uid")
    }
    headers = {
        "res://" + p.relative_to(OVERLAY).as_posix(): re.search(r'uid="([^"]+)"', p.read_text())
        for p in OVERLAY.rglob("*.tscn")
    }
    scene_uids = {path: m.group(1) for path, m in headers.items() if m}
    assert len(scene_uids) == len(headers), "every scene header has a uid"
    for scene in OVERLAY.rglob("*.tscn"):
        for uid, path in re.findall(r'ext_resource [^\]]*uid="([^"]+)" path="([^"]+)"', scene.read_text()):
            expected = uids.get(path) or scene_uids.get(path)
            assert expected == uid, f"{scene.name} refers to {path} with a stale uid"


def test_map_is_well_formed() -> None:
    rows = _map_rows()
    assert rows, "the map has rows"
    assert len({len(r) for r in rows}) == 1, "every row is the same width"
    tiles = "".join(rows)
    assert set(tiles) <= LEGEND
    assert tiles.count("P") == 1 and tiles.count("F") >= 1
    assert tiles.count("o") == 8 and tiles.count("C") == 2
    assert all(r[0] == "#" and r[-1] == "#" for r in rows), "walls close the level"
    assert "." in rows[-1], "the level has a pit to fall into"


@needs_godot
def test_main_menu_and_autostart_boot_cleanly(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT, [])
    boot(project, frames=90, user_args=("--settings-path=user://test_settings.cfg",))
    boot(project, frames=180)


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
