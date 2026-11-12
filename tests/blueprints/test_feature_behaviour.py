"""The behaviour feature: behaviour trees that run enemies in 2D and 3D.

The Godot check runs `res://tests/features/behaviour/behaviour_test.gd` in stock
Godot on a 2D and a 3D blueprint: tree text reads (and a broken tree names its
problems by line), the ready trees load, sequence, selector, invert, cooldown and
wait behave, a higher branch interrupts a running one, and enemies made in code
chase and attack the real player, flee, wander near home and patrol, while the
playtester reads each one's running branch, statuses and blackboard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

FEATURE = "behaviour"
TEST = "res://tests/features/behaviour/behaviour_test.gd"


def test_the_feature_is_code_and_text_only_and_combines_with_every_blueprint() -> None:
    feature = blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)
    assert feature.merge.autoload == {}
    overlay = blueprints.FEATURES_DIR / FEATURE / "overlay"
    suffixes = {p.suffix for p in overlay.rglob("*") if p.is_file()}
    assert suffixes <= {".gd", ".uid", ".tres", ".md"}
    for blueprint in blueprints.list_blueprints():
        assert blueprints.resolve(blueprint.id, [FEATURE]).problems == []


def test_ready_trees_keep_their_text_readable() -> None:
    trees = blueprints.FEATURES_DIR / FEATURE / "overlay/features/behaviour/trees"
    names = sorted(p.stem for p in trees.glob("*.tres"))
    assert names == ["chaser", "coward", "guard", "wanderer"]
    for path in trees.glob("*.tres"):
        text = path.read_text(encoding="utf-8")
        assert 'script_class="BehaviourTree"' in text
        assert 'source = "#' in text, f"{path.name}: the tree text starts with a comment"
        assert "\n\t" in text, f"{path.name}: one node a line, indented with tabs"


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "platformer-2d")
    before = (project / "project.godot").read_text(encoding="utf-8")
    blueprints.add_feature(project, FEATURE)
    assert (project / "features/behaviour/trees/guard.tres").is_file()
    blueprints.remove_feature(project, FEATURE)
    assert not (project / "features/behaviour").exists()
    assert (project / "project.godot").read_text(encoding="utf-8") == before


@needs_godot
@pytest.mark.parametrize("blueprint", ["top-down", "third-person-3d"])
def test_trees_run_enemies_in_the_game(tmp_path: Path, blueprint: str) -> None:
    features = sorted({*blueprints.describe(blueprint)["default_features"], FEATURE})
    project = make_project(tmp_path, blueprint, features)
    settings = f"--settings-path={tmp_path / 'settings.cfg'}"
    run_godot(project, TEST, ("--autostart", settings), timeout=240, marker=FEATURE)
