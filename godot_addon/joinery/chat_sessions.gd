@tool
extends Node
## Saved chats in the sidebar. Every conversation is saved by the chat host
## (<project>/.joinery/chats, see chat_store.gd); this puts them back on screen.
##
## The header gets two buttons beside the menu: Chats (the list, chats_page.gd) and New chat.
## When the editor opens, the most recent chat comes back as the open chat: its requests,
## replies and change cards, and the next message continues it. Opening an older chat from
## the list shows it read-only, with "Continue" in place of the message box; Continue makes it
## the open chat. Change cards show what became of each change now, from
## `joinery transactions list` (accepted, reverted, discarded, too old to revert).
##
## Continuing is the chat host's job: the next prompt names the chat, and the host resumes the
## provider's own session or starts a fresh one from a summary, which the transcript says.

const TASKS := preload("sidebar_tasks.gd")
const VARIANTS := preload("sidebar_variants.gd")
const STORE := preload("chat_store.gd")
const PAGE := preload("chats_page.gd")
const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const ATTACHMENTS := preload("chat_attachments.gd")
const PERSONA := "Journeyman"
const DISCARDED := "Discarded. Your files were not touched."
const BUSY := "Wait for Journeyman to finish, or stop it first."
## Decision line for each state `joinery transactions list` reports: [kind, text].
const STATES := {
	"accepted": ["success", "Change accepted"],
	"rejected": ["rejected", DISCARDED],
	"reverted": ["reverted", "Reverted"],
	"review": ["info", "Not reviewed"],
	"staging": ["info", "Not reviewed"],
	"new": ["info", "Not reviewed"],
	"gone": ["info", "This change is no longer on record"],
}

var dock: Control
var page: PAGE
var chats_button: Button
var new_button: Button
## Stands in for the message box while an older chat is only being viewed.
var past_bar: MarginContainer
## The chat on screen ("" for a new one), and whether it is only being viewed.
var shown_id := ""
var viewing := false
## The project folder whose chats these are ("" for the open project; tests set it).
var project := ""
## Runs a Joinery CLI call: (args: PackedStringArray, done: Callable(result)). Unset, it runs
## `uv run joinery …` in the background; tests set it to answer themselves.
var runner := Callable()
## The chat the next message continues ("" starts a new one).
var _current_id := ""
var _bridge: Node
var _transcript: Control
var _composer: Control
var _past_text: Label
var _continue: Button
## transaction id -> [card, state shown]
var _changes: Dictionary = {}
## slot -> [job, callback]
var _jobs: Dictionary = {}


## Add the header buttons, the Chats page and the bar for older chats to the sidebar.
static func attach(sidebar: Control, header_row: Control, content: Control) -> Node:
	var sessions: Node = load("res://addons/joinery/chat_sessions.gd").new()
	sessions.name = "Chats"
	sessions.dock = sidebar
	sessions.call("_build", header_row, content)
	sidebar.add_child(sessions)
	return sessions


func _build(header_row: Control, content: Control) -> void:
	_transcript = dock.get("_transcript")
	_composer = dock.get("_composer")
	chats_button = UI.icon_button("chats", "Chats")
	chats_button.accessibility_name = "Show saved chats"
	chats_button.pressed.connect(open_list)
	header_row.add_child(chats_button)
	new_button = UI.icon_button("compose", "New chat")
	new_button.accessibility_name = "Start a new chat"
	new_button.pressed.connect(new_chat)
	header_row.add_child(new_button)
	page = PAGE.new()
	page.hide()
	page.closed.connect(func() -> void: page.set_open(false))
	page.chosen.connect(open_chat)
	page.new_chat_requested.connect(new_chat)
	page.rename_requested.connect(rename)
	content.add_child(page)
	past_bar = MarginContainer.new()
	past_bar.accessibility_name = "Earlier chat"
	past_bar.hide()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	past_bar.add_child(row)
	_past_text = UI.label("", "JoineryCaption")
	_past_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_past_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_past_text)
	_continue = UI.primary("Continue")
	_continue.accessibility_name = "Continue this chat"
	_continue.tooltip_text = "Make this the open chat and write your next message"
	_continue.pressed.connect(continue_chat)
	row.add_child(_continue)
	content.add_child(past_bar)
	content.move_child(past_bar, _composer.get_index())
	dock.child_entered_tree.connect(_on_dock_child)


func _ready() -> void:
	set_process(false)
	for child: Node in dock.get_children():
		_on_dock_child(child)
	if Engine.is_editor_hint():
		restore_latest.call_deferred()


func _exit_tree() -> void:
	for slot: String in _jobs:
		_jobs[slot][0].cancel()
	_jobs.clear()


