extends SceneTree
## The Review panel, headless: counts, Before/After only with pairs, a split picture for a
## texture pair, loading and failure, the changed lines of text, details, optimistic
## Accept/Discard with rollback, and results. Also
## the settings page's Compact history, which asks in place before it runs.

const PANEL := preload("res://addons/joinery/review_panel.gd")
const DETAILS := preload("res://addons/joinery/review_details.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
const SETTINGS := preload("res://addons/joinery/journeyman_settings.gd")
const DISCARDED := "Discarded. Your files were not touched."

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
	await _check_renders_arrive(panel)
	await _check_details(panel)
	await _check_scene(panel)
	await _check_applying(panel)
	await _check_optimistic(panel)
	await _check_motion(panel)
	await _check_compact()
	await _check_history_policy()
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
	var facts: String = panel._summary.facts_text()
	_check(facts == "Door wood darker, 64×48 → 80×40 px · 1 image · 2 files", "Facts line: " + facts)
	_check(panel._segment.visible, "Before/After shows when a pair exists")
	_check(not panel._new_chip.visible, "A changed asset is not marked new")
	_check(
		not panel._after.button_pressed and not panel._before.button_pressed,
		"A texture pair opens as a split picture, neither side pressed"
	)
	var swipe: Control = panel._media.swipe()
	_check(swipe.visible and is_equal_approx(swipe.split, 0.5), "The split starts in the middle")
	_check(swipe.before != null and swipe.after != null, "Both sides are in the split")
	var texture: Texture2D = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(80, 40), "The After image renders")
	_check(panel._media.file_name() == "door_wood.png", "The shown file is named")
	_check(not panel._details_scroll.visible, "Details start closed when there is media")
	panel._before.button_pressed = true
	await _frames()
	texture = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(64, 48), "Before swaps the image")
	_check(is_equal_approx(swipe.split, 1.0), "Before slides the line all the way")
	panel._before.button_pressed = false
	await _frames()
	_check(
		panel._media.side == "compare" and is_equal_approx(swipe.split, 0.5),
		"Unpressing the side goes back to the split"
	)
	panel._before.button_pressed = true
	await _frames()
	_check(panel._media.texture().get_width() > 0, "Headless image has a real size")
	_check(panel._media.size.y > panel.size.y * 0.4, "The preview takes most of the height")
	_check(
		panel._accept.get_global_rect().end.y <= panel.get_global_rect().end.y + 1,
		"Accept stays inside the panel"
	)
	_check(
		is_equal_approx(panel._accept.size.x, panel._reject.size.x),
		"Accept and Discard share one width"
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
			"",
			[{"path": "art/models/hammer.glb", "media": "image", "after": still}],
		)
	)
	await _frames()
	_check(not panel._segment.visible, "Before/After hides without a pair")
	_check(panel._new_chip.visible, "A new asset shows the New chip")
	_check(panel._media.texture() != null, "A new model render shows")
	_check(not panel._media.filmstrip().visible, "One item has no filmstrip")
	var facts: String = panel._summary.facts_text()
	_check(facts == "1 model", "Counts without changes: " + facts)


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


