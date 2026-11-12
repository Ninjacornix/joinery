extends SceneTree
## Rewind or branch a chat from one of its requests (ADR 0080): every request gets the menu,
## Edit and resend asks first and only then rewinds (with or without the undo of the later
## accepted changes), the request comes back to the message box with its chips, and Branch opens
## the new chat. Joinery's CLI answers from the test.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const CHAT := "20261112-090000-aaaaaa"
const BRANCHED := "20261112-090500-bbbbbb"
const FIRST := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const SECOND := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _folder() -> String:
	return ProjectSettings.globalize_path("res://.joinery/chats")


static func entries() -> Array:
	return [
		{"type": "user", "text": "Make the door wider"},
		{"type": "agent", "text": "Wider now."},
		{"type": "turn", "outcome": "done", "total_ms": 4000},
		{
			"type": "user",
			"text": "Darken the wood",
			"targets": [{"id": "res://door.tscn", "label": "Door"}],
		},
		{"type": "agent", "text": "Darker."},
		{"type": "change", "transaction_id": FIRST, "state": "accepted"},
		{"type": "turn", "outcome": "done", "total_ms": 5000},
		{"type": "user", "text": "Add two lamps"},
		{"type": "agent", "text": "Two lamps."},
		{"type": "change", "transaction_id": SECOND, "state": "accepted"},
		{"type": "turn", "outcome": "done", "total_ms": 6000},
	]


