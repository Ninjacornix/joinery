"""The camera feature: rigs that follow, look ahead, frame groups and shake.

The Godot check runs `res://tests/features/camera/camera_test.gd` in stock Godot
on a 2D and a 3D blueprint: the rig becomes the current camera, keeps still while
its target moves inside the dead zone, eases after a bigger move and settles at
the dead zone's edge, looks ahead of a moving target, zooms out (2D) or pulls
back (3D) to keep a group in view, and shakes through `Cameras.shake()` only as
far as the screen shake and reduced motion settings allow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

FEATURE = "camera"
TEST = "res://tests/features/camera/camera_test.gd"


def test_the_feature_is_code_only_and_combines_with_every_blueprint() -> None:
    feature = blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)
    assert feature.merge.autoload == {"Cameras": "res://features/camera/cameras.gd"}
    overlay = blueprints.FEATURES_DIR / FEATURE / "overlay"
    suffixes = {p.suffix for p in overlay.rglob("*") if p.is_file()}
    assert suffixes <= {".gd", ".uid", ".md"}
    for blueprint in blueprints.list_blueprints():
        assert blueprints.resolve(blueprint.id, [FEATURE]).problems == []


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "third-person-3d")
    before = (project / "project.godot").read_text(encoding="utf-8")
    blueprints.add_feature(project, FEATURE)
    assert "Cameras=" in (project / "project.godot").read_text(encoding="utf-8")
    blueprints.remove_feature(project, FEATURE)
    assert not (project / "features/camera").exists()
    assert (project / "project.godot").read_text(encoding="utf-8") == before


@needs_godot
@pytest.mark.parametrize("blueprint", ["top-down", "third-person-3d"])
def test_rigs_follow_frame_and_shake_in_the_game(tmp_path: Path, blueprint: str) -> None:
    features = sorted({*blueprints.describe(blueprint)["default_features"], FEATURE})
    project = make_project(tmp_path, blueprint, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    run_godot(project, TEST, ("--autostart", settings), timeout=240, marker=FEATURE)
