"""The accessibility feature: its settings page, and every option applying in play.

The Godot check runs `res://tests/features/accessibility/accessibility_test.gd` in
stock Godot on blueprints that have what each option acts on (a held action, a camera
that shakes, a HUD): text size grows menu and HUD text, high contrast repaints the
menus, the colour-blind filter turns on its screen shader, reduced motion stops the
camera shaking, toggle latches a held action, game speed slows the game, subtitles show
and grow, and menu controls get the names screen readers say.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

FEATURE = "accessibility"
TEST = "res://tests/features/accessibility/accessibility_test.gd"


def test_the_feature_is_code_only_and_combines_with_every_blueprint() -> None:
    feature = blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)
    assert feature.merge.autoload == {
        "Accessibility": "res://features/accessibility/accessibility.gd",
        "Subtitles": "res://features/accessibility/subtitles.gd",
    }
    overlay = blueprints.FEATURES_DIR / FEATURE / "overlay"
    suffixes = {p.suffix for p in overlay.rglob("*") if p.is_file()}
    assert suffixes <= {".gd", ".uid", ".gdshader"}
    for blueprint in blueprints.list_blueprints():
        resolution = blueprints.resolve(blueprint.id, [FEATURE])
        assert FEATURE in [f.id for f in resolution.features]


@needs_godot
@pytest.mark.parametrize("blueprint", ["top-down", "third-person-3d"])
def test_every_option_applies_in_play(tmp_path: Path, blueprint: str) -> None:
    features = sorted({*blueprints.describe(blueprint)["default_features"], FEATURE})
    project = make_project(tmp_path, blueprint, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    run_godot(project, TEST, ("--autostart", settings), timeout=240, marker=FEATURE)
