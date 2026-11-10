"""The controller feature: pad bindings, prompts, pad-friendly menus, rebinding.

Static checks read `features/controller/feature.toml` and the merged
`project.godot` of every blueprint. The Godot checks run
`res://tests/features/controller/controller_test.gd` in stock Godot, which
injects simulated `InputEventJoypadButton` / `InputEventJoypadMotion` events
(plus keys and mouse motion) through `Input.parse_input_event`: pad bindings,
deadzone, the left stick moving the player, device switching and the hidden
cursor, text prompts per pad layout, rumble, pausing when the pad disconnects,
d-pad menu navigation into the Controls page, press-to-bind with swap and
cancel, and the page's settings. A second process with the same settings file
checks the rebinding survived the restart, then resets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from joinery.godot_config import (
    GodotConfig,
    action_deadzone,
    action_events,
    event_identity,
)
from tests.blueprints.godot_run import boot, make_project, needs_godot, run_godot
from tests.blueprints.test_core import check_overlay

FEATURE = "controller"
CONTROLLER_TEST = "res://tests/features/controller/controller_test.gd"
AUTOLOAD = "res://features/controller/controller.gd"
ROOTS = ("features/controller/", "tests/features/controller/")
STICK_DEADZONE = 0.25

# The standard vocabulary's pad defaults (contract C3): buttons and axes.
PAD_DEFAULTS: dict[str, tuple[list[int], list[str]]] = {
    "move_left": ([13], ["0-"]),
    "move_right": ([14], ["0+"]),
    "move_up": ([11], ["1-"]),
    "move_down": ([12], ["1+"]),
    "move_forward": ([11], ["1-"]),
    "move_back": ([12], ["1+"]),
    "look_left": ([], ["2-"]),
    "look_right": ([], ["2+"]),
    "look_up": ([], ["3-"]),
    "look_down": ([], ["3+"]),
    "jump": ([0], []),
    "interact": ([2], []),
    "attack": ([10], ["5+"]),
    "dash": ([1], []),
    "sprint": ([7], []),
    "crouch": ([1], []),
    "restart": ([4], []),
    "pause": ([6], []),
}
PAD = ("Object(InputEventJoypadButton", "Object(InputEventJoypadMotion")
STICK_AXES = ('"axis":0,', '"axis":1,', '"axis":2,', '"axis":3,')


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


def _input(project: Path) -> dict[str, list[str]]:
    """Every `[input]` action of a project: its event texts."""
    config = GodotConfig.parse((project / "project.godot").read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for action in blueprints.ACTIONS:
        raw = config.get("input", action)
        if raw is not None:
            found[action] = action_events(raw)
    return found


def _deadzone(project: Path, action: str) -> float:
    config = GodotConfig.parse((project / "project.godot").read_text(encoding="utf-8"))
    raw = config.get("input", action)
    assert raw is not None
    return action_deadzone(raw)


def _settings(tmp_path: Path) -> str:
    return f"--settings-path={tmp_path / 'settings.cfg'}"


def _run_both_phases(project: Path, tmp_path: Path) -> None:
    """Rebind in one process, check it came back in a second, then reset."""
    settings = _settings(tmp_path)
    run_godot(project, CONTROLLER_TEST, ("--autostart", settings), timeout=240, marker=FEATURE)
    assert (tmp_path / "settings.cfg").is_file(), "the rebinding was saved"
    saved = (tmp_path / "settings.cfg").read_text(encoding="utf-8")
    assert "[input]" in saved and "bindings=" in saved
    run_godot(
        project,
        CONTROLLER_TEST,
        ("--autostart", settings, "--controller-phase=verify"),
        timeout=180,
        marker=FEATURE,
    )


# --- Static ----------------------------------------------------------------------


def test_feature_declares_pad_bindings_for_the_whole_vocabulary() -> None:
    feature = _feature()
    assert feature.name == "Controller support"
    assert feature.blueprints == ["*"]
    assert feature.requires == [] and feature.conflicts == [] and feature.external == []
    assert feature.merge.autoload == {"Controller": AUTOLOAD}
    assert feature.merge.settings == {} and feature.merge.append == {}
    assert set(feature.merge.input) == set(blueprints.ACTIONS)
    for action, spec in feature.merge.input.items():
        buttons, axes = PAD_DEFAULTS[action]
        assert spec.pad_buttons == buttons, action
        assert spec.pad_axes == axes, action
        assert spec.keys == [] and spec.mouse == [], f"{action}: keyboard defaults are the blueprint's"
        assert spec.create is not True, f"{action}: features never create actions"
        sticks = [a for a in spec.pad_axes if a[0] in "0123"]
        if sticks:
            assert spec.deadzone == STICK_DEADZONE, action


def test_overlay_is_code_only_and_typed() -> None:
    feature_dir = blueprints.FEATURES_DIR / FEATURE
    assert check_overlay(feature_dir / "overlay", ROOTS) == []
    assert not (feature_dir / "adapters").exists(), "the vocabulary covers every blueprint"
    for script in (feature_dir / "overlay").rglob("*.gd"):
        text = script.read_text(encoding="utf-8")
        assert "load(\"res://game/" not in text, f"{script.name} must not depend on one blueprint"
        assert ".png" not in text and ".svg" not in text, f"{script.name}: text prompts only"


def test_catalogue_has_no_controller_problems() -> None:
    problems = [p for p in blueprints.validate_catalogue() if FEATURE in p.lower()]
    assert problems == []


def test_controller_combines_with_every_blueprint() -> None:
    for blueprint in blueprints.list_blueprints():
        resolution = blueprints.resolve(blueprint.id, [FEATURE])
        assert resolution.problems == [], (blueprint.id, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]
        assert resolution.added == {}


def test_no_blueprint_binds_crouch_and_dash_together() -> None:
    # Both default to B; a blueprint never has both (contract C3).
    for blueprint in blueprints.list_blueprints():
        actions = set(blueprints.describe(blueprint.id)["input"])
        assert not {"crouch", "dash"} <= actions, blueprint.id


@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_every_vocabulary_action_gets_pad_events_once(tmp_path: Path, blueprint: str) -> None:
    plain = make_project(tmp_path / "plain", blueprint)
    project = make_project(tmp_path / "pad", blueprint, [FEATURE])
    before = _input(plain)
    after = _input(project)
    assert set(after) == set(before), "the feature adds no actions"
    feature = _feature()
    for action, events in after.items():
        pads = [e for e in events if e.startswith(PAD)]
        expected = feature.merge.input[action].events()
        assert len(pads) == len(expected), (action, pads)
        assert {event_identity(e) for e in pads} == {event_identity(e) for e in expected}
        identities = [event_identity(e) for e in events]
        assert len(identities) == len(set(identities)), f"{action} has a duplicate event"
        keyboard = [e for e in events if e not in pads]
        assert keyboard == before[action], f"{action}: keyboard and mouse bindings are kept"
        if any(e.startswith(PAD[1]) and any(a in e for a in STICK_AXES) for e in pads):
            assert _deadzone(project, action) == pytest.approx(STICK_DEADZONE), action
    assert "pause" in after, "the core pause action gets Start"

    # Applying again changes nothing and duplicates nothing.
    text = (project / "project.godot").read_text(encoding="utf-8")
    again = blueprints.plan(project, blueprint, [FEATURE])
    assert again.conflicts == [] and again.changes == []
    blueprints.apply(project, blueprint, [FEATURE])
    assert (project / "project.godot").read_text(encoding="utf-8") == text


def test_add_then_remove_strips_only_pad_events(tmp_path: Path) -> None:
    project = make_project(tmp_path, "platformer-2d")
    before = (project / "project.godot").read_text(encoding="utf-8")
    keyboard = _input(project)
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text(encoding="utf-8")
    assert f'Controller="*{AUTOLOAD}"' in text
    assert (project / "features/controller/controller.gd").is_file()
    assert (project / "features/controller/controller.gd.uid").is_file()
    assert (project / "tests/features/controller/controller_test.gd").is_file()
    game = blueprints.project_game(project)
    assert game is not None and FEATURE in game["features"]

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_text(encoding="utf-8") == before
    assert _input(project) == keyboard
    assert not (project / "features/controller").exists()
    assert not (project / "tests/features/controller").exists()


def test_remove_keeps_a_file_the_user_changed(tmp_path: Path) -> None:
    """Removal deletes only unmodified files; pad events and the autoload go."""
    project = make_project(tmp_path, "top-down", [FEATURE])
    page = project / "features/controller/controls_page.gd"
    page.write_text(page.read_text(encoding="utf-8") + "\n# my note\n", encoding="utf-8")
    removed = blueprints.remove_feature(project, FEATURE)
    assert "features/controller/controls_page.gd" in removed.kept
    assert page.is_file()
    assert not (project / "features/controller/controller.gd").exists()
    text = (project / "project.godot").read_text(encoding="utf-8")
    assert "InputEventJoypad" not in text
    assert "Controller=" not in text


# --- Stock Godot -----------------------------------------------------------------


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_simulated_pad_on_every_blueprint(tmp_path: Path, blueprint: str) -> None:
    project = make_project(tmp_path, blueprint, [FEATURE])
    _run_both_phases(project, tmp_path)


@needs_godot
@pytest.mark.parametrize("blueprint", ["platformer-2d", "first-person-3d"])
def test_simulated_pad_with_every_feature(tmp_path: Path, blueprint: str) -> None:
    features = sorted(f.id for f in blueprints.list_features() if f.supports(blueprint))
    resolution = blueprints.resolve(blueprint, features)
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = make_project(tmp_path, blueprint, features)
    _run_both_phases(project, tmp_path)


@needs_godot
def test_project_boots_after_removal(tmp_path: Path) -> None:
    project = make_project(tmp_path, "third-person-3d", [FEATURE])
    boot(project, frames=60)
    blueprints.remove_feature(project, FEATURE)
    stamp = project / ".godot" / ".joinery-imported"
    stamp.unlink(missing_ok=True)
    boot(project, frames=60)
