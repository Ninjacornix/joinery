"""Playtest heatmaps: deaths, stuck points and time spent, over the last few runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from joinery import playtest, playtest_heat

GRID = ["####", "#..#", "####"]


def _bot(**extra: Any) -> dict[str, Any]:
    bot: dict[str, Any] = {
        "fps": 60,
        "coverage": {
            "mode": "3d",
            "cell": 2.0,
            "origin": [0.0, 0.0],
            "cols": 4,
            "rows": 3,
            "start": [0, 0],
            "grid": GRID,
            # 120 frames in the first cell, 30 in the last of the top row.
            "dwell": [[0, 0, 120], [3, 0, 30]],
        },
        # Lost a level at (1, _, 1): cell (0, 0) in x, z.
        "deaths": [{"t": 10.0, "frame": 600, "at": [1.0, 5.0, 1.0], "reason": "hit"}],
        "perf": {
            "respawn_at": [
                # The respawn right after that loss is the same death.
                {"frame": 660, "at": [1.0, 5.0, 1.0]},
                # One the game handled by itself, later and elsewhere: cell (3, 2).
                {"frame": 3000, "at": [7.0, 0.0, 5.0]},
            ]
        },
        "findings": [
            {"kind": "softlock", "t": 20.0, "at": [5.0, 0.0, 3.0]},
            {"kind": "script_error", "t": 21.0, "at": [5.0, 0.0, 3.0]},
        ],
    }
    bot.update(extra)
    return bot


def test_one_run_heat_is_on_the_ground_plane() -> None:
    heat = playtest_heat.run_heat(_bot())
    assert heat["mode"] == "3d"
    # x and z, not y: the map is top-down.
    assert [d["at"] for d in heat["deaths"]] == [[1.0, 1.0], [7.0, 5.0]]
    assert heat["deaths"][1]["reason"] == "respawned"
    assert [s["at"] for s in heat["stuck"]] == [[5.0, 3.0]]
    assert heat["time"] == [
        {"at": [1.0, 1.0], "seconds": 2.0},
        {"at": [7.0, 1.0], "seconds": 0.5},
    ]


def test_a_2d_run_keeps_x_and_y_and_nothing_mapped_means_no_heat() -> None:
    bot = _bot()
    bot["coverage"]["mode"] = "topdown"
    assert playtest_heat.run_heat(bot)["deaths"][0]["at"] == [1.0, 5.0]
    assert playtest_heat.run_heat({"coverage": {}}) == {}
    assert playtest_heat.run_heat(None) == {}


def _keep(project: Path, name: str, report: dict[str, Any]) -> None:
    folder = project / playtest.FOLDER / name
    folder.mkdir(parents=True)
    report = {**report, "out": str(folder)}
    (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")


def _report(bot: dict[str, Any], scene: str = "") -> dict[str, Any]:
    return {
        "scene": scene,
        "coverage": bot["coverage"],
        "heat": playtest_heat.run_heat(bot),
    }


def test_recent_runs_add_up_on_the_newest_grid(tmp_path: Path) -> None:
    # Another scene's run is another map: left out.
    _keep(tmp_path, "20261101-000000-cccccc", _report(_bot(), "res://other.tscn"))
    _keep(tmp_path, "20261102-000000-aaaaaa", _report(_bot()))
    _keep(tmp_path, "20261103-000000-bbbbbb", _report(_bot()))
    heat = playtest_heat.aggregate(tmp_path, last=5)
    assert heat["runs"] == 2
    assert heat["grid"] == GRID and heat["cols"] == 4 and heat["rows"] == 3
    assert heat["deaths"] == [[0, 0, 2], [3, 2, 2]]
    assert heat["stuck"] == [[2, 1, 2]]
    assert heat["time"] == [[0, 0, 4.0], [3, 0, 1.0]]
    assert heat["totals"] == {"deaths": 4.0, "stuck": 2.0, "time": 5.0}
    assert heat["hotspots"]["stuck"] == [{"at": [5.0, 3.0], "value": 2.0}]
    # Only the newest when asked for one.
    assert playtest_heat.aggregate(tmp_path, last=1)["runs"] == 1


def test_a_run_on_a_bigger_grid_lands_in_the_same_places(tmp_path: Path) -> None:
    # An older run mapped from another corner with the same cells: its points still land.
    older = _bot()
    older["coverage"] = {
        **older["coverage"],
        "origin": [-2.0, -2.0],
        "cols": 5,
        "rows": 4,
    }
    _keep(tmp_path, "20261101-000000-aaaaaa", _report(older))
    _keep(tmp_path, "20261102-000000-bbbbbb", _report(_bot()))
    heat = playtest_heat.aggregate(tmp_path, last=5)
    assert heat["runs"] == 2
    assert [0, 0, 2] in heat["deaths"]
    # Points past the newest grid's edge are dropped, not clamped.
    off = _bot(deaths=[{"t": 1.0, "frame": 60, "at": [99.0, 0.0, 99.0], "reason": "x"}])
    off["perf"] = {}
    _keep(tmp_path, "20261103-000000-cccccc", _report(off))
    assert playtest_heat.aggregate(tmp_path, last=1)["deaths"] == []


def test_the_just_played_run_is_the_base(tmp_path: Path) -> None:
    _keep(tmp_path, "20261101-000000-aaaaaa", _report(_bot()))
    current = {**_report(_bot()), "out": str(tmp_path / "elsewhere")}
    heat = playtest_heat.aggregate(tmp_path, last=5, base=current)
    assert heat["runs"] == 2
    assert playtest_heat.aggregate(tmp_path / "none", last=5) == {}


def test_how_many_runs_comes_from_the_project(tmp_path: Path) -> None:
    assert playtest_heat.runs_wanted(tmp_path) == playtest_heat.DEFAULT_RUNS
    (tmp_path / "joinery.toml").write_text("[agents]\nheatmap_runs = 40\n")
    assert playtest_heat.runs_wanted(tmp_path) == playtest.KEEP
    (tmp_path / "joinery.toml").write_text("[agents]\nheatmap_runs = 2\n")
    assert playtest_heat.runs_wanted(tmp_path) == 2


def test_the_agent_gets_hotspots_without_grids(tmp_path: Path) -> None:
    _keep(tmp_path, "20261101-000000-aaaaaa", _report(_bot()))
    heat = playtest_heat.for_agent(playtest_heat.aggregate(tmp_path))
    assert "grid" not in heat and heat["runs"] == 1
    assert heat["hotspots"]["deaths"][0]["at"] == [1.0, 1.0]
    lines = playtest_heat.summary_lines(playtest_heat.aggregate(tmp_path))
    assert lines[0] == "Over the last 1 playtests:"
    assert lines[1].startswith("- 2 deaths; most at (1, 1) 1")


def test_cli_heatmap_reads_without_playing(tmp_path: Path, capsys: Any) -> None:
    _keep(tmp_path, "20261101-000000-aaaaaa", _report(_bot()))
    parser = argparse.ArgumentParser()
    playtest.add_arguments(parser)
    args = parser.parse_args(
        ["--project", str(tmp_path), "--heatmap", "--last", "3", "--json"]
    )
    assert playtest.main(args) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["deaths"] == 2.0
