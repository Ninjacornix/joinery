"""The game brief is drafted from the pitch and the setup, and never invents taste."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from joinery import brief
from joinery.brief import (
    ANSWERS_FILE,
    BRIEF_PATH,
    GROUPS,
    QUESTIONS,
    TEMPLATE,
    TOKENS,
    UNSET,
    BriefFacts,
    consistency,
    controls_table,
    draft,
    draft_facts,
    grouped,
    load_brief,
    parse,
    parse_facts,
    parse_input_map,
    summary,
    write_brief,
    write_from_answers_file,
)

STOCK_GODOT = (
    Path(__file__).resolve().parents[1] / ".tools/Godot.app/Contents/MacOS/Godot"
)


def godot_binary() -> Path | None:
    configured = os.environ.get("JOINERY_TEST_GODOT")
    for candidate in (configured, str(STOCK_GODOT)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


needs_godot = pytest.mark.skipif(
    godot_binary() is None, reason="no Godot binary (set JOINERY_TEST_GODOT)"
)


def _blueprint(
    id_: str,
    name: str,
    dimension: str,
    genre: list[str],
    inputs: dict[str, list[str]],
    verbs: list[list[str]] | None = None,
) -> dict[str, Any]:
    """The shape `blueprints.describe(id)` returns (contract C2/E2)."""
    pads = {
        "move_left": ["0-", "13"],
        "move_right": ["0+", "14"],
        "move_up": ["1-", "11"],
        "move_down": ["1+", "12"],
        "move_forward": ["1-"],
        "move_back": ["1+"],
        "look_left": ["2-"],
        "look_right": ["2+"],
        "look_up": ["3-"],
        "look_down": ["3+"],
        "jump": ["0"],
        "interact": ["2"],
        "attack": ["5+", "10"],
        "dash": ["1"],
        "sprint": ["7"],
    }
    brief_defaults: dict[str, Any] = {
        "genre": genre,
        "camera": f"{name} camera.",
        "core_loop": "30 s: do the thing. 5 min: finish a level. Session: beat it faster.",
        "progression": "Win: reach the end. Lose: none.",
    }
    if verbs is not None:
        brief_defaults["verbs"] = verbs
    return {
        "id": id_,
        "name": name,
        "pitch": f"The {name} starting point.",
        "version": 1,
        "dimension": dimension,
        "default_features": ["controller", "save-load"],
        "brief": brief_defaults,
        "input": {
            action: {"keys": keys, "mouse": [], "pad": pads.get(action, [])}
            for action, keys in inputs.items()
        },
        "features_supported": ["*"],
    }


BLUEPRINTS: list[dict[str, Any]] = [
    _blueprint("blank", "Blank", "2d", [], {}),
    _blueprint(
        "third-person-3d",
        "Third-person 3D",
        "3d",
        ["action", "collectathon"],
        {
            "move_forward": ["W", "Up"],
            "move_back": ["S", "Down"],
            "move_left": ["A", "Left"],
            "move_right": ["D", "Right"],
            "jump": ["Space"],
            "sprint": ["Shift"],
        },
    ),
    _blueprint(
        "first-person-3d",
        "First-person 3D",
        "3d",
        ["exploration"],
        {"move_forward": ["W"], "interact": ["E"], "jump": ["Space"]},
    ),
    _blueprint(
        "platformer-2d",
        "2D platformer",
        "2d",
        ["platformer"],
        {
            "move_left": ["A", "Left"],
            "move_right": ["D", "Right"],
            "jump": ["Space", "W", "Up"],
        },
        verbs=[
            ["Run", "move_left / move_right", "Dust and lean"],
            ["Jump", "jump", "Squash and stretch"],
        ],
    ),
    _blueprint(
        "top-down",
        "Top-down action",
        "2d",
        ["action"],
        {"move_up": ["W"], "move_down": ["S"], "attack": ["J"], "dash": ["K"]},
    ),
    _blueprint("puzzle-cards", "Memory cards", "ui", ["puzzle"], {}),
]


def _real_blueprints() -> list[dict[str, Any]]:
    try:
        from joinery import blueprints  # type: ignore[attr-defined]
    except ImportError:
        return []
    return [blueprints.describe(b.id) for b in blueprints.list_blueprints()]


# --------------------------------------------------------------------------
# Template and wizard


def test_template_tokens_are_exactly_the_sections_tokens() -> None:
    found = set(re.findall(r"\{\{(\w+)\}\}", TEMPLATE.read_text()))
    assert found == set(TOKENS)


def test_template_headings_follow_the_section_order() -> None:
    headings = [heading for heading, _ in brief.sections(TEMPLATE.read_text())]
    assert headings == [heading for heading, _ in brief.SECTIONS]


def test_every_question_has_a_token_and_a_page() -> None:
    group_keys = {key for key, _ in GROUPS}
    for question in QUESTIONS:
        assert question["key"] in TOKENS
        assert question["group"] in group_keys
    assert sum(len(page["questions"]) for page in grouped()) == len(QUESTIONS)  # type: ignore[arg-type]
    assert [q["key"] for q in QUESTIONS] == ["pitch", "audience"]


# --------------------------------------------------------------------------
# Drafting


@pytest.mark.parametrize(
    "blueprint", BLUEPRINTS + _real_blueprints(), ids=lambda b: b["id"]
)
@pytest.mark.parametrize(
    "features",
    [
        [],
        ["controller", "save-load"],
        ["online-play", "steam", "controller", "save-load", "translations"],
    ],
    ids=["none", "defaults", "all"],
)
def test_draft_from_each_starting_point_leaves_no_token_and_facts_parse(
    blueprint: dict[str, Any], features: list[str]
) -> None:
    text = draft(
        {"pitch": "Hop across rooftops.", "title": "Rooftops"}, blueprint, features
    )
    assert "{{" not in text and "}}" not in text
    assert "Hop across rooftops." in text
    assert text.startswith("# Game brief — Rooftops\n")
    parsed = parse(text)
    assert parsed.problems == []
    assert parsed.facts is not None
    assert parsed.facts.dimension == blueprint["dimension"]
    assert parsed.facts.genre == blueprint["brief"].get("genre", [])
    assert "Pause (`pause`)" in text
    assert "Monetisation" in parsed.gaps
    assert "Pitch" not in parsed.gaps


def test_draft_without_a_starting_point_records_everything_as_open() -> None:
    text = draft({}, None, [])
    assert "{{" not in text
    parsed = parse(text)
    assert parsed.facts == BriefFacts()
    for heading in (
        "Pitch",
        "Core loop",
        "Controls per device",
        "Camera",
        "Player verbs",
    ):
        assert heading in parsed.gaps
    assert "# Game brief — Untitled game" in text
    # Nothing is made up: no starting-point credit, no default verbs.
    assert "starting point" not in text.split("## 1.")[1].split("## 19.")[0].replace(
        "the starting point you picked", ""
    )


def test_answers_reach_the_output_and_win_over_the_starting_point() -> None:
    blueprint = BLUEPRINTS[3]
    answers = {
        "pitch": "A frog\nwho jumps",
        "pitch_long": "Longer pitch here.",
        "audience": "Kids who like Celeste",
        "camera": "Fixed screens, no scrolling.",
        "monetisation": "Premium, one price.",
        "facts": "online = 'online'",  # computed only; never taken from answers
    }
    text = draft(answers, blueprint, [])
    assert "A frog who jumps" in text  # one line
    assert "Longer pitch here." in text
    assert "Kids who like Celeste" in text
    assert "Fixed screens, no scrolling." in text
    assert "2D platformer camera." not in text
    assert "Premium, one price." in text
    facts = parse(text).facts
    assert facts is not None and facts.online == "none"


def test_starting_point_sections_are_credited_and_loop_is_split() -> None:
    text = draft({}, BLUEPRINTS[3], [])
    core_loop = dict(brief.sections(text))["Core loop"]
    assert "- 30 s: do the thing." in core_loop
    assert "- Session: beat it faster." in core_loop
    assert "_From the 2D platformer starting point; change freely._" in core_loop
    verbs = dict(brief.sections(text))["Player verbs"]
    assert "| Run | `move_left`, `move_right` | Dust and lean |" in verbs


def test_verbs_fall_back_to_input_actions_with_open_feedback() -> None:
    text = draft({}, BLUEPRINTS[4], [])
    verbs = dict(brief.sections(text))["Player verbs"]
    assert f"| Attack | `attack` | {UNSET} |" in verbs
    assert "pause" not in verbs
    assert "Player verbs" in parse(text).gaps


@pytest.mark.parametrize(
    "features,expected",
    [
        (
            [],
            {
                "online": "none",
                "max_players": 1,
                "stores": [],
                "input": ["keyboard_mouse"],
                "save": "none",
            },
        ),
        (["online-play"], {"online": "lan", "max_players": 4}),
        (["steam"], {"online": "none", "stores": ["steam"]}),
        (
            ["online-play", "steam"],
            {"online": "online", "stores": ["steam"], "max_players": 4},
        ),
        (["controller"], {"input": ["keyboard_mouse", "gamepad"]}),
        (["save-load"], {"save": "both"}),
        (["translations"], {"languages": ["en"]}),
    ],
)
def test_feature_effects_on_facts(
    features: list[str], expected: dict[str, Any]
) -> None:
    facts = draft_facts(BLUEPRINTS[3], features).as_json()
    for key, value in expected.items():
        assert facts[key] == value, key
    # The rendered block says the same.
    rendered = parse(draft({}, BLUEPRINTS[3], features)).facts
    assert rendered is not None and rendered.as_json() == facts


def test_translations_add_an_open_question_and_online_asks_co_op_or_pvp() -> None:
    parsed = parse(draft({}, BLUEPRINTS[3], ["translations", "online-play"]))
    assert "Which languages besides English?" in parsed.open_questions
    assert any("co-op" in q for q in parsed.open_questions)
    assert parse(draft({}, BLUEPRINTS[3], [])).open_questions == []


def test_saving_mentions_the_host_only_for_online_games() -> None:
    def saving(features: list[str]) -> str:
        return dict(brief.sections(draft({}, BLUEPRINTS[3], features)))["Saving"]

    assert "host" not in saving(["save-load"])
    assert "Online, only the host saves." in saving(["save-load", "online-play"])


def test_pad_column_needs_the_controller_feature() -> None:
    without = dict(brief.sections(draft({}, BLUEPRINTS[3], [])))["Controls per device"]
    with_pad = dict(brief.sections(draft({}, BLUEPRINTS[3], ["controller"])))[
        "Controls per device"
    ]
    assert "| Jump (`jump`) | Space / W / Up arrow | — | — |" in without
    assert "| Jump (`jump`) | Space / W / Up arrow | A | — |" in with_pad
    assert "| Pause (`pause`) | Esc / P | Start | — |" in with_pad


# --------------------------------------------------------------------------
# Controls


def test_controls_table_names_keys_mouse_and_pad() -> None:
    table = controls_table(
        {
            "move_left": {"keys": ["A", "Left"], "mouse": [], "pad": ["0-", "13"]},
            "attack": {"keys": ["J"], "mouse": ["left"], "pad": ["5+", "10"]},
            "look_up": {"keys": [], "mouse": ["wheel_up"], "pad": ["3-"]},
            "ui_accept": {"keys": ["Enter"], "mouse": [], "pad": ["0"]},
            "odd|name": {"keys": ["F5"], "mouse": [], "pad": [99, "Custom"]},
        }
    )
    lines = table.splitlines()
    assert lines[0] == "| Action | Keyboard and mouse | Gamepad | Touch |"
    assert (
        "| Move left (`move_left`) | A / Left arrow | Left stick left / D-pad left | — |"
        in lines
    )
    assert "| Attack (`attack`) | J / Left click | Right trigger / RB | — |" in lines
    assert "| Look up (`look_up`) | Wheel up | Right stick up | — |" in lines
    assert not any("ui_accept" in line for line in lines)
    assert any("Button 99 / Custom" in line for line in lines)
    assert len(lines) == 2 + 4


@pytest.mark.parametrize(
    "spec,label",
    [
        ("0", "A"),
        ("1", "B"),
        ("6", "Start"),
        ("7", "Left stick click"),
        ("10", "RB"),
        ("0-", "Left stick left"),
        ("1+", "Left stick down"),
        ("5+", "Right trigger"),
        ("4+", "Left trigger"),
        ("axis:2-", "Right stick left"),
        ("button 3", "Y"),
        ("Start", "Start"),
    ],
)
def test_pad_labels(spec: str, label: str) -> None:
    assert brief.pad_label(spec) == label


GODOT_INPUT = """config_version=5

