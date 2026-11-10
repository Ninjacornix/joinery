extends SceneTree
## The Review panel, headless: counts, Before/After only with pairs, a real image preview,
## loading and failure, details, optimistic Accept/Reject with rollback, and results.

const PANEL := preload("res://addons/joinery/review_panel.gd")
const DETAILS := preload("res://addons/joinery/review_details.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")

var _failed := false
var _host: Control
var _cache: String


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _run() -> void:
	_cache = OS.get_cache_dir().path_join("joinery-review-panel-test")
	DirAccess.make_dir_recursive_absolute(_cache)
	var config := _cache.path_join("journeyman.cfg")
	DirAccess.remove_absolute(config)
	Engine.set_meta("joinery_ui_config", config)
	Engine.remove_meta("joinery_ui_prefs")
	root.size = Vector2i(900, 520)
	_host = Control.new()
	_host.size = Vector2(900, 520)
	root.add_child(_host)
	_check_counts()
	var panel: PANEL = PANEL.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(panel)
	await _frames()
	_check(panel._empty.visible and not panel.pending, "A new panel shows the empty state")
	await _check_image_pair(panel)
	await _check_new_asset(panel)
	await _check_audio(panel)
	await _check_loading(panel)
	await _check_details(panel)
	await _check_scene(panel)
	await _check_applying(panel)
	await _check_optimistic(panel)
	await _check_motion(panel)
	panel.clear()
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	Engine.remove_meta("joinery_ui_prefs")
	await _frames()
	print("review panel: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_counts() -> void:
	var model := [
		{"path": "art/sources/hammer.blend", "change": "created"},
		{"path": "art/models/hammer.glb", "change": "created"},
		{"path": "recipes/hammer.toml", "change": "created"},
	]
	_check(
		DETAILS.counts_text(model) == "1 model · 3 files",
		"Model counts: " + DETAILS.counts_text(model)
	)
	var images := [
		{"path": "art/textures/door_wood.png", "change": "modified"},
		{"path": "art/textures/door_metal.png", "change": "modified"},
	]
	_check(
		DETAILS.counts_text(images) == "2 images", "Image counts: " + DETAILS.counts_text(images)
	)
	var scripts := [{"path": "README.txt", "change": "modified"}]
	_check(DETAILS.counts_text(scripts) == "1 file", "Plain file counts")
	_check(DETAILS.scene_text(4) == "4 scene nodes", "Scene node counts")
	_check(DETAILS.scene_text(1) == "1 scene node", "One scene node")
	var sections := DETAILS.split_diff(
		"--- a.gd\n+++ a.gd\n@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+more\n--- b.gd\n+++ b.gd\n+x"
	)
	_check(sections.keys() == ["a.gd", "b.gd"], "Diff splits by file")
	_check(DETAILS.line_counts(sections["a.gd"]) == [2, 1], "Diff line counts")


func _image(name: String, tint: Color, size := Vector2i(64, 48)) -> String:
	var image := Image.create(size.x, size.y, false, Image.FORMAT_RGBA8)
	image.fill(tint)
	var path := _cache.path_join(name)
	image.save_png(path)
	return path


func _note(name: String) -> String:
	var path := _cache.path_join(name)
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string("SynthDef(\\barrel, {}).add;")
	file.close()
	return path


func _check_image_pair(panel: PANEL) -> void:
	var before := _image("before.png", Color(0.7, 0.5, 0.3))
	var after := _image("after.png", Color(0.4, 0.27, 0.14), Vector2i(80, 40))
	var shown: Array[int] = [0]
	panel.show_requested.connect(func() -> void: shown[0] += 1)
	(
		panel
		. show_file_review(
			"Darken the door wood",
			"",
			[
				{
					"path": "art/textures/door_wood.png",
					"media": "image",
					"before": before,
					"after": after
				}
			],
			[
				{"path": "art/textures/door_wood.kra", "change": "modified"},
				{"path": "art/textures/door_wood.png", "change": "modified"},
			]
		)
	)
	await _frames()
	_check(shown[0] == 1, "Showing a review asks to bring the panel into view")
	_check(panel.pending, "A shown review is pending")
	_check(panel._title.text == "Darken the door wood", "The title is just the summary")
	_check(panel._counts.text == "1 image · 2 files", "Counts caption: " + panel._counts.text)
	_check(panel._segment.visible, "Before/After shows when a pair exists")
	_check(not panel._new_chip.visible, "A changed asset is not marked new")
	_check(panel._after.button_pressed and not panel._before.button_pressed, "After is the default")
	var texture: Texture2D = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(80, 40), "The After image renders")
	_check(panel._media.file_name() == "door_wood.png", "The file name sits above the preview")
	_check(not panel._details_scroll.visible, "Details start closed when there is media")
	panel._before.button_pressed = true
	await _frames()
	texture = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(64, 48), "Before swaps the image")
	_check(panel._media.texture().get_width() > 0, "Headless image has a real size")
	_check(panel._media.size.y > panel.size.y * 0.4, "The preview takes most of the height")
	_check(
		panel._accept.get_global_rect().end.y <= panel.get_global_rect().end.y + 1,
		"Accept stays inside the panel"
	)
	_check(
		is_equal_approx(panel._accept.size.x, panel._reject.size.x),
		"Accept and Reject share one width"
	)
	_check(
		panel._reject.get_global_rect().position.x < panel._accept.get_global_rect().position.x,
		"Accept sits on the trailing edge"
	)


