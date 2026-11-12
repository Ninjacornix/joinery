extends RefCounted
## The visual suite's review screens: a blockout beside its sketch, and the review at the
## sidebar's width. Copied beside visual_editor_test.gd by run_visual_suite.py; [param suite]
## is that test, whose `_shot`, `_check` and `_frames` these use.


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