func _on_dock_child(node: Node) -> void:
	if node.has_signal("chat_changed") and node != _bridge:
		_bridge = node
		_bridge.connect("chat_changed", _on_chat_changed)
		_bridge.set("chat_id", _current_id)


## The chat the next message continues ("" for a new one).
func current_id() -> String:
	return _current_id


# --- Actions -----------------------------------------------------------------------------------


## The editor opened: bring back the most recent chat as the open one.
func restore_latest() -> void:
	var saved := STORE.list(project)
	if not saved.is_empty() and _transcript.call("current_card") == null:
		_show(str(saved[0]["id"]), false)


## The Chats page, with this project's chats.
func open_list() -> void:
	page.show_chats(STORE.list(project), _current_id)
	page.set_open(true)


## Open [param chat_id]: the open chat as it is, any other read-only with Continue.
func open_chat(chat_id: String) -> void:
	if chat_id == shown_id and not viewing:
		page.set_open(false)
		return
	if _occupied():
		dock.call("show_status", BUSY, 4.0)
		return
	page.set_open(false)
	_show(chat_id, chat_id != _current_id)


## Clear the conversation: the next message starts a new chat (and a fresh agent session).
func new_chat() -> void:
	if _occupied():
		dock.call("show_status", BUSY, 4.0)
		return
	page.set_open(false)
	_reset()
	shown_id = ""
	_set_current("")
	_set_viewing(false)
	var usage: Variant = dock.get("_header").get("usage")
	if usage is Object:
		usage.call("show_usage", {})
	_composer.get("prompt").grab_focus.call_deferred()


## The chat on screen becomes the open one; the next message continues it.
func continue_chat() -> void:
	if shown_id.is_empty():
		return
	_set_current(shown_id)
	_set_viewing(false)
	_composer.get("prompt").grab_focus.call_deferred()


## Rename a chat through `joinery chats rename`; the row already shows the new name.
func rename(chat_id: String, title: String) -> void:
	var args := _cli(
		["chats", "rename", "--project", _project(), "--id", chat_id, "--title", title]
	)
	_start(
		"rename",
		args,
		func(result: Dictionary) -> void:
			if int(result.get("code", 1)) != 0:
				dock.call("show_status", "Couldn't rename the chat.", 4.0)
				if page.visible:
					page.show_chats(STORE.list(project), _current_id)
	)


# --- Restoring a chat --------------------------------------------------------------------------


func _show(chat_id: String, read_only: bool) -> void:
	var data := STORE.read(chat_id, project)
	if data.is_empty():
		dock.call("show_status", "That chat is no longer saved.", 4.0)
		return
	# A restored chat appears whole; nothing in it is news. Motion comes back as it was.
	var key: String = UI_MOTION._ENABLED_META
	var motion: Variant = Engine.get_meta(key, null)
	UI_MOTION.set_enabled(false)
	_reset()
	render(data["entries"])
	if motion == null:
		Engine.remove_meta(key)
	else:
		Engine.set_meta(key, motion)
	shown_id = chat_id
	if not read_only:
		_set_current(chat_id)
	_set_viewing(read_only)
	if read_only:
		var meta: Dictionary = data["chat"]
		var parts := PackedStringArray(["Earlier chat"])
		if not str(meta.get("provider", "")).is_empty():
			parts.append(STORE.provider_name(str(meta["provider"])))
		for chat: Dictionary in STORE.list(project):
			if chat["id"] == chat_id:
				parts.append(STORE.when(int(chat["updated"])))
		_past_text.text = " · ".join(parts)
		_past_text.tooltip_text = str(meta.get("title", ""))
	_check_states()


## Put [param entries] (a chat file's lines after the first) back in the transcript.
func render(entries: Array) -> void:
	_changes.clear()
	var card: Control = null
	for entry: Variant in entries:
		if not entry is Dictionary:
			continue
		var text := str(entry.get("text", ""))
		match str(entry.get("type", "")):
			"user":
				card = _transcript.call("add_request", text, _targets(entry))
				card.call("set_sent")
			"step":
				if card != null:
					card.call("add_step", text)
			"agent":
				_transcript.call("add_message", PERSONA, text)
			"notice":
				var level := str(entry.get("level", "info"))
				_transcript.call("add_notice", text, "error" if level != "info" else "info")
			"change":
				var id := str(entry.get("transaction_id", ""))
				if card != null and not id.is_empty():
					card.set("transaction_id", id)
					_changes[id] = [card, str(entry.get("state", "review"))]
			"turn":
				var outcome := str(entry.get("outcome", "done"))
				var timing := {"outcome": outcome, "total_ms": int(entry.get("total_ms", 0))}
				_transcript.call("show_timing", timing)
				if card != null:
					card.call("finish", outcome)
			"variants":
				# A request tried several ways: what was kept, as the sidebar said it.
				var said := VARIANTS.saved_line(entry)
				if not said.is_empty():
					_transcript.call("add_notice", said, "info")
			"task":
				# A parallel task's outcome, as the sidebar said it when it finished. A way of a
				# request tried several ways is told by its group's line instead.
				if entry.has("variant_group"):
					continue
				var ending := str(TASKS.ENDINGS.get(str(entry.get("state", "")), ""))
				if not ending.is_empty():
					var line := "%s: %s" % [str(entry.get("title", "Task")), ending]
					_transcript.call("add_notice", line, "info")
			"session":
				if str(entry.get("mode", "")) == "summary":
					_transcript.call(
						"add_notice", "Continued with a summary; the agent starts fresh."
					)
	for id: String in _changes:
		_apply(id, str(_changes[id][1]), true)
	_transcript.call("finish_response")