func _check_new_asset(panel: PANEL) -> void:
	var still := _image("hammer.png", Color(0.35, 0.37, 0.4))
	(
		panel
		. show_file_review(
			"Create a low-poly hammer",
			"+ art/sources/hammer.blend\n+ recipes/hammer.toml",
			[{"path": "art/models/hammer.glb", "media": "image", "after": still}],
		)
	)
	await _frames()
	_check(not panel._segment.visible, "Before/After hides without a pair")
	_check(panel._new_chip.visible, "A new asset shows the New chip")
	_check(panel._media.texture() != null, "A new model render shows")
	_check(panel._counts.text == "1 model", "Counts without changes: " + panel._counts.text)


func _check_audio(panel: PANEL) -> void:
	var before := ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav")
	var after := _cache.path_join("door_creak_short.wav")
	DirAccess.copy_absolute(before, after)
	panel.show_file_review(
		"Make the door creak shorter",
		"",
		[{"path": "audio/sfx/door_creak.wav", "media": "audio", "before": before, "after": after}],
		[{"path": "audio/sfx/door_creak.wav", "change": "modified"}]
	)
	await _frames()
	var preview: Control = panel._media.audio()
	_check(preview != null, "Audio uses one audio preview")
	_check(panel._segment.visible, "Audio pairs get Before/After")
	if preview == null:
		return
	_check(preview.get("compact") == true, "The review's audio preview is compact")
	_check(
		panel._media.find_children("*", "AudioStreamPlayer", true, false).size() == 1, "One player"
	)
	if preview.has_method("seek_to"):
		preview.call("seek_to", 0.3)
		panel._before.button_pressed = true
		await _frames()
		_check(
			is_equal_approx(float(preview.get("playhead")), 0.3),
			"Switching sides keeps the playback position"
		)
	panel._after.button_pressed = true
	panel.clear()
	await _frames()
	_check(panel._media.audio() == null, "Clearing drops the audio preview")


