"""The design check: deterministic findings with evidence, agent findings that must
cite real places, the check_every setting, and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import cli, design

PROJECT_GODOT = """\
config_version=5

[application]

config/name="Broken"
run/main_scene="res://game/level.tscn"

[input]

jump={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":32,"location":0,"echo":false,"script":null)
]
}
shoot={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":70,"key_label":0,"unicode":102,"location":0,"echo":false,"script":null)
]
}
crouch={
"deadzone": 0.2,
"events": []
}
"""

BRIEF = """\
# Game brief — Broken

## 1. Pitch
Run and jump to the flag.

## 6. Player verbs
| Verb | Input action | Feedback |
|---|---|---|
| Jump | `jump` | Dust on take-off |
| Shoot | `shoot` | A bolt flies |
| Crouch | `crouch` | Lower hitbox |
| Dash | `dash` | A streak |
| Grab | _Not decided yet._ | _Not decided yet._ |
| Aim | mouse | Crosshair |

## 7. Controls per device
| Action | Keyboard and mouse |
|---|---|
| Jump (`jump`) | Space |

## 9. Win, lose, progression
Win: touch the flag. Lose: run out of lives.
"""

GDD = """\
# Broken GDD

## Mechanics
- **Wall jump**: push off walls to climb shafts.
- **Jump**: the basic jump.

## Story
- Bob: the hero.
"""

PLAYER = """\
extends CharacterBody2D

@export var jump_velocity: float = 300.0
@export var run_speed: float = 120.0


func _physics_process(_delta: float) -> void:
	if Input.is_action_just_pressed("jump"):
		velocity.y = -jump_velocity
	move_and_slide()
"""

OTHER_PLAYER = """\
extends CharacterBody2D

const JUMP_VELOCITY = -450.0
const SPEED = 120.0
"""

LEVEL = """\
extends Node2D


func _ready() -> void:
	pass


func _on_goal_reached() -> void:
	_complete()


func _complete() -> void:
	GameEvents.level_completed.emit({})
"""

GEM = """\
extends Area2D


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _on_body_entered(_body: Node) -> void:
	queue_free()
"""

COIN = """\
extends Area2D


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _on_body_entered(_body: Node) -> void:
	$Sound.play()
	queue_free()
"""

PLAYER_SCENE = """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://game/player.gd" id="1_p"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_p")
"""


def level_scene(jump: str = "") -> str:
    override = f"jump_velocity = {jump}\n" if jump else ""
    return (
        "[gd_scene load_steps=3 format=3]\n\n"
        '[ext_resource type="Script" path="res://game/level.gd" id="1_l"]\n'
        '[ext_resource type="PackedScene" path="res://game/player.tscn" id="2_p"]\n\n'
        '[node name="Level" type="Node2D"]\n'
        'script = ExtResource("1_l")\n\n'
        '[node name="Player" parent="." instance=ExtResource("2_p")]\n' + override
    )


LAYOUT = """\
version = 1
name = "caves"
mode = "3d"
units = "m"

[[rooms]]
id = "hall"
rect = [0, 0, 10, 10]
open = ["east"]

[[rooms]]
id = "pit"
rect = [10, 0, 10, 10]
floor = -4.0
open = ["west"]

[[rooms]]
id = "vault"
rect = [0, 14, 6, 6]

[spawn]
at = [2, 2]
facing = "east"

[[markers]]
kind = "goal"
id = "exit"
at = [5, 8]

