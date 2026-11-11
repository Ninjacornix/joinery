extends SceneTree
## Agents working alongside Journeyman: "Run alongside" in the composer, one card per task with
## its own Stop, the header's "3 agents working", Stop on the main request offering to stop the
## tasks too, and a task's change decided in the Review tab through the chat.

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

var _failed := false


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
	_check(int(backend.get("parallel")) == 3, "The host says how many turns may run")
	backend.set("_job", job)
	# The plugin links the bridge's connection state to the sidebar.
	dock.call("set_chat_state", "connected", "")
	var header: Control = dock.get("_header")
	var composer: Control = dock.get("_composer")
	var tasks: Control = dock.get("_tasks")
	var alongside: Button = composer.get("alongside_button")
	_check(not tasks.visible, "No tasks, no task list")

	# Idle: Send sends; there is nothing to run alongside.
	var prompt: TextEdit = composer.get("prompt")
	prompt.text = "Make a lamp"
	prompt.text_changed.emit()
	_check(not alongside.visible, "Idle: no Run alongside")
	dock.call("set_chat_busy", true)
	prompt.text_changed.emit()
	_check(alongside.visible, "While Journeyman works, Run alongside is offered")
	_check(str(header.call("agents_text")).is_empty(), "One agent: the header stays quiet")
	alongside.pressed.emit()
	var started := _sent(job, "task_start")
	_check(str(started.get("text", "")) == "Make a lamp", "Run alongside starts a task")
	_check(prompt.text.is_empty(), "The message left the composer")

	# The host reports the task: a card with its title, apps and step.
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "t1",
				"title": "Make a lamp",
				"state": "working",
				"apps": ["blender"],
			}
		)
	)
	var card: Control = tasks.call("card", "t1")
	_check(card != null and tasks.visible, "Each task gets its own card")
	_check(str(card.get("title_label").text) == "Make a lamp", "The card names the task")
	backend.call("_handle", {"kind": "progress", "text": "Working in Blender…", "task_id": "t1"})
	_check(card.call("step_text") == "Working in Blender…", "The card shows the task's step")
	_check(str(dock.get("_progress")) != "Working in Blender…", "Its steps stay off the main card")
	_check(header.call("agents_text") == "2 agents working", "Two agents: the header counts")
	backend.call(
		"_handle", {"kind": "task", "task_id": "t2", "title": "Make a chair", "state": "working"}
	)
	_check(header.call("agents_text") == "3 agents working", "Three agents")
	prompt.text = "And a rug"
	prompt.text_changed.emit()
	_check(not alongside.visible, "At the project's limit, no more Run alongside")

	# Each card has its own Stop.
	(card.get("stop") as Button).pressed.emit()
	var stopped := _sent(job, "cancel")
	_check(str(stopped.get("task_id", "")) == "t1", "A card's Stop stops only its task")
	backend.call(
		"_handle", {"kind": "task", "task_id": "t1", "title": "Make a lamp", "state": "stopped"}
	)
	_check(header.call("agents_text") == "2 agents working", "A stopped task leaves the count")
	var log: Array = dock.get("_transcript").get("_log")
	_check(str(log).contains("Make a lamp: stopped."), "The transcript says it stopped")

	# Stop on the main request asks whether to stop the tasks too.
	dock.call("_request_cancel")
	var stop_all := _button(dock, "Stop all")
	_check(stop_all != null, "Stop asks whether to stop the tasks too")
	_check(_button(dock, "Keep them") != null, "…or keep them")
	if stop_all != null:
		stop_all.pressed.emit()
	_check(not _sent(job, "cancel_tasks").is_empty(), "Stop all stops every task")

	# A task's change is ready: the card says so and links to the Review tab.
	var opened: Array[String] = []
	backend.connect("task_review", func(id: String) -> void: opened.append(id))
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "t2",
				"title": "Make a chair",
				"state": "in_review",
				"transaction_id": "abc",
			}
		)
	)
	var chair: Control = tasks.call("card", "t2")
	_check(chair.call("step_text") == "Ready for review", "Ready for review")
	var review := _button(chair, "Review")
	_check(review != null, "Its Review link shows")
	if review != null:
		review.pressed.emit()
	_check(opened == ["abc"], "Review opens the task's change")

	# The Review tab decides the task's change through the chat, not the CLI.
	var tab: Control = load("res://addons/joinery/review_tab.gd").new()
	tab.set("cli_enabled", false)
	root.add_child(tab)
	tab.call("follow", backend)
	backend.call(
		"_handle",
		{"kind": "file_preview", "task_id": "t2", "transaction_id": "abc", "summary": "Chair"}
	)
	tab.call("_decide", "abc", true, false)
	var decision := _sent(job, "decision")
	_check(str(decision.get("transaction_id", "")) == "abc", "Decided through the chat")
	var applied: Array[String] = []
	tab.connect(
		"changes_applied",
		func(id: String, _paths: PackedStringArray, _changes: Array) -> void: applied.append(id)
	)
	(
		backend
		. call(
			"_handle",
			{
				"kind": "file_decision",
				"task_id": "t2",
				"transaction_id": "abc",
				"accepted": true,
				"changes": [{"path": "art/chair.glb", "change": "created"}],
			}
		)
	)
	_check(applied == ["abc"], "An accepted task change is applied like any other")
	backend.call(
		"_handle", {"kind": "task", "task_id": "t2", "title": "Make a chair", "state": "accepted"}
	)
	log = dock.get("_transcript").get("_log")
	_check(str(log).contains("Make a chair: change accepted."), "The outcome is one line")
	dock.call("set_chat_busy", false)
	_check(str(header.call("agents_text")).is_empty(), "Back to one agent: quiet again")

	tab.queue_free()
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("parallel tasks: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