[application]

config/name="Rooftops"
run/main_scene="res://core/main.tscn"

[input]

jump={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":32,"location":0,"echo":false,"script":null)
, Object(InputEventJoypadButton,"resource_local_to_scene":false,"resource_name":"","device":-1,"button_index":0,"pressure":0.0,"pressed":true,"script":null)
]
}
move_left={
"deadzone": 0.25,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":4194319,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
, Object(InputEventJoypadMotion,"resource_local_to_scene":false,"resource_name":"","device":-1,"axis":0,"axis_value":-1.0,"script":null)
]
}
attack={
"deadzone": 0.2,
"events": [Object(InputEventMouseButton,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"button_mask":0,"position":Vector2(0, 0),"global_position":Vector2(0, 0),"factor":1.0,"button_index":1,"canceled":false,"pressed":true,"double_click":false,"script":null)
]
}

[rendering]

textures/canvas_textures/default_texture_filter=0
"""


def test_input_map_is_read_from_project_godot_text() -> None:
    actions = parse_input_map(GODOT_INPUT)
    assert actions == {
        "jump": {"keys": ["Space"], "mouse": [], "pad": ["0"]},
        "move_left": {"keys": ["Left"], "mouse": [], "pad": ["0-"]},
        "attack": {"keys": [], "mouse": ["left"], "pad": []},
    }
    assert (
        brief.godot_section(GODOT_INPUT, "application")["config/name"] == '"Rooftops"'
    )


GODOT_WRITER = """extends SceneTree


