@tool
extends Node
## Project memory in the sidebar (ADR 0077). "/remember <text>" sends the line to the chat host,
## which adds it to JOURNEYMAN.md as one change in Review (bridge/remember.py): nothing is
## written until the user accepts it. "Remember this" on a reply (chat_remember.gd) puts
## "/remember …" in the message box first, so the user can trim it before sending. The host
## says in a notice why a line can't be kept (empty, too long, already there, file full).

const USAGE := "Type /remember and the rule to keep."
const NOT_CONNECTED := "Connect Journeyman to remember it: the line is a change you review."

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): its bridge is the chat's.
var tasks: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var memory: Node = load("res://addons/joinery/sidebar_memory.gd").new()
	memory.set("dock", sidebar)
	memory.set("tasks", list)
	(list as Node).add_child(memory)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("remember_requested"):
		slash.connect("remember_requested", memory.remember)
	var transcript: Object = sidebar.get("_transcript")
	if transcript != null and transcript.has_signal("remember_requested"):
		transcript.connect("remember_requested", memory.prefill)
	return memory


func _init() -> void:
	name = "Memory"


## "/remember <text>": one line to JOURNEYMAN.md, through Review.
func remember(text: String) -> void:
	var line := text.strip_edges()
	if line.is_empty():
		dock.call("show_status", USAGE, 5.0)
		return
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("send_remember"):
		dock.call("show_status", NOT_CONNECTED, 4.0)
		return
	if not bridge.call("send_remember", line):
		dock.call("show_status", NOT_CONNECTED, 4.0)


## "Remember this": the words go in the message box, ready to trim and send.
func prefill(text: String) -> void:
	if text.strip_edges().is_empty():
		return
	dock.call("fill_composer", "/remember " + text.strip_edges())
