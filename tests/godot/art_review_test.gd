extends SceneTree
## The art direction dialog: the five decided sections, the references board with its images,
## bible-check offers that fill the composer, the upgrade offer for a first-format bible, and
## "Approve these rules".

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const DIALOG_PATH := "res://addons/joinery/art_review_dialog.gd"
const MOTION_PATH := "res://addons/joinery/ui_motion.gd"
const PHOTO := "art/reference/quarry.png"
const BIBLE := """# Art Bible — Quarry

## Vision
Calm, a little lonely, handmade.

## Visual style
- Overall look: stylised PBR, chunky bevels, soft rim light

## Palette
Warm wood, sea blue accent.

## Audio
Soft synths, wooden foley.

## Camera
> e.g. Perspective, FOV 70
Perspective, FOV 60, 5 m behind.

## Never
- Neon, photo-real faces, gore
"""

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _event(version: int) -> Dictionary:
	return {
		"kind": "art_review",
		"text": BIBLE,
		"sha256": "abc",
		"approved": false,
		"version": version,
		"references":
		[
			{
				"id": "R1",
				"reference": "my quarry photo",
				"kind": "photo",
				"take": "warm stone against a cold sky",
				"avoid": "the haze",
				"file": PHOTO
			},
			{
				"id": "R2",
				"reference": "Hob",
				"kind": "game",
				"take": "chunky silhouettes",
				"avoid": "its saturated greens",
				"file": ""
			},
		],
		"suggestions":
		[
			"Before approving the bible: the palette section is not set yet. " + "Ask about it.",
			"This bible uses the first format. Offer the upgrade: it keeps every line.",
			"When adding sound: the audio section is open. Ask about it.",
		],
	}


func _run() -> void:
	load(MOTION_PATH).set_enabled(false)
	root.size = Vector2i(1100, 900)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://art/reference"))
	var picture := Image.create(64, 48, false, Image.FORMAT_RGBA8)
	picture.fill(Color(0.8, 0.6, 0.4))
	picture.save_png(ProjectSettings.globalize_path("res://" + PHOTO))
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_state("connected", "")
	var dialog_script: Script = load(DIALOG_PATH)
	var approved := [0]
	var approve := func() -> void: approved[0] += 1

	# A first-format bible: the upgrade offer, the board, the offers, the decided sections.
	var dialog: ConfirmationDialog = dialog_script.open(
		chat, _event(1), Vector2i(900, 700), approve
	)
	await process_frame
	_check(
		dialog.get_ok_button().text == "Approve these rules", "The button reads Approve these rules"
	)
	var board := dialog.find_child("Board", true, false)
	_check(board != null and board.get_child_count() == 2, "The references board lists both")
	if board != null and board.get_child_count() == 2:
		var photo := board.get_child(0)
		var image := photo.find_child("Picture", true, false) as TextureRect
		_check(image != null and image.texture != null, "A reference image shows on the board")
		_check(image.texture.get_width() == 64, "The board shows the file from art/reference")
		var labels: Array[String] = []
		for label: Node in photo.find_children("*", "Label", true, false):
			labels.append((label as Label).text)
		_check(labels.has("R1 · my quarry photo"), "Each image has its caption")
		_check(
			labels.has("Take: warm stone against a cold sky\nNot: the haze"),
			"The caption says what to take and what to leave"
		)
		var game := board.get_child(1).find_child("Picture", true, false) as TextureRect
		_check(game.texture == null, "A reference without a picture keeps its place")
	var rules := dialog.find_child("Rules", true, false)
	var feel := rules.find_child("Feel", true, false) as RichTextLabel
	_check(feel != null and feel.text.contains("Calm, a little lonely"), "Feel shows the vision")
	var never := rules.find_child("Never", true, false) as RichTextLabel
	_check(never != null and never.text.contains("Neon"), "Never shows the hard bans")
	var technical := dialog.find_child("Technical", true, false)
	_check(technical != null and not technical.body.visible, "Technical rules start folded")
	_check(
		technical.header.text == "Technical rules (preset for you)",
		"The technical rules say they were preset"
	)
	var upgrade := dialog.find_child("Upgrade", true, false)
	_check(
		upgrade != null and upgrade.is_visible_in_tree(), "A first-format bible offers the update"
	)
	var update := upgrade.find_child("Update", true, false) as Button
	_check(update.text == "Update to the new layout", "The update says what it does")
	var upgrade_texts: Array[String] = []
	for label: Node in upgrade.find_children("*", "Label", true, false):
		upgrade_texts.append((label as Label).text)
	_check(
		upgrade_texts.any(func(text: String) -> bool: return text.contains("approve it again")),
		"The update says approval is needed again"
	)
	var offers := dialog.find_child("Offers", true, false)
	var rows: Array = offers.get_children().filter(
		func(row: Node) -> bool: return row.has_meta("offer")
	)
	_check(rows.size() == 2, "Bible checks are one-line offers (the upgrade has its own)")
	_check(
		(
			str(rows[0].get_meta("offer"))
			== "Before approving the bible: the palette section is not set yet."
		),
		"An offer is one plain line"
	)
	(rows[0].find_child("Ask", true, false) as Button).pressed.emit()
	await process_frame
	_check(
		chat._prompt.text.contains("the palette section is not set yet"),
		"An offer fills the composer"
	)
	_check(not is_instance_valid(dialog) or dialog.is_queued_for_deletion(), "The dialog closes")
	_check(approved[0] == 0, "Nothing is approved by an offer")
	chat._prompt.clear()
	await process_frame

	# The update runs `joinery bible --upgrade` through a job.
	dialog = dialog_script.open(chat, _event(1), Vector2i(900, 700), approve)
	await process_frame
	update = dialog.find_child("Update", true, false) as Button
	update.pressed.emit()
	_check(chat._slash._jobs.has("upgrade"), "Update runs the CLI through a job")
	var deadline := Time.get_ticks_msec() + 30000
	while chat._slash._jobs.has("upgrade") and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not chat._slash._jobs.has("upgrade"), "The update finishes")
	# This project has no bible file, so the update says why it couldn't run.
	_check(
		chat._transcript.notice_texts()[-1].begins_with("Couldn't update the art direction."),
		"A failed update says so in the chat"
	)

	# Given only the text, the dialog picks up the bridge's event when the sidebar hears it.
	chat.art_review_event = {}
	dialog = dialog_script.open(chat, BIBLE, Vector2i(900, 700), approve)
	_check(dialog.find_child("Board", true, false) == null, "No board before the event")
	chat.art_review_event = _event(2)
	await process_frame
	await process_frame
	_check(dialog.find_child("Board", true, false) != null, "The board arrives with the event")
	dialog.hide()
	dialog.queue_free()
	await process_frame

	# A current bible: no upgrade offer; the string form finds the bridge's event.
	chat.art_review_event = _event(2)
	dialog = dialog_script.open(chat, BIBLE, Vector2i(900, 700), approve)
	await process_frame
	_check(dialog.find_child("Upgrade", true, false) == null, "A current bible offers no update")
	_check(dialog.find_child("Board", true, false) != null, "The board comes from the event")
	dialog.get_ok_button().pressed.emit()
	await process_frame
	_check(approved[0] == 1, "Approve these rules approves")

	# An approved bible says so and can't be approved twice.
	var done := _event(2)
	done["approved"] = true
	dialog = dialog_script.open(chat, done, Vector2i(900, 700), approve)
	await process_frame
	_check(dialog.get_ok_button().disabled, "An approved bible can't be approved again")
	dialog.hide()
	dialog.queue_free()

	chat.queue_free()
	await process_frame
	print("art review: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
