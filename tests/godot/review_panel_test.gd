extends SceneTree
## The Review panel, headless: counts, the stage and the rail, Before/After only with pairs, a
## split picture for a texture pair, loading and failure, the changed lines of text, Details as
## a drawer, optimistic Accept/Discard with rollback, and results. Also
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
	await _check_music(panel)
	await _check_loading(panel)
	await _check_renders_arrive(panel)
	await _check_details(panel)
	await _check_files(panel)
	await _check_shapes(panel)
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
	_check(
		facts == "Door wood darker, 64×48 → 80×40 px · 1 image · 2 files", "Facts line: " + facts
	)
	_check(
		_tag(panel, "before").is_visible_in_tree() and _tag(panel, "after").is_visible_in_tree(),
		"Before and After tags show on the media when a pair exists"
	)
	_check(_tag(panel, "after").text == "After", "A changed asset is not marked new")
	_check(
		_tag(panel, "compare").button_pressed and not _tag(panel, "before").button_pressed,
		"A texture pair opens as a split picture: Both is pressed"
	)
	_check(_tag(panel, "compare").is_visible_in_tree(), "A split picture offers Both")
	_check(_tags_on_their_sides(panel), "A tiny label names each half of the picture")
	_check(_pill_in_corner(panel), "The Before/After pill floats in the stage's top corner")
	var swipe: Control = panel._media.swipe()
	_check(swipe.visible and is_equal_approx(swipe.split, 0.5), "The split starts in the middle")
	_check(swipe.before != null and swipe.after != null, "Both sides are in the split")
	var texture: Texture2D = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(80, 40), "The After image renders")
	_check(panel._media.file_name() == "door_wood.png", "The shown file is named")
	_check(not panel.details_open(), "Details start closed when there is media")
	_tag(panel, "before").pressed.emit()
	await _frames()
	texture = panel._media.texture()
	_check(texture != null and texture.get_size() == Vector2(64, 48), "Before swaps the image")
	_check(is_equal_approx(swipe.split, 1.0), "Before slides the line all the way")
	_check(_tag(panel, "before").button_pressed, "The Before tag shows it is in focus")
	_check(panel._media.sides().tag_rects().is_empty(), "One side in focus needs no side labels")
	_tag(panel, "compare").pressed.emit()
	await _frames()
	_check(
		panel._media.side == "compare" and is_equal_approx(swipe.split, 0.5),
		"Both goes back to the split"
	)
	_tag(panel, "before").pressed.emit()
	await _frames()
	_check_rows(panel)
	_check(panel._media.texture().get_width() > 0, "Headless image has a real size")
	_check(panel._media.size.y > panel.size.y * 0.8, "The stage takes the full height")
	_check(
		panel._accept.get_global_rect().end.y <= panel.get_global_rect().end.y + 1,
		"Accept stays inside the panel"
	)
	_check(panel._accept.size.x > panel._reject.size.x, "Accept is the larger action")
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
	_check(not _tag(panel, "before").is_visible_in_tree(), "No Before tag without a pair")
	_check(
		_tag(panel, "after").is_visible_in_tree() and _tag(panel, "after").text == "New",
		"A new asset is tagged New on the media"
	)
	_check(not _tag(panel, "after").toggle_mode, "New names the side; it switches nothing")
	_check(panel._media.texture() != null, "A new model render shows")
	_check(not panel._media.files().visible, "One item has no file list")
	_check(
		panel._media.frame_height() >= panel._stage_area.size.y * 0.8,
		"One file keeps the whole stage"
	)
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
	_check(_tag(panel, "before").is_visible_in_tree(), "Audio pairs get Before/After tags")
	if preview == null:
		return
	_check(preview.get("compact") == true, "The review's audio preview is compact")
	_check(
		panel._media.find_children("*", "AudioStreamPlayer", true, false).size() == 1, "One player"
	)
	if preview.has_method("seek_to"):
		preview.call("seek_to", 0.3)
		_tag(panel, "before").pressed.emit()
		await _frames()
		_check(
			is_equal_approx(float(preview.get("playhead")), 0.3),
			"Switching sides keeps the playback position"
		)
	_tag(panel, "after").pressed.emit()
	panel.clear()
	await _frames()
	_check(panel._media.audio() == null, "Clearing drops the audio preview")


