@tool
extends Node
## Debugging from the sidebar: "/debug", and one quiet offer when Godot's Output shows a new
## error ("Godot reported an error: … Debug / Dismiss"). Debug sends the error line, the Output
## text around it and the tail of the game's log to the chat host (chat_bridge.gd's start_debug).
## A debugger agent finds the cause (read-only, offline) and a worker stages the fix; the user
## sees a "Debugging: <error>" card (sidebar_tasks.gd) and then a normal review.
##
## Output is read every second from the editor's Output panel. Errors from Joinery itself are
## ignored, each error line is offered once, and only one offer shows at a time.

## Seconds between reads of the Output panel.
const POLL := 1.0
## Output lines sent with an error.
const CONTEXT_LINES := 120
## Characters of the game's log sent with an error.
const LOG_CHARS := 8000
const LOG_PATH := "user://logs/godot.log"
const NO_ERROR := "No error to debug yet. Run the game, or say what goes wrong after /debug."

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): its bridge is the chat's.
var tasks: Object
## Returns the Output panel's text; tests replace it. Empty: the editor's own Output panel.
var output_source := Callable()
## Returns the game's log tail; tests replace it.
var log_source := Callable()
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


## Read what Output gained since the last read; the first read only sets where to start.
func poll() -> void:
	var text := _output_text()
	if _read < 0 or text.length() < _read:
		# The first read, or Output was cleared: start from here.
		_read = text.length()
		return
	var added := text.substr(_read)
	_read = text.length()
	if not added.is_empty():
		take_output(added)


## New Output text: remember its errors and offer to debug the newest.
func take_output(added: String) -> void:
	var found := ""
	for raw: String in added.split("\n", false):
		_context.append(raw)
		var line := raw.strip_edges()
		if _regex.search(line) != null and not line.contains("addons/joinery"):
			found = line
	if _context.size() > CONTEXT_LINES:
		_context = _context.slice(_context.size() - CONTEXT_LINES)
	if found.is_empty():
		return
	_last_error = found
	_offer_debug(found)


## The error the next Debug sends ("" when there is none).
func last_error() -> String:
	return _last_error


## "/debug [what goes wrong]", or Debug on the offer.
func debug(details := "") -> void:
	var error := _last_error if not _last_error.is_empty() else details.strip_edges()
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
	if not bridge.call("start_debug", error, output, _log_tail()):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	_offered[error] = true
	dock.call("show_status", "Debugging…", 2.0)


func offer_text() -> String:
	return str(_offer.get_meta("joinery_offer", "")) if is_instance_valid(_offer) else ""


func _offer_debug(error: String) -> void:
	if _offered.has(error) or is_instance_valid(_offer):
		return
	var transcript: Object = dock.get("_transcript") if dock != null else null
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if transcript == null or bridge == null or not bool(bridge.get("_connected")):
		return
	_offered[error] = true
	var words := _prefix.sub(error, "")
	var short := words if words.length() <= 90 else words.left(89) + "…"
	_offer = transcript.call(
		"add_offer",
		"Godot reported an error: " + short,
		[["Debug", debug.bind("")], ["Dismiss", _drop_offer]]
	)


func _drop_offer() -> void:
	var transcript: Object = dock.get("_transcript") if dock != null else null
	if transcript != null and is_instance_valid(_offer):
		transcript.call("remove_offer", _offer)
	_offer = null


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
