"""Play test for the puzzle-cards (memory cards) blueprint, run in stock Godot.

The GDScript play test (`res://tests/game/play_test.gd`) drives the game with
injected input only: a seed deals a known layout; `ui_*` moves focus and
`ui_accept` flips; a matching pair is removed and counts one move; a miss
turns back after 0.8 s and input is ignored meanwhile; clicking cards by
position works; streaks add bonus points; save_state/load_state round-trips
the board (also mid-miss); reduced motion skips the flip; pause, Restart and
the win screen (stars, score, Play again) work by keyboard; running out of
moves fails the level; two seats alternate turns (a miss passes the turn,
only the seated peer may flip); and the main menu's "Two players" entry
starts a same-screen game.

With the online-play feature, `res://tests/game/online_test.gd` runs a host
and a client in two processes: the seats are the peers, only the peer whose
turn it is can flip, a client's flip runs on the server and comes back, a
miss passes the turn and a match keeps it, on both screens.
"""

from __future__ import annotations

import re
import tomllib
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

BLUEPRINT = "puzzle-cards"
PLAY_TEST = "res://tests/game/play_test.gd"
ONLINE_TEST = "res://tests/game/online_test.gd"
ROOT = blueprints.BLUEPRINTS_DIR / BLUEPRINT
OVERLAY = ROOT / "overlay"


def _supported_features() -> list[str]:
    """Every catalogue feature that supports this blueprint."""
    return sorted(f.id for f in blueprints.list_features() if f.supports(BLUEPRINT))


def _settings_arg(tmp_path: Path) -> str:
    return f"--settings-path={tmp_path / 'settings.cfg'}"


def test_blueprint_is_in_the_catalogue() -> None:
    described = blueprints.describe(BLUEPRINT)
    assert described["dimension"] == "ui"
    assert described["bible_preset"] == "cards-ui"
    assert described["suggested_tools"] == ["godot", "krita", "supercollider"]
    assert described["default_features"] == ["controller", "save-load", "translations"]
    assert described["main_scene"] == "res://core/main.tscn"
    assert described["hooks"]["level_scene"] == "res://game/level.tscn"
    assert described["hooks"]["player_scene"] == ""
    assert "restart" in described["input"]
    brief = described["brief"]
    assert brief["genre"] and brief["core_loop"] and brief["progression"] and brief["camera"]
    assert all(len(verb) == 3 for verb in brief["verbs"])


def test_blueprint_uses_only_the_standard_vocabulary() -> None:
    unit = blueprints.load_blueprint(ROOT)
    assert set(unit.merge.input) <= {"restart"}
    assert not any(action.startswith("ui_") for action in unit.merge.input)
    assert unit.merge.autoload == {"MemoryCards": "res://game/cards_menu.gd"}


def _is_mine(problem: str) -> bool:
    """A catalogue problem caused by this blueprint's own files or manifest.

    Problems a feature reports about its adapter for this blueprint
    (`save-load/puzzle-cards: …`) belong to that feature, and a default
    feature may not exist until its package lands.
    """
    if problem.startswith(f"blueprint {BLUEPRINT}:"):
        return "does not exist" not in problem
    if problem.startswith((f"{BLUEPRINT}:", f"{BLUEPRINT}+")):
        return True
    reuse = re.match(r"^(\S+) reuses \S+ from (\S+)$", problem)
    return reuse is not None and BLUEPRINT in reuse.groups()


def test_catalogue_has_no_problems_of_its_own() -> None:
    mine = [problem for problem in blueprints.validate_catalogue() if _is_mine(problem)]
    assert mine == []


def test_overlay_is_code_only_greybox() -> None:
    assert check_overlay(OVERLAY, blueprints.BLUEPRINT_ROOTS) == []
    found = [
        p for p in ROOT.rglob("*")
        if p.suffix.lower() in blueprints.BANNED_SUFFIXES and p.relative_to(ROOT).as_posix() not in blueprints.CAPTURE_FILES
    ]
    assert found == []
    level = (OVERLAY / "game/level.tscn").read_text()
    assert '[node name="Players"' in level
    for script in OVERLAY.rglob("*.gd"):
        text = script.read_text()
        assert "load(\"res://art" not in text and "load(\"res://audio" not in text


def test_apply_writes_its_settings(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT)
    text = (project / "project.godot").read_text()
    assert 'blueprint="puzzle-cards"' in text
    assert 'hooks/level_scene="res://game/level.tscn"' in text
    assert "window/size/viewport_height=720" in text
    assert 'window/stretch/mode="canvas_items"' in text
    assert 'MemoryCards="*res://game/cards_menu.gd"' in text
    assert "restart={" in text
    for rel in ("game/level.tscn", "game/cards/card.tscn", "tests/game/play_test.gd"):
        assert (project / rel).is_file(), rel
    toml = tomllib.loads((project / "joinery.toml").read_text())
    assert toml["game"]["blueprint"] == BLUEPRINT


@needs_godot
def test_main_menu_boots_without_errors(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT)
    boot(project, frames=90, user_args=(_settings_arg(tmp_path),))


@needs_godot
def test_play_blueprint_alone(tmp_path: Path) -> None:
    project = make_project(tmp_path, BLUEPRINT)
    args = ("--autostart", _settings_arg(tmp_path))
    result = run_godot(project, PLAY_TEST, args, timeout=300, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output


@needs_godot
def test_play_with_all_features(tmp_path: Path) -> None:
    features = _supported_features()
    if not features:
        pytest.skip("no features in the catalogue yet")
    resolution = blueprints.resolve(BLUEPRINT, features)
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = make_project(tmp_path, BLUEPRINT, features)
    args = ("--autostart", _settings_arg(tmp_path))
    result = run_godot(project, PLAY_TEST, args, timeout=300, marker=BLUEPRINT)
    assert f"{BLUEPRINT}: ok" in result.output


@needs_godot
def test_two_peers_take_turns_online(tmp_path: Path) -> None:
    if "online-play" not in _supported_features():
        pytest.skip("the online-play feature is not in the catalogue yet")
    project = make_project(tmp_path, BLUEPRINT, ["online-play"])
    host_args = ("--host", "--port={port}", "--autostart", _settings_arg(tmp_path / "host"))
    client_args = ("--join=127.0.0.1:{port}", "--autostart", _settings_arg(tmp_path / "client"))
    host, client = run_pair(
        project,
        host_args,
        client_args,
        timeout=120,
        script=ONLINE_TEST,
        host_marker="cards_host",
        client_marker="cards_client",
    )
    assert "cards_host: ok" in host.output
    assert "cards_client: ok" in client.output