## A change to a music track: one row per layer with its stem's waveform and its level at the
## track's start, the tempo, bars and key above, and loudness from the facts.
func _check_music(panel: PANEL) -> void:
	var stem := _cache.path_join("main_pads.wav")
	DirAccess.copy_absolute(ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav"), stem)
	var stems := {}
	var levels := {}
	for layer: String in ["pads", "bass", "drums"]:
		stems[layer] = "res://audio/music/harbour/main_%s.wav" % layer
		levels[layer] = 1.0 if layer != "drums" else 0.5
	var track := {
		"version": 1,
		"name": "harbour",
		"key": "D minor",
		"tempo_bpm": 120.0,
		"layers":
		[
			{"name": "pads", "role": "pads", "volume_db": -6.0},
			{"name": "bass", "role": "bass", "muted": true},
			{"name": "drums", "role": "percussion"},
		],
		"sections": [{"name": "main", "bars": 8, "stems": stems}],
		"states": {"explore": {"section": "main", "layers": levels}},
		"default_state": "explore",
	}
	var diff := (
		"--- music/harbour.tres\n+++ music/harbour.tres\n@@ -1,0 +1,4 @@\n"
		+ '+[gd_resource type="Resource" format=3]\n+\n+[resource]\n'
		+ "+metadata/joinery_music = %s\n" % JSON.stringify(track)
	)
	(
		panel
		. show_file_review(
			"Write the harbour theme",
			diff,
			[
				{
					"path": "audio/music/harbour/main_pads.wav",
					"media": "audio",
					"after": stem,
				}
			],
			[
				{"path": "music/harbour.tres", "change": "created"},
				{"path": "audio/music/harbour/main_pads.wav", "change": "created"},
			]
		)
	)
	panel.show_glance({"facts": ["Pads −15.8 LUFS", "Mix −16.1 LUFS (target −16)"]})
	await _frames(4)
	var music: Control = panel._music
	_check(music.visible, "A music change shows its layers")
	var rows: Dictionary = music.call("rows")
	_check(rows.keys() == ["pads", "bass", "drums"], "One row per layer: " + str(rows.keys()))
	var title := (music.get_node("Track") as Label).text
	_check(title == "Harbour · 120 BPM · 8 bars · D minor · mix −16.1 LUFS", title)
	if rows.size() == 3:
		var level := func(layer: String) -> String:
			return (rows[layer].get_node("Level") as Label).text
		_check(level.call("pads") == "−6.0 dB", "The fader sets the level: " + level.call("pads"))
		_check(level.call("bass") == "Silent", "A muted layer is silent")
		_check(level.call("drums") == "−6.0 dB", "Half level is −6 dB: " + level.call("drums"))
		var loud := rows["pads"].get_node("Loudness") as Label
		_check(loud.visible and loud.text == "−15.8 LUFS", "Loudness joins its layer")
		var wave: Control = rows["pads"].get_node("Wave")
		_check(wave.get("duration") > 0.0, "The proposed stem is drawn")
		_check(wave.get("mini") == true, "as a small waveform")
	panel.show_file_review(
		"Make the door creak shorter",
		"",
		[],
		[{"path": "audio/sfx/door_creak.wav", "change": "modified"}]
	)
	_check(not music.visible, "Other changes show no music layers")
	panel.clear()
	await _frames()


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
	_check(not panel._media.sides().is_visible_in_tree(), "Text has no Before/After tags")
	_check(
		panel._summary.facts_text() == "+2 −1 lines · 1 model · 1 script",
		"Text facts: " + panel._summary.facts_text()
	)
	_check(not panel.details_open(), "Details stay closed with a preview")
	panel._details_button.button_pressed = true
	await _frames()
	_check(panel.details_open(), "The Details button opens them")
	_check_drawer(panel)
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
	_check(not panel.details_open(), "Details stay closed with media")
	_check(panel._media.files().visible, "A picture and text make a file list")
	_check(panel._media.files().rows().size() == 2, "One row per item")
	_check(panel._media.current_kind() == "image", "The picture comes first")
	panel._media.select_item(1)
	await _frames()
	_check(panel._media.current_kind() == "text", "A row shows its item")
	panel.show_file_review("Only a model went", "", [], [{"path": "a.glb", "change": "deleted"}])
	await _frames()
	_check(not panel._media.visible, "Nothing to preview hides the media")
	_check(panel.details_open(), "Details open when there is nothing to preview")
	_check(
		panel._drawer.fill and not panel._drawer.close_button().visible,
		"…as the whole stage, with nothing to close"
	)
	panel.set_details_open(true)
	await _frames()
	_check(panel.details_open(), "The preference opens details")
	panel.show_file_review(
		"And again",
		diff,
		[{"path": "art/textures/x.png", "media": "image", "after": _cache.path_join("x.png")}],
		[{"path": "scripts/door.gd", "change": "modified"}]
	)
	await _frames()
	_check(panel.details_open(), "The details preference holds for the next review")
	panel.set_details_open(false)


func _check_scene(panel: PANEL) -> void:
	panel.show_scene_review("Move the lamp up", 4)
	await _frames()
	var facts: String = panel._summary.facts_text()
	_check(facts == "4 scene nodes", "Scene counts: " + facts)
	_check(
		not panel._media.visible and panel._scene_note.visible, "Scene edits point at the viewport"
	)
	_check(not panel._media.sides().is_visible_in_tree(), "No tags for scene edits")
	_check(not panel._open_link.visible, "No Open in editor outside the editor")
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
	_check(_tag(panel, "before").is_visible_in_tree(), "The scene's sides can be flipped")


static func _tag(panel: PANEL, key: String) -> Button:
	return panel._media.sides().tag(key)


## The Before label lies on the left half of the split picture, After on the right.
static func _tags_on_their_sides(panel: PANEL) -> bool:
	var swipe: Control = panel._media.swipe()
	var sides: Control = panel._media.sides()
	var rects: Array[Rect2] = sides.tag_rects()
	if rects.size() != 2:
		return false
	var middle := swipe.get_global_rect().get_center().x
	var before := Rect2(sides.get_global_rect().position + rects[0].position, rects[0].size)
	var after := Rect2(sides.get_global_rect().position + rects[1].position, rects[1].size)
	var inside := swipe.get_global_rect().grow(1.0)
	return (
		before.end.x <= middle
		and after.position.x >= middle - 1.0
		and inside.encloses(before)
		and inside.encloses(after)
	)


## The pill sits in the top trailing corner of the stage, inside it.
static func _pill_in_corner(panel: PANEL) -> bool:
	var stage := panel._stage_area.get_global_rect()
	var pill: Rect2 = panel._media.sides().pill().get_global_rect()
	return (
		stage.grow(1.0).encloses(pill)
		and pill.get_center().x > stage.get_center().x
		and pill.get_center().y < stage.get_center().y
	)


## Stage and rail: the media fills the leading side at the full height; the rail on the trailing
## side holds the title over the facts, and the actions anchored at its foot: Details and the
## other links on one line, then Discard and Accept, Accept on the trailing edge.
func _check_rows(panel: PANEL) -> void:
	var stage := panel._stage_area.get_global_rect()
	var rail := panel._rail.get_global_rect()
	_check(stage.end.x <= rail.position.x, "The stage leads, the rail trails")
	_check(stage.size.x > rail.size.x * 1.8, "The stage takes most of the width")
	_check(absf(stage.size.y - rail.size.y) <= 1.0, "Both take the full height")
	var title := panel._title.get_global_rect()
	_check(rail.encloses(title), "The title is in the rail")
	var details := panel._details_button.get_global_rect()
	var footer := panel._footer.get_global_rect()
	_check(title.end.y <= footer.position.y, "The title sits above the actions")
	_check(footer.grow(1.0).encloses(details), "Details are a link beside the actions")
	_check(
		details.end.y <= panel._accept.get_global_rect().position.y + 1,
		"…on the line above Discard and Accept"
	)
	_check(absf(footer.end.y - rail.end.y) <= 1.0, "The actions are anchored at the rail's foot")
	_check(
		absf(panel._accept.get_global_rect().end.x - footer.end.x) <= 1.0,
		"Accept ends the actions row"
	)


## Details slide over the stage from the rail's side: the stage and the rail stay put.
func _check_drawer(panel: PANEL) -> void:
	var drawer := panel._drawer.get_global_rect()
	var stage := panel._stage_area.get_global_rect()
	_check(stage.grow(1.0).encloses(drawer), "The drawer lies over the stage")
	_check(absf(drawer.end.x - stage.end.x) <= 1.0, "…against the rail's side")
	_check(drawer.size.x < stage.size.x, "…leaving part of the stage in view")


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


## A picture whose edges are all different, like a real texture: it has no ground of its own.
func _busy(name: String, size: Vector2i, salt := 0) -> String:
	var image := Image.create(size.x, size.y, false, Image.FORMAT_RGB8)
	var noise := RandomNumberGenerator.new()
	noise.seed = 11 + salt
	for y: int in size.y:
		for x: int in size.x:
			image.set_pixel(x, y, Color(noise.randf(), noise.randf(), noise.randf()))
	var path := _cache.path_join(name)
	image.save_png(path)
	return path


## A model with its textures: one row per file down the leading side of the stage, the model
## first with its textures under it, whole names, a mark each, the shown row filled; Up and Down
## move through them once the review has the focus, and a long list scrolls. Narrow, the list
## folds above the preview.
func _check_files(panel: PANEL) -> void:
	var media: Array = []
	for index: int in 16:
		(
			media
			. append(
				{
					"path": "art/textures/agedgreytrim_albedo_%02d.png" % index,
					"media": "image",
					"before": _busy("tex-%d-a.png" % index, Vector2i(32, 32), index),
					"after": _busy("tex-%d-b.png" % index, Vector2i(32, 32), index + 40),
				}
			)
		)
	(
		media
		. append(
			{
				"path": "art/sources/crate.blend",
				"media": "image",
				"before": _image("crate-a.png", Color(0.3, 0.3, 0.32)),
				"before_back": _image("crate-a-back.png", Color(0.3, 0.3, 0.32)),
				"after": _image("crate-b.png", Color(0.4, 0.35, 0.3)),
				"after_back": _image("crate-b-back.png", Color(0.4, 0.35, 0.3)),
			}
		)
	)
	(
		media
		. append(
			{
				"path": "art/textures/crate_decal.png",
				"media": "image",
				"after": _image("decal.png", Color(0.8, 0.2, 0.1)),
			}
		)
	)
	panel.show_file_review("Weather the crate", "", media, [])
	await _frames(4)
	var files: ScrollContainer = panel._media.files()
	var rows: Array = files.rows()
	_check(files.visible and rows.size() == 18, "One row per file: %d" % rows.size())
	_check(rows[0].name_label.text == "crate.blend" and rows[0].depth == 0, "The model comes first")
	_check(
		rows.slice(1).all(func(row: Control) -> bool: return row.get("depth") == 1),
		"Its textures are grouped under it"
	)
	_check(panel._media.current_kind() == "model", "The model shows first")
	var label: Label = rows[1].name_label
	var whole: String = rows[1].plain_name()
	_check(whole == "agedgreytrim_albedo_00.png", "A row has the whole name: " + whole)
	_check(
		(
			label.text_overrun_behavior == TextServer.OVERRUN_NO_TRIMMING
			and label.get_visible_line_count() >= label.get_line_count()
		),
		"…never cut short"
	)
	_check(rows[1].mark_label.text == "Changed", "A changed file is marked Changed")
	_check(rows[17].mark_label.text == "New", "A new file is marked New")
	_check(rows[1].picture != null, "A texture's row shows its picture")
	_check(rows[0].button_pressed and not rows[1].button_pressed, "The shown row is selected")
	var list := files.get_global_rect()
	var frame: Rect2 = panel._media._frame.get_global_rect()
	_check(list.end.x <= frame.position.x, "The list runs down the left, the preview on the right")
	_check(frame.size.x > list.size.x, "The preview takes the larger part of the stage")
	_check(absf(list.size.y - panel._stage_area.size.y) <= 1.0, "The list takes the stage's height")
	_check(files.get_v_scroll_bar().max_value > files.size.y, "A long list scrolls")
	var down := InputEventKey.new()
	down.keycode = KEY_DOWN
	down.pressed = true
	var up := down.duplicate() as InputEventKey
	up.keycode = KEY_UP
	var focus := root.gui_get_focus_owner()
	if focus != null:
		focus.release_focus()
	panel._input(down)
	_check(panel._media.current_index() == 0, "Down does nothing until the review is clicked")
	panel.grab_focus()
	panel._input(down)
	await _frames()
	_check(panel._media.current_index() == 1, "Down shows the next file")
	_check(rows[1].button_pressed and not rows[0].button_pressed, "…and selects its row")
	_check(panel._media.current_kind() == "image", "The texture shows")
	panel._input(up)
	_check(panel._media.current_index() == 0, "Up goes back")
	for step: int in 20:
		panel._input(down)
	await _frames(3)
	_check(panel._media.current_index() == 17, "Down stops at the last file")
	_check(files.scroll_vertical > 0, "The shown row scrolls into view")
	panel.release_focus()
	_host.size = Vector2(380, 520)
	await _frames(4)
	list = files.get_global_rect()
	frame = panel._media._frame.get_global_rect()
	_check(panel._media.split().stacked, "Narrow, the list folds")
	_check(list.end.y <= frame.position.y, "…above the preview")
	_check(
		list.size.y <= panel._stage_area.size.y * 0.35 and list.size.y >= rows[0].size.y - 1.0,
		"…to a few rows: %s of %s" % [list.size.y, panel._stage_area.size.y]
	)
	_host.size = Vector2(900, 520)
	await _frames(4)
	_check(not panel._media.split().stacked, "Wide again, the list is beside the preview")


## A picture of another shape than the stage shows no empty bands: one with a background of its
## own fills the frame with it, one without sits on the panel. The Before/After pill never lies
## over the picture, square, tall or wide.
func _check_shapes(panel: PANEL) -> void:
	var cases := [
		["square", Vector2i(64, 64)],
		["tall", Vector2i(40, 120)],
		["wide", Vector2i(160, 90)],
		["new tall", Vector2i(30, 120)],
	]
	for spec: Array in cases:
		var size: Vector2i = spec[1]
		var item := {
			"path": "art/textures/%s.png" % str(spec[0]).replace(" ", "_"),
			"media": "image",
			"after": _busy("shape-%s-b.png" % spec[0], size, 3),
		}
		if not str(spec[0]).begins_with("new"):
			item["before"] = _busy("shape-%s-a.png" % spec[0], size, 5)
		panel.show_file_review("Retexture", "", [item], [])
		await _frames(6)
		var swipe: Control = panel._media.swipe()
		var picture: Rect2 = swipe.call("picture_rect")
		picture.position += swipe.get_global_rect().position
		var pill: Rect2 = panel._media.sides().pill().get_global_rect()
		_check(
			panel._media.frame_ground().a <= 0.0,
			"%s: a picture without a ground leaves the frame unfilled" % spec[0]
		)
		_check(
			not picture.grow(-0.5).intersects(pill),
			"%s: the pill keeps off the picture: %s and %s" % [spec[0], pill, picture]
		)
		_check(
			picture.size.y >= panel._stage_area.size.y * 0.5,
			"%s: the picture still fills the stage: %s" % [spec[0], picture]
		)
		if spec[0] == "wide":
			_check(panel._media.pill_reserve() == 0.0, "A wide picture leaves room: no strip")
	var ground := Color8(51, 102, 77)
	var flat := _image("flat.png", ground, Vector2i(48, 48))
	panel.show_file_review(
		"Retint", "", [{"path": "art/textures/flat.png", "media": "image", "after": flat}], []
	)
	await _frames(4)
	_check(
		panel._media.frame_ground().to_html(false) == ground.to_html(false),
		"A picture on a flat background fills the frame with it"
	)
