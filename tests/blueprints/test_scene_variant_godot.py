"""A scene variant Joinery writes opens in stock Godot as Godot's own inherited scene
(ADR 0066): instanced, it carries the base's nodes with the overrides applied, and Godot
saves it back as an inherited scene that still lists only what differs."""

from __future__ import annotations

from pathlib import Path

from joinery import scene_variants
from tests.blueprints.godot_run import make_project, needs_godot, run_godot

PROBE = """extends SceneTree


func _initialize() -> void:
	var packed: PackedScene = load("res://game/props/red_coin.tscn")
	var coin: Node = packed.instantiate()
	var face: Polygon2D = coin.get_node(^"Visual/Face")
	var rim: Polygon2D = coin.get_node(^"Visual/Rim")
	print("face ", face.color.to_html(false), " rim ", rim.color.to_html(false))
	print("title ", coin.get_meta(&"title", ""), " groups ", coin.get_groups())
	print("base ", packed.get_state().get_node_instance(0).resource_path)
	ResourceSaver.save(packed, "res://game/props/red_coin_saved.tscn")
	coin.free()
	print("variant: ok")
	quit(0)
"""


@needs_godot
def test_a_made_variant_opens_and_saves_as_godots_inherited_scene(tmp_path: Path) -> None:
    project = make_project(tmp_path, "platformer-2d")
    made = scene_variants.make(
        project,
        "res://game/props/coin.tscn",
        "res://game/props/red_coin.tscn",
        [
            {"node": "Visual/Face", "property": "color", "value": "Color(1, 0, 0, 1)"},
            {"node": ".", "property": "metadata/title", "value": "Red coin"},
        ],
    )
    (project / made.path).write_text(made.text)
    (project / "tests/variant_probe.gd").write_text(PROBE)
    result = run_godot(project, "res://tests/variant_probe.gd", marker="variant")
    assert "face ff0000 rim b38224" in result.output
    assert "title Red coin" in result.output and "coin" in result.output
    assert "base res://game/props/coin.tscn" in result.output
    saved = (project / "game/props/red_coin_saved.tscn").read_text()
    assert 'instance=ExtResource("' in saved
    again = scene_variants.read(project, "game/props/red_coin_saved.tscn", saved)
    assert again is not None
    assert {(o.node, o.property) for o in again.overrides} == {
        ("Visual/Face", "color"),
        (".", "metadata/title"),
    }