func _check_loading(panel: PANEL) -> void:
	var later := _cache.path_join("rendering.png")
	DirAccess.remove_absolute(later)
	panel.show_file_review(
		"Repaint the crate",
		"",
		[{"path": "art/textures/crate.png", "media": "image", "after": later}],
		[{"path": "art/textures/crate.png", "change": "modified"}]
	)
	await _frames()
	_check(panel._media.is_loading(), "A preview still being written shows as loading")
	_check(panel._media.texture() == null, "Nothing is drawn while loading")
	_image("rendering.png", Color(0.2, 0.5, 0.3))
	var deadline := Time.get_ticks_msec() + 3000
	while panel._media.is_loading() and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(panel._media.texture() != null, "The preview appears once the file exists")
	panel.show_file_review(
		"Shorten the barrel knock",
		"",
		[{"path": "audio/sfx/barrel.scd", "media": "audio", "after": _note("barrel.scd")}],
		[{"path": "audio/sfx/barrel.scd", "change": "modified"}]
	)
	await _frames()
	_check(panel._media.is_failed(), "An unreadable preview says there is no preview")


func _check_details(panel: PANEL) -> void:
	var diff := (
		"\n"
		. join(
			[
				"--- scripts/door.gd",
				"+++ scripts/door.gd",
				"@@ -1 +1,2 @@",
				"-var a := 1",
				"+var a := 2",
				"+var b := 3",
			]
		)
	)
	(
		panel
		. show_file_review(
			"Make the door open faster",
			diff,
			[],
			[
				{"path": "scripts/door.gd", "change": "modified"},
				{"path": "art/models/door.glb", "change": "deleted"},
			]
		)
	)
	await _frames()
	_check(not panel._media.visible, "No media hides the preview")
	_check(panel._details_scroll.visible, "Details open by default without media")
	_check(panel._details.rows().size() == 2, "One row per changed file")
	_check(panel._details.row_counts(0) == "+2 −1", "Line counts: " + panel._details.row_counts(0))
	_check(panel._details.row_counts(1) == "", "A binary file has no line counts")
	var body: RichTextLabel = panel._details.rows()[0].get_meta("body")
	_check(body.is_visible_in_tree(), "The only diff opens by itself")
	_check(body.get_parsed_text().contains("var b := 3"), "Raw diff lines are shown")
	panel.show_file_review(
		"Darken it again",
		diff,
		[{"path": "art/textures/x.png", "media": "image", "after": _image("x.png", Color.GRAY)}],
		[{"path": "scripts/door.gd", "change": "modified"}]
	)
	await _frames()
	_check(not panel._details_scroll.visible, "Details stay closed with media")
	panel.set_details_open(true)
	await _frames()
	_check(panel._details_scroll.visible, "The preference opens details")
	panel.show_file_review(
		"And again",
		diff,
		[{"path": "art/textures/x.png", "media": "image", "after": _cache.path_join("x.png")}],
		[{"path": "scripts/door.gd", "change": "modified"}]
	)
	await _frames()
	_check(panel._details_scroll.visible, "The details preference holds for the next review")
	panel.set_details_open(false)
	panel._details_button.button_pressed = true
	await _frames()
	_check(panel._details_scroll.visible, "The Details button opens them")


func _check_scene(panel: PANEL) -> void:
	panel.show_scene_review("Move the lamp up", 4)
	await _frames()
	_check(panel._counts.text == "4 scene nodes", "Scene counts: " + panel._counts.text)
	_check(
		not panel._media.visible and panel._scene_note.visible, "Scene edits point at the viewport"
	)
	_check(not panel._segment.visible and not panel._new_chip.visible, "No toggle for scene edits")
	_check(not panel._details_button.visible, "Scene edits have no details")
	_check(
		panel._footer.get_global_rect().end.y >= panel.get_global_rect().end.y - 2,
		"Buttons stay at the bottom without media"
	)


func _check_applying(panel: PANEL) -> void:
	panel.set_applying(true)
	_check(panel._accept.disabled and panel._reject.disabled, "Buttons are disabled while applying")
	_check(panel._applying.visible and panel._accept.text.is_empty(), "Accept shows Applying…")
	panel.set_applying(false)
	_check(not panel._accept.disabled and not panel._reject.disabled, "Buttons come back")
	_check(panel._accept.text == "Accept", "Accept text comes back")


