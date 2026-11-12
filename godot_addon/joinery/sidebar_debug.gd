@tool
extends Node
## Debugging from the sidebar: "/debug", and the console errors the chat host watches (ADR 0075).
## Debug sends the error line, the Output text around it and the tail of the game's log to the
## chat host (chat_bridge.gd's start_debug). A debugger agent finds the cause (read-only,
## offline) and a worker stages the fix; the user sees a "Debugging: <error>" card
## (sidebar_tasks.gd) and then a normal review.
##
## Every second the editor's Output panel and the game's log are read, and what they gained goes
## to the host, which groups the errors (same message and place: one row with a count), drops
## Joinery's own and known noise, and sends back the list (errors_card.gd). With
## [agents] fix_errors = "ask" (the default) each new error is offered once, one offer at a time
## ("Godot reported an error: … Debug / Always fix errors like this / Dismiss"); with "auto"
## the host starts the fixes itself, and each is still a change the user reviews.

## Seconds between reads of the Output panel.
const POLL := 1.0
## Output lines sent with an error.
const CONTEXT_LINES := 120
## Characters of the game's log sent with an error.
const LOG_CHARS := 8000
const LOG_PATH := "user://logs/godot.log"
const NO_ERROR := "No error to debug yet. Run the game, or say what goes wrong after /debug."
const ERRORS_CARD := preload("errors_card.gd")
const UI_MOTION := preload("ui_motion.gd")
## Characters of new console text sent at once; a flood keeps its newest part.
const SEND_CHARS := 60000

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): its bridge is the chat's.
var tasks: Object
## Returns the Output panel's text; tests replace it. Empty: the editor's own Output panel.
var output_source := Callable()
## Returns the game's log tail; tests replace it.
var log_source := Callable()
## Returns the game's whole log as it stands; tests replace it. Empty: user://logs/godot.log.
var game_log_source := Callable()
## The Errors list (errors_card.gd), once the host has sent one.
var card: Control
## The host's [agents] fix_errors: "ask", "auto" or "off".
var mode := "ask"
var _regex := RegEx.create_from_string(
	"^\\s*(SCRIPT ERROR|ERROR|Parse Error|USER ERROR|E \\d+:\\d+:\\d+)"
)
var _prefix := RegEx.create_from_string("^(SCRIPT ERROR|USER ERROR|ERROR|Parse Error):\\s*")
var _read := -1
var _wait := 0.0
var _offer: Control
var _offered: Dictionary = {}
var _last_error := ""
var _context: PackedStringArray = []
var _output: RichTextLabel
var _log_read := -1
var _listening: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var watcher: Node = load("res://addons/joinery/sidebar_debug.gd").new()
	watcher.set("dock", sidebar)
	watcher.set("tasks", list)
	(list as Node).add_child(watcher)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("debug_requested"):
		slash.connect("debug_requested", watcher.debug)
	return watcher


func _init() -> void:
	name = "Debugging"


func _process(delta: float) -> void:
	_wait -= delta
	if _wait > 0.0:
		return
	_wait = POLL
	poll()


## Hear the host's console error list ([param bridge]'s errors_event).
func listen(bridge: Object) -> void:
	if _listening == bridge or bridge == null or not bridge.has_signal("errors_event"):
		return
	_listening = bridge
	bridge.connect("errors_event", on_errors)


## Read what Output and the game's log gained since the last read; the first read of each
## only sets where to start.
func poll() -> void:
	var text := _output_text()
	if _read < 0 or text.length() < _read:
		# The first read, or Output was cleared: start from here.
		_read = text.length()
	else:
		var added := text.substr(_read)
		_read = text.length()
		if not added.is_empty():
			take_output(added)
	var more := _game_log_since()
	if not more.is_empty():
		_report("game", more)


## What the game's log gained since the last read: the first read only sets where to start,
## and a log shorter than before is a new run's, read from its start.
func _game_log_since() -> String:
	if game_log_source.is_valid():
		var whole := str(game_log_source.call())
		var start := _log_read if whole.length() >= _log_read else 0
		_log_read = whole.length()
		return "" if start < 0 else whole.substr(start)
	if not Engine.is_editor_hint() or not FileAccess.file_exists(LOG_PATH):
		return ""
	var file := FileAccess.open(LOG_PATH, FileAccess.READ)
	if file == null:
		return ""
	var length := file.get_length()
	var from := _log_read if length >= _log_read else 0
	_log_read = length
	if from < 0 or length == from:
		return ""
	# A flood keeps its newest part, as Output's does.
	from = maxi(from, length - SEND_CHARS * 4)
	file.seek(from)
	return file.get_buffer(length - from).get_string_from_utf8()


## New Output text: remember its errors for /debug, and send it to the host to group.
func take_output(added: String) -> void:
	var found := ""
	for raw: String in added.split("\n", false):
		_context.append(raw)
		var line := raw.strip_edges()
		if _regex.search(line) != null and not line.contains("addons/joinery"):
			found = line
	if _context.size() > CONTEXT_LINES:
		_context = _context.slice(_context.size() - CONTEXT_LINES)
	_report("output", added)
	if not found.is_empty():
		_last_error = found


func _report(source: String, text: String) -> void:
	if mode == "off":
		return
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge != null and bridge.has_method("report_errors"):
		bridge.call("report_errors", source, text.right(SEND_CHARS))


