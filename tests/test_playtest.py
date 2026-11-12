"""Playtest reports: console parsing, severity, balance notes, settings and the CLI.

Real headless runs in Godot are in tests/blueprints/test_playtest.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import cli, playtest, playtest_report

OPTIONS = {"project": "/games/arena", "seed": 42, "seconds": 120.0, "scene": ""}

CONSOLE = """Godot Engine v4.7.2.stable.official - https://godotengine.org
PLAYTEST {"t": 1, "frame": 60}
SCRIPT ERROR: Invalid call. Nonexistent function 'jump_to' in base 'Nil'.
          at: _on_hit (res://game/player/player.gd:88)
          GDScript backtrace (most recent call first):
              [0] _on_hit (res://game/player/player.gd:88)
ERROR: Condition "!is_inside_tree()" is true. Returning: Transform3D()
   at: get_global_transform (scene/3d/node_3d.cpp:412)
WARNING: ObjectDB instances leaked at exit (run with --verbose for details).
     at: cleanup (core/object/object.cpp:2378)
USER WARNING: The coin has no save_id.
   at: push_warning (core/variant/variant_utility.cpp:1034)
   GDScript backtrace (most recent call first):
       [0] _ready (res://game/props/coin.gd:12)
"""


def _bot(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "finished": True,
        "seed": 42,
        "fps": 60,
        "seconds_played": 120.0,
        "core": True,
        "interactive": True,
        "player": "3d",
        "style": "3d",
        "goals": {
            "levels_started": 1,
            "completed": [],
            "failed": [],
            "checkpoints": 1,
            "score_changes": 2,
            "world_changes": 0,
        },
        "findings": [],
        "checks": {"pause": "ok"},
        "moments": [],
        "perf": {"frame_ms_p50": 2.0, "spikes": [], "samples": [], "respawns": 0},
        "log": [],
    }
    data.update(extra)
    return data


def test_console_errors_name_the_script_line_and_skip_exit_noise() -> None:
    entries = playtest_report.console_entries(CONSOLE)
    assert [e["kind"] for e in entries] == ["script_error", "error", "warning"]
    assert entries[0]["where"] == "res://game/player/player.gd:88"
    assert entries[0]["function"] == "_on_hit"
    assert entries[1]["where"] == "scene/3d/node_3d.cpp:412"
    assert entries[2]["where"] == "res://game/props/coin.gd:12"


def test_severities_follow_the_kind_of_trouble() -> None:
    bot = _bot(
        findings=[
            {"kind": "softlock", "detail": "stuck", "frame": 600, "t": 10.0},
            {"kind": "text", "detail": "pseudo: cut off", "frame": 60, "t": 1.0},
            {"kind": "fell", "detail": "fell", "frame": 1200, "t": 20.0},
        ],
        log=[
            {"kind": "warning", "message": "slow", "where": "res://a.gd:3", "frame": 5}
        ],
    )
    report = playtest_report.build(bot, CONSOLE, 0, options=OPTIONS)
    severity = {item["kind"]: item["severity"] for item in report["items"]}
    assert severity["softlock"] == "high"
    assert severity["fell"] == "high"
    assert severity["script_error"] == "high"
    assert severity["error"] == "medium"
    assert severity["text"] == "low"
    assert severity["warning"] == "low"
    # Most serious first; the counts add up.
    order = [playtest_report.SEVERITIES.index(i["severity"]) for i in report["items"]]
    assert order == sorted(order)
    assert sum(report["counts"].values()) == len(report["items"])
    # An engine C++ file is no place for the user to look.
    engine = next(i for i in report["items"] if i["kind"] == "error")
    assert engine["where"] == ""


def test_items_carry_repro_steps_with_the_seed() -> None:
    bot = _bot(
        findings=[{"kind": "softlock", "detail": "stuck", "frame": 2520, "t": 42.0}]
    )
    item = playtest_report.build(bot, "", 0, options=OPTIONS)["items"][0]
    assert item["seed"] == 42
    assert "--seed 42" in item["repro"] and "--seconds 120" in item["repro"]
    assert "0:42" in item["repro"]


def test_the_same_error_is_one_item_counted() -> None:
    log = [
        {"kind": "script_error", "message": "boom", "where": "res://a.gd:1", "frame": f}
        for f in (60, 120, 180)
    ]
    report = playtest_report.build(
        _bot(log=log),
        "SCRIPT ERROR: boom\n   at: f (res://a.gd:1)\n",
        0,
        options=OPTIONS,
    )
    errors = [i for i in report["items"] if i["kind"] == "script_error"]
    assert len(errors) == 1
    assert errors[0]["count"] == 3 and "(×3)" in errors[0]["detail"]
    assert errors[0]["t"] == 1.0


def test_how_the_process_ended_decides_the_status() -> None:
    crashed = playtest_report.build(
        None,
        "handle_crash: Program crashed with signal 11",
        139,
        options=OPTIONS,
        last_t=33,
    )
    assert crashed["status"] == "crashed"
    assert crashed["items"][0]["severity"] == "critical"
    assert playtest_report.build(None, "", -11, options=OPTIONS)["status"] == "crashed"
    froze = playtest_report.build(
        _bot(finished=False), "", -9, options=OPTIONS, froze_at=12.0
    )
    assert froze["status"] == "froze" and froze["items"][0]["kind"] == "freeze"
    quit_early = playtest_report.build(_bot(finished=False), "", 0, options=OPTIONS)
    assert quit_early["status"] == "quit"
    assert (
        playtest_report.build(None, "", 1, options=OPTIONS)["status"]
        == "failed_to_start"
    )


def test_hitches_and_growing_memory_are_medium() -> None:
    samples = [
        {"frame": 300 * i, "nodes": 400 + 400 * i, "static_mb": 40.0}
        for i in range(1, 8)
    ]
    perf = {
        "frame_ms_p50": 1.5,
        "spikes": [{"frame": 240, "ms": 310.0}],
        "samples": samples,
        "respawns": 0,
    }
    report = playtest_report.build(_bot(perf=perf), "", 0, options=OPTIONS)
    kinds = {i["kind"]: i for i in report["items"]}
    assert (
        kinds["hitch"]["severity"] == "medium" and "310 ms" in kinds["hitch"]["detail"]
    )
    assert kinds["memory"]["severity"] == "medium"
    assert "Node count grew" in kinds["memory"]["detail"]
    # A few small spikes and flat memory are fine.
    calm = {**perf, "spikes": [{"frame": 9, "ms": 60.0}], "samples": samples[:2]}
    quiet = playtest_report.build(_bot(perf=calm), "", 0, options=OPTIONS)
    assert not [i for i in quiet["items"] if i["kind"] in {"hitch", "memory"}]


def test_balance_notes_from_timings_and_deaths() -> None:
    quick = _bot(
        goals={**_bot()["goals"], "completed": [{"t": 9.0, "level_time": 9.0}]}
    )
    notes = playtest_report.balance_notes(quick)
    assert len(notes) == 1 and "too easy" in notes[0]["detail"]
    deaths = _bot(
        goals={
            **_bot()["goals"],
            "failed": [{"t": t, "reason": "hit"} for t in range(8)],
        }
    )
    assert "too hard" in playtest_report.balance_notes(deaths)[0]["detail"]
    lost = _bot(
        goals={
            "levels_started": 1,
            "completed": [],
            "failed": [],
            "checkpoints": 0,
            "score_changes": 0,
            "world_changes": 0,
        }
    )
    assert "hard to find" in playtest_report.balance_notes(lost)[0]["detail"]
    # A game with nothing to play yet gets no lecture about its goals.
    assert playtest_report.balance_notes({**lost, "interactive": False}) == []
    report = playtest_report.build(quick, "", 0, options=OPTIONS)
    assert report["items"][-1]["severity"] == "note"


def test_summary_line_counts_by_severity() -> None:
    report = playtest_report.build(
        _bot(findings=[{"kind": "softlock", "detail": "x", "frame": 1, "t": 0.0}]),
        "",
        0,
        options=OPTIONS,
    )
    assert playtest_report.summary_line(report) == "Played 2:00 (seed 42): 1 high."
    assert playtest_report.stamp(725) == "12:05"


def test_config_reads_the_agents_table(tmp_path: Path) -> None:
    assert playtest.config(tmp_path) == {
        "playtest_after_change": False,
        "playtest_minutes": 2.0,
    }
    (tmp_path / "joinery.toml").write_text(
        "[agents]\nparallel = 2\nplaytest_after_change = true\nplaytest_minutes = 5\n"
    )
    assert playtest.config(tmp_path) == {
        "playtest_after_change": True,
        "playtest_minutes": 5.0,
    }
    (tmp_path / "joinery.toml").write_text('[agents]\nplaytest_after_change = "yes"\n')
    assert playtest.config(tmp_path)["playtest_after_change"] is False


def test_bot_arguments_keep_the_run_outside_the_project(tmp_path: Path) -> None:
    options = playtest.Options(
        project=tmp_path,
        seconds=30,
        seed=9,
        focus="the lever, and coins!",
        scene="res://a.tscn",
    )
    args = playtest._bot_args(options, tmp_path / "out", shots=[60, 120])
    assert f"--playtest-out={tmp_path / 'out'}" in args
    assert "--seed=9" in args and "--seconds=30" in args
    assert "--focus=the,lever,and,coins" in args
    assert "--scene=res://a.tscn" in args and "--shots=60,120" in args
    assert all(
        str(tmp_path / "out") in a for a in args if "settings" in a or "saves" in a
    )


def test_for_agent_trims_the_report() -> None:
    report = playtest_report.build(
        _bot(findings=[{"kind": "softlock", "detail": "x", "frame": 1, "t": 0.0}]),
        "",
        0,
        options=OPTIONS,
    )
    data = playtest.for_agent(report)
    assert data["summary"].startswith("Played 2:00")
    assert set(data["items"][0]) <= {
        "id",
        "severity",
        "title",
        "detail",
        "where",
        "t",
        "count",
        "repro",
        "shot",
    }
    assert "bot" in data["note"]
    assert "states" not in data


def test_nodes_that_report_their_state_reach_the_agent() -> None:
    tree = {"kind": "behaviour", "running": "selector > sequence > action chase"}
    report = playtest_report.build(
        _bot(states={"/root/Level/Enemy/Behaviour": tree, "/root/Bad": "not a dict"}),
        "",
        0,
        options=OPTIONS,
    )
    assert report["states"] == {"/root/Level/Enemy/Behaviour": tree}
    assert playtest.for_agent(report)["states"] == report["states"]


def test_cli_prints_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(
        options: playtest.Options, progress: Any = None, stop: Any = None
    ) -> Any:
        seen["options"] = options
        report = playtest_report.build(
            _bot(
                findings=[
                    {"kind": "softlock", "detail": "stuck", "frame": 60, "t": 1.0}
                ]
            ),
            "",
            0,
            options={**options.public(), "seed": options.seed or 5},
        )
        report["out"] = str(tmp_path / "run")
        return report

    monkeypatch.setattr(playtest, "run", fake_run)
    assert (
        cli.main(
            ["playtest", "--project", str(tmp_path), "--minutes", "0.5", "--seed", "5"]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "Played 2:00 (seed 5): 1 high." in printed
    assert "- [high] Softlock at 0:01: stuck" in printed
    assert seen["options"].seconds == 30 and seen["options"].seed == 5
    assert cli.main(["playtest", "--project", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["high"] == 1
    assert seen["options"].seconds == 120


def test_cli_refuses_a_scene_outside_the_project(tmp_path: Path) -> None:
    args = argparse.Namespace(
        project=tmp_path,
        seconds=None,
        minutes=None,
        seed=0,
        scene="/etc/x.tscn",
        focus="",
        softlock=15.0,
        screenshots=False,
        json=True,
    )
    assert playtest.main(args) == 2


def test_run_refuses_a_folder_that_is_not_a_game(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a Godot project"):
        playtest.run(playtest.Options(project=tmp_path))


#: A coverage grid as the bot writes it: "#" visited, "." never reached, " " not floor.
GRID = [
    "##..  ",
    "##..  ",
    "      ",
    ".   ..",
]


def _coverage(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "3d",
        "cell": 2.0,
        "origin": [-4.0, -10.0],
        "cols": 6,
        "rows": 4,
        "start": [0, 0],
        "navigable": 11,
        "visited": 4,
        "percent": 36.4,
        "grid": GRID,
    }
    data.update(extra)
    return data


def test_coverage_names_the_biggest_unreached_areas_and_where_they_are() -> None:
    report = playtest_report.build(_bot(coverage=_coverage()), "", 0, options=OPTIONS)
    coverage = report["coverage"]
    assert coverage["percent"] == 36.4
    assert coverage["visited"] == 4 and coverage["navigable"] == 11
    # Biggest first; centres in world units on the ground plane (cells are 2 units).
    assert [area["cells"] for area in coverage["unreached"]] == [4, 2, 1]
    assert coverage["unreached"][0]["at"] == [2.0, -8.0]
    assert coverage["unreached"][2]["at"] == [-3.0, -3.0]


def test_a_long_run_that_saw_little_of_the_level_gets_a_note() -> None:
    report = playtest_report.build(_bot(coverage=_coverage()), "", 0, options=OPTIONS)
    notes = [item for item in report["items"] if item["kind"] == "coverage"]
    assert len(notes) == 1 and notes[0]["severity"] == "note"
    assert "36.4% of the level" in notes[0]["detail"]
    assert "around (2, -8)" in notes[0]["detail"]
    # A short run, or a well explored level, says nothing.
    short = playtest_report.build(
        _bot(coverage=_coverage(), seconds_played=20.0), "", 0, options=OPTIONS
    )
    assert not [item for item in short["items"] if item["kind"] == "coverage"]
    seen = playtest_report.build(
        _bot(coverage=_coverage(percent=80.0)), "", 0, options=OPTIONS
    )
    assert not [item for item in seen["items"] if item["kind"] == "coverage"]


def test_no_grid_means_no_coverage() -> None:
    report = playtest_report.build(_bot(), "", 0, options=OPTIONS)
    assert report["coverage"] == {}
    assert "coverage" not in playtest.for_agent(report)


def test_reach_places_reach_the_bot_as_plain_words(tmp_path: Path) -> None:
    assert playtest.reach_words(["Key room", "key_room", "the LEVER!", ""]) == [
        "keyroom",
        "lever",
    ]
    options = playtest.Options(project=tmp_path, seed=3, reach=["Key room"])
    assert "--reach=keyroom" in playtest._bot_args(options, tmp_path / "out")
    assert not any(
        a.startswith("--reach")
        for a in playtest._bot_args(playtest.Options(project=tmp_path), tmp_path)
    )


def test_the_agent_gets_coverage_and_reach() -> None:
    reach = [
        {
            "target": "keyroom",
            "found": True,
            "reached": True,
            "t": 42.5,
            "closest": 0.0,
        },
        {
            "target": "vault",
            "found": False,
            "reached": False,
            "t": None,
            "closest": None,
        },
    ]
    report = playtest_report.build(
        _bot(coverage=_coverage(), reach=reach), "", 0, options=OPTIONS
    )
    data = playtest.for_agent(report)
    assert data["coverage"]["percent"] == 36.4
    assert data["coverage"]["unreached"][0]["cells"] == 4
    assert data["reach"] == reach
    assert playtest.reach_line(reach[0]) == "reached at 0:42"
    assert playtest.reach_line(reach[1]) == "nothing in the game is called that"
    assert (
        playtest.reach_line({"found": True, "reached": False, "closest": 3.25})
        == "not reached (closest 3.25)"
    )
