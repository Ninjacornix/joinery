extends SceneTree
## Reviewer and debugger agents in the editor: the reviewer's notes reach the review's notes
## line, "/debug" and the offer after a new error in Output, a debugging
## task's card, and the Reviewer agent switch in settings.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const JOB_SOURCE := """extends RefCounted
var lines: Array[Dictionary] = []
func send_line(line: String) -> void:
	lines.append(JSON.parse_string(line))
func take_lines() -> Array:
	return []
func poll() -> Dictionary:
	return {}
func close_pipes() -> void:
	pass
"""
## An error as Godot's Output shows it; built here so this test's own output has none.
var _error := "SCRIPT" + " ERROR: Invalid call. Nonexistent function 'jump' in base 'Node2D'."
var _failed := false
var _output := ""


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _button(node: Node, text: String) -> Button:
	for found: Node in node.find_children("", "Button", true, false):
		if (found as Button).text == text and (found as Button).is_visible_in_tree():
			return found as Button
	return null


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	await _debugging()
	_reviewer_setting()
	await process_frame
	print("agent roles: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## A reviewer note from the host's file_preview (joinery/turn.py) reaches the review panel's
## notes line; a skipped reviewer joins its status.
func _review_notes(backend: Node) -> void:
	var tab: Control = load("res://addons/joinery/review_tab.gd").new()
	tab.set("cli_enabled", false)
	root.add_child(tab)
	tab.call("follow", backend)
	var preview := {
		"kind": "file_preview",
		"task_id": "t9",
		"transaction_id": "rv",
		"summary": "A lamp",
		"diff": "+lamp",
		"changes": [{"path": "lamp.tscn", "change": "created"}],
		"checks": {"ran": ["references"], "skipped": []},
		"review_notes":
		[
			{"severity": "info", "text": "Scale matches the bible."},
			{
				"severity": "warn",
				"text": "The shade is brighter than the palette.",
				"file": "lamp.tscn"
			},
		],
		"reviewer": {"status": "reviewed", "rounds": 1, "elapsed_s": 31.0},
		"reviewer_status": "reviewed",
	}
	backend.call("_handle", preview)
	tab.call("show_task", "rv")
	await process_frame
	var tasks: Object = tab.get("_tasks")
	var panel: Object = tasks.get("panel")
	var summary: Object = panel.get("_summary")
	_check(
		str(summary.call("notes_text")).begins_with("The shade is brighter than the palette."),
		"The reviewer's worst note is the panel's line: %s" % summary.call("notes_text")
	)
	_check(not (panel.get("_accept") as Button).disabled, "The user still decides")
	preview["transaction_id"] = "rv2"
	preview["review_notes"] = []
	preview["reviewer"] = {"status": "timeout", "rounds": 0, "elapsed_s": 105.0}
	preview["reviewer_status"] = "timeout"
	backend.call("_handle", preview)
	tab.call("show_task", "rv2")
	await process_frame
	_check(
		str(summary.call("status_text")).contains("Reviewer ran out of time"),
		"A timed-out reviewer says so: %s" % summary.call("status_text")
	)
	tab.queue_free()
	await process_frame


func _debugging() -> void:
	var dock: Control = load(DOCK_PATH).new()
	root.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	dock.add_child(backend)
	var fake := GDScript.new()
	fake.source_code = JOB_SOURCE
	fake.reload()
	var job: RefCounted = fake.new()
	backend.set("_job", job)
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})
	backend.set("_job", job)
	dock.call("set_chat_state", "connected", "")
	await _review_notes(backend)
	var tasks: Control = dock.get("_tasks")
	var watcher: Node = tasks.find_child("Debugging", false, false)
	_check(watcher != null, "The sidebar watches Output for errors")
	if watcher == null:
		return
	watcher.set("output_source", func() -> String: return _output)
	watcher.set("log_source", func() -> String: return "game log tail")
	# As when the sidebar opens: what Output already shows is where reading starts.
	watcher.set("_read", -1)
	var transcript: Object = dock.get("_transcript")

	# "/debug" with nothing reported yet says so.
	dock.call("run_command", "/debug")
	_check(str(transcript.get("_log")).contains("No error to debug yet"), "/debug needs an error")
	_check(_sent(job, "debug_start").is_empty(), "…and starts nothing")

	# Errors already in Output when the sidebar opened are not sent or offered.
	_output = "Godot Engine v4.7\n" + _error + "\n"
	watcher.call("poll")
	_check(_sent(job, "errors_seen").is_empty(), "Old errors aren't sent")
	_check(str(watcher.call("offer_text")).is_empty(), "Old errors aren't offered")

	# New Output goes to the host, which groups it; nothing is offered before it answers.
	_output += "Running the game\n" + _error + "\n   at: _process (res://player.gd:12)\n"
	watcher.call("poll")
	var seen := _sent(job, "errors_seen")
	_check(
		(
			str(seen.get("source", "")) == "output"
			and str(seen.get("text", "")).contains("player.gd:12")
		),
		"New Output goes to the host: %s" % seen
	)
	_check(str(watcher.call("offer_text")).is_empty(), "The host decides what is offered")

	# The game's log is read too, from where it stood when the sidebar opened.
	var game_log: Array[String] = ["old run\n"]
	watcher.set("game_log_source", func() -> String: return "".join(PackedStringArray(game_log)))
	watcher.set("_log_read", -1)
	watcher.call("poll")
	game_log.append("USER ERROR: Out of ammo\n")
	watcher.call("poll")
	seen = _sent(job, "errors_seen")
	_check(
		(
			str(seen.get("source", "")) == "game"
			and str(seen.get("text", "")) == "USER ERROR: Out of ammo\n"
		),
		"Only what the game's log gained: %s" % seen
	)

	# The host's list: one quiet offer for its new error, and the Errors card.
	var group := {
		"id": "g1",
		"kind": "error",
		"message": "Invalid call. Nonexistent function 'jump' in base 'Node2D'.",
		"label": "Invalid call. Nonexistent function 'jump' in base 'Node2D'. (player.gd:12)",
		"place": "player.gd:12",
		"count": 2,
		"status": "open",
		"line": _error,
	}
	var listed := {"kind": "errors", "mode": "ask", "groups": [group], "offer": group}
	backend.call("_handle", listed)
	_check(
		str(watcher.call("offer_text")).begins_with("Godot reported an error: Invalid call."),
		"A new error is offered: %s" % watcher.call("offer_text")
	)
	backend.call("_handle", listed)
	_check(
		(transcript.call("offer_texts") as PackedStringArray).size() == 1,
		"One offer at a time, each error once"
	)
	var list_card: Control = tasks.get("errors_card")
	_check(list_card != null and list_card.visible, "The Errors card lists it")
	if list_card != null:
		_check((list_card.call("row_texts") as PackedStringArray).size() == 1, "One row per error")
		_check(str(list_card.get("headline").text) == "1 console error", "Its headline counts")
	_check(_button(dock, "Always fix errors like this") != null, "The offer can turn on auto")
	var debug := _button(dock, "Debug")
	_check(debug != null, "Debug starts it")
	if debug != null:
		debug.pressed.emit()
	var started := _sent(job, "debug_start")
	_check(str(started.get("error", "")) == _error, "The error line goes to the host")
	_check(str(started.get("group_id", "")) == "g1", "With the error it fixes")
	_check(
		str(started.get("output", "")).contains("res://player.gd:12"), "With the Output around it"
	)
	_check(str(started.get("log", "")) == "game log tail", "And the game's log")
	_check(str(watcher.call("offer_text")).is_empty(), "The offer steps out")

	# "Always fix errors like this": the project switches to auto, starting with this one.
	var second := group.duplicate()
	second.merge({"id": "g2", "label": "Node not found (hud.gd:7)", "place": "hud.gd:7"}, true)
	backend.call(
		"_handle", {"kind": "errors", "mode": "ask", "groups": [group, second], "offer": second}
	)
	var always := _button(dock, "Always fix errors like this")
	_check(always != null, "Each new error is offered")
	if always != null:
		always.pressed.emit()
	var mode := _sent(job, "errors_mode")
	_check(
		str(mode.get("mode", "")) == "auto" and str(mode.get("group_id", "")) == "g2",
		"Auto is saved and this error fixed: %s" % mode
	)
	# In auto nothing is offered; Fix on a row, Ignore and Fix all go to the host.
	second["status"] = "fixing"
	var third := group.duplicate()
	third.merge({"id": "g3", "label": "Out of ammo", "place": "", "status": "open"}, true)
	backend.call(
		"_handle",
		{"kind": "errors", "mode": "auto", "groups": [second, group, third], "offer": third}
	)
	_check(str(watcher.call("offer_text")).is_empty(), "Auto offers nothing")
	if list_card != null:
		_check(not (list_card.get("fix_all") as Button).visible, "Fix all is for ask")
		_check(str(list_card.get("headline").text) == "3 console errors", "Three rows")
		var fix := _button(list_card, "Fix")
		if fix != null:
			fix.pressed.emit()
		_check(_sent(job, "errors_fix").get("ids", []) == ["g1"], "Fix sends its row")
		backend.call("_handle", {"kind": "errors", "mode": "ask", "groups": [group, third]})
		var all := list_card.get("fix_all") as Button
		_check(all.visible, "Ask: Fix all with two to fix")
		all.pressed.emit()
		_check(
			_sent(job, "errors_fix").get("ids", []) == ["g1", "g3"], "Fix all sends the open ones"
		)
		var ignore: Button = null
		for found: Node in list_card.find_children("", "Button", true, false):
			if (found as Button).tooltip_text == "Ignore this error":
				ignore = found as Button
				break
		_check(ignore != null, "Each open row can be ignored")
		if ignore != null:
			ignore.pressed.emit()
		var ignored := _sent(job, "errors_ignore")
		_check(str(ignored.get("id", "")) == "g1" and bool(ignored.get("ignore")), "Ignore goes")
		backend.call("_handle", {"kind": "errors", "mode": "ask", "groups": []})
		_check(not list_card.visible, "No errors: no card")

	# "/debug what goes wrong" sends the last error with the user's words.
	dock.call("run_command", "/debug the player can't jump")
	started = _sent(job, "debug_start")
	_check(str(started.get("output", "")).contains("The user says: the player can't jump"), "Words")

	# "/debug report <id>" hands a player's bug report to the host, nothing else.
	dock.call("run_command", "/debug report 20261112-153012-a3f9")
	started = _sent(job, "debug_start")
	_check(str(started.get("report", "")) == "20261112-153012-a3f9", "A report goes by its id")
	_check(not started.has("error"), "…without the last Output error")

	# "/record" runs the game once with a fixed frame rate and recording on, keeping the
	# user's own run arguments on both sides of "--".
	var record: Script = load("res://addons/joinery/replay_record.gd")
	_check(
		str(record.call("run_args", "", 60)) == "--fixed-fps 60 -- --joinery-record --joinery-pace",
		"Recording arguments: %s" % record.call("run_args", "", 60)
	)
	_check(
		(
			str(record.call("run_args", "--verbose -- --autostart", 120))
			== "--verbose --fixed-fps 120 -- --autostart --joinery-record --joinery-pace"
		),
		"The user's arguments stay: %s" % record.call("run_args", "--verbose -- --autostart", 120)
	)

	# The host's debugging task: a card titled with the error, then the cause.
	var title := "Debugging: " + _error.left(60)
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "d1",
				"title": title,
				"origin": "debug",
				"role": "debugger",
				"error": _error,
				"state": "working",
				"step": "Reading the error…",
			}
		)
	)
	var card: Control = tasks.call("card", "d1")
	_check(card != null, "Debugging has its own card")
	if card != null:
		_check(str(card.get("role")) == "debugger", "Its debugger reads first")
		_check((card.get("title_label") as Label).tooltip_text == _error, "The error on hover")
		(
			backend
			. call(
				"_handle",
				{
					"kind": "progress",
					"task_id": "d1",
					"text": "Reproducing the error offline…",
				}
			)
		)
		_check(card.call("step_text") == "Reproducing the error offline…", "Its step shows")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "d1",
				"title": title,
				"role": "debugger",
				"state": "done",
				"cause": "The GPU driver crashed.",
				"message": "Cause: The GPU driver crashed.",
			}
		)
	)
	var lines: Array = transcript.get("_log")
	_check(
		"\n".join(lines).contains(title + ": Cause: The GPU driver crashed."),
		"A cause with nothing to fix is one line"
	)
	backend.queue_free()
	dock.queue_free()
	await process_frame


func _reviewer_setting() -> void:
	var setting: Control = load("res://addons/joinery/reviewer_setting.gd").new()
	var row: Object = setting.get("row")
	var model_row: Control = setting.get("model_row")
	var model: LineEdit = setting.get("model")
	_check((row.get("switch_button") as CheckButton).disabled, "Unknown until the project answers")
	setting.call("apply_status", {"enabled": true, "model": "gpt-5.2-codex"})
	_check(bool(row.call("is_on")), "The reviewer is on")
	_check(model.text == "gpt-5.2-codex" and model_row.visible, "Its model shows")
	setting.call("apply_status", {"enabled": false, "model": ""})
	_check(not bool(row.call("is_on")) and not model_row.visible, "Off: no model to choose")
	setting.call("apply_status", {"error": "joinery.toml can't be read"})
	_check((row.get("reason") as Label).visible, "A failure says why")
	setting.free()