static func save(folder: String, chat_id: String, title: String, lines: Array) -> void:
	DirAccess.make_dir_recursive_absolute(folder)
	var head := {
		"type": "chat", "id": chat_id, "title": title, "provider": "codex", "updated": "2026-11-12"
	}
	var text := PackedStringArray([JSON.stringify(head)])
	for entry: Dictionary in lines:
		text.append(JSON.stringify(entry))
	var file := FileAccess.open(folder.path_join(chat_id + ".jsonl"), FileAccess.WRITE)
	file.store_string("\n".join(text) + "\n")
	file.close()


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	save(_folder(), CHAT, "Make the door wider", entries())
	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	chat.add_child(backend)
	var sessions: Node = chat.get("_chats")
	var rewinder: RefCounted = sessions.get("rewinder")
	var calls: Array[String] = []
	var rewound := {
		"request": "Darken the wood",
		"targets": [{"id": "res://door.tscn", "label": "Door"}],
		"attachments": [],
		"dropped": 2,
		"changes":
		[
			{"transaction_id": FIRST, "state": "accepted"},
			{"transaction_id": SECOND, "state": "reverted"},
		],
	}
	sessions.set(
		"runner",
		func(args: PackedStringArray, done: Callable) -> void:
			var words := " ".join(args.slice(args.find("joinery") + 1))
			if words.begins_with("chats "):
				calls.append(words)
			if words.begins_with("chats rewind"):
				# Joinery cut the chat: what is left is the first turn and the mark.
				var kept := entries().slice(0, 3)
				kept.append({"type": "rewind", "dropped": 2, "request": "Darken the wood"})
				save(_folder(), CHAT, "Make the door wider", kept)
				done.call({"code": 0, "output": JSON.stringify(rewound)})
			elif words.begins_with("chats undo"):
				done.call({"code": 0, "output": JSON.stringify({"undone": 1, "state": "review"})})
			elif words.begins_with("chats branch"):
				save(_folder(), BRANCHED, "Make the door wider (branch)", entries().slice(0, 7))
				done.call({"code": 0, "output": JSON.stringify({"chat": {"id": BRANCHED}})})
			else:
				done.call({"code": 1, "output": ""})
	)
	var transcript: Node = chat.get("_transcript")
	var composer: Control = chat.get("_composer")
	sessions.call("restore_latest")
	await process_frame
	await process_frame
	var cards: Array = transcript.get("_cards")
	_check(cards.size() == 3, "Three requests: %d" % cards.size())
	for card: Control in cards:
		var menu: MenuButton = rewinder.menu_of(card)
		_check(menu != null, "Every request has the menu")
	var second: Control = cards[1]
	var menu: MenuButton = rewinder.menu_of(second)
	var items: Array[String] = []
	for index: int in menu.get_popup().item_count:
		items.append(menu.get_popup().get_item_text(index))
	_check(items == ["Edit and resend from here", "Branch from here"], "Menu: %s" % [items])
	_check(menu.modulate.a == 0.0, "The menu stays out of sight until the request is pointed at")
	_check(
		second.get("text_label").get_parent() == menu.get_parent(),
		"The menu sits beside the request text, not over it"
	)
	_check(rewinder.turn_of(second) == 1, "The second request is turn 1")
	_check(rewinder.turn_of(cards[2]) == 2, "The third is turn 2")

	# Edit and resend asks first; nothing runs yet.
	rewinder.choose(rewinder.EDIT, second)
	await process_frame
	var confirm: Control = rewinder.get("confirm")
	_check(confirm != null and confirm.get_parent() == second, "The question opens under it")
	_check(second.get_child(1) == confirm, "Right under the bubble")
	var labels: Array[String] = []
	for label: Label in confirm.find_children("", "Label", true, false):
		labels.append(label.text)
	_check(labels[0] == "Edit and resend from here?", "Asks plainly: %s" % [labels])
	_check(
		labels[1].contains("the 1 after it leave the chat") and labels[1].contains("Your files"),
		"Says what leaves and that files stay: %s" % [labels]
	)
	var undo: CheckBox = rewinder.get("undo_box")
	_check(undo != null and undo.text == "Also undo the 2 changes made from here", undo.text)
	_check(not undo.button_pressed, "Undo is the user's choice, off by default")
	_check(calls.is_empty(), "Nothing ran before the answer: %s" % [calls])
	rewinder.close()
	await process_frame
	_check(rewinder.get("confirm") == null, "Cancel takes the question away")

	# Rewind and edit, with the undo: rewind, then one reviewed undo of the still-accepted one.
	rewinder.ask(second, 1)
	(rewinder.get("undo_box") as CheckBox).button_pressed = true
	var go: Button = null
	for button: Button in (rewinder.get("confirm") as Node).find_children(
		"", "Button", true, false
	):
		if button.text == "Rewind and edit":
			go = button
	_check(go != null, "Rewind and edit is the main action")
	go.pressed.emit()
	await process_frame
	_check(
		(
			calls[0]
			== "chats rewind --project %s --id %s --turn 1" % [sessions.call("_project"), CHAT]
		),
		calls[0]
	)
	_check(
		calls[1].begins_with("chats undo --project") and calls[1].ends_with("--changes " + FIRST),
		"Only the change still accepted is undone: %s" % [calls]
	)
	cards = transcript.get("_cards")
	_check(cards.size() == 1, "The chat shows what is left: %d" % cards.size())
	_check(composer.get("prompt").text == "Darken the wood", "The request is back to edit")
	var chips: Array = composer.call("target_snapshot")
	_check(chips.size() == 1 and chips[0]["label"] == "Door", "With its chips: %s" % [chips])
	var notices: PackedStringArray = transcript.call("notice_texts")
	_check(notices.has("Rewound to here. Accepted changes stay in the Timeline."), "%s" % [notices])
	_check(
		notices.has("Undo of the change made from there is waiting for you in Review."),
		"%s" % [notices]
	)
	_check(sessions.call("current_id") == CHAT, "The rewound chat is still the open one")

	# Branch from here: the new chat opens; the original stays.
	composer.get("prompt").text = ""
	calls.clear()
	rewinder.choose(rewinder.BRANCH, cards[0])
	await process_frame
	_check(calls.size() == 1 and calls[0].ends_with("--id %s --turn 0" % CHAT), "%s" % [calls])
	_check(sessions.call("current_id") == BRANCHED, "The branch is the open chat")
	_check(backend.get("chat_id") == BRANCHED, "Its next prompt continues the branch")
	_check(transcript.get("_cards").size() == 2, "With the history it copied")

	# While Journeyman works the chat can't be rewound.
	chat.set_chat_busy(true)
	calls.clear()
	rewinder.choose(rewinder.BRANCH, transcript.get("_cards")[0])
	_check(calls.is_empty(), "Busy: nothing runs")
	chat.set_chat_busy(false)

	backend.queue_free()
	chat.queue_free()
	await process_frame
	for file_name: String in DirAccess.get_files_at(_folder()):
		DirAccess.remove_absolute(_folder().path_join(file_name))
	print("chat rewind: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
