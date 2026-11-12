@tool
extends RefCounted
## Rewind or branch a saved chat from one of its requests (ADR 0080).
##
## Every request bubble gets a quiet menu button at its top right, shown while the pointer is
## over the request or the button has focus: "Edit and resend from here" and "Branch from
## here". Nothing happens to the project from this menu on its own.
##
## Edit and resend asks first, under the request: the request comes back to the message box
## and the turns from it on leave the chat; the files stay as they are, and accepted changes
## stay in the Timeline. "Also undo the N changes made from here" stages one change that puts
## their files back (`joinery chats undo`), which waits in Review like any other. Rewind and
## edit runs `joinery chats rewind`, shows the chat again and puts the request back in the
## message box with its chips. The chat host then starts the agent fresh from a summary of what
## is left, for every agent alike.
##
## Branch from here copies the chat up to and including that turn into a new chat
## (`joinery chats branch`) and opens it as the open chat; the original stays as it was.

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STORE := preload("chat_store.gd")
const SLASH := preload("slash_commands.gd")
const EDIT := 0
const BRANCH := 1
const MENU_META := "joinery_rewind_menu"
## The Review tab refreshes when an undo is staged (review_tab.gd joins this group).
const REVIEW_GROUP := "joinery_review"
const BUSY := "Wait for Journeyman to finish, or stop it first."
const NOT_SAVED := "This message isn't in the saved chat."

## The chat_sessions.gd node this works for.
var sessions: Node
## The question open under a request (null when none), and its undo choice.
var confirm: PanelContainer
var undo_box: CheckBox
var _confirm_card: Control


func _init(owner: Node) -> void:
	sessions = owner


## Give a request card ([code]sidebar_change_card.gd[/code]) its menu. Safe to call twice.
func offer(card: Control) -> void:
	if card.has_meta(MENU_META) or not card.has_method("set_sent"):
		return
	var bubble: Variant = card.get("_bubble")
	if not bubble is PanelContainer or (bubble as Node).get_child_count() != 1:
		return
	var content: Control = (bubble as Node).get_child(0)
	(bubble as Node).remove_child(content)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(content)
	var button := MenuButton.new()
	button.theme_type_variation = "JoineryIconButton"
	button.focus_mode = Control.FOCUS_ALL
	button.flat = false
	button.tooltip_text = "Rewind or branch from here"
	button.accessibility_name = "Rewind or branch from this message"
	button.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	UI.bind_icon(button, "menu")
	UI._on_theme(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "xs"))
			button.custom_minimum_size = Vector2(side, side)
	)
	button.modulate.a = 0.0
	row.add_child(button)
	(bubble as Node).add_child(row)
	var popup := button.get_popup()
	popup.add_item("Edit and resend from here", EDIT)
	popup.add_item("Branch from here", BRANCH)
	popup.id_pressed.connect(choose.bind(card))
	card.set_meta(MENU_META, button)
	card.mouse_entered.connect(_reveal.bind(card, true))
	card.mouse_exited.connect(_reveal.bind(card, false))
	button.focus_entered.connect(_reveal.bind(card, true))
	button.focus_exited.connect(_reveal.bind(card, false))
	button.about_to_popup.connect(_reveal.bind(card, true))
	popup.popup_hide.connect(_reveal.bind(card, false))


## The menu button of [param card] (null without one).
static func menu_of(card: Control) -> MenuButton:
	return card.get_meta(MENU_META, null) as MenuButton


func _reveal(card: Control, on: bool) -> void:
	var button := menu_of(card)
	if button == null or not button.is_inside_tree():
		return
	var shown := (
		on
		or button.has_focus()
		or button.get_popup().visible
		or card.get_global_rect().has_point(card.get_global_mouse_position())
	)
	UI_MOTION.tween_to(button, button, "modulate:a", 1.0 if shown else 0.0, "fast")


## The menu's choice for [param card]: Edit and resend asks first; Branch runs at once.
func choose(id: int, card: Control) -> void:
	if sessions.call("_occupied"):
		sessions.dock.call("show_status", BUSY, 4.0)
		return
	var turn := turn_of(card)
	if turn < 0:
		sessions.dock.call("show_status", NOT_SAVED, 4.0)
		return
	if id == BRANCH:
		branch(turn)
	else:
		ask(card, turn)


