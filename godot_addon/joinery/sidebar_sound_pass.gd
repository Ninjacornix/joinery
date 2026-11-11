@tool
extends Node
## The sound pass from the sidebar: "/sound-pass [focus]" asks the chat host to listen to the
## level and make what it is missing (chat_bridge.gd's send_sound_pass). Its card
## (sound_pass_card.gd) sits with the agents at work; each sound is a task with its own card and
## review. The sound designer's short report goes to the transcript.

const CARD := preload("sound_pass_card.gd")
const UI_MOTION := preload("ui_motion.gd")

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## The sound pass card, while there is one.
var card: Control
var _bridge: Object
var _reported := false


static func attach(sidebar: Control, list: Object) -> Node:
	var sounder: Node = load("res://addons/joinery/sidebar_sound_pass.gd").new()
	sounder.set("dock", sidebar)
	sounder.set("tasks", list)
	(list as Node).add_child(sounder)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("sound_pass_requested"):
		slash.connect("sound_pass_requested", sounder.start)
	return sounder


func _init() -> void:
	name = "SoundPass"


## "/sound-pass [focus]": the host listens and makes the sounds; the card shows at once.
func start(focus: String) -> void:
	_listen()
	if is_instance_valid(card) and card.call("working"):
		dock.call("show_status", "The sound pass is already running.", 3.0)
		return
	if _bridge == null or not _bridge.has_method("send_sound_pass"):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	if not _bridge.call("send_sound_pass", focus):
		dock.call("show_status", "Connect Journeyman to run the sound pass.", 3.0)
		return
	_open()


func close() -> void:
	var leaving := card
	card = null
	tasks.set("sound_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


func _open() -> void:
	if is_instance_valid(card):
		card.queue_free()
	_reported = false
	card = CARD.new()
	card.connect("stop_pressed", _stop)
	card.connect("close_pressed", close)
	card.connect("review_pressed", _review)
	(tasks as Node).add_child(card)
	tasks.set("sound_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


func _listen() -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == _bridge or bridge == null:
		return
	_bridge = bridge
	if bridge.has_signal("sound_pass_event"):
		if not bridge.is_connected("sound_pass_event", _on_event):
			bridge.connect("sound_pass_event", _on_event)
	if bridge.has_signal("task_event") and not bridge.is_connected("task_event", _on_task):
		bridge.connect("task_event", _on_task)


func _on_event(event: Dictionary) -> void:
	if not is_instance_valid(card):
		_open()
	card.call("apply", event)
	var transcript: Object = dock.get("_transcript")
	var words := str(event.get("summary", ""))
	if not words.is_empty() and not _reported and transcript != null:
		_reported = true
		transcript.call("add_message", "Sound designer", words)
	if bool(event.get("art_gate", false)):
		# The sounds follow the art bible: its rules must be the user's first.
		dock.call("run_command", "/art")


func _on_task(event: Dictionary) -> void:
	if not is_instance_valid(card) or str(event.get("kind", "")) != "task":
		return
	card.call("task_changed", str(event.get("task_id", "")), str(event.get("state", "")))


func _stop() -> void:
	if _bridge != null and _bridge.has_method("send_sound_pass"):
		_bridge.call("send_sound_pass", "", "", true)


## Review: the first change of this pass that waits in the Review tab.
func _review() -> void:
	for row: Variant in card.get("_run"):
		var entry := row as Dictionary
		if str(entry.get("state", "")) != "in_review":
			continue
		var each: Control = tasks.call("card", str(entry.get("task_id", "")))
		if each != null and _bridge != null and _bridge.has_method("show_task_review"):
			_bridge.call("show_task_review", str(each.get("transaction_id")))
			return
