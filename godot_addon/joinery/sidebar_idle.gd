@tool
extends Node
## "/idle" in the sidebar (ADR 0072): the fixes and checks waiting for when you're away. With
## `[agents] unhurried = "idle"` in joinery.toml, Make it match, the accessibility and
## performance fixes and the design check's agent pass wait in a queue the chat host starts
## in off-peak hours once the editor has been quiet. "/idle" lists it, "/idle now" starts it
## at once and "/idle clear" drops it; the host answers each in a notice line.

const ACTIONS := ["", "now", "clear"]

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): its bridge is the chat's.
var tasks: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var idle: Node = load("res://addons/joinery/sidebar_idle.gd").new()
	idle.set("dock", sidebar)
	idle.set("tasks", list)
	(list as Node).add_child(idle)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("idle_requested"):
		slash.connect("idle_requested", idle.ask)
	return idle


func _init() -> void:
	name = "Idle"


## Ask the chat host; [param action] is "", "now" or "clear".
func ask(action: String) -> void:
	if not action in ACTIONS:
		dock.call("show_status", "Type /idle, /idle now or /idle clear.", 4.0)
		return
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("send_idle") or not bridge.call("send_idle", action):
		dock.call("show_status", "Connect Journeyman to see what is waiting.", 4.0)