## The request's place among the chat's saved requests (0-based), or -1. The n-th sent card
## with this text is the n-th saved request with it: unsent cards were never saved.
func turn_of(card: Control) -> int:
	if not is_instance_valid(card) or str(card.get("state")) in ["sending", "failed"]:
		return -1
	var data := STORE.read(str(sessions.shown_id), sessions.project)
	if data.is_empty():
		return -1
	var text := str(card.get("request"))
	var before := 0
	for other: Variant in sessions.get("_transcript").get("_cards"):
		if other == card:
			break
		if (
			is_instance_valid(other)
			and str(other.get("request")) == text
			and not str(other.get("state")) in ["sending", "failed"]
		):
			before += 1
	var turn := -1
	for entry: Dictionary in data["entries"]:
		if str(entry.get("type", "")) != "user":
			continue
		turn += 1
		if str(entry.get("text", "")) == text:
			if before == 0:
				return turn
			before -= 1
	return -1


## What leaves the chat when it is rewound to [param turn]: {turns, accepted: [ids]}.
func later(turn: int) -> Dictionary:
	var data := STORE.read(str(sessions.shown_id), sessions.project)
	var seen := -1
	var states := {}
	var turns := 0
	for entry: Dictionary in data.get("entries", []):
		var kind := str(entry.get("type", ""))
		if kind == "user":
			seen += 1
		if seen < turn:
			continue
		if kind == "user":
			turns += 1
		var id := str(entry.get("transaction_id", ""))
		if kind == "change" and not id.is_empty():
			states[id] = str(entry.get("state", ""))
	var accepted: Array[String] = []
	for id: String in states:
		if states[id] == "accepted":
			accepted.append(id)
	return {"turns": turns, "accepted": accepted}


## The question under [param card] before rewinding to it.
func ask(card: Control, turn: int) -> void:
	close()
	var gone := later(turn)
	var after := int(gone["turns"]) - 1
	var accepted: Array = gone["accepted"]
	confirm = UI.card()
	confirm.accessibility_name = "Edit and resend from here"
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryStack"
	confirm.add_child(stack)
	var title := UI.label("Edit and resend from here?", "", true)
	stack.add_child(title)
	var words := (
		"This message comes back to the message box."
		if after <= 0
		else (
			"This message comes back to the message box, and the %d after it leave the chat."
			% after
		)
	)
	var note := UI.label(words + " Your files stay as they are.", "JoineryCaption", true)
	stack.add_child(note)
	undo_box = null
	if not accepted.is_empty():
		undo_box = CheckBox.new()
		undo_box.text = (
			"Also undo the change made from here"
			if accepted.size() == 1
			else "Also undo the %d changes made from here" % accepted.size()
		)
		undo_box.tooltip_text = "Staged as one change; you review it before any file changes"
		undo_box.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		stack.add_child(undo_box)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.alignment = BoxContainer.ALIGNMENT_END
	var cancel := UI.quiet("Cancel")
	cancel.pressed.connect(close)
	row.add_child(cancel)
	var go := UI.primary("Rewind and edit")
	go.accessibility_name = "Rewind the chat to this message and edit it"
	go.pressed.connect(func() -> void: rewind(turn, undo_box != null and undo_box.button_pressed))
	row.add_child(go)
	stack.add_child(row)
	card.add_child(confirm)
	card.move_child(confirm, 1)
	_confirm_card = card
	UI_MOTION.fade_in(confirm)
	# The question stays in sight: the transcript stops following the newest message.
	var transcript: Variant = sessions.get("_transcript")
	if transcript is ScrollContainer and (transcript as Node).is_inside_tree():
		(transcript as Node).set("_follow", false)
		# Weak references: the question may be answered and freed before the next frame.
		var shown := weakref(confirm)
		var scroll := weakref(transcript)
		var show := func() -> void:
			var question := shown.get_ref() as Control
			var view := scroll.get_ref() as ScrollContainer
			if question != null and view != null and question.is_inside_tree():
				view.ensure_control_visible(question)
		(transcript as Node).get_tree().process_frame.connect(show, CONNECT_ONE_SHOT)
	var button := weakref(go)
	var focus := func() -> void:
		var target := button.get_ref() as Control
		if target != null and target.is_inside_tree():
			target.grab_focus()
	focus.call_deferred()


