"""/tidy on the shipped starting points: a fresh game has nothing unused, with every
feature it supports; and what a user's edit leaves unused is found and removed whole."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import blueprints, tidy, tidy_cli, tidy_nodes, tidy_remove, tidy_report
from tests.blueprints.godot_run import make_project
from tests.blueprints.matrix import cells

BLUEPRINTS = sorted(b.id for b in blueprints.list_blueprints())


@pytest.mark.parametrize("blueprint", BLUEPRINTS)
def test_a_fresh_game_has_nothing_unused_with_any_features(
    tmp_path: Path, blueprint: str
) -> None:
    ran = 0
    for index, cell in enumerate(c for c in cells() if c.blueprint == blueprint):
        project = make_project(tmp_path / str(index), cell.blueprint, cell.features)
        report = tidy_report.check(project)
        assert not report.findings, (
            cell.id,
            [(f.confidence, f.key, f.why) for f in report.findings],
        )
        assert report.summary == "Nothing unused"
        ran += 1
    assert ran >= 3


def test_a_prop_the_level_no_longer_places_is_found_and_removed_whole(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, "third-person-3d", ("translations",))
    level = project / "game/level.tscn"
    level.write_text(tidy_nodes.remove_nodes(level.read_text(), ["Props/Crate"]))
    assert "crate.tscn" not in level.read_text()
    graph = tidy.build(project)
    report = tidy_report.check(project, graph)
    found = {f.key: f for f in report.findings}
    crate = found["game/props/crate.tscn"]
    assert crate.confidence == "unused"
    assert crate.why.startswith("Nothing in the game loads it")
    # The blueprint's play test still names the Crate class: its script stays.
    assert "game/props/crate.gd" in report.used_by_tests
    assert set(found) == {"game/props/crate.tscn"}
    tx = tidy_cli.stage(project, tidy_remove.plan(graph, report, [crate.id]))
    tx.accept()
    assert not (project / "game/props/crate.tscn").exists()
    assert (project / "game/props/crate.gd").is_file()
    assert not tidy_report.check(project).findings
