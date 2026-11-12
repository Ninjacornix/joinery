extends SceneTree
## Message history in the composer: Up and Down go through the messages sent in this chat, a
## reopened saved chat included; the draft comes back past the newest or with Escape; a message
## of several lines keeps its own caret movement; the `/` menu keeps its arrows; a message sent
## twice in a row comes back once; an edited recall goes as a new message.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const CHAT := "20261112-090000-recall"
const DOOR := "Make the door wider"
const WOOD := "Darken the wood"
const CREAK := "Add a creak\nwhen it opens"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _folder() -> String:
	return ProjectSettings.globalize_path("res://.joinery/chats")


func _save() -> void:
	DirAccess.make_dir_recursive_absolute(_folder())
	var lines := PackedStringArray()
	for entry: Dictionary in [
		{"type": "chat", "id": CHAT, "title": DOOR, "provider": "codex", "updated": _now()},
		{"type": "user", "text": DOOR},
		{"type": "agent", "text": "Wider now."},
		{"type": "user", "text": WOOD},
		{"type": "agent", "text": "Darker."},
		{"type": "user", "text": WOOD},
		{"type": "agent", "text": "Darker still."},
		{"type": "user", "text": CREAK},
		{"type": "agent", "text": "It creaks."},
	]:
		lines.append(JSON.stringify(entry))
	var file := FileAccess.open(_folder().path_join(CHAT + ".jsonl"), FileAccess.WRITE)
	file.store_string("\n".join(lines) + "\n")
	file.close()


func _now() -> String:
	return Time.get_datetime_string_from_unix_time(int(Time.get_unix_time_from_system())) + "+00:00"


func _key(code: Key, shift := false) -> InputEventKey:
	var key := InputEventKey.new()
	key.pressed = true
	key.keycode = code
	key.shift_pressed = shift
	return key