func _key(code: int) -> InputEventKey:
	var event := InputEventKey.new()
	event.physical_keycode = code
	return event


func _init() -> void:
	var codes := {
		"Escape": KEY_ESCAPE, "Tab": KEY_TAB, "Backspace": KEY_BACKSPACE,
		"Enter": KEY_ENTER, "Left": KEY_LEFT, "Up": KEY_UP, "Right": KEY_RIGHT,
		"Down": KEY_DOWN, "Shift": KEY_SHIFT, "Ctrl": KEY_CTRL, "Alt": KEY_ALT,
		"F1": KEY_F1, "F12": KEY_F12, "Home": KEY_HOME, "PageDown": KEY_PAGEDOWN,
	}
	print("JOINERY_KEYS:" + JSON.stringify(codes))
	var pad := InputEventJoypadButton.new()
	pad.button_index = JOY_BUTTON_START
	var stick := InputEventJoypadMotion.new()
	stick.axis = JOY_AXIS_LEFT_Y
	stick.axis_value = -1.0
	var trigger := InputEventJoypadMotion.new()
	trigger.axis = JOY_AXIS_TRIGGER_RIGHT
	trigger.axis_value = 1.0
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN
	ProjectSettings.set_setting("input/pause", {
		"deadzone": 0.2, "events": [_key(KEY_ESCAPE), _key(KEY_P), pad],
	})
	ProjectSettings.set_setting("input/move_up", {
		"deadzone": 0.25, "events": [_key(KEY_W), _key(KEY_UP), stick],
	})
	ProjectSettings.set_setting("input/attack", {
		"deadzone": 0.2, "events": [_key(KEY_F1), wheel, trigger],
	})
	var err := ProjectSettings.save()
	print("JOINERY_SAVED:%d" % err)
	quit(0 if err == OK else 1)
