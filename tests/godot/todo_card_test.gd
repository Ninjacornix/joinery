extends SceneTree
## A request's to-do: the card shows Journeyman's steps and "done when" items before any work;
## every line edits in place and has its own remove; Go sends the list as left as a request of
## its own ("Go"); the items then read Pass, Fail or Inconclusive with the evidence, on the card
## and in the review. Cancel drops a to-do; one dropped for a new message steps aside.

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
const STEPS := [
	"Put a lever beside the gate",
	"Open the gate when the lever is pulled",
	"Put the key in the vault",
]
const DONE := [
	{"id": "d1", "text": "The player can reach the vault", "kind": "reach"},
	{"id": "d2", "text": "The gate opens when the lever is pulled", "kind": "test"},
	{"id": "d3", "text": "No errors in 60 s of play", "kind": "no_errors"},
	{"id": "d4", "text": "The key looks like brass", "kind": "look"},
]

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


func _results() -> Array:
	return [
		{
			"id": "d1",
			"text": "The player can reach the vault",
			"status": "fail",
			"evidence": "The bot didn't get to Vault in 1:00 of play (closest 3.4), seed 4242.",
		},
		{
			"id": "d3",
			"text": "No errors in 60 s of play",
			"status": "pass",
			"evidence": "No errors in 1:00 of play, seed 4242.",
		},
		{
			"id": "d2",
			"text": "The gate opens when the lever is pulled",
			"status": "inconclusive",
			"evidence": "res://tests/test_gate.gd didn't run: the project has no GUT tests set up.",
		},
	]


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
	backend.set("_job", job)
	dock.call("set_chat_state", "connected", "")
	dock.prompt_submitted.connect(
		func(text: String, targets: Array) -> void: backend.call("submit", text, targets)
	)
	var tasks: Control = dock.get("_tasks")

	# Journeyman proposes: the card shows the steps and what done means, ready to edit.
	var event := {
		"kind": "todo", "todo_id": "t1", "state": "waiting", "steps": STEPS, "done_when": DONE
	}
	backend.call("_handle", event)
	var card: Control = tasks.get("todo_card")
	_check(card != null and card.is_visible_in_tree(), "The to-do card shows")
	_check(card.call("steps") == PackedStringArray(STEPS), "Its steps: %s" % card.call("steps"))
	_check((card.call("done_rows") as Array).size() == 4, "Its done-when items")
	_check((card.get("go") as Button).is_visible_in_tree(), "Go is there")
	_check(card.call("step_text") == "Edit anything, then press Go.", card.call("step_text"))
	var fields := card.find_children("Words", "LineEdit", true, false)
	_check(fields.size() == 7, "Every line edits in place: %d" % fields.size())

	# The user rewords the first step, removes the second and the look item, presses Go.
	(fields[0] as LineEdit).text = "Put a lever by the gate"
	var removes: Array[Button] = card.call("remove_buttons")
	_check(removes.size() == 7, "Every line has its remove")
	removes[1].pressed.emit()
	removes[6].pressed.emit()
	_check(
		card.call("steps") == PackedStringArray([STEPS[0].replace("beside", "by"), STEPS[2]]),
		"Edited steps: %s" % card.call("steps")
	)
	(card.get("go") as Button).pressed.emit()
	var go := _sent(job, "todo_go")
	_check(str(go.get("text", "")) == "Go", "Go is a request of its own: %s" % go)
	_check(str(go.get("todo_id", "")) == "t1", "For this to-do")
	_check(
		go.get("steps") == [STEPS[0].replace("beside", "by"), STEPS[2]],
		"With the steps as left: %s" % [go.get("steps")]
	)
	var ids: Array = []
	for item: Variant in go.get("done_when", []):
		ids.append(str((item as Dictionary)["id"]))
	_check(ids == ["d1", "d2", "d3"], "And the done-when items as left: %s" % [ids])
	_check(_sent(job, "prompt").is_empty(), "Not as a plain prompt")

	# Working: the list is read-only; then the items are checked.
	event = {
		"kind": "todo",
		"todo_id": "t1",
		"state": "going",
		"steps": go["steps"],
		"done_when": [DONE[0], DONE[1], DONE[2]],
	}
	backend.call("_handle", event)
	_check(card.find_children("Words", "LineEdit", true, false).is_empty(), "Read-only now")
	_check(not (card.get("go") as Button).is_visible_in_tree(), "No Go while working")
	_check(card.call("step_text") == "Working through the steps…", card.call("step_text"))
	backend.call("_handle", {"kind": "todo", "todo_id": "t1", "state": "checking"})
	_check(card.call("step_text") == "Checking what done means…", "It says it is checking")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "todo",
				"todo_id": "t1",
				"state": "checked",
				"results": _results(),
				"summary": "1 of 3 done",
			}
		)
	)
	_check(card.call("step_text") == "1 of 3 done", "Checked: %s" % card.call("step_text"))
	var rows: Array[Dictionary] = card.call("done_rows")
	_check(rows[0]["status"] == "fail" and str(rows[0]["evidence"]).contains("Vault"), str(rows))
	_check(rows[1]["status"] == "inconclusive" and rows[2]["status"] == "pass", str(rows))
	_check(card.find_children("Evidence", "Label", true, false).size() == 3, "Evidence shows")
	_check((card.get("close") as Button).visible, "It can be dismissed")

	# The review shows the same results under what changed.
	var summary: Control = load("res://addons/joinery/review_summary.gd").new()
	dock.add_child(summary)
	summary.call("show_review", {"changes": [{"path": "game/gate.gd", "change": "modified"}]})
	_check(summary.call("done_text") == "", "No to-do, no done-when chip")
	summary.call("update", {"done_when": _results()})
	_check(summary.call("done_text") == "1 of 3 done", "Chip: %s" % summary.call("done_text"))
	var shown: Array[Dictionary] = summary.call("done_rows")
	_check(shown.size() == 3 and shown[0]["status"] == "fail", "Rows: %s" % [shown])
	_check(str(shown[2]["evidence"]).begins_with("res://tests/test_gate.gd"), "%s" % [shown])
	_check(not summary.call("simple", 0), "A Fail opens the panel rather than deciding inline")
	summary.queue_free()

	# A second to-do, cancelled.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	_check(tasks.get("todo_card") == null, "Dismissed")
	event = {"kind": "todo", "todo_id": "t2", "state": "waiting", "steps": STEPS, "done_when": []}
	backend.call("_handle", event)
	card = tasks.get("todo_card")
	_check(not (card.get("_done_title") as Control).visible, "No done-when items, no heading")
	(card.get("cancel") as Button).pressed.emit()
	_check(str(_sent(job, "todo_cancel").get("todo_id", "")) == "t2", "Cancel drops it")
	backend.call("_handle", {"kind": "todo", "todo_id": "t2", "state": "cancelled"})
	_check(card.call("step_text") == "Cancelled", "It says so")
	# A to-do the card never showed, dropped, changes nothing.
	backend.call("_handle", {"kind": "todo", "todo_id": "t9", "state": "dropped"})
	_check(tasks.get("todo_card") == card, "An unknown to-do is ignored")

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("todo card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
