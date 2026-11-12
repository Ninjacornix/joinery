extends SceneTree
## The tidy card: "/tidy" runs `joinery tidy --json` and shows one headline, the kinds in one
## line and the findings grouped by kind, maybe-used ones apart; Remove sends one removal to the
## chat host (never deletes here) and its rows step out; without Journeyman nothing is sent and
## the rows come back; Keep runs `--keep`; Free caches runs `--clear-caches` and says what went;
## Settings and the Review tab's Game checks row open it; and the real CLI answers.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const CHECKS := preload("res://addons/joinery/review_checks.gd")
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
var _calls: Array[PackedStringArray] = []
var _clean := false


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


func _tidy(args: PackedStringArray) -> Dictionary:
	_calls.append(args)
	if args.has("--keep"):
		return {"keep": ["art/crate_v1.glb"], "key": "art/crate_v1.glb", "message": "Kept"}
	if args.has("--clear-caches"):
		return {
			"cleared": [],
			"size": 124 << 20,
			"message": "Freed 124 MB of Joinery's caches: review renders (120 MB)"
		}
	return _fixture.call("clean") if _clean else _fixture.call("report")


func _row_text(row: Control) -> String:
	return _toggle(row).text


func _toggle(row: Control) -> Button:
	for found: Node in row.find_children("", "Button", true, false):
		if (found as Button).toggle_mode:
			return found as Button
	return null


## Open a row: why, what goes with it, Keep and Remove.
func _open(row: Control) -> void:
	_toggle(row).button_pressed = true


