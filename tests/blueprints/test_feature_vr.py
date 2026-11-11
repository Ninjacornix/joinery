"""The VR feature: OpenXR settings, the XR rig, comfort locomotion and the
desktop fallback, checked in stock Godot without a headset.

`res://tests/features/vr/vr_test.gd` runs the comfort maths (snap turn,
smooth move, teleport arc and landings, seated height, vignette), loads the
rig scene, grabs and throws with a hand, and drives the VR comfort settings
page. Without `--vr-simulate` the game must be on the desktop with a notice;
with it, the rig is carried on the player and walks, snaps, teleports,
points, grabs, opens and clicks the headset menu panel, and (with save-load)
keeps its turn in a save. Real headsets were not available here: everything
that needs one is behind the OpenXR interface Godot itself provides.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import (
    boot,
    godot_binary,
    godot_env,
    import_project,
    make_project,
    needs_godot,
    run_godot,
)
from tests.blueprints.test_core import check_overlay

FEATURE = "vr"
FEATURE_DIR = blueprints.FEATURES_DIR / FEATURE
TEST = "res://tests/features/vr/vr_test.gd"
ROOTS = ("features/vr/", "tests/features/vr/")
THREE_D = ["first-person-3d", "third-person-3d", "vr-room"]
SETTINGS = {
    "xr/openxr/enabled": True,
    "xr/openxr/startup_alert": False,
    "xr/openxr/default_action_map": "res://features/vr/openxr_action_map.tres",
    "xr/shaders/enabled": True,
}
#: Actions the rig and hands read from the action map.
RIG_ACTIONS = (
    "primary", "grip", "grip_click", "trigger", "trigger_click",
    "menu_button", "by_button", "aim_pose", "haptic",
)
_ERROR = re.compile(r"^(SCRIPT ERROR|ERROR:|USER SCRIPT ERROR|USER ERROR)", re.MULTILINE)


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(FEATURE_DIR)


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


def _settings(tmp_path: Path, name: str = "settings.cfg") -> str:
    return f"--settings-path={tmp_path / name}"


# --- static -----------------------------------------------------------------------


def test_feature_declares_openxr_and_the_vr_autoload() -> None:
    feature = _feature()
    assert feature.name == "VR"
    assert sorted(feature.blueprints) == THREE_D
    assert feature.requires == [] and feature.conflicts == []
    assert feature.enhances == ["controller", "save-load"]
    assert feature.external == [], "OpenXR is built into Godot; nothing is downloaded"
    assert feature.merge.autoload == {"VR": "res://features/vr/vr.gd"}
    assert feature.merge.settings == SETTINGS
    assert feature.merge.input == {}
    assert feature.adapters == {}


def test_vr_overlay_is_code_only() -> None:
    assert check_overlay(FEATURE_DIR / "overlay", ROOTS) == []
    assert not (FEATURE_DIR / "adapters").exists(), "the rig works on every 3D player"


def test_action_map_is_a_plain_godot_resource() -> None:
    text = (FEATURE_DIR / "overlay/features/vr/openxr_action_map.tres").read_text()
    assert text.startswith('[gd_resource type="OpenXRActionMap" format=3 uid="uid://')
    names = set(re.findall(r'^resource_name = "([^"]+)"$', text, re.MULTILINE))
    assert "godot" in names, "the action set"
    assert set(RIG_ACTIONS) <= names
    profiles = re.findall(r'^interaction_profile_path = "([^"]+)"$', text, re.MULTILINE)
    assert "/interaction_profiles/khr/generic_controller" in profiles
    assert "/interaction_profiles/oculus/touch_controller" in profiles


def test_vr_passes_catalogue_checks() -> None:
    problems = [p for p in blueprints.validate_catalogue() if "vr" in p.split(":", 1)[0]]
    assert problems == []


def test_vr_combines_with_every_3d_blueprint() -> None:
    for blueprint in THREE_D:
        resolution = blueprints.resolve(blueprint, [FEATURE])
        assert resolution.problems == [], (blueprint, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]


def test_vr_is_refused_on_2d_blueprints_with_a_plain_message() -> None:
    flat = [b for b in blueprints.list_blueprints() if b.dimension != "3d"]
    assert {b.id for b in flat} >= {"blank", "platformer-2d", "top-down", "puzzle-cards"}
    for blueprint in flat:
        problems = blueprints.resolve(blueprint.id, [FEATURE]).problems
        expected = (
            f"VR does not work with the {blueprint.name} blueprint. VR needs a 3D "
            "starting point: First-person 3D, Third-person 3D or VR room."
        )
        assert problems == [expected], blueprint.id
    for blueprint in blueprints.list_blueprints():
        supported = blueprint.dimension == "3d"
        assert _feature().supports(blueprint.id) == supported, blueprint.id


def test_the_wizard_says_why_vr_is_not_offered(tmp_path: Path) -> None:
    from joinery import game_setup

    catalogue = game_setup.wizard_catalogue()
    rows = {
        b["id"]: next(f for f in b["features"] if f["id"] == FEATURE)
        for b in catalogue["blueprints"]
    }
    assert rows["vr-room"]["available"] and rows["vr-room"]["default"]
    assert rows["first-person-3d"]["available"] and not rows["first-person-3d"]["default"]
    assert not rows["platformer-2d"]["available"]
    assert "VR needs a 3D starting point" in rows["platformer-2d"]["reason"]


@pytest.mark.parametrize("blueprint", THREE_D)
def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path, blueprint: str) -> None:
    project = make_project(tmp_path, blueprint)
    before = (project / "project.godot").read_bytes()
    files_before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text()
    assert 'VR="*res://features/vr/vr.gd"' in text
    assert "openxr/enabled=true" in text and "shaders/enabled=true" in text
    assert 'openxr/default_action_map="res://features/vr/openxr_action_map.tres"' in text
    assert (project / "features/vr/xr_rig.tscn").is_file()
    assert (project / "tests/features/vr/vr_test.gd").is_file()
    again = blueprints.plan(project, blueprint, [FEATURE])
    assert again.conflicts == [] and again.changes == [], "applying again changes nothing"

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_bytes() == before
    assert not (project / "features/vr").exists()
    assert not (project / "tests/features/vr").exists()
    files_after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    changed = set(files_before) ^ set(files_after)
    assert {f for f in changed if not f.startswith(".joinery/")} <= {"joinery.toml"}


def test_new_vr_room_from_the_command_line(tmp_path: Path) -> None:
    from joinery.cli import main

    project = tmp_path / "room"
    code = main([
        "new", str(project), "--tools", "godot",
        "--blueprint", "vr-room", "--features", "vr,save-load",
    ])
    assert code == 0
    game = blueprints.project_game(project)
    assert game is not None
    assert game["blueprint"] == "vr-room"
    assert set(game["features"]) == {"vr", "save-load"}
    assert (project / "game/level.tscn").is_file()
    assert (project / "features/vr/xr_rig.tscn").is_file()
    assert (project / "features/save-load/blueprint_adapter.gd").is_file()
    assert "openxr/enabled=true" in (project / "project.godot").read_text()
    brief = (project / "design/brief.md").read_text()
    assert "Teleport" in brief and "{{" not in brief


# --- in Godot -----------------------------------------------------------------------


@needs_godot
@pytest.mark.parametrize("blueprint", THREE_D)
def test_desktop_fallback(tmp_path: Path, blueprint: str) -> None:
    """No OpenXR here: the game starts on the desktop, says so, and plays."""
    project = make_project(tmp_path, blueprint, [FEATURE])
    result = run_godot(project, TEST, ("--autostart", _settings(tmp_path)), timeout=240, marker="vr")
    assert "VR: " in result.output and "playing on the desktop" in result.output


@needs_godot
@pytest.mark.parametrize("blueprint", THREE_D)
def test_simulated_rig(tmp_path: Path, blueprint: str) -> None:
    """The rig on the player: walk, snap, teleport, point, grab, menu, save."""
    project = make_project(tmp_path, blueprint, [FEATURE, "save-load"])
    result = run_godot(
        project, TEST, ("--autostart", _settings(tmp_path), "--vr-simulate"),
        timeout=240, marker="vr",
    )
    assert "VR: simulated rig, no headset" in result.output


@needs_godot
def test_without_an_openxr_runtime_godot_starts_in_normal_mode(tmp_path: Path) -> None:
    """OpenXR on, no runtime: only Godot's own OpenXR lines, then the desktop."""
    project = make_project(tmp_path, "vr-room", [FEATURE, "controller", "save-load"])
    result = boot(project, frames=180, xr=True)
    assert "VR: no OpenXR runtime or headset; playing on the desktop" in result.output
    run_godot(project, TEST, ("--autostart", _settings(tmp_path)), timeout=240, marker="vr", xr=True)


@needs_godot
def test_comfort_settings_are_kept_between_sessions(tmp_path: Path) -> None:
    project = make_project(tmp_path, "vr-room", [FEATURE, "save-load"])
    settings = _settings(tmp_path, "kept.cfg")
    run_godot(project, TEST, (settings, "--vr-phase=write"), timeout=120, marker="vr")
    text = (tmp_path / "kept.cfg").read_text()
    assert "[vr]" in text and "snap_turn=30" in text and "seated=true" in text
    run_godot(project, TEST, (settings, "--vr-phase=read"), timeout=120, marker="vr")


@needs_godot
def test_the_project_opens_in_the_stock_editor(tmp_path: Path) -> None:
    project = make_project(tmp_path, "vr-room", [FEATURE, "controller", "save-load"])
    import_project(project)
    binary = godot_binary()
    assert binary is not None
    result = subprocess.run(
        [str(binary), "--headless", "--editor", "--path", str(project), "--quit-after", "120"],
        capture_output=True, text=True, timeout=180, env=godot_env(project), check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-3000:]
    assert _ERROR.findall(output) == [], output[-3000:]
    assert not (project / "openxr_action_map.tres").exists(), "the editor uses the shipped map"