## Press [param code] in the focused message box, through the window like a real key.
func _press(code: Key, shift := false) -> void:
	root.push_input(_key(code, shift))
	await process_frame


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_save()
	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var sessions: Node = chat.get("_chats")
	sessions.set("runner", func(_args: PackedStringArray, done: Callable) -> void: done.call({}))
	var composer: Control = chat.get("_composer")
	var prompt: TextEdit = composer.get("prompt")
	var recall: RefCounted = prompt.get("recall")
	var popup: Control = chat.get("_command_popup")

	# A saved chat opened again: its requests are the history, a repeat collapsed.
	sessions.call("restore_latest")
	await process_frame
	_check(sessions.call("current_id") == CHAT, "The saved chat is open")
	var history: PackedStringArray = recall.call("history")
	_check(history == PackedStringArray([DOOR, WOOD, CREAK]), "History: %s" % [history])

	# Up goes back from the newest, Down comes forward, and the draft comes back past the newest.
	prompt.grab_focus()
	prompt.text = "half typed"
	prompt.set_caret_column(4)
	await _press(KEY_UP)
	_check(prompt.text == CREAK, "Up recalls the newest: %s" % prompt.text)
	_check(prompt.get_caret_line() == 0, "On the first line, so Up goes on back")
	await _press(KEY_UP)
	_check(prompt.text == WOOD, "The repeat comes back once: %s" % prompt.text)
	await _press(KEY_UP)
	_check(prompt.text == DOOR, "Further back: %s" % prompt.text)
	await _press(KEY_UP)
	_check(prompt.text == DOOR and recall.call("browsing"), "The oldest stays")
	await _press(KEY_DOWN)
	_check(prompt.text == WOOD, "Down comes forward: %s" % prompt.text)
	await _press(KEY_DOWN)
	_check(prompt.text == CREAK, "To the newest: %s" % prompt.text)
	_check(prompt.get_caret_line() == 1, "With the caret on its last line")
	await _press(KEY_DOWN)
	_check(prompt.text == "half typed", "Past the newest the draft is back: %s" % prompt.text)
	_check(not recall.call("browsing"), "And browsing ends")
	await _press(KEY_DOWN)
	_check(prompt.text == "half typed", "Down without browsing leaves the field alone")

	# Several lines: the arrows move the caret until the first or last line.
	prompt.text = "first line\nsecond line"
	prompt.set_caret_line(1)
	await _press(KEY_UP)
	_check(prompt.text == "first line\nsecond line", "Up on a later line is the caret's")
	_check(prompt.get_caret_line() == 0, "The caret went up a line")
	await _press(KEY_DOWN)
	_check(prompt.get_caret_line() == 1 and not recall.call("browsing"), "Down is the caret's")
	await _press(KEY_UP)
	await _press(KEY_UP)
	_check(prompt.text == CREAK, "From the first line Up recalls: %s" % prompt.text)
	prompt.set_caret_line(1)
	await _press(KEY_UP)
	_check(prompt.text == CREAK and prompt.get_caret_line() == 0, "Inside a recall the caret moves")
	await _press(KEY_DOWN)
	_check(prompt.text == CREAK and prompt.get_caret_line() == 1, "Both ways")
	await _press(KEY_UP, true)
	_check(prompt.text == CREAK, "Shift+Up selects, it doesn't recall")
	prompt.deselect()

	# Escape goes back to the draft.
	prompt.set_caret_line(0)
	await _press(KEY_UP)
	_check(prompt.text == WOOD, "Further back again: %s" % prompt.text)
	await _press(KEY_ESCAPE)
	_check(prompt.text == "first line\nsecond line", "Escape restores the draft: %s" % prompt.text)
	_check(not recall.call("browsing"), "And ends browsing")

	# The `/` menu keeps its arrows, and nothing is recalled while it shows.
	prompt.text = "/"
	chat.call("_update_commands", "/")
	await process_frame
	_check(popup.visible, "The command menu is open")
	var first: String = popup.call("current")
	await _press(KEY_DOWN)
	_check(popup.call("current") != first, "Down moves the menu's highlight")
	await _press(KEY_UP)
	_check(popup.call("current") == first and prompt.text == "/", "Up is the menu's too")
	_check(not recall.call("handle", _key(KEY_UP)), "History waits while a menu is open")
	await _press(KEY_ESCAPE)
	_check(not popup.visible, "Escape closes the menu")
	_check(recall.call("handle", _key(KEY_UP)) and prompt.text == CREAK, "Then Up recalls")
	await _press(KEY_ESCAPE)
	_check(prompt.text == "/", "Back to the draft")

	# An edited recall goes as a new message; the history keeps the original too.
	prompt.clear()
	await _press(KEY_UP)
	prompt.set_caret_line(1)
	prompt.set_caret_column(prompt.get_line(1).length())
	prompt.insert_text_at_caret(" slowly")
	await process_frame
	_check(recall.call("browsing"), "Editing a recall keeps browsing")
	var edited := CREAK + " slowly"
	composer.call("set_connection", "connected")
	composer.call("_on_enter", prompt.text)
	await process_frame
	var cards: Array = chat.get("_transcript").get("_cards")
	_check(cards[-1].get("request") == edited, "The edited text is sent: %s" % cards[-1].request)
	_check(prompt.text.is_empty() and not recall.call("browsing"), "Sending ends browsing")
	history = recall.call("history")
	_check(history == PackedStringArray([DOOR, WOOD, CREAK, edited]), "History: %s" % [history])

	# A command that runs in the sidebar is not a message, so it is not recalled.
	chat.call("_on_submit", "/nonsense")
	history = recall.call("history")
	_check(history[-1] == edited, "Commands are not recalled: %s" % [history])

	# A locked composer (a check running) recalls nothing.
	composer.call("set_locked", true)
	_check(not recall.call("handle", _key(KEY_UP)), "Nothing is recalled while locked")
	composer.call("set_locked", false)

	chat.queue_free()
	await process_frame
	for file_name: String in DirAccess.get_files_at(_folder()):
		DirAccess.remove_absolute(_folder().path_join(file_name))
	print("composer recall: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
