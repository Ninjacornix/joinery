@tool
extends Node
## A request's to-do in the sidebar (ADR 0060). Journeyman proposes it for a request with
## several steps; the host's "todo" events (chat_bridge.gd's todo_event) bring the card
## (todo_card.gd) in with the agents at work and move it on: waiting for Go, working,
## checking the "done when" items, and their results. Go sends the to-do as the user left it
## as a request of its own ("Go"), so it has its change card and review like any other;
## Cancel drops it.

const CARD := preload("todo_card.gd")
const UI_MOTION := preload("ui_motion.gd")

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it.
var tasks: Object
## The to-do card, while there is one.
var card: Control
var _bridge: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var todo: Node = load("res://addons/joinery/sidebar_todo.gd").new()
	todo.set("dock", sidebar)
	todo.set("tasks", list)
	(list as Node).add_child(todo)
	return todo


func _init() -> void:
	name = "TodoList"


## Follow [param bridge] (chat_bridge.gd) for the host's to-do events.
func listen(bridge: Object) -> void:
	if bridge == null or bridge == _bridge:
		return
	_bridge = bridge
	if bridge.has_signal("todo_event") and not bridge.is_connected("todo_event", on_event):
		bridge.connect("todo_event", on_event)


## One event from the host: {todo_id, state, steps?, done_when?, results?, summary?}.
func on_event(event: Dictionary) -> void:
	var id := str(event.get("todo_id", ""))
	var state := str(event.get("state", ""))
	var same := is_instance_valid(card) and str(card.get("todo_id")) == id
	if not same:
		if state in ["dropped", "cancelled"] or not event.has("steps"):
			return
		_replace(id)
	card.call("apply", event)
	tasks.call("sync_visible")


func _replace(id: String) -> void:
	if is_instance_valid(card):
		card.queue_free()
	card = CARD.new(id)
	card.connect("go_pressed", _go)
	card.connect("cancel_pressed", _cancel)
	card.connect("close_pressed", close)
	(tasks as Node).add_child(card)
	tasks.set("todo_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


## Go: the to-do as the user left it, sent as a request of its own.
func _go(line: Dictionary) -> void:
	if _bridge == null or not _bridge.has_method("hold_line") or not bool(_bridge.call("is_ready")):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		card.call("apply", {"state": "waiting"})
		return
	if bool(dock.call("_occupied")):
		dock.call("show_status", "Wait for Journeyman to finish, then press Go.", 3.0)
		card.call("apply", {"state": "waiting"})
		return
	_bridge.call("hold_line", line)
	dock.call("_start_request", "Go", [])


func _cancel() -> void:
	if _bridge != null and _bridge.has_method("cancel_todo"):
		_bridge.call("cancel_todo", str(card.get("todo_id")))


func close() -> void:
	var leaving := card
	card = null
	tasks.set("todo_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)