## The host's list of console errors: the Errors card follows it, and in "ask" a new error
## is offered once.
func on_errors(event: Dictionary) -> void:
	mode = str(event.get("mode", mode))
	_show_card(event)
	var offer: Variant = event.get("offer", {})
	if mode == "ask" and offer is Dictionary and not (offer as Dictionary).is_empty():
		_offer_debug(offer)
	elif mode != "ask":
		_drop_offer()


## The error the next Debug sends ("" when there is none).
func last_error() -> String:
	return _last_error


## "/debug [what goes wrong]", "/debug report <id>" (a player's bug report, read by
## `joinery reports`), or Debug on the offer ([param group] is the offered error).
func debug(details := "", group: Dictionary = {}) -> void:
	var words := details.strip_edges().split(" ", false)
	if words.size() == 2 and words[0] == "report":
		debug_report(words[1])
		return
	var error := _last_error if not _last_error.is_empty() else details.strip_edges()
	if not group.is_empty():
		error = str(group.get("line", group.get("message", error)))
	var transcript: Object = dock.get("_transcript") if dock != null else null
	if error.is_empty():
		if transcript != null:
			transcript.call("add_notice", NO_ERROR, "info")
		return
	_drop_offer()
	var output := "\n".join(_context)
	if not details.strip_edges().is_empty() and error != details.strip_edges():
		output += "\nThe user says: " + details.strip_edges()
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("start_debug"):
		return
	var group_id := str(group.get("id", ""))
	if not bridge.call("start_debug", error, output, _log_tail(), "", group_id):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	_offered[error] = true
	dock.call("show_status", "Debugging…", 2.0)


## Hands a player's bug report to the debugger: the chat host reads its note, log and
## replay, and a worker stages the fix for review like any other.
func debug_report(report_id: String) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("start_debug_report"):
		return
	if not bridge.call("start_debug_report", report_id):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	dock.call("show_status", "Debugging report %s…" % report_id, 2.0)


func offer_text() -> String:
	return str(_offer.get_meta("joinery_offer", "")) if is_instance_valid(_offer) else ""


## One quiet offer for a new error group: Debug, Always fix errors like this, or Dismiss.
func _offer_debug(group: Dictionary) -> void:
	var id := str(group.get("id", ""))
	if _offered.has(id) or is_instance_valid(_offer):
		return
	var transcript: Object = dock.get("_transcript") if dock != null else null
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if transcript == null or bridge == null or not bool(bridge.get("_connected")):
		return
	_offered[id] = true
	var words := _prefix.sub(str(group.get("label", group.get("message", ""))), "")
	var short := words if words.length() <= 90 else words.left(89) + "…"
	var kind := "a warning" if str(group.get("kind", "")) == "warning" else "an error"
	_offer = (
		transcript
		. call(
			"add_offer",
			"Godot reported %s: %s" % [kind, short],
			[
				["Debug", debug.bind("", group)],
				["Always fix errors like this", _always_fix.bind(group)],
				["Dismiss", _drop_offer],
			]
		)
	)


## "Always fix errors like this": the project switches to fixing console errors on its own
## ([agents] fix_errors = "auto"), starting with this one.
func _always_fix(group: Dictionary) -> void:
	_drop_offer()
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.call("set_fix_errors", "auto", str(group.get("id", ""))):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	mode = "auto"
	dock.call("show_status", "Fixing console errors automatically", 3.0)


func _drop_offer() -> void:
	var transcript: Object = dock.get("_transcript") if dock != null else null
	if transcript != null and is_instance_valid(_offer):
		transcript.call("remove_offer", _offer)
	_offer = null


func _show_card(event: Dictionary) -> void:
	if not is_instance_valid(card):
		var groups: Variant = event.get("groups", [])
		if not groups is Array or (groups as Array).is_empty():
			return
		card = ERRORS_CARD.new()
		card.connect("fix_requested", _fix)
		card.connect("ignore_requested", _ignore)
		card.connect("review_requested", _review)
		(tasks as Node).add_child(card)
		(tasks as Node).move_child(card, 0)
		tasks.set("errors_card", card)
		card.call("apply", event)
		UI_MOTION.enter(card)
	else:
		card.call("apply", event)
	var keep := bool(card.call("has_rows"))
	if keep != card.visible:
		if keep:
			card.show()
			UI_MOTION.enter(card)
		else:
			card.hide()
	if tasks.has_method("sync_visible"):
		tasks.call("sync_visible")


func _fix(ids: Array) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.call("fix_errors", ids):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)


func _ignore(id: String, ignore: bool) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge != null:
		bridge.call("ignore_error", id, ignore)


func _review(transaction_id: String) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", transaction_id)


func _output_text() -> String:
	if output_source.is_valid():
		return str(output_source.call())
	if not Engine.is_editor_hint():
		return ""
	if not is_instance_valid(_output):
		var base := EditorInterface.get_base_control()
		for panel: Node in base.find_children("*", "EditorLog", true, false):
			for found: Node in panel.find_children("*", "RichTextLabel", true, false):
				_output = found as RichTextLabel
				break
		if not is_instance_valid(_output):
			return ""
	return _output.get_parsed_text()


func _log_tail() -> String:
	if log_source.is_valid():
		return str(log_source.call())
	if not FileAccess.file_exists(LOG_PATH):
		return ""
	var text := FileAccess.get_file_as_string(LOG_PATH)
	return text.right(LOG_CHARS)