## Take the question away.
func close() -> void:
	if is_instance_valid(confirm):
		var leaving := confirm
		UI_MOTION.exit(leaving, null, Callable(), true)
	confirm = null
	undo_box = null
	_confirm_card = null


## Rewind the chat on screen to [param turn]; with [param undo], stage the undo of the changes
## accepted from there on.
func rewind(turn: int, undo := false) -> void:
	var chat_id := str(sessions.shown_id)
	var args: PackedStringArray = sessions.call(
		"_cli",
		["chats", "rewind", "--project", sessions.call("_project"), "--id", chat_id, "--turn", turn]
	)
	sessions.call("_start", "rewind", args, _rewound.bind(chat_id, undo))


func _rewound(result: Dictionary, chat_id: String, undo: bool) -> void:
	var data := SLASH.parse_json(str(result.get("output", "")))
	if int(result.get("code", 1)) != 0:
		var why := str(data.get("error", "")) if data.has("error") else "Couldn't rewind the chat."
		sessions.dock.call("show_status", why, 5.0)
		return
	close()
	sessions.call("_show", chat_id, false)
	var entry := {"targets": data.get("targets", []), "attachments": data.get("attachments", [])}
	var composer: Control = sessions.get("_composer")
	if not composer.call("restore", str(data.get("request", "")), sessions.call("_targets", entry)):
		sessions.dock.call("show_status", "Rewound. Your draft stays in the message box.", 4.0)
	if not undo:
		return
	var ids := PackedStringArray()
	for change: Variant in data.get("changes", []):
		if change is Dictionary and str(change.get("state", "")) == "accepted":
			ids.append(str(change.get("transaction_id", "")))
	if ids.is_empty():
		sessions.get("_transcript").call(
			"add_notice", "The changes made from there on are no longer accepted.", "info"
		)
		return
	var args: PackedStringArray = sessions.call(
		"_cli",
		["chats", "undo", "--project", sessions.call("_project"), "--changes", ",".join(ids)]
	)
	sessions.call("_start", "undo", args, _undone)


func _undone(result: Dictionary) -> void:
	var data := SLASH.parse_json(str(result.get("output", "")))
	var transcript: Node = sessions.get("_transcript")
	if int(result.get("code", 1)) != 0:
		var why := str(data.get("error", "Couldn't stage the undo."))
		transcript.call("add_notice", why, "error")
		return
	var count := int(data.get("undone", 1))
	var line := (
		"Undo of the change made from there is waiting for you in Review."
		if count == 1
		else "Undo of the %d changes made from there is waiting for you in Review." % count
	)
	transcript.call("add_notice", line, "info")
	if sessions.is_inside_tree():
		sessions.get_tree().call_group(REVIEW_GROUP, "refresh")


## Copy the chat on screen up to and including [param turn] into a new chat and open it.
func branch(turn: int) -> void:
	var args: PackedStringArray = sessions.call(
		"_cli",
		[
			"chats",
			"branch",
			"--project",
			sessions.call("_project"),
			"--id",
			str(sessions.shown_id),
			"--turn",
			turn
		]
	)
	sessions.call("_start", "branch", args, _branched)


func _branched(result: Dictionary) -> void:
	var data := SLASH.parse_json(str(result.get("output", "")))
	var chat: Variant = data.get("chat", {})
	if int(result.get("code", 1)) != 0 or not chat is Dictionary:
		sessions.dock.call("show_status", str(data.get("error", "Couldn't branch the chat.")), 5.0)
		return
	sessions.call("_show", str(chat.get("id", "")), false)
	sessions.dock.call(
		"show_status", "Branched into a new chat. The original stays as it was.", 4.0
	)
	sessions.get("_composer").get("prompt").grab_focus.call_deferred()