## A change card's decision line for [param state] (from the chat, then from Joinery).
func _apply(id: String, state: String, revertible: bool) -> void:
	var card: Control = _changes[id][0]
	if not is_instance_valid(card):
		return
	var spec: Array = STATES.get(state, ["error", "This change didn't finish"])
	card.call("set_outcome", spec[0], spec[1])
	if state == "accepted":
		card.call("set_revertible", revertible)
	_changes[id][1] = state


## Ask Joinery what became of each change on screen.
func _check_states() -> void:
	if _changes.is_empty():
		return
	_start(
		"states",
		_cli(["transactions", "list", "--project", _project()]),
		func(result: Dictionary) -> void:
			if int(result.get("code", 1)) == 0:
				show_states(SLASH.parse_json(str(result.get("output", ""))))
	)


## Show each change as `joinery transactions list` reports it ([param report] is its JSON). A
## change it no longer lists is no longer on record.
func show_states(report: Dictionary) -> void:
	var known := {}
	for entry: Variant in report.get("transactions", []):
		if entry is Dictionary:
			known[str(entry.get("id", ""))] = entry
	for id: String in _changes:
		var entry: Dictionary = known.get(id, {"state": "gone", "revertible": false})
		var state := str(entry.get("state", "gone"))
		var revertible := bool(entry.get("revertible", true))
		if state != str(_changes[id][1]):
			_apply(id, state, revertible)
		elif state == "accepted" and is_instance_valid(_changes[id][0]):
			_changes[id][0].call("set_revertible", revertible)


# --- Helpers -----------------------------------------------------------------------------------


## The message's chips again: its scene targets, and its images when they are still saved.
func _targets(entry: Dictionary) -> Array:
	var targets: Array = []
	for target: Variant in entry.get("targets", []):
		if target is Dictionary:
			targets.append(target)
	for path: Variant in entry.get("attachments", []):
		var file := str(path)
		if FileAccess.file_exists(file):
			var stem := file.get_file().get_basename()
			targets.append(
				{"attachment": "image", "id": "attachment:" + stem, "path": file, "label": stem}
			)
	return targets


func _reset() -> void:
	_transcript.call("clear")
	_changes.clear()
	dock.set("_active_card", null)
	dock.set("_assistant_open", false)


func _set_current(chat_id: String) -> void:
	_current_id = chat_id
	if is_instance_valid(_bridge):
		_bridge.set("chat_id", chat_id)


func _set_viewing(on: bool) -> void:
	viewing = on
	past_bar.visible = on
	_composer.visible = not on
	if on:
		UI_MOTION.fade_in(past_bar)


## Journeyman is working, a change waits for review, or messages wait to go.
func _occupied() -> bool:
	var queue: Variant = _transcript.get("queue")
	var queued: bool = queue is Object and int(queue.call("size_queued")) > 0
	return bool(dock.call("_occupied")) or queued


func _on_chat_changed(event: Dictionary) -> void:
	var chat_id := str(event.get("chat_id", ""))
	_current_id = chat_id
	if not viewing:
		shown_id = chat_id
	var message := str(event.get("message", ""))
	if not message.is_empty():
		_transcript.call("add_notice", message, "info")


func _project() -> String:
	return (
		project if not project.is_empty() else ProjectSettings.globalize_path("res://").rstrip("/")
	)


static func _cli(words: Array) -> PackedStringArray:
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery"]
	)
	for word: Variant in words:
		args.append(str(word))
	return args


func _start(slot: String, args: PackedStringArray, callback: Callable) -> void:
	if runner.is_valid():
		runner.call(args, callback)
		return
	if _jobs.has(slot):
		_jobs[slot][0].cancel()
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start("uv", args) != OK:
		return
	_jobs[slot] = [job, callback]
	set_process(true)


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		var result: Dictionary = _jobs[slot][0].poll()
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		callback.call(result)
	if _jobs.is_empty():
		set_process(false)
