"""joinery map: a short ranked map of the project, cached by content (ADR 0063)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.protocol import Prompt
from bridge.provider_process import project_map as prompt_map
from bridge.provider_process import turn_text
from joinery import project_map
from joinery.cli import main


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def game(root: Path, props: int = 3) -> Path:
    write(
        root,
        "project.godot",
        'config_version=5\n\n[application]\n\nconfig/name="Quarry"\n'
        'run/main_scene="res://game/level.tscn"\n\n[autoload]\n\n'
        'Score="*res://core/score.gd"\n\n[input]\n\njump={\n"deadzone": 0.2,\n"events": []\n}\n',
    )
    write(
        root,
        "joinery.toml",
        '[game]\nblueprint = "platformer-2d"\nblueprint_version = 1\nfeatures = ["save-load"]\n',
    )
    write(
        root,
        "core/score.gd",
        "extends Node\nsignal changed\nfunc add(n: int) -> void:\n\tpass\n",
    )
    write(
        root,
        "core/style.gd",
        "class_name Style\nextends RefCounted\nstatic func colour() -> Color:\n\treturn Color.RED\n",
    )
    ext = [
        '[ext_resource type="Script" path="res://game/level.gd" id="1"]',
        '[ext_resource type="PackedScene" path="res://game/player.tscn" id="2"]',
    ]
    nodes = [
        '[node name="Level" type="Node2D"]\nscript = ExtResource("1")\n',
        '[node name="Player" parent="." instance=ExtResource("2")]\n',
    ]
    for i in range(props):
        ext.append(
            f'[ext_resource type="PackedScene" path="res://game/prop{i}.tscn" id="p{i}"]'
        )
        nodes.append(f'[node name="Prop{i}" parent="." instance=ExtResource("p{i}")]\n')
        write(
            root,
            f"game/prop{i}.tscn",
            f'[gd_scene format=3]\n\n[node name="Prop{i}" type="Area2D"]\n',
        )
    write(
        root,
        "game/level.tscn",
        "[gd_scene format=3]\n\n" + "\n".join(ext) + "\n\n" + "\n".join(nodes),
    )
    write(
        root,
        "game/level.gd",
        "extends Node2D\n@export var gravity: float = 9.8\nfunc _ready() -> void:\n"
        '\tStyle.colour()\n\tload("res://game/player.tscn")\nfunc restart() -> void:\n\tpass\n',
    )
    write(
        root,
        "game/player.tscn",
        '[gd_scene format=3]\n\n[ext_resource type="Script" path="res://game/player.gd" id="1"]\n\n'
        '[node name="Player" type="CharacterBody2D"]\nscript = ExtResource("1")\n\n'
        '[node name="Shape" type="CollisionShape2D" parent="."]\n',
    )
    write(
        root,
        "game/player.gd",
        "extends CharacterBody2D\nsignal jumped\n@export var speed := 4.0\n",
    )
    write(root, "art/props/crate.glb", "glTF")
    write(root, "art/props/crate.png", "png")
    write(root, "audio/sfx/jump.wav", "RIFF")
    write(root, "tests/test_level.gd", "extends Node\n")
    write(
        root,
        "recipes/crate.toml",
        'version = 1\ntool = "blender"\nscript = "recipes/crate.py"\noutputs = ["art/props/crate.glb"]\n',
    )
    write(root, "recipes/crate.py", "import bpy\n")
    return root


def test_the_map_lists_the_project_most_referenced_first(tmp_path: Path) -> None:
    text = project_map.build(game(tmp_path))
    lines = text.splitlines()
    assert lines[0].startswith("Project map")
    assert (
        'Game "Quarry": blueprint platformer-2d; features save-load; main scene game/level.tscn.'
        in text
    )
    assert "Autoloads: Score core/score.gd." in text
    assert "Input actions: jump." in text
    scenes = [line for line in lines if line.startswith("- ") and ".tscn:" in line]
    # The main scene is where the walk starts; the player is referred to twice.
    assert scenes[0].startswith(
        "- game/level.tscn: Level (Node2D); 5 nodes; script game/level.gd"
    )
    assert "instances player.tscn, prop0.tscn" in scenes[0]
    assert scenes[1].startswith("- game/player.tscn: Player (CharacterBody2D); 2 nodes")
    assert "used by 2" in scenes[1]
    # class_name use counts as a reference.
    assert (
        "- core/style.gd: class Style; extends RefCounted; funcs colour; used by 1"
        in text
    )
    assert "exports gravity; funcs restart" in text  # _ready is not listed
    assert "- art/props/: 1 .glb, 1 .png" in text and "- audio/sfx/: 1 .wav" in text
    assert "recipes/: " not in text  # recipes are their own section
    assert "- recipes/crate.toml (blender) → art/props/crate.glb" in text
    assert "Tests: 1 scripts under tests/." in text
    assert "test_level" not in text


def test_a_big_project_stays_within_its_budget(tmp_path: Path) -> None:
    root = game(tmp_path, props=200)
    text = project_map.build(root)
    assert len(text) <= project_map.BUDGET
    assert project_map.tokens(text) <= 2000
    assert "more scenes: ask for the full map" in text
    # The best-ranked scenes stay.
    assert "- game/level.tscn:" in text and "- game/player.tscn:" in text


def test_the_map_is_cached_by_content(tmp_path: Path) -> None:
    root = game(tmp_path)
    first = project_map.cached(root)
    cache = list((root / project_map.MAP_DIR).glob("*.txt"))
    assert len(cache) == 1 and cache[0].read_text(encoding="utf-8") == first
    project_map._memo.clear()
    assert project_map.cached(root) == first
    before = project_map.fingerprint(root)
    # A changed script is a new map; touching a file without changing it is not.
    (root / "core/score.gd").write_text("extends Node\nsignal changed\nsignal reset\n")
    assert project_map.fingerprint(root) != before
    second = project_map.cached(root)
    assert "signals changed, reset" in second and second != first
    unchanged = project_map.fingerprint(root)
    (root / "core/score.gd").touch()
    assert project_map.fingerprint(root) == unchanged
    # Joinery's own state never changes the map.
    write(root, ".joinery/metrics/x.jsonl", "{}")
    write(root, "addons/joinery/plugin.gd", "extends EditorPlugin\n")
    assert project_map.fingerprint(root) == unchanged


def test_no_map_outside_a_godot_project(tmp_path: Path) -> None:
    assert project_map.cached(tmp_path) == ""


def turn(root: Path, text: str, revision: int) -> Prompt:
    return Prompt(
        text=text,
        project_dir=str(root),
        editor_context={
            "scene_path": "res://game/level.tscn",
            "scene_revision": str(revision),
        },
    )


def test_the_map_leads_the_stable_prefix(tmp_path: Path) -> None:
    root = game(tmp_path)
    first, second = turn(root, "Make the crate red", 1), turn(root, "Add a coin", 2)
    head = prompt_map(first)
    assert head.startswith("Project map")
    assert head.index("Project map") < head.index("Editor context:")
    assert prompt_map(second) == head
    a, b = turn_text(first), turn_text(second)
    cut = a.index("This turn:")
    assert a[:cut] == b[:cut]


def test_the_command_prints_the_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = game(tmp_path)
    assert main(["map", "--project", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["map"].startswith("Project map") and data[
        "tokens"
    ] == project_map.tokens(data["map"])
    assert main(["map", "--project", str(tmp_path / "nope")]) == 1
