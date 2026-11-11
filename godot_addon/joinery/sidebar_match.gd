@tool
extends Node
## "Make it match" from the sidebar (ADR 0045): "/match", or Make it match in Settings, the
## Review tab's art bible row or the art direction. The audit is `joinery match --json`, run
## off the main thread with no model; its card (match_card.gd) sits with the agents at work.
## Fix all (or one area's Fix) asks the chat host to make the changes as parallel tasks
## (chat_bridge.gd's send_match); their cards and reviews are the usual ones. After any of
## them is accepted the project is checked again, so the scores rise on the card.
##
## The accessibility and performance checks (ADR 0049, ADR 0050) work the same way with
## [member kind] "access" or "perf": "/access" and "/perf [target]", `joinery access|perf
## --json`, and send_check for the fixes. A missing game feature is the user's to add, so
## its Fix opens the feature's plan ("/feature add accessibility") instead.

const CARD := preload("match_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
## Seconds to wait after an accept before checking again (several may land together).
const RECHECK_DELAY := 1.0
## Per kind: the node's name and the sidebar_tasks.gd property that holds its card.
const NAMES := {"match": "Match", "access": "Access", "perf": "Perf"}

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## "match", "access" or "perf".
var kind := "match"
## The card, while there is one.
var card: Control
## Runs the check; tests replace it with Callable(args) -> Dictionary (the JSON).
var audit_runner := Callable()
## Performance only: the target machine ("" = the project's own).
var target := ""
## Opens a feature's plan; tests replace it with Callable(feature: String).
var feature_runner := Callable()
var _job: RefCounted
var _recheck := 0.0
var _bridge: Object


static func attach(sidebar: Control, list: Object, check := "match") -> Node:
	var matcher: Node = load("res://addons/joinery/sidebar_match.gd").new()
	matcher.set("kind", check)
	matcher.name = str(NAMES.get(check, "Match"))
	matcher.set("dock", sidebar)
	matcher.set("tasks", list)
	(list as Node).add_child(matcher)
	var slash: Object = sidebar.get("_slash")
	var requested := "%s_requested" % check
	if slash != null and slash.has_signal(requested):
		slash.connect(requested, matcher.open if check != "perf" else matcher.open_target)
	return matcher


func _init() -> void:
	name = "Match"
	set_process(false)


## "/perf [target]": the card, measured against [param machine] ("" keeps the last).
func open_target(machine: String) -> void:
	if not machine.is_empty():
		target = machine
	open()


## Show the card and measure the project (again).
func open() -> void:
	_listen()
	if not is_instance_valid(card):
		card = CARD.new(kind)
		card.connect("fix_requested", fix)
		card.connect("recheck_requested", check)
		card.connect("stop_pressed", _stop)
		card.connect("close_pressed", close)
		card.connect("review_pressed", _review)
		card.connect("target_changed", _on_target)
		(tasks as Node).add_child(card)
		tasks.set(_slot(), card)
		(tasks as Control).show()
		UI_MOTION.enter(card)
	check()


## Measure the project: `joinery <kind> --project <this project> --json`.
func check() -> void:
	if not is_instance_valid(card) or _job != null:
		return
	card.call("set_checking")
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", kind]
	)
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/"), "--json"])
	if kind == "perf" and not target.is_empty():
		args.append_array(["--target", target])
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
	if kind == "match" and not bool(audit.get("approved", true)):
		# Fixes change art and audio: the rules they follow must be the user's first.
		dock.call("show_status", "Approve the art direction first.", 3.0)
		dock.call("run_command", "/art")
		return
	var features := _features(audit, areas)
	if not features.is_empty():
		# Adding a feature is the user's step: its plan shows, Apply makes it.
		if feature_runner.is_valid():
			feature_runner.call(features[0])
		else:
			dock.call("run_command", "/feature add " + features[0])
	if kind != "match" and _others(audit, areas) == 0:
		return
	if _bridge == null or not _bridge.has_method("send_match"):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	var sent: bool = (
		_bridge.call("send_match", areas, [])
		if kind == "match"
		else _bridge.call("send_check", kind, areas, [], false, target)
	)
	if not sent:
		dock.call("show_status", "Connect Journeyman to make the changes.", 3.0)
		return
	card.call("apply_run", {"state": "auditing", "tasks": []})


func close() -> void:
	var leaving := card
	card = null
	tasks.set(_slot(), null)
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
		print("[Joinery] %s\n" % kind, output, "\n", result.get("error", ""))
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
		print("[Joinery] %s: not measured: " % kind, error)
	card.call("show_audit", data)


func _slot() -> String:
	return "%s_card" % kind


## Features whose findings are in [param areas] (all when empty): the user adds those.
func _features(audit: Dictionary, areas: Array) -> PackedStringArray:
	var found := PackedStringArray()
	for item: Variant in audit.get("findings", []):
		var entry := item as Dictionary
		var how: Variant = entry.get("fix")
		if not how is Dictionary or str((how as Dictionary).get("kind", "")) != "feature":
			continue
		var feature := str((how as Dictionary).get("feature", ""))
		if (areas.is_empty() or str(entry.get("area", "")) in areas) and not feature in found:
			found.append(feature)
	return found


## Findings in [param areas] that Joinery's own changes fix (typed or an agent's).
func _others(audit: Dictionary, areas: Array) -> int:
	var count := 0
	for item: Variant in audit.get("findings", []):
		var entry := item as Dictionary
		var how: Variant = entry.get("fix")
		if how is Dictionary and str((how as Dictionary).get("kind", "typed")) != "feature":
			if areas.is_empty() or str(entry.get("area", "")) in areas:
				count += 1
	return count


func _listen() -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == _bridge or bridge == null:
		return
	_bridge = bridge
	var event_signal := "match_event" if kind == "match" else "check_event"
	if bridge.has_signal(event_signal) and not bridge.is_connected(event_signal, _on_match):
		bridge.connect(event_signal, _on_match)
	if bridge.has_signal("task_event") and not bridge.is_connected("task_event", _on_task):
		bridge.connect("task_event", _on_task)


func _on_match(event: Dictionary) -> void:
	if kind != "match" and str(event.get("kind", "")) != kind:
		return
	if not is_instance_valid(card):
		open()
	if str(event.get("state", "")) == "checked":
		# A check after accepted changes ([agents] access_check_every / perf_check_every).
		card.call("show_audit", event.get("audit", {}))
		return
	card.call("apply_run", event)


func _on_task(event: Dictionary) -> void:
	if not is_instance_valid(card) or str(event.get("kind", "")) != "task":
		return
	var state := str(event.get("state", ""))
	if card.call("task_changed", str(event.get("task_id", "")), state) and state == "accepted":
		_recheck = RECHECK_DELAY
		set_process(true)


func _on_target(machine: String) -> void:
	target = machine
	check()


func _stop() -> void:
	if _bridge == null or not _bridge.has_method("send_match"):
		return
	if kind == "match":
		_bridge.call("send_match", [], [], true)
	else:
		_bridge.call("send_check", kind, [], [], true)


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
