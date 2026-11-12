extends RefCounted
## The visual suite's review screens: a blockout beside its sketch, a scene variant with its
## overrides, and the review at the sidebar's width. Copied beside visual_editor_test.gd by
## run_visual_suite.py; [param suite] is that test, whose `_shot`, `_check` and `_frames`
## these use.

## What Joinery reads from VARIANT_SCENE (joinery.scene_variants).
const VARIANT := {
	"base": "res://visual.tscn",
	"overrides":
	[
		{
			"node": "Crate",
			"property": "transform",
			"base": "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.5, 0)",
			"value": "Transform3D(1.6, 0, 0, 0, 1.6, 0, 0, 0, 1.6, 0, 0.8, 0)",
			"base_said": "at (0, 0.5, 0)",
			"said": "at (0, 0.8, 0), ×1.6",
		},
		{
			"node": "Crate",
			"property": "material_override",
			"base": "",
			"value": 'SubResource("red")',
			"base_said": "",
			"said": "its own StandardMaterial3D",
		},
		{
			"node": "Floor",
			"property": "metadata/tint",
			"base": "Color(1, 1, 1, 1)",
			"value": "Color(0.82, 0.3, 0.24, 1)",
			"base_said": "Color(1, 1, 1, 1)",
			"said": "Color(0.82, 0.3, 0.24, 1)",
		},
	],
	"added": [{"node": "Barrel", "type": "MeshInstance3D"}],
	"connections": [],
	"count": 4,
}
const VARIANT_SCENE := """[gd_scene format=3]

[ext_resource type="PackedScene" path="res://visual.tscn" id="1_base"]

[sub_resource type="StandardMaterial3D" id="red"]
albedo_color = Color(0.82, 0.3, 0.24, 1)

[sub_resource type="CylinderMesh" id="barrel"]
top_radius = 0.35
bottom_radius = 0.35
height = 1.0

[node name="Harbour" instance=ExtResource("1_base")]

[node name="Crate" parent="."]
transform = Transform3D(1.6, 0, 0, 0, 1.6, 0, 0, 0, 1.6, 0, 0.8, 0)
material_override = SubResource("red")

[node name="Floor" parent="."]
metadata/tint = Color(0.82, 0.3, 0.24, 1)

[node name="Barrel" type="MeshInstance3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 0.5, 1)
mesh = SubResource("barrel")
"""


## The review at the sidebar's width: the rail stacks under the stage, the actions on screen.
## The panel is held narrow at the bottom panel's trailing edge for the capture.
static func narrow(suite: Variant, panel: Control, dock: Control) -> void:
	var holder := panel.get_parent()
	var index := panel.get_index()
	var row := HBoxContainer.new()
	row.size_flags_vertical = Control.SIZE_EXPAND_FILL
	holder.add_child(row)
	holder.move_child(row, index)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(push)
	panel.reparent(row, false)
	panel.size_flags_horizontal = Control.SIZE_FILL
	panel.custom_minimum_size.x = 380.0 * EditorInterface.get_editor_scale()
	await suite._frames()
	suite._check(bool(panel.get("_body").get("narrow")), "A narrow review stacks its rail")
	await suite._shot("review-narrow", [panel, dock])
	panel.custom_minimum_size.x = 0.0
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.reparent(holder, false)
	holder.move_child(panel, index)
	row.queue_free()
	await suite._frames()


## A change made from a to-do (ADR 0060): what "done" meant, checked, under what changed.
## The text review on screen gets the done-when results the host sends with it.
static func done_when(suite: Variant, panel: Control, dock: Control) -> void:
	var results := [
		{
			"id": "d1",
			"text": "The player can reach the vault",
			"status": "pass",
			"evidence": "The bot reached Vault at 0:42, seed 4242.",
		},
		{
			"id": "d2",
			"text": "The gate opens when the lever is pulled",
			"status": "fail",
			"evidence": "res://tests/test_gate.gd isn't in the change.",
		},
		{
			"id": "d3",
			"text": "No errors in 60 s of play",
			"status": "inconclusive",
			"evidence": "The game ran only 0:41 of the 60 s asked for.",
		},
	]
	panel.call("show_glance", {"done_when": results})
	await suite._shot("review-done-when", [panel, dock])


## A level built from the user's sketch: the sketch and the plan side by side (ADR 0043). The
## suite copies a fixture sketch and the plan Joinery draws for it into the project.
static func blockout(suite: Variant, dock: Control, panel: Control) -> void:
	var sketch := ProjectSettings.globalize_path("res://blockout_fixture/keep.png")
	var plan := ProjectSettings.globalize_path("res://blockout_fixture/keep-plan.png")
	suite._check(
		FileAccess.file_exists(sketch) and FileAccess.file_exists(plan), "Blockout fixture"
	)
	(
		dock
		. show_file_review(
			"Build the keep from my sketch",
			"+ design/blockouts/keep.toml\n+ game/keep_blockout.tscn",
			[
				{
					"path": "design/blockouts/keep.toml",
					"media": "image",
					"label": "Blockout",
					"after": plan,
					"reference": sketch,
				}
			],
			[
				{"path": "design/blockouts/keep.toml", "change": "created"},
				{"path": "game/keep_blockout.tscn", "change": "created"},
				{"path": "game/blockout/blockout_level.gd", "change": "created"},
			],
		)
	)
	(
		panel
		. call(
			"show_glance",
			{
				"facts": ["Walkable: spawn reaches 3 of 3 goals"],
				"checks": {"ran": ["references", "Godot import"], "skipped": ["tests"]},
			}
		)
	)
	await suite._shot("review-blockout", [panel, dock])
	dock.hide_edit_review()
	await suite._frames()


## A scene variant (ADR 0066): the base and the variant drawn by the editor as one split
## picture, and only what the variant overrides listed under it. The variant inherits the
## suite's harbour scene: a bigger, red crate and a barrel beside it.
static func variant(suite: Variant, dock: Control, panel: Control) -> void:
	var folder := OS.get_user_data_dir().path_join("visual-variant")
	DirAccess.make_dir_recursive_absolute(folder)
	var scene := folder.path_join("red_harbour.tscn")
	var file := FileAccess.open(scene, FileAccess.WRITE)
	file.store_string(VARIANT_SCENE)
	file.close()
	var item := {
		"path": "harbour/red_harbour.tscn",
		"media": "image",
		"label": "Variant",
		"before": folder.path_join("before.png"),
		"after": folder.path_join("after.png"),
		"scenes": {"before": "res://visual.tscn", "after": scene},
		"variant": VARIANT,
	}
	for side: String in ["before", "after"]:
		DirAccess.remove_absolute(str(item[side]))
	(
		dock
		. show_file_review(
			"A red harbour with a bigger crate",
			"+ harbour/red_harbour.tscn",
			[item],
			[{"path": "harbour/red_harbour.tscn", "change": "created"}],
		)
	)
	panel.call("show_glance", {"facts": ["variant of visual.tscn, 3 overrides, 1 added node"]})
	for wait: int in 120:
		if FileAccess.file_exists(str(item["after"])):
			break
		await suite._frames(1)
	suite._check(FileAccess.file_exists(str(item["after"])), "The editor drew the variant")
	# The stage looks for the picture every 0.25 s, then fades it in.
	await (Engine.get_main_loop() as SceneTree).create_timer(1.0).timeout
	await suite._frames(8)
	await suite._shot("review-variant", [panel, dock])
	dock.hide_edit_review()
	await suite._frames()
