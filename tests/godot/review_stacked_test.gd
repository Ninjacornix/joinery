extends SceneTree
## A short, narrow Review panel (the bottom panel of a 1024 × 768 editor) stacks the rail
## under the stage and still keeps Accept and Discard inside the panel, for a long diff and
## for a model picture with its filmstrip. The stage gives up height; the actions never do.

const PANEL := preload("res://addons/joinery/review_panel.gd")

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 6) -> void:
	for index: int in count:
		await process_frame


func _button(root: Node, prefix: String) -> Button:
	for node: Node in root.find_children("*", "Button", true, false):
		if (node as Button).text.begins_with(prefix) and (node as Button).is_visible_in_tree():
			return node as Button
	return null


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-review-stacked-test")
	DirAccess.make_dir_recursive_absolute(cache)
	Engine.set_meta("joinery_ui_config", cache.path_join("journeyman.cfg"))
	root.size = Vector2i(1024, 768)
	_host = Control.new()
	root.add_child(_host)
	var panel: PANEL = PANEL.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(panel)
	var still := Image.create(320, 320, false, Image.FORMAT_RGBA8)
	still.fill(Color(0.35, 0.37, 0.4))
	var still_path := cache.path_join("hammer.png")
	still.save_png(still_path)
	var lines := PackedStringArray()
	for index: int in 250:
		lines.append("+ Long reviewed scene change %d with properties and source context" % index)
	var cases := [
		["long diff", "Large technical fixture import", "\n".join(lines), [], []],
		[
			"model preview",
			"Create a low-poly hammer",
			"+ art/sources/hammer.blend\n+ recipes/hammer.toml",
			[{"path": "art/models/hammer.glb", "media": "image", "after": still_path}],
			[{"path": "art/models/hammer.glb", "change": "created"}],
		],
	]
	for size: Vector2 in [Vector2(580, 460), Vector2(580, 380), Vector2(420, 340)]:
		_host.size = size
		for case: Array in cases:
			panel.show_file_review(case[1], case[2], case[3], case[4])
			await _frames()
			var where := "%s at %s" % [case[0], size]
			var bounds := _host.get_global_rect().grow(0.5)
			for prefix: String in ["Accept", "Discard"]:
				var button := _button(panel, prefix)
				_check(button != null, "%s is shown: %s" % [prefix, where])
				if button != null:
					_check(
						bounds.encloses(button.get_global_rect()),
						(
							"%s stays inside the panel: %s (%s in %s)"
							% [prefix, where, button.get_global_rect(), bounds]
						)
					)
			_check(
				panel.size.y <= size.y + 0.5,
				"The review fits the panel: %s (%s tall)" % [where, panel.size.y]
			)
			_check(panel._body.narrow, "The rail stacks under the stage: " + where)
			var stage: float = panel._stage_area.size.y
			_check(
				stage >= size.y * 0.4,
				"The stage keeps most of the height: %s (%s of %s)" % [where, stage, size.y]
			)
	panel.clear()
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	await _frames()
	print("review stacked: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
