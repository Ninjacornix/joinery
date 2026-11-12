extends RefCounted
## The visual suite's rewind screens (ADR 0080): Edit and resend from here asking first under a
## request, with the menu shown and the undo ticked, then the chat rewound with the request
## back in the message box. [param suite] is visual_sidebar_test.gd, whose `_shot` these use;
## the chat on screen is its saved chats' latest one.

const LATEST := "20261110-090000-aaaaaa"


static func shots(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var sessions: Node = chat.get("_chats")
	var rewinder: RefCounted = sessions.get("rewinder")
	sessions.call("_show", LATEST, false)
	await suite._frames(2)
	var cards: Array = chat.get("_transcript").get("_cards")
	var first: Control = cards[0]
	# The pointer is over the first request: its menu shows.
	rewinder.menu_of(first).modulate.a = 1.0
	rewinder.ask(first, 0)
	(rewinder.get("undo_box") as CheckBox).button_pressed = true
	await suite._shot("rewind-ask", light, width, chat)
	var folder := ProjectSettings.globalize_path("res://.joinery/chats")
	var request := str(first.get("request"))
	var answer := {
		"request": request,
		"targets": [{"id": "res://door.tscn::Door", "label": "Door"}],
		"changes": [{"transaction_id": "a".repeat(32), "state": "accepted"}],
	}
	sessions.set(
		"runner",
		func(args: PackedStringArray, done: Callable) -> void:
			var words := " ".join(args.slice(args.find("joinery") + 1))
			if words.begins_with("chats rewind"):
				var head := {"type": "chat", "id": LATEST, "title": request}
				var mark := {"type": "rewind", "dropped": 2, "request": request}
				var file := FileAccess.open(folder.path_join(LATEST + ".jsonl"), FileAccess.WRITE)
				file.store_string(JSON.stringify(head) + "\n" + JSON.stringify(mark) + "\n")
				file.close()
				done.call({"code": 0, "output": JSON.stringify(answer)})
			elif words.begins_with("chats undo"):
				done.call({"code": 0, "output": JSON.stringify({"undone": 2})})
	)
	rewinder.rewind(0, true)
	await suite._shot("rewound", light, width, chat)
	sessions.set("runner", func(_args: PackedStringArray, _done: Callable) -> void: pass)
