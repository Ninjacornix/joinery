"""`joinery access` without Godot: each check on what the probe would report.

The fixture (tests/fixtures/access_game) is a custom game with problems planted on
purpose: grey text on a grey panel at 9 px, a light switched between a red and a green
that look alike with deuteranopia, a button with no name, a voice line with no
subtitles, keys only (no gamepad, no remapping, no pause), a held sprint and a screen
that flashes 7.5 times a second. The probe's report is given here as Godot would write
it (the Godot tests in tests/blueprints/test_checks.py play the real thing).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from joinery import access, checks_core
from joinery.bible_rules import contrast_ratio

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "access_game"


@pytest.fixture
def game(tmp_path: Path) -> Path:
    project = tmp_path / "access"
    shutil.copytree(FIXTURE, project)
    return project


def _control(**over: Any) -> dict[str, Any]:
    control: dict[str, Any] = {
        "page": "hud",
        "scene": "res://main.tscn",
        "node": "Box/Row/Dim",
        "name": "Dim",
        "type": "Label",
        "text": "Score: 12",
        "focusable": False,
        "disabled": False,
        "accessible": "",
        "tooltip": "",
        "label": "",
        "size": 9,
        "colour": "#777777",
        "outline": 0,
        "background": "#888888",
        "colour_from": {
            "kind": "scene",
            "file": "res://main.tscn",
            "node": "Box/Row/Dim",
        },
        "size_from": {
            "kind": "scene",
            "file": "res://main.tscn",
            "node": "Box/Row/Dim",
        },
    }
    control.update(over)
    return control


def _probe(
    *controls: dict[str, Any], pages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"hud": list(controls), "pages": pages or [], "viewport": [1280, 720]}


def _found(state: access._State) -> dict[str, checks_core.Finding]:
    return {f"{f.area}:{f.check}": f for f in state.tally.findings}


def test_low_contrast_text_is_recoloured_in_its_scene(game: Path) -> None:
    state = access._State(game)
    access.contrast_and_size(state, _probe(_control()), seed=5)
    found = _found(state)
    contrast = found["contrast:contrast"]
    assert contrast.measured == "1.3:1" and contrast.target == "≥ 4.5:1"
    assert contrast.severity == "high"
    assert contrast.evidence == {
        "scene": "res://main.tscn",
        "node": "Box/Row/Dim",
        "page": "hud",
        "seed": 5,
    }
    fix = contrast.fix
    assert fix is not None and fix.kind == "typed" and fix.source == "main.tscn"
    (edit,) = fix.arguments["edits"]
    assert edit["node"] == ["Dim", "Box/Row"]
    assert edit["props"] == {"theme_override_colors/font_color": "Color(0, 0, 0, 1)"}
    size = found["text:text_size"]
    # 9 px in a 720 px viewport that stretches is 14 px at 1080p; 18 px wanted.
    assert size.measured == "14 px" and size.target == "≥ 18 px"
    assert size.fix is not None
    assert size.fix.arguments["edits"][0]["props"] == {
        "theme_override_font_sizes/font_size": 12
    }


def test_a_recolour_stays_in_the_palette_when_a_palette_colour_reads(
    game: Path,
) -> None:
    colour, why = access.recolour(
        "#777777", "#888888", 4.5, ["#101820", "#f0e0c0", "#7a7a7a"]
    )
    assert colour == "#101820" and "palette" in why
    assert contrast_ratio(colour, "#888888") >= 4.5
    plain, why = access.recolour("#777777", "#888888", 4.5, ["#808080"])
    assert plain == "#000000" and "black" in why


def test_text_on_the_world_with_no_outline_gets_one(game: Path) -> None:
    state = access._State(game)
    access.contrast_and_size(state, _probe(_control(background="", size=40)), seed=1)
    backdrop = _found(state)["contrast:backdrop"]
    assert backdrop.severity == "low" and backdrop.fix is not None
    props = backdrop.fix.arguments["edits"][0]["props"]
    assert props["theme_override_constants/outline_size"] == 5


def test_code_set_text_needs_an_agent_naming_the_file(game: Path) -> None:
    state = access._State(game)
    code = {"kind": "code", "file": "res://ui/hud.gd", "node": ""}
    access.contrast_and_size(
        state, _probe(_control(colour_from=code, size_from=code)), seed=1
    )
    fix = _found(state)["contrast:contrast"].fix
    assert fix is not None and fix.kind == "agent" and fix.targets == ["ui/hud.gd"]
    assert "#000000" in fix.request


def test_red_and_green_alike_with_deuteranopia_are_a_colour_only_signal(
    game: Path,
) -> None:
    state = access._State(game)
    access.colours(state)
    finding = _found(state)["colour:colour_only"]
    assert finding.path == "main.gd" and "deuteranopia" in finding.measured
    assert finding.fix is not None and finding.fix.kind == "agent"
    assert "#40a040" in finding.message and "#d04040" in finding.message


def test_voice_without_subtitles_keys_only_and_held_actions(game: Path) -> None:
    state = access._State(game)
    access.captions(state)
    access.controls(state, _probe())
    found = _found(state)
    assert found["captions:voice_subtitles"].path == "main.tscn"
    assert found["controls:remap"].severity == "high"
    assert found["controls:controller"].measured == "keyboard only"
    assert found["controls:hold_toggle"].measured == "hold only: sprint"
    # Not a blueprint game: no feature to add, so agents make the changes.
    assert all(f.fix is not None and f.fix.kind == "agent" for f in found.values())


def test_options_come_from_the_feature_in_a_blueprint_game(game: Path) -> None:
    (game / "joinery.toml").write_text(
        '[game]\nblueprint = "top-down"\nblueprint_version = 1\nfeatures = []\n'
        "feature_versions = {}\n"
    )
    state = access._State(game)
    access.options(state, _probe())
    missing = {f.subject: f for f in state.tally.findings}
    assert {
        "text_scale",
        "colour_filter",
        "subtitles",
        "high_contrast",
        "game_speed",
    } <= set(missing)
    fix = missing["colour_filter"].fix
    assert fix is not None and (fix.kind, fix.feature) == ("feature", "accessibility")
    tasks = checks_core.plan(
        game, state.tally.report("access", game, []), labels=access.AREAS
    )
    (offer,) = tasks
    assert offer.kind == "feature" and offer.request == "/feature add accessibility"


def test_options_on_a_settings_page_count(game: Path) -> None:
    state = access._State(game)
    page = {
        "id": "accessibility",
        "controls": [
            _control(
                page="accessibility",
                node=f"Rows/access_{key}",
                name=f"access_{key}",
                text="",
            )
            for key in access.OPTIONS
        ],
    }
    access.options(state, _probe(pages=[page]))
    assert state.tally.findings == []


def test_missing_options_in_a_custom_game_are_one_agent_change(game: Path) -> None:
    state = access._State(game)
    access.options(state, _probe())
    tasks = checks_core.plan(
        game, state.tally.report("access", game, []), labels=access.AREAS
    )
    (task,) = tasks
    assert task.kind == "agent" and task.title == "Add accessibility options"
    assert "colour-blind filter" in task.request and "text size option" in task.request


def test_unnamed_controls_are_named_from_their_scene(game: Path) -> None:
    state = access._State(game)
    gear = _control(
        node="Box/Row/Gear",
        name="Gear",
        type="Button",
        text="",
        focusable=True,
        label="Score: 12",
    )
    access.menus(state, _probe(gear))
    finding = _found(state)["menus:accessible_name"]
    assert finding.fix is not None
    edit = finding.fix.arguments["edits"][0]
    assert edit == {
        "node": ["Gear", "Box/Row"],
        "props": {"accessibility_name": '"Gear"'},
    }


def test_core_menu_controls_are_named_by_the_feature(game: Path) -> None:
    (game / "joinery.toml").write_text(
        '[game]\nblueprint = "top-down"\nblueprint_version = 1\nfeatures = []\n'
        "feature_versions = {}\n"
    )
    state = access._State(game)
    code = {"kind": "code", "file": "res://core/menus.gd", "node": ""}
    slider = _control(
        page="audio",
        scene="",
        node="/root/Menus/Root/x/audio_master",
        type="HSlider",
        text="",
        focusable=True,
        label="Master volume",
        colour_from=code,
    )
    access.menus(state, _probe(pages=[{"id": "audio", "controls": [slider]}]))
    finding = _found(state)["menus:accessible_name"]
    assert '"Master volume"' in finding.message
    assert finding.fix is not None and finding.fix.feature == "accessibility"


def test_pause_must_exist_and_work(game: Path) -> None:
    state = access._State(game)
    access.pause(state, {"checks": {}})
    assert _found(state)["pause:pause"].measured == "no pause action"
    (game / "project.godot").write_text(
        (game / "project.godot").read_text()
        + 'pause={\n"deadzone": 0.2,\n"events": []\n}\n'
    )
    state = access._State(game)
    access.pause(state, {"checks": {"pause": "failed: pause did nothing"}})
    assert "didn't work" in _found(state)["pause:pause"].message
    state = access._State(game)
    access.pause(state, {"checks": {"pause": "ok"}})
    assert state.tally.findings == []


def _trace(period: int, frames: int) -> list[list[float]]:
    rows = []
    for frame in range(frames):
        value = 1.0 if (frame // period) % 2 == 0 else 0.0
        rows.append([frame, value, value, value, value, value])
    return rows


def test_flashes_are_counted_per_second() -> None:
    rate, start = access.flashes(_trace(4, 180))
    assert rate == 7.5 and start >= 0
    assert access.flashes(_trace(30, 180))[0] == 1.0
    # A slow fade is no flash; small changes are none either.
    fade = [[f, f / 300, f / 300, 0, 0, 0] for f in range(300)]
    assert access.flashes(fade)[0] <= 0.5
    flicker = [
        [f, 0.5 + (0.04 if f % 2 else 0), 0.5, 0.5, 0.5, 0.5] for f in range(120)
    ]
    assert access.flashes(flicker)[0] == 0.0


def test_fast_flashing_is_a_photosensitivity_finding(game: Path) -> None:
    state = access._State(game)
    access.motion(state, _trace(4, 240), seed=9, measured=True)
    found = _found(state)
    flash = found["motion:photosensitivity"]
    assert flash.measured == "7.5 flashes/s" and flash.severity == "high"
    assert flash.evidence["seed"] == 9
    assert found["motion:flash_setting"].path == "main.gd"