## The review opens before its renders are done: Accept works at once, a render that fails says
## so, and one that arrives fills its place.
func _check_renders_arrive(panel: PANEL) -> void:
	var later := _cache.path_join("crate_render.png")
	DirAccess.remove_absolute(later)
	var media := [
		{"path": "art/crate.blend", "media": "image", "after": later},
		{"path": "art/lamp.blend", "media": "image", "after": _cache.path_join("lamp.png")},
	]
	panel.show_file_review("Weather the crate", "", media, [])
	await _frames()
	_check(panel._media.is_loading(), "Placeholders while the renders run")
	_check(not panel._accept.disabled, "Accept works while the renders run")
	var failed := [
		{"path": "art/crate.blend", "media": "image", "error": "Blender stopped"},
		{"path": "art/lamp.blend", "media": "image", "after": _cache.path_join("lamp.png")},
	]
	panel.update_media(failed)
	await _frames()
	_check(not panel._media.is_loading(), "A failed render stops the placeholder")
	_check(panel._media.is_failed(), "A failed render says there is no preview")
	_check(panel._media._failure.text.contains("Blender stopped"), "It says why")
	panel.show_file_review("Weather the crate", "", media, [])
	await _frames()
	_image("crate_render.png", Color(0.5, 0.4, 0.3))
	panel.update_media(media)
	var deadline := Time.get_ticks_msec() + 3000
	while panel._media.is_loading() and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(panel._media.texture() != null, "An arrived render shows in place")
	# A render slower than the wait still shows once it arrives.
	var slow := _cache.path_join("slow_render.png")
	DirAccess.remove_absolute(slow)
	var pending := [{"path": "art/chair.blend", "media": "image", "after": slow}]
	panel.show_file_review("Round the chair", "", pending, [])
	await _frames()
	panel._media._stop_waiting()
	panel._media._fail()
	_image("slow_render.png", Color(0.3, 0.3, 0.6))
	panel.update_media(pending)
	await _frames()
	_check(not panel._media.is_failed(), "A late render clears the no-preview line")
	_check(panel._media.texture() != null, "A late render shows after the wait gave up")
	# Only the After render failed: never show the Before as if it were the change.
	var half := [
		{
			"path": "art/lamp.blend",
			"media": "image",
			"before": _cache.path_join("lamp.png"),
			"error": "Blender crashed",
		}
	]
	panel.show_file_review("Brighten the lamp", "", half, [])
	await _frames()
	_check(panel._media.is_failed(), "A failed After render says so")
	_check(panel._media.texture() == null, "The Before is not shown as the change")
	_check(
		panel._media._failure.text == "No preview of the change. Blender crashed",
		"It names the missing side and why"
	)


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
	_check(panel._media.visible, "Text changes are the preview")
	_check(panel._media.current_kind() == "text", "The preview is the changed lines")
	var shown: String = panel._media.diff_view().shown_text()
	_check(shown.contains("+var b := 3") and shown.contains("-var a := 1"), "Changed lines show")
	_check(not shown.contains("@@"), "No hunk headers in the preview")
	_check(not panel._segment.visible, "Text has no Before/After switch")
	_check(
		panel._summary.facts_text() == "+2 −1 lines · 1 model · 1 script",
		"Text facts: " + panel._summary.facts_text()
	)
	_check(not panel._details_scroll.visible, "Details stay closed with a preview")
	panel._details_button.button_pressed = true
	await _frames()
	_check(panel._details_scroll.visible, "The Details button opens them")
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
	_check(panel._media.filmstrip().visible, "A picture and text make a filmstrip")
	_check(panel._media.filmstrip().tiles().size() == 2, "One tile per item")
	_check(panel._media.current_kind() == "image", "The picture comes first")
	panel._media.select_item(1)
	await _frames()
	_check(panel._media.current_kind() == "text", "A tile shows its item")
	panel.show_file_review("Only a model went", "", [], [{"path": "a.glb", "change": "deleted"}])
	await _frames()
	_check(not panel._media.visible, "Nothing to preview hides the stage")
	_check(panel._details_scroll.visible, "Details open when there is nothing to preview")
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


func _check_scene(panel: PANEL) -> void:
	panel.show_scene_review("Move the lamp up", 4)
	await _frames()
	var facts: String = panel._summary.facts_text()
	_check(facts == "4 scene nodes", "Scene counts: " + facts)
	_check(
		not panel._media.visible and panel._scene_note.visible, "Scene edits point at the viewport"
	)
	_check(not panel._segment.visible and not panel._new_chip.visible, "No toggle for scene edits")
	_check(not panel._details_button.visible, "Scene edits have no details")
	_check(
		panel._footer.get_global_rect().end.y >= panel.get_global_rect().end.y - 2,
		"Buttons stay at the bottom without media"
	)
	# With the viewport's before and after, a scene edit is one split picture.
	var frames := [
		{
			"path": "scenes/room.tscn",
			"media": "image",
			"label": "Viewport",
			"before": _image("room-before.png", Color(0.2, 0.2, 0.25), Vector2i(160, 90)),
			"after": _image("room-after.png", Color(0.5, 0.45, 0.3), Vector2i(160, 90)),
		}
	]
	panel.show_scene_review("Light the room", 2, frames)
	await _frames()
	_check(panel._media.visible and not panel._scene_note.visible, "The viewport frames show")
	_check(panel._media.swipe().visible, "A scene edit compares in one frame")
	_check(panel._segment.visible, "The scene's sides can be flipped")


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
	_check(panel._reject.text == "Discard", "The button reads Discard, never Reject")
	panel._reject.pressed.emit()
	_check(rejected[0] == 1, "Discard emits reject_pressed")
	_check(panel._outcome_text.text == DISCARDED, "The outcome says the files were not touched")
	_check(panel._outcome_note.text == "Discarding…", "The note says it is discarding")
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
	_check(panel._summary.facts_text() == "3 images", "Facts: " + panel._summary.facts_text())
	_check(is_equal_approx(panel._header.modulate.a, 1.0), "The header lands from the card")
	_check(is_equal_approx(panel.modulate.a, 1.0), "The panel finishes fading in")
	panel._accept.pressed.emit()
	await create_timer(0.4).timeout
	_check(panel._outcome.visible and not panel._body.visible, "The crossfade settles")
	panel.show_error("Try again")
	await create_timer(0.4).timeout
	_check(panel._body.visible and not panel._outcome.visible, "The rollback settles")
	UI_MOTION.set_enabled(false)


