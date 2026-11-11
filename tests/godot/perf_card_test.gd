extends SceneTree
## The performance card: "/perf [target]" measures the game against a target machine and shows
## one headline naming it, a score per area and the top findings; the target picker measures
## again for another machine; Fix asks the chat host for the changes with that target; a clean
## game shows what was measured.

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
var _fixture: Script
var _asked: Array[PackedStringArray] = []
var _fixed := false


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


func _audit(args: PackedStringArray) -> Dictionary:
	_asked.append(args)
	var at := args.find("--target")
	var target := args[at + 1] if at >= 0 else "steam-deck"
	return _fixture.call("perf", _fixed, target)


func _run() -> void:
	_fixture = load(get_script().resource_path.get_base_dir().path_join("checks_fixture.gd"))
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
	var tasks: Control = dock.get("_tasks")
	var checker: Node = tasks.get_node("Perf")
	checker.set("audit_runner", _audit)

	# "/perf web": measured against a web browser.
	dock.call("run_command", "/perf web")
	await process_frame
	var card: Control = tasks.get("perf_card")
	_check(card != null, "The performance card shows")
	_check(_asked.size() == 1 and _asked[0].has("perf"), "It runs joinery perf --json")
	var at := _asked[0].find("--target")
	_check(at >= 0 and _asked[0][at + 1] == "web", "with the target asked for")
	var title := (card.get("title_label") as Label).text
	_check(title == "4 problems on a Web browser", "The headline names the machine: " + title)
	var picker: OptionButton = card.get("target_picker")
	_check(picker != null and picker.get_item_text(picker.selected) == "Web browser", "Picker")
	var words: PackedStringArray = card.call("area_words")
	_check(words[0] == "Frame time 0%" and words[2] == "Lights 0%", ", ".join(words))
	var first: Control = (card.call("finding_rows") as Array)[0]
	var row_text := (first.find_children("", "Label", true, false)[0] as Label).text
	_check(row_text.begins_with("hot.gd: Costs 41.3 ms"), "A script finding: " + row_text)
	_check((card.get("fix_all") as Button).text == "Fix 3 things", "Three can be fixed")
	_check(card.get("_needs").text == "1 need you", "The frame time needs the others")

	# Another machine: measured again.
	picker.select(3)
	picker.item_selected.emit(3)
	at = _asked[-1].find("--target")
	_check(_asked.size() == 2 and _asked[-1][at + 1] == "steam-deck", "The picker measures again")
	title = (card.get("title_label") as Label).text
	_check(title == "4 problems on a Steam Deck", "for the machine picked: " + title)

	# One area's Fix: the host gets the check, the area and the target.
	(card.call("area_fix", "lighting") as Button).pressed.emit()
	var sent := _sent(job, "check_fix")
	_check(sent.get("check") == "perf" and sent.get("areas") == ["lighting"], str(sent))
	_check(sent.get("target") == "steam-deck", "with the target")
	var rows := [
		{
			"key": "lighting",
			"title": "Turn off shadows on 4 lights in Godot (1 file)",
			"area": "lighting",
			"task_id": "p1",
			"state": "in_review",
		}
	]
	backend.call("_handle", {"kind": "perf", "state": "fixing", "tasks": rows})
	_check(card.call("step_text") == "Making 1 change… 1 ready to review", card.call("step_text"))

	# Accepted: measured again, and what was measured shows on a clean game.
	_fixed = true
	backend.call("_handle", {"kind": "perf", "state": "done", "tasks": rows})
	backend.call(
		"_handle", {"kind": "task", "task_id": "p1", "state": "accepted", "title": "Shadows"}
	)
	checker.set("_recheck", 0.01)
	await create_timer(0.1).timeout
	await process_frame
	title = (card.get("title_label") as Label).text
	_check(title == "Runs within a Steam Deck's budget", "Clean after the accept: " + title)
	_check(
		card.call("step_text") == "p95 9.3 ms a frame here · 134 draw calls",
		"What was measured: " + card.call("step_text")
	)

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("perf card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
