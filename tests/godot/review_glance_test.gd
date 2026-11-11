extends SceneTree
## The review at a glance, headless: the facts line (the agent's words, what the media
## measures, the counts), the checks as one status, the reviewer's notes as one line with the
## rest in Details, the keyboard (Enter, Backspace / Delete, A / B) only with focus in the
## review, a filmstrip for several items with the most important first, a model from two
## angles, Accept on screen at a narrow width, and the chat card's row that decides a small
## change and offers Review for a big one.

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
	_check(not panel._details_scroll.visible, "Notes never open Details by themselves")
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
	panel.show_file_review("Darken the crate", "", media, [])
	await _frames()
	field.grab_focus()
	_key(KEY_ENTER)
	await _frames()
	_check(accepted[0] == 0 and panel.pending, "Enter elsewhere never accepts")
	panel.grab_focus()
	_key(KEY_B)
	await _frames()
	_check(panel._media.side == "before", "B flips a split picture to the original")
	_check(panel._before.button_pressed, "The switch follows the key")
	_key(KEY_A)
	await _frames()
	_check(panel._media.side == "after", "A flips back to the change")
	panel._reject.grab_focus()
	_key(KEY_ENTER)
	await _frames()
	_check(accepted[0] == 1 and rejected[0] == 0, "Enter accepts, even on Discard")
	panel.show_error("Try again")
	panel._accept.grab_focus()
	_key(KEY_DELETE)
	await _frames()
	_check(rejected[0] == 1, "Delete discards")
	panel.show_error("Try again")
	panel.grab_focus()
	_key(KEY_BACKSPACE)
	await _frames()
	_check(rejected[0] == 2, "Backspace discards")
	panel.set_applying(false)
	await _frames()
	field.queue_free()


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
	var strip: Control = panel._media.filmstrip()
	_check(strip.visible, "Several items show a filmstrip")
	_check(strip.tiles().size() == 4, "One tile each, text last: %d" % strip.tiles().size())
	_check(strip.selected() == 0 and panel._media.current_index() == 0, "The first item shows")
	_check(strip.tiles()[0].picture != null, "A picture tile shows the change")
	_check(strip.tiles()[2].glyph == "sound", "A sound tile shows its glyph")
	_check(strip.tiles()[3].word == ".gd", "The text tile names its file type")
	strip.tiles()[1].pressed.emit()
	await _frames()
	_check(panel._media.current_index() == 1, "Pressing a tile shows its item")
	_check(panel._new_chip.visible and not panel._segment.visible, "A new item is marked New")
	var glance := panel.glance()
	_check(not bool(glance.get("simple", true)), "Several assets open the panel")
	_check(glance.get("thumbnail") != null, "The card gets a picture")


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
	_check(panel._after.button_pressed, "A model opens on its After")
	_check(panel._media.far_texture() != null, "The far side shows beside the front")
	var front: Texture2D = panel._media.texture()
	panel.flip()
	await _frames()
	_check(panel._media.side == "before", "Flip shows the original model")
	_check(panel._media.texture() != front, "Both angles swap to the original")
	_check(panel._media.far_texture() != null, "The original's far side shows too")


func _check_narrow(panel: PANEL) -> void:
	_host.size = Vector2(340, 420)
	await _frames(4)
	_check(panel._accept.is_visible_in_tree(), "Accept shows at a narrow width")
	var inside := panel.get_global_rect().grow(1.0)
	_check(
		inside.encloses(panel._accept.get_global_rect()),
		"Accept stays inside a narrow panel: %s in %s" % [panel._accept.get_global_rect(), inside]
	)
	_check(
		inside.encloses(panel._details_button.get_global_rect()), "Details stay inside the panel"
	)
	_host.size = Vector2(900, 560)
	await _frames()


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
