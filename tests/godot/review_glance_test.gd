extends SceneTree
## The review at a glance, headless: the facts line (the agent's words, what the media
## measures, the counts), the checks as chips, the reviewer's notes as one line with the rest
## in Details, the keyboard (A, D, B, Escape) only once the review has the focus, a file list
## for several items with the model first and its textures under it, a model from two angles,
## a blockout's plan beside the sketch it was built from, the rail stacked under the stage at a
## narrow width with Accept on screen, and the chat card's row that decides a small change and
## offers Review for a big one.

const PANEL := preload("res://addons/joinery/review_panel.gd")
const GLANCE := preload("res://addons/joinery/review_glance.gd")
const CARD := preload("res://addons/joinery/sidebar_change_card.gd")

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
	_cache = OS.get_cache_dir().path_join("joinery-review-glance-test")
	DirAccess.make_dir_recursive_absolute(_cache)
	var config := _cache.path_join("journeyman.cfg")
	DirAccess.remove_absolute(config)
	Engine.set_meta("joinery_ui_config", config)
	Engine.remove_meta("joinery_ui_prefs")
	root.size = Vector2i(900, 560)
	_host = Control.new()
	_host.size = Vector2(900, 560)
	root.add_child(_host)
	_check_words()
	var panel: PANEL = PANEL.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(panel)
	await _frames()
	await _check_facts_and_notes(panel)
	await _check_keyboard(panel)
	await _check_filmstrip(panel)
	await _check_model(panel)
	await _check_blockout(panel)
	await _check_scaling(panel)
	await _check_narrow(panel)
	await _check_card(panel)
	panel.clear()
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	Engine.remove_meta("joinery_ui_prefs")
	await _frames()
	print("review glance: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _image(name: String, tint: Color, size := Vector2i(64, 64)) -> String:
	var image := Image.create(size.x, size.y, false, Image.FORMAT_RGBA8)
	image.fill(tint)
	var path := _cache.path_join(name)
	image.save_png(path)
	return path


func _check_words() -> void:
	var dark := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	dark.fill(Color(0.3, 0.2, 0.1))
	var light := Image.create(16, 16, false, Image.FORMAT_RGBA8)
	light.fill(Color(0.7, 0.5, 0.3))
	_check(
		GLANCE.image_facts("wood", light, dark) == ["wood darker, 16 → 8 px"],
		"Image facts: " + str(GLANCE.image_facts("wood", light, dark))
	)
	var grey := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	grey.fill(Color(0.5, 0.5, 0.5))
	var blue := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	blue.fill(Color(0.3, 0.4, 0.6))
	var orange := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	orange.fill(Color(0.6, 0.4, 0.25))
	_check(GLANCE.image_facts("a", grey, grey).is_empty(), "No change, no fact")
	_check(
		GLANCE.image_facts("sky", orange, blue)[0].contains("cooler"),
		"Hue towards blue reads cooler"
	)
	_check(GLANCE.audio_fact("creak", 1.42, 0.9) == "creak 1.4 → 0.9 s", "Sound length fact")
	_check(GLANCE.audio_fact("creak", 1.0, 1.05).is_empty(), "A tiny length change is no fact")
	_check(GLANCE.plain_name("art/textures/door_wood-02.png") == "door wood 02", "Plain name")
	var review := {
		"facts": ["door 20% wider", "wood darker", "hinge moved", "a fourth"],
		"changes": [{"path": "a.gd", "change": "modified"}, {"path": "b.gd", "change": "created"}],
	}
	_check(
		GLANCE.facts_line(review) == "Door 20% wider · wood darker · hinge moved · 2 scripts",
		"Agent facts first, three at most: " + GLANCE.facts_line(review)
	)
	var checks := {"checks": {"ran": ["references", "art bible", "tests"], "skipped": []}}
	_check(
		GLANCE.checks_detail(checks) == "Checked references, the art bible and tests.",
		"Checks detail: " + GLANCE.checks_detail(checks)
	)
	var skipped := {"checks": {"ran": ["references"], "skipped": ["tests"]}}
	_check(GLANCE.checks_detail(skipped) == "Checked references. No tests to run.", "Skipped tests")
	var notes := (
		GLANCE
		. sorted_notes(
			[
				{"severity": "info", "text": "Nice bevels"},
				{"severity": "block", "text": "Door clips the wall", "file": "scenes/room.tscn"},
				{"severity": "warn", "text": "Texel density low"},
				"not a note",
			]
		)
	)
	_check(notes.size() == 3 and notes[0]["severity"] == "block", "Notes sort worst first")
	_check(GLANCE.notes_line(notes) == "Door clips the wall · 2 more", "Notes as one line")
	_check(not GLANCE.is_simple({}, 1, notes), "A blocking note is not simple")
	_check(GLANCE.is_simple({}, 1, []), "One asset is simple")
	_check(not GLANCE.is_simple({}, 2, []), "Two assets are not simple")


func _check_facts_and_notes(panel: PANEL) -> void:
	var before := _image("wood-before.png", Color(0.7, 0.5, 0.3))
	var after := _image("wood-after.png", Color(0.35, 0.24, 0.12))
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
	var facts: String = panel._summary.facts_text()
	_check(facts == "Door wood darker · 1 image · 2 files", "Measured facts: " + facts)
	_check(panel._summary.status_text() == "Checks passed", "The checks are one quiet status")
	_check(panel._summary.notes_text().is_empty(), "No notes line without notes")
	_check(panel._summary.note_chips().is_empty(), "No note chips without notes")
	(
		panel
		. show_glance(
			{
				"facts": ["wood darker"],
				"checks": {"ran": ["references", "provenance"], "skipped": []},
				"reviewer_status": "running",
			}
		)
	)
	await _frames()
	facts = panel._summary.facts_text()
	_check(facts == "Wood darker · 1 image · 2 files", "Agent facts replace measured: " + facts)
	_check(
		panel._summary.status_text() == "Checks passed · Reviewer checking…",
		"A running reviewer joins the status: " + panel._summary.status_text()
	)
	(
		panel
		. show_glance(
			{
				"review_notes":
				[
					{"severity": "warn", "text": "The grain runs across the planks"},
					{"severity": "info", "text": "Matches the art bible palette"},
				],
				"reviewer_status": "done",
			}
		)
	)
	await _frames()
	_check(
		panel._summary.notes_text() == "The grain runs across the planks · 1 more",
		"Notes as one line: " + panel._summary.notes_text()
	)
	_check(panel._summary.status_text() == "Checks passed", "A finished reviewer drops off")
	_check(
		panel._summary.note_chips() == ["1 warning", "1 note"],
		"A chip per kind of note: %s" % [panel._summary.note_chips()]
	)
	_check(not panel.details_open(), "Notes never open Details by themselves")
	panel._details_button.button_pressed = true
	await _frames()
	var info: Control = panel._summary.info()
	_check(info.is_visible_in_tree(), "Details hold the checks and notes")
	var texts: Array = info.find_children("*", "Label", true, false).map(
		func(label: Label) -> String: return label.text
	)
	_check("Matches the art bible palette" in texts, "Every note is in Details: " + str(texts))
	_check(
		"Checked references and provenance." in texts, "The checks are in Details: " + str(texts)
	)
	var glance := panel.glance()
	_check(not bool(glance.get("simple", true)), "A warning keeps the change in the panel")
	panel._details_button.button_pressed = false


func _key(code: Key) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = true
	root.push_input(event)
	var up := event.duplicate() as InputEventKey
	up.pressed = false
	root.push_input(up)


func _check_keyboard(panel: PANEL) -> void:
	var accepted: Array[int] = [0]
	var rejected: Array[int] = [0]
	panel.accept_pressed.connect(func() -> void: accepted[0] += 1)
	panel.reject_pressed.connect(func() -> void: rejected[0] += 1)
	var before := _image("crate-before.png", Color(0.6, 0.5, 0.4))
	var after := _image("crate-after.png", Color(0.3, 0.25, 0.2))
	var media := [
		{"path": "art/textures/crate.png", "media": "image", "before": before, "after": after}
	]
	var field := LineEdit.new()
	_host.add_child(field)
	field.grab_focus()
	panel.show_file_review("Darken the crate", "", media, [])
	await _frames()
	_check(field.has_focus(), "A review that opens takes no focus")
	_key(KEY_A)
	await _frames()
	_check(accepted[0] == 0 and panel.pending, "A elsewhere never accepts")
	field.release_focus()
	_key(KEY_A)
	await _frames()
	_check(accepted[0] == 0 and panel.pending, "Without a click in the review, A does nothing")
	_click(panel)
	await _frames()
	_check(panel.has_focus(), "A click in the review gives it the keys")
	_key(KEY_B)
	await _frames()
	_check(panel._media.side == "before", "B flips a split picture to the original")
	_check(panel._media.sides().tag("before").button_pressed, "The pill follows the key")
	_key(KEY_B)
	await _frames()
	_check(panel._media.side == "after", "B flips back to the change")
	panel._details_button.button_pressed = true
	await _frames()
	panel.grab_focus()
	_key(KEY_ESCAPE)
	await _frames()
	_check(not panel.details_open(), "Escape closes Details")
	_check(panel._accept.tooltip_text.ends_with("(A)"), "Accept's tooltip names its key")
	_check(panel._reject.tooltip_text.ends_with("(D)"), "Discard's tooltip names its key")
	_key(KEY_A)
	await _frames()
	_check(accepted[0] == 1 and rejected[0] == 0, "A accepts")
	panel.show_error("Try again")
	panel._accept.grab_focus()
	_key(KEY_D)
	await _frames()
	_check(rejected[0] == 1, "D discards, with the focus on any of the review's controls")
	panel.show_error("Try again")
	panel.grab_focus()
	_key(KEY_ENTER)
	_key(KEY_DELETE)
	await _frames()
	_check(accepted[0] == 1 and rejected[0] == 1, "Enter and Delete decide nothing")
	var steps: Array[int] = []
	panel.step_requested.connect(func(step: int) -> void: steps.append(step))
	_key(KEY_RIGHT)
	await _frames()
	_check(steps.is_empty(), "Left and Right go nowhere without other changes waiting")
	panel.set_queue(1, 3)
	_key(KEY_RIGHT)
	_key(KEY_LEFT)
	await _frames()
	_check(steps == [1, -1], "Right and Left step through the changes waiting: %s" % [steps])
	panel.set_queue(0, -1)
	panel.set_applying(false)
	await _frames()
	field.queue_free()


## A left click on [param control], as the viewport would deliver it.
func _click(control: Control) -> void:
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	click.position = control.get_global_rect().get_center()
	control.call("_gui_input", click)


func _check_filmstrip(panel: PANEL) -> void:
	var media := [
		{
			"path": "art/textures/door_wood.png",
			"media": "image",
			"before": _image("w1.png", Color(0.7, 0.5, 0.3)),
			"after": _image("w2.png", Color(0.4, 0.3, 0.2)),
		},
		{
			"path": "art/sources/door.blend",
			"media": "image",
			"after": _image("door.png", Color(0.5, 0.5, 0.5)),
		},
		{
			"path": "audio/sfx/door_creak.wav",
			"media": "audio",
			"after": ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav"),
		},
	]
	var diff := "--- scripts/door.gd\n+++ scripts/door.gd\n@@ -1 +1 @@\n-var w := 1.0\n+var w := 1.2"
	panel.show_file_review("Widen the door", diff, media, [])
	await _frames()
	var files: Control = panel._media.files()
	var rows: Array = files.rows()
	_check(files.visible, "Several items show a file list")
	_check(rows.size() == 4, "One row each, text last: %d" % rows.size())
	_check(files.selected() == 0 and panel._media.current_index() == 0, "The first item shows")
	_check(rows[0].name_label.text == "door.blend", "The model comes first")
	_check(rows[1].depth == 1, "Its texture is grouped under it")
	_check(rows[1].picture != null, "A picture row shows the change")
	_check(rows[2].glyph == "sound", "A sound row shows its glyph")
	_check(rows[3].word == ".gd", "The text row names its file type")
	rows[1].pressed.emit()
	await _frames()
	_check(panel._media.current_index() == 1, "Pressing a row shows its file")
	rows[0].pressed.emit()
	await _frames()
	var sides: Control = panel._media.sides()
	_check(
		sides.tag("after").text == "New" and not sides.tag("before").is_visible_in_tree(),
		"A new item is tagged New on the media"
	)
	var glance := panel.glance()
	_check(not bool(glance.get("simple", true)), "Several assets open the panel")
	_check(glance.get("thumbnail") != null, "The card gets a picture")


## The wheel zooms both sides about the pointer, a drag pans both, a double-click fits again;
## nothing here takes the keyboard focus.
func _check_zoom(swipe: Control) -> void:
	var focus_before := swipe.get_viewport().gui_get_focus_owner()
	var fitted: Rect2 = swipe.call("picture_rect")
	var at := fitted.get_center() + Vector2(fitted.size.x * 0.25, 0.0)
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_UP
	wheel.pressed = true
	wheel.position = at
	wheel.factor = 1.0
	for notch: int in 4:
		swipe.call("_gui_input", wheel)
	var view: RefCounted = swipe.get("view")
	_check(view.call("zoomed"), "The wheel zooms in")
	var drawn: Rect2 = view.call("view", fitted)
	var spot := (at - fitted.position) / fitted.size
	_check(
		(at - drawn.position).distance_to(spot * drawn.size) < 1.0,
		"The point under the pointer stays put: %s" % drawn
	)
	_check(swipe.texture_filter == CanvasItem.TEXTURE_FILTER_NEAREST, "Zoomed pixel art is crisp")
	var press := InputEventMouseButton.new()
	press.button_index = MOUSE_BUTTON_LEFT
	press.pressed = true
	press.position = fitted.position + Vector2(4.0, 4.0)
	swipe.call("_gui_input", press)
	var drag := InputEventMouseMotion.new()
	drag.relative = Vector2(10.0, 6.0)
	drag.position = press.position + drag.relative
	drag.button_mask = MOUSE_BUTTON_MASK_LEFT
	swipe.call("_gui_input", drag)
	press.pressed = false
	swipe.call("_gui_input", press)
	var panned: Rect2 = view.call("view", fitted)
	_check(panned.position.is_equal_approx(drawn.position + drag.relative), "A drag pans")
	_check(is_equal_approx(float(swipe.get("split")), 0.5), "…and leaves the line where it was")
	# Both sides are drawn from the same view, so they stay lined up while zoomed.
	_check(swipe.get("before") != null and swipe.get("after") != null, "Both sides zoom together")
	var twice := InputEventMouseButton.new()
	twice.button_index = MOUSE_BUTTON_LEFT
	twice.pressed = true
	twice.double_click = true
	twice.position = at
	swipe.call("_gui_input", twice)
	twice.pressed = false
	twice.double_click = false
	swipe.call("_gui_input", twice)
	_check(not view.call("zoomed"), "A double-click fits the picture again")
	_check(
		swipe.get_viewport().gui_get_focus_owner() == focus_before,
		"Zooming and panning by event take no focus"
	)


func _check_model(panel: PANEL) -> void:
	var media := [
		{
			"path": "art/sources/door.blend",
			"media": "image",
			"before": _image("m-front-a.png", Color(0.4, 0.4, 0.4)),
			"before_back": _image("m-back-a.png", Color(0.3, 0.3, 0.3)),
			"after": _image("m-front-b.png", Color(0.6, 0.4, 0.3)),
			"after_back": _image("m-back-b.png", Color(0.5, 0.3, 0.2)),
		}
	]
	panel.show_file_review("Widen the door", "", media, [])
	await _frames()
	_check(panel._media.current_kind() == "model", "A render with a far side is a model")
	_check(not panel._media.swipe().visible, "Models do not split")
	var tags: Control = panel._media.sides()
	_check(tags.tag("after").button_pressed, "A model opens on its After")
	_check(not tags.both_shown, "Model renders show one side at a time: the tags sit together")
	_check(panel._media.far_texture() != null, "The far side shows beside the front")
	var front: Texture2D = panel._media.texture()
	panel.flip()
	await _frames()
	_check(panel._media.side == "before", "Flip shows the original model")
	_check(panel._media.texture() != front, "Both angles swap to the original")
	_check(panel._media.far_texture() != null, "The original's far side shows too")


## A blockout (ADR 0043): the user's sketch on the left, the plan built from it on the right;
## an earlier plan flips in place, and pixels of a plan are no facts.
func _check_blockout(panel: PANEL) -> void:
	var sketch := _image("sketch.png", Color(0.95, 0.94, 0.9), Vector2i(90, 60))
	var plan_before := _image("plan-a.png", Color(0.2, 0.21, 0.24), Vector2i(80, 60))
	var plan_after := _image("plan-b.png", Color(0.3, 0.32, 0.36), Vector2i(80, 60))
	var media := [
		{
			"path": "design/blockouts/keep.toml",
			"media": "image",
			"label": "Blockout",
			"before": plan_before,
			"after": plan_after,
			"reference": sketch,
		}
	]
	panel.show_file_review("Build the keep from my sketch", "", media, [])
	await _frames()
	var shown: Texture2D = panel._media.texture()
	_check(not panel._media.swipe().visible, "A blockout does not split")
	_check(shown != null and shown.get_width() == 90, "The sketch shows on the left")
	var plan: Texture2D = panel._media.far_texture()
	_check(plan != null and plan.get_width() == 80, "The plan shows beside the sketch")
	_check(panel._media.measured_facts().is_empty(), "A plan's pixels make no facts")
	panel.flip()
	await _frames()
	_check(panel._media.side == "before", "Flip shows the earlier plan")
	_check(panel._media.texture().get_width() == 90, "The sketch stays")
	_check(panel._media.far_texture() != plan, "The plan beside it is the earlier one")
	_check(panel.glance().get("thumbnail") != null, "The card gets the plan's picture")


## A noisy picture, like a photo or a render: many colours.
func _photo(name: String, size: Vector2i) -> String:
	var image := Image.create(size.x, size.y, false, Image.FORMAT_RGB8)
	var noise := RandomNumberGenerator.new()
	noise.seed = 7
	for y: int in size.y:
		for x: int in size.x:
			image.set_pixel(x, y, Color(noise.randf(), noise.randf(), noise.randf()))
	var path := _cache.path_join(name)
	image.save_png(path)
	return path


## Small pictures fill the frame, crisp; photos smooth; a pair shares the width; the frame
## grows with the panel's width, and stays shorter under a row of ways.
func _check_scaling(panel: PANEL) -> void:
	var media := [
		{
			"path": "art/textures/tile.png",
			"media": "image",
			"before": _image("px-a.png", Color(0.7, 0.5, 0.3), Vector2i(16, 16)),
			"after": _image("px-b.png", Color(0.4, 0.3, 0.2), Vector2i(16, 16)),
		},
		{
			"path": "art/photo.png",
			"media": "image",
			"after": _photo("photo.png", Vector2i(400, 300))
		},
	]
	panel.show_file_review("Retint the tile", "", media, [])
	await _frames(3)
	var swipe: Control = panel._media.swipe()
	var frame := float(panel._media.frame_height())
	_check(
		frame >= panel._stage_area.size.y * 0.7,
		"The frame fills the stage: %.0f of %.0f" % [frame, panel._stage_area.size.y]
	)
	var shown: Rect2 = swipe.call("fit", Vector2(16, 16))
	_check(
		(
			(shown.size.y >= swipe.size.y - 1.0 or shown.size.x >= swipe.size.x - 1.0)
			and shown.size.y > 16.0 * 4.0
		),
		"A 16 px texture fills the frame: %s in %s" % [shown.size, swipe.size]
	)
	_check(swipe.texture_filter == CanvasItem.TEXTURE_FILTER_NEAREST, "…and stays crisp")
	var rows: Array = panel._media.files().rows()
	_check(
		rows[0].thumb.texture_filter == CanvasItem.TEXTURE_FILTER_NEAREST, "Its row is crisp too"
	)
	await _check_zoom(swipe)
	panel._media.select_item(1)
	await _frames(3)
	# A new picture shows alone in the same zoomable view, without a line.
	var photo: Rect2 = swipe.call("picture_rect")
	_check(swipe.visible and swipe.before == null, "A new picture shows alone")
	_check(
		photo.size.y >= frame - 1.0 or photo.size.x >= swipe.size.x - 1.0,
		"A photo fills the frame: %s in %s" % [photo.size, swipe.size]
	)
	_check(swipe.texture_filter == CanvasItem.TEXTURE_FILTER_LINEAR, "…smoothly")
	var blockout := [
		{
			"path": "design/blockouts/keep.toml",
			"media": "image",
			"after": _image("plan-s.png", Color(0.3, 0.32, 0.36), Vector2i(60, 80)),
			"reference": _image("sketch-s.png", Color(0.95, 0.94, 0.9), Vector2i(60, 80)),
		}
	]
	panel.show_file_review("Build the keep", "", blockout, [])
	await _frames(3)
	var view = panel._media._front
	var sketch: TextureRect = view.main
	var plan: TextureRect = view.far
	frame = float(panel._media.frame_height())
	_check(
		sketch.size.y >= frame - 1.0 and sketch.size.x * 2.0 >= view.size.x - 8.0,
		"The sketch and the plan fill the frame: %s in %s" % [sketch.size, view.size]
	)
	_check(absf(plan.size.x - sketch.size.x) <= 1.0, "The plan shares the space equally")
	_check(sketch.texture_filter == CanvasItem.TEXTURE_FILTER_NEAREST, "A small sketch is crisp")
	_check(
		panel._media.frame_ground().a <= 0.0,
		"A sketch and a plan with different grounds leave the frame unfilled: no bands"
	)
	var backdrop := Color8(56, 59, 66)
	var model := [
		{
			"path": "art/sources/hammer.blend",
			"media": "image",
			"after": _image("h-front.png", backdrop, Vector2i(96, 96)),
			"after_back": _image("h-back.png", backdrop, Vector2i(96, 96)),
		}
	]
	panel.show_file_review("Make a hammer", "", model, [])
	await _frames(3)
	_check(
		panel._media.frame_ground().to_html(false) == backdrop.to_html(false),
		"Renders on one backdrop fill the frame with it: no empty bands"
	)
	_check(
		panel._media._frame.get_theme_stylebox("panel").get_margin(SIDE_LEFT) <= 0.0,
		"The stage is the picture: no frame inside it"
	)


func _check_narrow(panel: PANEL) -> void:
	# The sidebar's width, and the bottom panel at its usual short height.
	for spec: Array in [[Vector2(340, 420), "narrow"], [Vector2(900, 300), "short"]]:
		_host.size = spec[0]
		await _frames(4)
		var name: String = spec[1]
		_check(panel._accept.is_visible_in_tree(), "%s: Accept shows" % name)
		var inside := panel.get_global_rect().grow(1.0)
		for part: Control in [panel._accept, panel._reject, panel._details_button]:
			_check(
				inside.encloses(part.get_global_rect()),
				"%s: %s stays inside: %s in %s" % [name, part.name, part.get_global_rect(), inside]
			)
		_check(
			panel._body.narrow == (name == "narrow"), "%s: the rail stacks only when narrow" % name
		)
		_check_stacked(panel, name)
	_host.size = Vector2(900, 560)
	await _frames()


## Narrow, the rail stacks under the stage, which keeps at least 55% of the height; wide, they
## sit side by side at the full height. Accept and Discard keep their text whole.
func _check_stacked(panel: PANEL, name: String) -> void:
	var stage := panel._stage_area.get_global_rect()
	var rail := panel._rail.get_global_rect()
	if panel._body.narrow:
		_check(stage.end.y <= rail.position.y, "%s: the rail sits under the stage" % name)
		_check(
			stage.size.y >= panel._body.size.y * 0.55 - 1.0,
			(
				"%s: the stage keeps most of the height: %.0f of %.0f"
				% [name, stage.size.y, panel._body.size.y]
			)
		)
	else:
		_check(stage.end.x <= rail.position.x, "%s: the rail sits beside the stage" % name)
		_check(
			absf(stage.size.y - panel._body.size.y) <= 1.0, "%s: the stage is full height" % name
		)
	for button: Button in [panel._accept, panel._reject]:
		var font := button.get_theme_font("font")
		var text := font.get_string_size(
			button.text, HORIZONTAL_ALIGNMENT_LEFT, -1, button.get_theme_font_size("font_size")
		)
		_check(button.size.x >= text.x, "%s: %s is not squeezed" % [name, button.text])


func _check_card(panel: PANEL) -> void:
	var card: VBoxContainer = CARD.new("Darken the crate")
	_host.add_child(card)
	var media := [
		{
			"path": "art/textures/crate.png",
			"media": "image",
			"before": _image("c-before.png", Color(0.6, 0.5, 0.4)),
			"after": _image("c-after.png", Color(0.3, 0.25, 0.2)),
		}
	]
	panel.show_file_review("Darken the crate", "", media, [])
	card.set_ready(media, panel)
	await _frames()
	var row: Control = card.review_row()
	_check(row.visible and row.call("thumbnail") != null, "The card shows a tiny picture")
	_check(bool(row.call("decidable")), "A small change is decided from the card")
	_check(row._line.text == "Crate darker · 1 image", "The card's one line: " + row._line.text)
	# The request's parts, ticked: quiet lines above the change's row, blanks dropped.
	card.set_done(["Made the mug in Blender", " ", "Put it on Table in kitchen.tscn"])
	await _frames()
	_check(
		(
			card.done_lines()
			== PackedStringArray(["Made the mug in Blender", "Put it on Table in kitchen.tscn"])
		),
		"The card lists each part of the request: " + str(card.done_lines())
	)
	var done_list: Control = card._done_list
	_check(done_list.visible and row.is_greater_than(done_list), "Listed above the row")
	var first: Label = done_list.get_child(0).get_child(1)
	_check(first.theme_type_variation == "JoineryCaption", "The list is quiet caption text")
	card.set_done([])
	_check(not done_list.visible, "An empty list hides it")
	var accepted: Array[int] = [0]
	panel.accept_pressed.connect(func() -> void: accepted[0] += 1)
	row._accept.pressed.emit()
	_check(accepted[0] == 1 and not panel.pending or panel._state == "deciding", "Card accepts")
	panel.show_error("Try again")
	panel.show_glance({"review_notes": [{"severity": "block", "text": "Clips the wall"}]})
	await _frames()
	_check(not bool(row.call("decidable")), "A blocking note takes the decision to the panel")
	_check(row._open.visible, "The card offers Review instead")
	var shown: Array[int] = [0]
	panel.show_requested.connect(func() -> void: shown[0] += 1)
	row._open.pressed.emit()
	_check(shown[0] == 1, "Review brings the panel into view")
	card.queue_free()
