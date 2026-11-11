"""Real headless playtests in stock Godot: every blueprint, and a fixture with planted faults.

Each blueprint gets one short seeded run (20 s of game time, about a second of real
time) that must find nothing serious and must show the bot really played: it moved the
player around or pressed the game's buttons, paused and resumed, and saved and loaded
where the game can. The longer runs, two minutes with every feature, are marked
``playtest_slow`` (JOINERY_PLAYTEST_SLOW=1 or ``-m playtest_slow``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from joinery import blueprints, playtest
from tests.blueprints.godot_run import import_project, make_project, needs_godot

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "playtest_game"
#: blueprint -> how the bot plays it.
STYLES = {
    "blank": "generic",
    "first-person-3d": "3d",
    "third-person-3d": "3d",
    "platformer-2d": "platformer",
    "top-down": "topdown",
    "puzzle-cards": "generic",
}
SLOW = pytest.mark.skipif(
    os.environ.get("JOINERY_PLAYTEST_SLOW") != "1",
    reason="slow; JOINERY_PLAYTEST_SLOW=1",
)


def _play(project: Path, seconds: float, seed: int, **extra: Any) -> dict[str, Any]:
    import_project(project)
    return playtest.run(
        playtest.Options(project=project, seconds=seconds, seed=seed, **extra)
    )


def _serious(report: dict[str, Any]) -> list[str]:
    return [
        f"{i['severity']} {i['kind']}: {i['detail']} {i['where']}"
        for i in report["items"]
        if i["severity"] in {"critical", "high"}
    ]


@needs_godot
@pytest.mark.parametrize("blueprint", sorted(STYLES))
def test_a_short_playtest_of_each_blueprint_finds_nothing_serious(
    tmp_path: Path, blueprint: str
) -> None:
    features = blueprints.describe(blueprint)["default_features"]
    project = make_project(tmp_path, blueprint, features)
    report = _play(project, 20, seed=7)
    assert report["status"] == "played", report
    assert _serious(report) == []
    assert report["style"] == STYLES[blueprint]
    assert report["checks"]["pause"] == "ok"
    if "save-load" in features:
        assert report["checks"]["save_load"] == "ok"
    goals = report["goals"]
    if STYLES[blueprint] in {"3d", "topdown", "platformer"}:
        assert goals["coverage_cells"] >= 5, goals
    if blueprint == "puzzle-cards":
        assert goals["score_changes"] > 0, goals
    # The run wrote nothing in the game but its own folder.
    assert Path(report["out"]).is_relative_to(project / ".joinery" / "playtests")


@needs_godot
def test_a_seeded_run_finds_the_planted_faults_and_replays_exactly(
    tmp_path: Path,
) -> None:
    project = tmp_path / "fixture"
    shutil.copytree(FIXTURE, project)
    first = _play(project, 30, seed=11, softlock=8)
    kinds = {item["kind"]: item for item in first["items"]}
    error = kinds["script_error"]
    assert error["severity"] == "high"
    assert error["where"] == "res://main.gd:23"
    assert error["t"] == 2.0
    lock = kinds["softlock"]
    assert lock["severity"] == "high" and lock["t"] == 5.0
    assert "couldn't move" in lock["detail"]
    assert kinds["hitch"]["t"] == 4.0
    assert "--seed 11" in lock["repro"]
    # A custom game without the game core is still played, by its input map.
    assert first["style"] == "topdown" and first["core"] is False
    again = _play(project, 30, seed=11, softlock=8)
    assert [(i["kind"], i["t"]) for i in again["items"]] == [
        (i["kind"], i["t"]) for i in first["items"]
    ]


@needs_godot
def test_a_game_that_does_not_start_is_critical(tmp_path: Path) -> None:
    project = tmp_path / "broken"
    shutil.copytree(FIXTURE, project)
    (project / "main.tscn").unlink()
    report = playtest.run(playtest.Options(project=project, seconds=5, seed=1))
    assert report["status"] == "played"
    assert report["items"][0]["kind"] == "boot"
    assert report["items"][0]["severity"] == "critical"


@SLOW
@needs_godot
@pytest.mark.playtest_slow
@pytest.mark.parametrize("blueprint", sorted(STYLES))
def test_two_minutes_with_every_feature(tmp_path: Path, blueprint: str) -> None:
    features = sorted(f.id for f in blueprints.list_features() if f.supports(blueprint))
    resolution = blueprints.resolve(blueprint, features)
    if resolution.problems:
        features = list(blueprints.describe(blueprint)["default_features"])
    project = make_project(tmp_path, blueprint, features)
    report = _play(project, 120, seed=3)
    assert report["status"] == "played", report
    assert _serious(report) == []
    if "translations" in features:
        assert report["checks"]["text"] in {"ok", "failed"}