"""


@needs_godot
def test_stock_godot_written_input_map_reads_back_and_key_codes_match(
    tmp_path: Path,
) -> None:
    godot = godot_binary()
    assert godot is not None
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="Rooftops"\n'
    )
    (project / "writer.gd").write_text(GODOT_WRITER)
    result = subprocess.run(
        [
            str(godot),
            "--headless",
            "--path",
            str(project),
            "--script",
            "res://writer.gd",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "JOINERY_SAVED:0" in output
    keys_line = next(
        line for line in output.splitlines() if line.startswith("JOINERY_KEYS:")
    )
    godot_codes = json.loads(keys_line.removeprefix("JOINERY_KEYS:"))
    for name, code in godot_codes.items():
        assert brief._SPECIAL_KEYS[name] == code, name

    actions = brief.input_actions(project)
    assert actions["pause"] == {"keys": ["Escape", "P"], "mouse": [], "pad": ["6"]}
    assert actions["move_up"] == {"keys": ["W", "Up"], "mouse": [], "pad": ["1-"]}
    assert actions["attack"] == {"keys": ["F1"], "mouse": ["wheel_down"], "pad": ["5+"]}

    # The brief written into that project shows the applied map, not the blueprint's.
    path = write_brief(project, {"pitch": "Climb."}, BLUEPRINTS[4], [])
    text = path.read_text()
    assert "# Game brief — Rooftops" in text
    assert "| Pause (`pause`) | Esc / P | Start | — |" in text
    assert "| Move up (`move_up`) | W / Up arrow | Left stick up | — |" in text
    assert "| Attack (`attack`) | F1 / Wheel down | Right trigger | — |" in text


# --------------------------------------------------------------------------
# Writing and reading


def test_write_brief_refuses_to_overwrite(tmp_path: Path) -> None:
    path = write_brief(tmp_path, {"pitch": "One"}, None, [])
    assert path == tmp_path / BRIEF_PATH
    with pytest.raises(FileExistsError):
        write_brief(tmp_path, {"pitch": "Two"}, None, [])
    assert "One" in path.read_text()


def test_answers_file_is_consumed(tmp_path: Path) -> None:
    (tmp_path / ANSWERS_FILE).write_text(
        json.dumps({"pitch": "Jump the gaps.", "audience": "teens"})
    )
    path = write_from_answers_file(tmp_path, BLUEPRINTS[3], ["controller"])
    assert path is not None and "Jump the gaps." in path.read_text()
    assert "teens" in path.read_text()
    assert not (tmp_path / ANSWERS_FILE).exists()
    assert write_from_answers_file(tmp_path, BLUEPRINTS[3], []) is None


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_bad_answers_file_is_removed_and_reported(tmp_path: Path, content: str) -> None:
    (tmp_path / ANSWERS_FILE).write_text(content)
    with pytest.raises(ValueError):
        write_from_answers_file(tmp_path, None, [])
    assert not (tmp_path / ANSWERS_FILE).exists()
    assert not (tmp_path / BRIEF_PATH).exists()


def test_load_brief_requires_a_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_brief(tmp_path)


def _with_facts(tmp_path: Path, facts: str, features: list[str] | None = None) -> Path:
    text = draft({"pitch": "Go."}, BLUEPRINTS[3], features or [])
    text = re.sub(
        r"```toml\n.*?```", "```toml\n" + facts + "\n```", text, flags=re.DOTALL
    )
    path = tmp_path / BRIEF_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    "facts,needle",
    [
        ("schema = 2", "newer Joinery"),
        ('online = "mmo"', "online"),
        ('platforms = ["dreamcast"]', "platforms"),
        ('languages = ["English"]', "languages are codes"),
        ("max_players = 0", "max_players"),
        ('surprise = "yes"', "surprise"),
        ("online = ", "not valid TOML"),
    ],
)
def test_bad_facts_are_problems_not_crashes(
    tmp_path: Path, facts: str, needle: str
) -> None:
    loaded = load_brief(_with_facts(tmp_path, facts))
    assert loaded.facts is None
    assert any(needle in problem for problem in loaded.problems), loaded.problems
    data = summary(tmp_path, None)
    assert data["problems"] and data["problems"][0] in data["suggestions"]


def test_facts_block_outside_a_facts_heading_still_parses() -> None:
    facts, problems = parse_facts(
        '# Brief\n\n```toml\nschema = 1\nonline = "lan"\nmax_players = 2\n```\n'
    )
    assert problems == [] and facts is not None and facts.online == "lan"
    facts, problems = parse_facts("# Brief\n\nNo block.\n")
    assert facts is None and "no facts block" in problems[0]


# --------------------------------------------------------------------------
# Suggestions


@pytest.mark.parametrize(
    "facts,features,needle",
    [
        ('online = "online"\nmax_players = 4', [], "/feature add online-play"),
        (
            'online = "lan"\nmax_players = 2',
            ["controller"],
            "LAN play, but online play is not added",
        ),
        ('input = ["keyboard_mouse", "gamepad"]', [], "/feature add controller"),
        ('languages = ["en", "fr"]', [], "/feature add translations"),
        ('stores = ["steam"]', [], "/feature add steam"),
        ('save = "checkpoints"', [], "/feature add save-load"),
        (
            'online = "none"',
            ["online-play"],
            "Online play is added, but the brief says no online play",
        ),
        ('save = "none"', ["save-load"], "Saving is added"),
        (
            'input = ["keyboard_mouse"]',
            ["controller"],
            "Add gamepad to the brief's facts",
        ),
    ],
)
def test_consistency_suggestions(
    tmp_path: Path, facts: str, features: list[str], needle: str
) -> None:
    project = _with_facts(tmp_path, facts)
    found = consistency(project, {"blueprint": "platformer-2d", "features": features})
    assert any(needle in line for line in found), found


def test_consistent_brief_has_no_setup_suggestions(tmp_path: Path) -> None:
    features = ["online-play", "steam", "controller", "save-load", "translations"]
    write_brief(tmp_path, {"pitch": "Go."}, BLUEPRINTS[3], features)
    assert (
        consistency(tmp_path, {"blueprint": "platformer-2d", "features": features})
        == []
    )


def test_without_a_game_record_only_self_contradictions_are_reported(
    tmp_path: Path,
) -> None:
    project = _with_facts(tmp_path, 'online = "lan"\ninput = ["gamepad"]')
    found = consistency(project, None)
    assert found == [
        "The brief says online play but max_players is 1. How many players can join?"
    ]
    assert consistency(tmp_path / "missing", None) == []


def test_dimension_mismatch_is_asked(tmp_path: Path) -> None:
    project = _with_facts(tmp_path, 'dimension = "3d"')
    found = consistency(
        project, {"blueprint": "platformer-2d", "features": [], "dimension": "2d"}
    )
    assert any("3D" in line and "2D" in line for line in found)


def test_summary_without_a_brief(tmp_path: Path) -> None:
    data = summary(tmp_path, None)
    assert data["exists"] is False and data["facts"] is None
    assert "/brief" in data["suggestions"][0]
    json.dumps(data)


def test_summary_orders_suggestions_and_never_suggests_monetisation(
    tmp_path: Path,
) -> None:
    write_brief(tmp_path, {}, BLUEPRINTS[3], ["translations"])
    data = summary(
        tmp_path, {"blueprint": "platformer-2d", "features": ["translations"]}
    )
    json.dumps(data)
    assert data["exists"] is True
    assert data["facts"]["schema"] == 1
    assert "Monetisation" in data["gaps"]
    assert not any("onetis" in line for line in data["suggestions"])
    suggestions = data["suggestions"]
    assert (
        suggestions[0] == "Open question in the brief: Which languages besides English?"
    )
    assert suggestions[1].startswith("The brief has no pitch yet.")
    assert len(suggestions) == 1 + 3  # open question, then at most three open sections


def test_summary_puts_setup_disagreements_first(tmp_path: Path) -> None:
    project = _with_facts(tmp_path, 'save = "slots"')
    data = summary(project, {"blueprint": "platformer-2d", "features": []})
    assert "/feature add save-load" in data["suggestions"][0]


def test_verb_inputs_that_are_not_actions_stay_plain_text() -> None:
    blueprint = {
        **BLUEPRINTS[1],
        "input": {
            "look_left": {"keys": ["J"], "mouse": [], "pad": []},
            "look_right": {"keys": ["L"], "mouse": [], "pad": []},
        },
        "brief": {
            "verbs": [["Look", "look_left / look_right / mouse", "Camera orbits"]]
        },
    }
    verbs = dict(brief.sections(draft({}, blueprint, [])))["Player verbs"]
    assert "| Look | `look_left`, `look_right`, mouse | Camera orbits |" in verbs