func _run() -> void:
	_fixture = load(get_script().resource_path.get_base_dir().path_join("tidy_fixture.gd"))
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
	var tidier: Node = tasks.get_node("Tidy")
	tidier.set("runner", _tidy)

	# "/tidy": what the game never uses, at a glance.
	dock.call("run_command", "/tidy")
	await process_frame
	var card: Control = tasks.get("tidy_card")
	_check(card != null and tasks.visible, "The tidy card shows")
	_check(_calls.size() == 1 and _calls[0].has("--json"), "It ran joinery tidy --json")
	_check(
		(card.get("title_label") as Label).text == "7 unused things · 38 MB",
		"The headline counts and sizes: " + (card.get("title_label") as Label).text
	)
	_check(
		card.call("step_text") == "18 scenes, 41 scripts", "What it read: " + card.call("step_text")
	)
	_check(
		(
			card.call("kinds_line")
			== "2 models · 2 textures · 1 sound · 1 input action · 1 node in a scene"
		),
		"The kinds in one line: " + card.call("kinds_line")
	)
	var rows: Array = card.call("finding_rows")
	_check(rows.size() == 5, "The first five findings show: %d" % rows.size())
	var first: Control = rows[0]
	_check(_row_text(first) == "old_tower.glb", "One row per finding, biggest first in its kind")
	var tip := _toggle(first).tooltip_text
	_check(tip.contains("Nothing in the game loads it"), "Why is in the tooltip")
	_check(tip.contains("old_tower.blend (native source)"), "And what goes with it")
	_check(_button(first, "Remove") == null, "A closed row is a line to scan")
	_open(first)
	_check(
		_button(first, "Keep") != null and _button(first, "Remove") != null, "Open: Keep, Remove"
	)
	_check(_button(card, "Models") == null, "Group names are headings, not buttons")
	var more := _button(card, "Show all 9")
	_check(more != null, "The rest wait behind Show all")
	more.pressed.emit()
	rows = card.call("finding_rows")
	_check(rows.size() == 9, "Show all shows every finding")
	_check(_row_text(rows[8]) == "level_8.tscn", "Maybe-used ones come last")
	var all := _button(card, "Remove 7")
	_check(all != null, "Remove counts only the sure ones")
	_check(_button(card, "Free 124 MB of Joinery caches") != null, "Caches are offered quietly")

	# Remove sends one removal to the host; nothing is deleted here.
	all.pressed.emit()
	var sent := _sent(job, "tidy_remove")
	_check((sent.get("ids", []) as Array).size() == 7, "All sure ids go in one removal")
	_check(not (sent.get("ids", []) as Array).has("m1"), "Never a maybe-used one")
	_check(card.get("state") == "sent", "The card waits for Review")
	_check(
		(card.get("title_label") as Label).text == "7 unused things wait in Review",
		"And says where: " + (card.get("title_label") as Label).text
	)
	_check(
		card.call("step_text") == "Nothing is deleted until you accept",
		"Nothing is deleted yet: " + card.call("step_text")
	)
	_check((card.call("finding_rows") as Array).size() == 2, "Only the maybe-used rows stay")

	# One row, without Journeyman: nothing is sent and the row comes back, with why.
	card.call("show_report", _fixture.call("report"))
	backend.call("_handle", {"kind": "connection", "state": "error", "message": "gone"})
	var before: int = (job.get("lines") as Array).size()
	rows = card.call("finding_rows")
	_open(rows[1])
	_button(rows[1], "Remove").pressed.emit()
	await process_frame
	_check((job.get("lines") as Array).size() == before, "Nothing is sent when not connected")
	_check(card.call("step_text").begins_with("Connect Journeyman"), "It says why")
	_check((card.call("finding_rows") as Array).size() == 5, "The row is back")
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})

	# Keep: the user's own list, through the CLI; the row steps out.
	rows = card.call("finding_rows")
	_open(rows[1])
	_button(rows[1], "Keep").pressed.emit()
	_check(_calls[-1].has("--keep") and _calls[-1].has("art/crate_v1.glb"), "Keep runs --keep KEY")
	_check(
		(card.get("title_label") as Label).text.begins_with("6 unused things"),
		"The headline follows: " + (card.get("title_label") as Label).text
	)

	# Free caches: at once, and the card says what went.
	_button(card, "Free 124 MB of Joinery caches").pressed.emit()
	_check(_calls[-1].has("--clear-caches"), "Free caches runs --clear-caches")
	_check(card.call("step_text").begins_with("Freed 124 MB"), "It says what went")
	_check(_button(card, "Free 124 MB of Joinery caches") == null, "And the offer is spent")

	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("tidy_card") == null, "Dismissed")

	# Settings: Unused content opens the card; a clean project says so.
	_clean = true
	var settings: Control = dock.get("_settings")
	settings.call("set_open", true)
	var row := _button(settings, "Unused content")
	_check(row != null, "Settings has Unused content")
	row.pressed.emit()
	await process_frame
	card = tasks.get("tidy_card")
	_check(card != null and not settings.visible, "Settings opens the card")
	_check((card.get("title_label") as Label).text == "Nothing unused", "A clean headline")
	_check(card.call("step_text").begins_with("9 scenes"), "It says what it read")
	_check((card.call("finding_rows") as Array).is_empty(), "No rows")
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame

	# The Review tab's Game checks row opens it too.
	var slot := VBoxContainer.new()
	root.add_child(slot)
	var checks: Control = CHECKS.attach(slot, dock.get("_slash"))
	(checks.get_node("Open_tidy") as Button).pressed.emit()
	await process_frame
	_check(tasks.get("tidy_card") != null, "Tidy on the Game checks row opens the card")
	slot.queue_free()

	await _real_cli(tidier, tasks)
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("tidy card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The real `joinery tidy --json` on this project answers with a report.
func _real_cli(tidier: Node, tasks: Control) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var said: Array = []
	var real := func(args: PackedStringArray) -> Dictionary:
		var at := args.find("tidy")
		var argv := PackedStringArray(
			["-c", "import sys; from joinery.cli import main; sys.exit(main())"]
		)
		argv.append_array(args.slice(at))
		var output: Array = []
		OS.execute(python, argv, output, true)
		said.append("".join(output))
		return load("res://addons/joinery/slash_commands.gd").call("parse_json", "".join(output))
	tidier.set("runner", real)
	tidier.call("check")
	var card: Control = tasks.get("tidy_card")
	var data: Dictionary = card.get("report")
	_check(data.has("findings") and data.has("summary"), "The real CLI answers: %s" % data)
	_check(card.get("state") == "ready", "And the card shows it: %s" % str(said).left(2000))