func _check_optimistic(panel: PANEL) -> void:
	var accepted: Array[int] = [0]
	var rejected: Array[int] = [0]
	panel.accept_pressed.connect(func() -> void: accepted[0] += 1)
	panel.reject_pressed.connect(func() -> void: rejected[0] += 1)
	panel.show_scene_review("Move the lamp up", 1)
	panel._accept.pressed.emit()
	_check(accepted[0] == 1, "Accept emits accept_pressed")
	_check(
		panel._outcome.visible and not panel._body.visible, "Accept collapses the review at once"
	)
	_check(panel._outcome_text.text == "Change accepted", "The outcome reads Change accepted")
	_check(panel._accept.disabled and panel._reject.disabled, "No second decision while applying")
	panel._accept.pressed.emit()
	_check(accepted[0] == 1, "A second press does nothing")
	panel.show_error("Blender did not respond. Try again.")
	_check(panel._body.visible and not panel._outcome.visible, "An error rolls the review back")
	_check(panel._error.visible and panel._error.text.begins_with("Blender"), "The error shows")
	_check(not panel._accept.disabled, "Buttons work again after an error")
	_check(panel.pending, "The change is still pending after an error")
	panel._reject.pressed.emit()
	_check(rejected[0] == 1, "Reject emits reject_pressed")
	_check(panel._outcome_text.text == "Change rejected", "The outcome reads Change rejected")
	_check(not panel._error.visible, "Deciding again hides the old error")
	panel.set_applying(false)
	await _frames()
	_check(panel._body.visible and panel.pending, "A decision that never ran restores the review")
	panel._accept.pressed.emit()
	panel.set_applying(true)
	panel.set_applying(false)
	panel.show_result(true, "Kept · hammer.glb")
	await _frames()
	_check(panel._outcome_text.text == "Kept · hammer.glb", "The result text shows")
	_check(not panel.pending and panel._outcome.visible, "The result replaces the review")
	await create_timer(UI_MOTION.duration("confirm") + 0.1).timeout
	_check(panel._empty.visible and not panel.pending, "The panel clears after the result")
	panel.show_scene_review("Move the lamp", 2)
	panel._accept.pressed.emit()
	panel.show_result(true, "")
	panel.show_scene_review("Next change", 3)
	await create_timer(UI_MOTION.duration("confirm") + 0.1).timeout
	_check(
		panel.pending and panel._title.text == "Next change", "A new review survives an old result"
	)


func _check_motion(panel: PANEL) -> void:
	UI_MOTION.set_enabled(true)
	panel.clear()
	(
		panel
		. show_file_review(
			"Darken the door wood",
			"",
			[],
			[
				{"path": "art/textures/a.png", "change": "modified"},
				{"path": "art/textures/b.png", "change": "modified"},
				{"path": "art/textures/c.png", "change": "modified"},
			]
		)
	)
	panel.arrive_from(Rect2(Vector2(600, 40), Vector2(240, 80)))
	panel._details_button.button_pressed = false
	panel._details_button.button_pressed = true
	await create_timer(0.6).timeout
	_check(panel._counts.text == "3 images", "Counts roll to their value: " + panel._counts.text)
	_check(panel._counts.visible_characters == -1, "The roll finishes")
	_check(is_equal_approx(panel._header.modulate.a, 1.0), "The header lands from the card")
	_check(is_equal_approx(panel.modulate.a, 1.0), "The panel finishes fading in")
	panel._accept.pressed.emit()
	await create_timer(0.4).timeout
	_check(panel._outcome.visible and not panel._body.visible, "The crossfade settles")
	panel.show_error("Try again")
	await create_timer(0.4).timeout
	_check(panel._body.visible and not panel._outcome.visible, "The rollback settles")
	UI_MOTION.set_enabled(false)
