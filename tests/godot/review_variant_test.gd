extends SceneTree
## A scene variant in review (ADR 0066): under the picture, only what the variant overrides,
## one row per property with the base's value and the variant's (a swatch for colours), then
## the nodes it adds; a long list stops with a count; a variant that changes nothing says so.
## Headless there is no display to draw the pictures on: the item says so instead of waiting.
## Other items hide the list. A bare "/variant" is the sidebar's own command, sent to the
## chat host with the scene open; "/variant <words>" is a request for the agent.

const MEDIA := preload("res://addons/joinery/review_media.gd")
const OVERRIDES := preload("res://addons/joinery/review_overrides.gd")
const SLASH := preload("res://addons/joinery/slash_commands.gd")
const SCENE_VARIANT := preload("res://addons/joinery/sidebar_scene_variant.gd")

const BASE := """[gd_scene format=3]

[node name="Goblin" type="Node2D"]

[node name="Body" type="Polygon2D" parent="."]
color = Color(0.4, 0.7, 0.3, 1)
polygon = PackedVector2Array(-10, -10, 10, -10, 10, 10, -10, 10)
"""

var _failed := false


class FakeBridge:
	extends RefCounted
	var lines: Array = []

	func start_task(text: String, targets: Array, extra := {}) -> bool:
		lines.append({"text": text, "targets": targets, "extra": extra})
		return true


class FakeTasks:
	extends Node
	var bridge: Object = null


class FakeSidebar:
	extends Control
	var statuses: Array[String] = []

	func show_status(text: String, _seconds := 0.0) -> void:
		statuses.append(text)


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


static func variant_item(folder: String, overrides: Array, added: Array = []) -> Dictionary:
	var base := folder.path_join("goblin.tscn")
	var file := FileAccess.open(base, FileAccess.WRITE)
	file.store_string(BASE)
	file.close()
	return {
		"path": "enemies/red_goblin.tscn",
		"media": "image",
		"label": "Variant",
		"before": folder.path_join("variant-before.png"),
		"after": folder.path_join("variant-after.png"),
		"scenes": {"before": base, "after": base},
		"variant":
		{
			"base": "res://enemies/goblin.tscn",
			"overrides": overrides,
			"added": added,
			"connections": [],
			"count": overrides.size() + added.size(),
		},
	}


func _run() -> void:
	root.size = Vector2i(900, 600)
	var folder := OS.get_user_data_dir().path_join("review-variant-test")
	DirAccess.make_dir_recursive_absolute(folder)
	for name: String in ["variant-before.png", "variant-after.png"]:
		DirAccess.remove_absolute(folder.path_join(name))
	var media: MEDIA = MEDIA.new()
	root.add_child(media)
	media.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var overrides := [
		{
			"node": "Body",
			"property": "color",
			"base": "Color(0.4, 0.7, 0.3, 1)",
			"value": "Color(0.85, 0.25, 0.2, 1)",
			"base_said": "Color(0.4, 0.7, 0.3, 1)",
			"said": "Color(0.85, 0.25, 0.2, 1)",
		},
		{
			"node": ".",
			"property": "transform",
			"base": "",
			"value": "Transform2D(1.5, 0, 0, 1.5, 0, 0)",
			"base_said": "",
			"said": "at (0, 0), ×1.5",
		},
	]
	media.show_media(
		[variant_item(folder, overrides, [{"node": "Body/Horn", "type": "Polygon2D"}])]
	)
	await process_frame
	await process_frame
	var list: OVERRIDES = media.get("_overrides")
	_check(list.visible, "The overrides show under the picture")
	var head: Label = list.get("_head")
	_check(head.text == "Differs from goblin.tscn", head.text)
	var rows := list.row_texts()
	_check(
		(
			rows
			== PackedStringArray(
				[
					"Body · color: Color(0.4, 0.7, 0.3, 1) → Color(0.85, 0.25, 0.2, 1)",
					"Root · transform: default → at (0, 0), ×1.5",
					"Adds Body/Horn (Polygon2D)",
				]
			)
		),
		"Rows: %s" % [rows]
	)
	var swatches := list.find_children("*", "Panel", true, false)
	_check(swatches.size() == 2, "A swatch beside each colour: %d" % swatches.size())
	var more: Label = list.get("_more")
	_check(not more.visible, "No count under a short list")
	# Headless: no display to draw on, so the item says so rather than waiting.
	for frame: int in 5:
		await process_frame
	_check(not media.is_loading(), "It doesn't wait for pictures that can't come")
	_check(media.is_failed(), "The stage says there is no picture")
	var failure: Label = media.get("_failure")
	_check(failure.text.contains("No picture without a display"), failure.text)
	_check(list.visible, "The overrides still show")
	# A long list stops with a count.
	var many: Array = []
	for index: int in 11:
		many.append({"node": "Body", "property": "p%d" % index, "base": "1", "value": "2"})
	media.show_media([variant_item(folder, many)])
	await process_frame
	_check(list.row_texts().size() == OVERRIDES.MAX_ROWS, "At most %d rows" % OVERRIDES.MAX_ROWS)
	_check(more.visible and more.text == "and 3 more in Details", more.text)
	# A variant that changes nothing yet.
	media.show_media([variant_item(folder, [])])
	await process_frame
	_check(list.row_texts().is_empty() and more.text.begins_with("Nothing yet"), more.text)
	# Another kind hides the list.
	media.show_media([{"path": "scripts/a.gd", "media": "text", "diff": "+a\n-b"}])
	await process_frame
	_check(not list.visible, "Text hides the overrides")
	media.clear_media()
	_check(not list.visible, "Clearing hides them")
	media.queue_free()
	await process_frame
	_check_command()
	print("review variant: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_command() -> void:
	_check(SLASH.resolve("/variant") == "/variant", "/variant is a command")
	_check(SLASH.resolve("/variants") == "/variants", "/variants stays its own")
	_check(not SLASH.is_prompt("/variant"), "A bare /variant runs in the sidebar")
	_check(SLASH.is_prompt("/variant red and faster"), "/variant with words asks the agent")
	var slash: SLASH = SLASH.new()
	var heard: Array[bool] = []
	slash.scene_variant_requested.connect(func() -> void: heard.append(true))
	_check(slash.run("/variant"), "/variant runs")
	_check(heard == [true], "and asks for a variant")
	slash.free()
	var sidebar := FakeSidebar.new()
	var tasks := FakeTasks.new()
	var bridge := FakeBridge.new()
	tasks.bridge = bridge
	_check(not SCENE_VARIANT.make(sidebar, tasks), "No scene open: nothing is sent")
	_check(SCENE_VARIANT.make(sidebar, tasks, "res://enemies/goblin.tscn"), "Sent")
	_check(
		(
			bridge.lines
			== [
				{
					"text": "",
					"targets": [],
					"extra": {"kind": "variant_make", "base": "res://enemies/goblin.tscn"},
				}
			]
		),
		"The host is asked for a variant: %s" % [bridge.lines]
	)
	_check(sidebar.statuses == ["Making a variant of goblin.tscn…"], "%s" % [sidebar.statuses])
	sidebar.free()
	tasks.free()
