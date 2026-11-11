@tool
extends Node
## "Make it match" from the sidebar (ADR 0045): "/match", or Make it match in Settings, the
## Review tab's art bible row or the art direction. The audit is `joinery match --json`, run
## off the main thread with no model; its card (match_card.gd) sits with the agents at work.
## Fix all (or one area's Fix) asks the chat host to make the changes as parallel tasks
## (chat_bridge.gd's send_match); their cards and reviews are the usual ones. After any of
## them is accepted the project is checked again, so the scores rise on the card.

const CARD := preload("match_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
## Seconds to wait after an accept before checking again (several may land together).
const RECHECK_DELAY := 1.0

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## The match card, while there is one.
var card: Control
## Runs `joinery match`; tests replace it with Callable(args) -> Dictionary (the JSON).
var audit_runner := Callable()
var _job: RefCounted
var _recheck := 0.0
var _bridge: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var matcher: Node = load("res://addons/joinery/sidebar_match.gd").new()
	matcher.set("dock", sidebar)
	matcher.set("tasks", list)
	(list as Node).add_child(matcher)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("match_requested"):
		slash.connect("match_requested", matcher.open)
	return matcher


func _init() -> void:
	name = "Match"
	set_process(false)


## Show the card and measure the project (again).
func open() -> void:
	_listen()
	if not is_instance_valid(card):
		card = CARD.new()
		card.connect("fix_requested", fix)
		card.connect("recheck_requested", check)
		card.connect("stop_pressed", _stop)
		card.connect("close_pressed", close)
		card.connect("review_pressed", _review)
		(tasks as Node).add_child(card)
		tasks.set("match_card", card)
		(tasks as Control).show()
		UI_MOTION.enter(card)
	check()


## Measure the project: `joinery match --project <this project> --json`.
func check() -> void:
	if not is_instance_valid(card) or _job != null:
		return
	card.call("set_checking")
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", "match"]
	)
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/"), "--json"])
	if audit_runner.is_valid():
		_take(audit_runner.call(args))
		return
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args) != OK:
		_job = null
		card.call("apply_run", {"state": "failed", "message": "Couldn't start Joinery."})
		return
	set_process(true)


## Fix all ([param areas] empty) or the areas named, as reviewed changes.
func fix(areas: Array) -> void:
	_listen()
	var audit: Dictionary = card.get("audit") if is_instance_valid(card) else {}
	if not bool(audit.get("approved", true)):
		# Fixes change art and audio: the rules they follow must be the user's first.
		dock.call("show_status", "Approve the art direction first.", 3.0)
		dock.call("run_command", "/art")
		return
	if _bridge == null or not _bridge.has_method("send_match"):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	if not _bridge.call("send_match", areas, []):
		dock.call("show_status", "Connect Journeyman to make the changes.", 3.0)
		return
	card.call("apply_run", {"state": "auditing", "tasks": []})


func close() -> void:
	var leaving := card
	card = null
	tasks.set("match_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


func _process(delta: float) -> void:
	if _recheck > 0.0:
		_recheck -= delta
		if _recheck <= 0.0:
			check()
	if _job == null:
		if _recheck <= 0.0:
			set_process(false)
		return
	var result: Dictionary = _job.poll()
	if result.is_empty():
		return
	_job = null
	var output := str(result.get("output", ""))
	if int(result.get("code", 1)) != 0:
		print("[Joinery] match\n", output, "\n", result.get("error", ""))
	_take(SLASH.parse_json(output))


func _take(data: Dictionary) -> void:
	if not is_instance_valid(card):
		return
	if not data.has("areas"):
		card.call(
			"apply_run",
			{"state": "failed", "message": "Couldn't check the project. Details are in Output."}
		)
		return
	for error: Variant in data.get("errors", []):
		print("[Joinery] match: not measured: ", error)
	card.call("show_audit", data)


func _listen() -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == _bridge or bridge == null:
		return
	_bridge = bridge
	if bridge.has_signal("match_event") and not bridge.is_connected("match_event", _on_match):
		bridge.connect("match_event", _on_match)
	if bridge.has_signal("task_event") and not bridge.is_connected("task_event", _on_task):
		bridge.connect("task_event", _on_task)


func _on_match(event: Dictionary) -> void:
	if not is_instance_valid(card):
		open()
	card.call("apply_run", event)


func _on_task(event: Dictionary) -> void:
	if not is_instance_valid(card) or str(event.get("kind", "")) != "task":
		return
	var state := str(event.get("state", ""))
	if card.call("task_changed", str(event.get("task_id", "")), state) and state == "accepted":
		_recheck = RECHECK_DELAY
		set_process(true)


func _stop() -> void:
	if _bridge != null and _bridge.has_method("send_match"):
		_bridge.call("send_match", [], [], true)


## Review: the first change of this run that waits in the Review tab.
func _review() -> void:
	for row: Variant in card.get("_run"):
		var entry := row as Dictionary
		if str(entry.get("state", "")) != "in_review":
			continue
		var each: Control = tasks.call("card", str(entry.get("task_id", "")))
		if each != null and _bridge != null and _bridge.has_method("show_task_review"):
			_bridge.call("show_task_review", str(each.get("transaction_id")))
			return