## Compact history asks first, in a row on the page (no dialog): Cancel leaves the history as
## it is; Compact runs `joinery history compact`.
func _check_compact() -> void:
	var page: ScrollContainer = SETTINGS.new()
	page.size = Vector2(600, 500)
	_host.add_child(page)
	await _frames()
	var menu: PopupMenu = page._history_button.get_popup()
	menu.id_pressed.emit(0)
	await _frames()
	_check(page._compact_confirm.visible, "Compact history asks first")
	_check(page._history_job == null, "Nothing runs before the answer")
	var texts: Array[String] = []
	for label: Node in page._compact_confirm.find_children("*", "Label", true, false):
		texts.append((label as Label).text)
	_check(
		texts.has("Compact history? Older changes can no longer be reverted."),
		"The question says what Compact costs"
	)
	var buttons: Array[String] = []
	for button: Node in page._compact_confirm.find_children("*", "Button", true, false):
		buttons.append((button as Button).text)
	_check(buttons == ["Cancel", "Compact"], "The row offers Compact and Cancel")
	_check(page.find_children("*", "ConfirmationDialog", true, false).is_empty(), "No dialog")
	(page._compact_confirm.find_children("*", "Button", true, false)[0] as Button).pressed.emit()
	_check(not page._compact_confirm.visible, "Cancel closes the question")
	_check(page._history_job == null, "Cancel runs nothing")
	menu.id_pressed.emit(0)
	await _frames()
	page._compact_button.pressed.emit()
	_check(not page._compact_confirm.visible, "Compact closes the question")
	_check(
		page._history_job != null and page._history_action == "compact",
		"Compact runs joinery history compact"
	)
	page._cancel_history()
	page.queue_free()
	await _frames()


## "Older versions" next to History: a choice that removes nothing applies at once; one that
## removes restore data says what first, in place, and only "Keep fewer" applies it. Custom
## checks its numbers before asking anything.
func _check_history_policy() -> void:
	var page: ScrollContainer = SETTINGS.new()
	page.size = Vector2(600, 500)
	_host.add_child(page)
	await _frames()
	var picker: OptionButton = page._policy_picker
	_check(picker.disabled, "The choice waits until the CLI says what is kept")
	page._policy = {"keep": 50, "max_mb": 2048, "preset": "keep-50", "label": "Last 50 changes"}
	page._show_policy()
	picker.disabled = false
	_check(picker.get_item_text(picker.selected) == "Last 50 changes", "The saved choice shows")
	picker.select(2)
	picker.item_selected.emit(2)
	_check(page._history_action == "policy-check", "A new choice is checked first (a dry run)")
	_check(page._pending_policy == {"keep": 1, "max_mb": 1024}, "It checks Only the newest")
	page._cancel_history()
	page._policy_checked({"pruned": 3, "freed_bytes": 120000000})
	await _frames()
	var panel: Control = page._policy_panel
	_check(panel.visible, "Removing restore data asks first, in place")
	_check(
		(
			(panel.get("text") as Label).text
			== "3 older changes can no longer be reverted. Frees 120 MB."
		),
		"The question says what it removes and frees"
	)
	_check((panel.get("apply") as Button).text == "Keep fewer", "The action says what it does")
	_check(page.find_children("*", "ConfirmationDialog", true, false).is_empty(), "No dialog")
	panel.emit_signal("cancelled")
	_check(not panel.visible and page._history_job == null, "Cancel changes nothing")
	_check(picker.selected == 0, "Cancel shows the saved choice again")
	picker.select(3)
	picker.item_selected.emit(3)
	await _frames()
	var keep: LineEdit = (panel.get("keep") as Control).get("control")
	var space: LineEdit = (panel.get("space") as Control).get("control")
	_check(
		panel.visible and keep.text == "50" and space.text == "2", "Custom starts from the saved"
	)
	space.text = "none"
	panel.emit_signal("applied")
	var error: Label = (panel.get("space") as Control).get("error")
	_check(error.visible and error.text.begins_with("Enter the space in GB"), "Bad numbers say why")
	_check(page._history_job == null, "Nothing runs for bad numbers")
	keep.text = "10"
	space.text = "1.5"
	panel.emit_signal("applied")
	_check(page._pending_policy == {"keep": 10, "max_mb": 1536}, "Custom checks its numbers")
	_check(page._history_action == "policy-check", "…with a dry run first")
	page._cancel_history()
	page.queue_free()
	await _frames()