[[markers]]
kind = "coin"
id = "gem"
at = [3, 17]
"""


def report_json(failed: list[float]) -> str:
    return json.dumps(
        {
            "seed": 4242,
            "played_seconds": 120,
            "items": [
                {
                    "id": "p1",
                    "kind": "softlock",
                    "severity": "high",
                    "detail": "The player didn't move for 15 s near the crates.",
                    "where": "res://game/level.gd:9",
                    "t": 42.0,
                },
                {
                    "id": "p2",
                    "kind": "balance",
                    "severity": "note",
                    "detail": "A bot pressing keys at random finished the level in 0:12: it may be too easy.",
                    "t": 12.0,
                },
            ],
            "goals": {"failed": [{"t": t, "reason": "spikes"} for t in failed]},
        }
    )


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def broken(tmp_path: Path) -> Path:
    return write(
        tmp_path / "broken",
        {
            "project.godot": PROJECT_GODOT,
            "design/brief.md": BRIEF,
            "design/docs/gdd.md": GDD,
            "game/player.gd": PLAYER,
            "game/player.tscn": PLAYER_SCENE,
            "game/old_player.gd": OTHER_PLAYER,
            "game/level.gd": LEVEL,
            "game/level.tscn": level_scene(),
            "game/level_2.tscn": level_scene("500.0"),
            "game/props/gem.gd": GEM,
            "game/props/coin.gd": COIN,
            "design/blockouts/caves.toml": LAYOUT,
            ".joinery/playtests/20261111-100000-abc/report.json": report_json(
                [10.0, 61.0, 64.0, 70.0, 75.0]
            ),
            # Test code is never read: it would "reach" anything.
            "tests/level_test.gd": "func t():\n\t_on_goal_reached()\n",
        },
    )


def by_check(report: design.Report) -> dict[str, list[design.Finding]]:
    found: dict[str, list[design.Finding]] = {}
    for item in report.findings:
        found.setdefault(item.check, []).append(item)
    return found


def test_every_check_finds_its_problem_with_evidence(broken: Path) -> None:
    report = design.check(broken)
    found = by_check(report)
    titles = [f.title for f in report.findings]

    # Verbs: unbound, not in the map, no events, bound but unread.
    unbound = {f.title for f in found["unbound-verb"]}
    assert "Dash: `dash` isn't in the input map" in unbound
    assert "Crouch: `crouch` has no key or button" in unbound
    assert "Grab has no input action" in unbound
    assert not any("Aim" in t for t in titles), (
        "a cell with no action name is not a finding"
    )
    assert [f.title for f in found["unused-verb"]] == [
        "Shoot is bound but nothing reads `shoot`"
    ]
    assert not any(t.startswith("Jump") for t in unbound)
    shoot = found["unused-verb"][0]
    assert shoot.evidence[0].path == "design/brief.md" and shoot.evidence[0].line == 10
    assert (
        shoot.evidence[1].path == "project.godot" and shoot.evidence[1].node == "shoot"
    )

    # The design document's wall jump is never implemented; jump is; story isn't a mechanic.
    assert [f.title for f in found["missing-mechanic"]] == [
        "Wall jump from the design document isn't in the game"
    ]
    assert found["missing-mechanic"][0].evidence[0].line == 4

    # Win exists but nothing connects the goal; lose isn't there at all.
    win = found["unreachable-win"][0]
    assert (
        win.evidence[0].path == "game/level.gd" and win.evidence[0].node == "_complete"
    )
    assert "Win: touch the flag" in win.why
    assert found["no-lose"][0].severity == "high"

    # Controls: bound gameplay actions and nothing shows them.
    assert found["controls-hidden"][0].evidence[0].path == "project.godot"

    # The gem vanishes silently; the coin plays a sound.
    pickups = found["silent-pickup"]
    assert [f.evidence[0].path for f in pickups] == ["game/props/gem.gd"]
    assert pickups[0].evidence[0].line == 9

    # Jump differs between the two levels, and between two player scripts.
    uneven = {f.title for f in found["uneven-tuning"]}
    assert (
        "Jump velocity differs between scenes (level.tscn 300, level_2.tscn 500)"
        in uneven
    )
    assert any(
        t.startswith("Jump velocity differs between player scripts") for t in uneven
    )

    # The blockout: the vault's coin can't be reached, the pit traps the player.
    assert [f.title for f in found["unreachable"]] == ["The coin gem can't be reached"]
    trap = next(f for f in found["softlock"] if f.title == "Room pit traps the player")
    assert trap.evidence[0].path == "design/blockouts/caves.toml"
    assert trap.evidence[0].line == 11

    # The playtest: a softlock with its seed and place; a spike; "too easy" is taste.
    stuck = [
        f for f in found["softlock"] if f.title.startswith("The playtester got stuck")
    ]
    assert stuck and stuck[0].evidence[0].path == "game/level.gd"
    assert stuck[0].evidence[1].node == "seed 4242 at 0:42"
    assert found["difficulty-spike"][0].title == "Difficulty spike around 1:01"
    assert "too easy" not in json.dumps(report.model_dump())

    # Every finding cites a real place and carries a complete request.
    for item in report.findings:
        assert item.evidence, item.title
        for place in item.evidence:
            assert (broken / place.path).is_file(), place
        assert item.request.startswith("The design check found: ")
        assert item.source == "check"
    assert [f.severity for f in report.findings] == sorted(
        (f.severity for f in report.findings), key=design.SEVERITY_ORDER.__getitem__
    )
    assert report.summary == f"{len(report.findings)} design problems"


def test_a_sound_project_has_nothing_to_flag(broken: Path) -> None:
    fixed = {
        "design/brief.md": BRIEF.replace("| Shoot | `shoot` | A bolt flies |\n", "")
        .replace("| Crouch | `crouch` | Lower hitbox |\n", "")
        .replace("| Dash | `dash` | A streak |\n", "")
        .replace("| Grab | _Not decided yet._ | _Not decided yet._ |\n", "")
        .replace("Lose: run out of lives.", "Lose: none."),
        "design/docs/gdd.md": GDD.replace(
            "- **Wall jump**: push off walls to climb shafts.\n", ""
        ),
        "game/level.gd": LEVEL.replace(
            "\tpass", "\t$Goal.reached.connect(_on_goal_reached)"
        ),
        "game/level_2.tscn": level_scene(),
        "game/old_player.gd": "extends Node\n",
        "game/props/gem.gd": COIN,
        "game/hud.gd": 'extends Label\n\nfunc _ready() -> void:\n\ttext = "Press Space to jump"\n',
        "design/blockouts/caves.toml": LAYOUT.replace("floor = -4.0\n", "")
        .replace('open = ["east"]', 'open = ["east", "south"]')
        .replace("rect = [0, 14, 6, 6]", 'rect = [0, 10, 6, 10]\nopen = ["north"]'),
    }
    write(broken, fixed)
    (broken / ".joinery/playtests/20261111-100000-abc/report.json").unlink()
    report = design.check(broken)
    assert report.findings == [], [f.title for f in report.findings]
    assert report.summary == "No design problems found"
    assert "design/brief.md" in report.read and "design/docs/gdd.md" in report.read


def test_no_brief_means_nothing_is_promised(tmp_path: Path) -> None:
    root = write(
        tmp_path / "bare", {"project.godot": "config_version=5\n", "main.gd": LEVEL}
    )
    assert design.check(root).findings == []


def test_controls_hint_from_the_input_map_counts(broken: Path) -> None:
    write(
        broken,
        {
            "game/hint.gd": "extends Label\n\nfunc _ready() -> void:\n"
            '\ttext = InputMap.action_get_events("jump")[0].as_text()\n'
        },
    )
    assert "controls-hidden" not in by_check(design.check(broken))


def test_the_newest_playtest_is_read(broken: Path) -> None:
    newer = broken / ".joinery/playtests/20261112-090000-def/report.json"
    newer.parent.mkdir(parents=True)
    newer.write_text(
        json.dumps({"seed": 1, "items": [], "goals": {}}), encoding="utf-8"
    )
    found = by_check(design.check(broken))
    assert "difficulty-spike" not in found
    assert not any(
        f.title.startswith("The playtester") for f in found.get("softlock", [])
    )


# --- the agent pass --------------------------------------------------------------


def agent(**overrides: object) -> dict[str, object]:
    finding: dict[str, object] = {
        "severity": "medium",
        "title": "The goal is never shown",
        "player": "The player doesn't know to look for the flag.",
        "why": "The brief's win is to touch the flag; nothing on screen says so.",
        "basis": "brief",
        "evidence": [
            {"path": "design/brief.md", "line": 22, "quote": "Win: touch the flag"},
            {"path": "res://game/level.tscn", "node": "Level"},
        ],
        "fix": "Show a one-line goal when the level starts.",
    }
    finding.update(overrides)
    return finding


def test_agent_findings_need_evidence_that_holds(broken: Path) -> None:
    kept, dropped = design.agent_findings(
        broken,
        {
            "findings": [
                agent(),
                agent(title="Too dark", basis="taste"),
                agent(title="No evidence", evidence=[]),
                agent(title="Made up", evidence=[{"path": "game/nope.gd", "line": 3}]),
                agent(
                    title="Wrong line", evidence=[{"path": "game/level.gd", "line": 99}]
                ),
                agent(
                    title="Misquoted",
                    evidence=[
                        {"path": "game/level.gd", "line": 12, "quote": "game_over"}
                    ],
                ),
                agent(
                    title="No node",
                    evidence=[{"path": "game/level.tscn", "node": "Boss"}],
                ),
                agent(title="Escapes", evidence=[{"path": "../outside.gd", "line": 1}]),
            ]
        },
    )
    assert [f.title for f in kept] == ["The goal is never shown"]
    only = kept[0]
    assert only.source == "agent" and only.check == "agent"
    assert only.evidence[1].path == "game/level.tscn"
    assert "Show a one-line goal" in only.request
    assert len(dropped) == 7
    assert any("taste" in d for d in dropped)
    assert any("doesn't say 'game_over'" in d for d in dropped)
    assert any("has no line 99" in d for d in dropped)
    assert any("no 'Boss'" in d for d in dropped)
    assert any("../outside.gd is not a file" in d for d in dropped)
    kept, dropped = design.agent_findings(
        broken,
        {"findings": [agent(evidence=[{"path": "game/level.gd", "line": 1}])]},
    )
    assert not kept and "cites no line of it" in dropped[0]


def test_a_broken_game_finding_needs_no_brief_line(broken: Path) -> None:
    kept, dropped = design.agent_findings(
        broken,
        {
            "findings": [
                agent(
                    basis="broken",
                    evidence=[
                        {
                            "path": "game/props/gem.gd",
                            "line": 9,
                            "quote": "queue_free()",
                        }
                    ],
                )
            ]
        },
    )
    assert len(kept) == 1 and not dropped


def test_agent_findings_are_capped(broken: Path) -> None:
    kept, dropped = design.agent_findings(broken, {"findings": [agent()] * 10})
    assert len(kept) == design.AGENT_LIMIT
    assert len(dropped) == 2
    with pytest.raises(TypeError):
        design.agent_findings(broken, {"findings": "all of them"})


# --- the setting and the CLI -----------------------------------------------------


def test_check_every_keeps_the_rest_of_joinery_toml(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text(
        '[agents]\nreviewer = true\n\n[match]\nfinal = ["a.png"]\n', encoding="utf-8"
    )
    assert design.check_every(tmp_path) == 0
    assert design.set_check_every(tmp_path, 5) == 5
    assert design.set_check_every(tmp_path, 3) == 3
    text = config.read_text(encoding="utf-8")
    assert text.count("[design]") == 1 and "check_every = 3" in text
    assert 'final = ["a.png"]' in text and "reviewer = true" in text
    assert design.set_check_every(tmp_path, 0) == 0
    with pytest.raises(ValueError):
        design.set_check_every(tmp_path, -1)


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_cli_json_and_plain(broken: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["design", "--project", str(broken), "--json"], capsys)
    data = json.loads(out)
    assert code == 0 and data["summary"].endswith("design problems")
    assert data["findings"][0]["severity"] == "high"
    code, out = run_cli(["design", "--project", str(broken)], capsys)
    assert code == 0 and out.splitlines()[0].endswith("design problems.")
    assert "  high   " in out
    before = {p: p.read_bytes() for p in broken.rglob("*") if p.is_file()}
    run_cli(["design", "--project", str(broken), "--json"], capsys)
    after = {p: p.read_bytes() for p in broken.rglob("*") if p.is_file()}
    assert before == after, "the check never changes the project"


def test_cli_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(
        ["design", "--project", str(tmp_path), "--every", "5", "--json"], capsys
    )
    assert code == 0 and json.loads(out)["check_every"] == 5
    code, out = run_cli(
        ["design", "--project", str(tmp_path), "--settings", "--json"], capsys
    )
    assert json.loads(out) == {"check_every": 5, "choices": [0, 1, 3, 5, 10]}
    code, out = run_cli(
        ["design", "--project", str(tmp_path), "--every", "500", "--json"], capsys
    )
    assert code == 1 and "error" in json.loads(out)
    code, out = run_cli(["design", "--project", str(tmp_path), "--json"], capsys)
    assert code == 1 and "not a Godot project" in json.loads(out)["error"]
