"""The accessibility and performance checks on real games in stock Godot.

Headless runs measure the planted problems of the fixtures (a script that loops 300 000
times a frame, shadowed lights, repeated props; grey text on grey, 9 px text, an unnamed
button, no pause) and real numbers from blueprints. With ``JOINERY_GODOT_BIN`` set to an
editor binary, the windowed runs measure drawing too (draw calls, primitives, video
memory) and the fixture's flashing screen. After a fix, measuring again shows it: the
Accessibility feature added to a blueprint, or the planned scene edits applied.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from joinery import access, blueprints, checks_core, perf
from tests.blueprints.godot_run import import_project, make_project, needs_godot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
# Drawing needs the editor binary and a display: a Linux runner without one only plays
# headless, so its windowed measurements are not asked for.
WINDOW = (
    bool(os.environ.get("JOINERY_GODOT_BIN"))
    and Path(os.environ.get("JOINERY_GODOT_BIN", "")).is_file()
    and not (sys.platform.startswith("linux") and not os.environ.get("DISPLAY"))
)
needs_window = pytest.mark.skipif(
    not WINDOW, reason="needs JOINERY_GODOT_BIN and a display to draw"
)


def _copy(tmp_path: Path, name: str) -> Path:
    project = tmp_path / name
    shutil.copytree(FIXTURES / name, project)
    import_project(project)
    return project


def _by_check(report: checks_core.Report) -> dict[str, list[checks_core.Finding]]:
    found: dict[str, list[checks_core.Finding]] = {}
    for finding in report.findings:
        found.setdefault(finding.check, []).append(finding)
    return found


@needs_godot
def test_perf_measures_the_planted_hot_script_and_heavy_scene(tmp_path: Path) -> None:
    project = _copy(tmp_path, "perf_game")
    report = perf.audit(project, "mid", window=False)
    assert report.errors == []
    found = _by_check(report)
    (hot,) = found["hot_script"]
    assert hot.path == "hot.gd" and hot.severity == "high"
    cost = float(hot.measured.split()[0])
    assert cost >= 2.0, hot.measured  # 300 000 square roots a frame, doubled for "mid"
    assert found["shadow_lights"][0].measured == "6 shadowed lights"
    assert found["repeats"][0].measured == "24 copies"
    measured = report.facts["measured"]
    assert measured["cpu_frame_ms"]["p50"] > 1.0
    assert measured["load_ms"] >= 0 and measured["nodes"] >= 30
    # Apply the planned scene edits as an accepted change would, and measure again.
    tasks = checks_core.plan(
        project, report, areas=["lighting", "rendering"], labels=perf.AREAS
    )
    for task in tasks:
        for step in task.steps:
            if step.tool == "write_file":
                (project / step.arguments["filePath"]).write_text(
                    step.arguments["content"]
                )
    again = perf.audit(project, "mid", window=False)
    scores = {a.id: a.score for a in again.areas}
    before = {a.id: a.score for a in report.areas}
    assert scores["lighting"] == 100 > before["lighting"]
    assert "shadow_lights" not in _by_check(again)
    assert "visibility_range" not in _by_check(again)


@needs_godot
@pytest.mark.parametrize("blueprint", ["third-person-3d", "top-down"])
def test_perf_measures_real_numbers_on_blueprints(
    tmp_path: Path, blueprint: str
) -> None:
    features = blueprints.describe(blueprint)["default_features"]
    project = make_project(tmp_path, blueprint, features)
    import_project(project)
    report = perf.audit(project, "steam-deck", window=WINDOW)
    assert report.errors == [], report.errors
    measured = report.facts["measured"]
    print(
        blueprint,
        report.summary,
        measured["frame_ms"],
        measured["cpu_frame_ms"],
        measured["draw_calls"],
        measured["primitives"],
        measured["nodes"],
        measured["load_ms"],
    )
    assert measured["nodes"] > 20 and measured["load_ms"] >= 0
    assert measured["cpu_frame_ms"]["p50"] > 0
    if WINDOW:
        assert measured["windowed"] and measured["draw_calls"] > 0
        assert measured["frame_ms"]["p50"] > 0 and measured["primitives"] > 0
    # A blueprint's own work fits a Steam Deck frame with room to spare. Held on the
    # CPU frame time: a windowed frame on a CI machine times its virtual GPU (13 ms
    # p95 and 42 ms p99 on a macOS runner, beside 0.24 ms of the game's own CPU work).
    cpu = measured["cpu_frame_ms"]
    assert cpu["p95"] < 8.0, cpu
    if not WINDOW:
        assert not [f for f in report.findings if f.area == "frames"], report.findings


@needs_godot
def test_access_finds_the_planted_problems(tmp_path: Path) -> None:
    project = _copy(tmp_path, "access_game")
    report = access.audit(project, window=WINDOW)
    found = _by_check(report)
    (contrast,) = found["contrast"]
    assert contrast.measured == "1.3:1" and contrast.fix is not None
    assert contrast.fix.kind == "typed" and contrast.fix.source == "main.tscn"
    assert found["text_size"][0].measured == "14 px"
    assert found["accessible_name"][0].evidence["node"] == "Box/Row/Gear"
    assert found["pause"][0].measured == "no pause action"
    assert found["remap"] and found["controller"] and found["voice_subtitles"]
    assert found["colour_only"][0].path == "main.gd"
    if WINDOW:
        flash = found["photosensitivity"][0]
        assert flash.measured.endswith("flashes/s")
        assert float(flash.measured.split()[0]) >= 7.0
    # The typed fixes, applied as accepted changes would be, pass when measured again.
    tasks = checks_core.plan(
        project, report, areas=["contrast", "text", "menus"], labels=access.AREAS
    )
    for task in [t for t in tasks if t.kind == "scripted"]:
        for step in task.steps:
            (project / step.arguments["filePath"]).write_text(step.arguments["content"])
    again = _by_check(access.audit(project, window=False))
    assert "contrast" not in again and "text_size" not in again
    assert "accessible_name" not in again


@needs_godot
def test_adding_the_feature_raises_a_blueprints_scores(tmp_path: Path) -> None:
    project = make_project(tmp_path, "third-person-3d", ["controller"])
    import_project(project)
    before = access.audit(project, window=False)
    assert before.errors == []
    pages = before.facts["pages"]
    assert {"pause", "settings", "accessibility", "controls", "main"} <= set(pages)
    offers = {
        f.fix.feature for f in before.findings if f.fix and f.fix.kind == "feature"
    }
    assert offers == {"accessibility"}
    blueprints.add_feature(project, "accessibility")
    import_project(project)
    after = access.audit(project, window=False)
    gone = {f.subject for f in before.findings} - {f.subject for f in after.findings}
    assert {
        "text_scale",
        "colour_filter",
        "subtitles",
        "high_contrast",
        "game_speed",
    } <= gone
    scores = {a.id: a.score for a in after.areas}
    assert scores["menus"] == 100 and scores["colour"] == 100
    assert sum(scores.values()) > sum(a.score for a in before.areas)
