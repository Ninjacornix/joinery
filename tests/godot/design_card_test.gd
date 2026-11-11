extends SceneTree
## The design check card: "/design" runs `joinery design --json` and shows one headline, one
## line per finding with its severity and "Ask agent to fix" (which only fills the composer),
## Look deeper asks the chat host for the designer agent, Stop stops it, a check after accepted
## changes shows only when it finds something, and the "Check the design" setting saves
## `[agents] design_check_every` through the real CLI.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const SETTING := preload("res://addons/joinery/design_setting.gd")
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
var _checks := 0
var _clean := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		if bool(condition.call()):
			return true
		await process_frame
	return bool(condition.call())


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


func _design(args: PackedStringArray) -> Dictionary:
	_checks += 1
	_check(args.has("design") and args.has("--json"), "The card runs joinery design --json")
	return _fixture.call("clean") if _clean else _fixture.call("report")


func _row_text(row: Control) -> String:
	return (row.find_children("", "Label", true, false)[0] as Label).text


func _run() -> void:
	_fixture = load(get_script().resource_path.get_base_dir().path_join("design_fixture.gd"))
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
	var designer: Node = tasks.get_node("Design")
	designer.set("check_runner", _design)

	# "/design": the card shows the check at a glance.
	dock.call("run_command", "/design")
	await process_frame
	var card: Control = tasks.get("design_card")
	_check(card != null and tasks.visible, "The design card shows")
	_check(_checks == 1, "It checked the project once")
	_check(
		(card.get("title_label") as Label).text == "7 design problems",
		"The headline counts: " + (card.get("title_label") as Label).text
	)
	_check(
		card.call("step_text") == "3 high · 3 medium · 1 low",
		"How serious, in one line: " + card.call("step_text")
	)
	var rows: Array = card.call("finding_rows")
	_check(rows.size() == 5, "The first five findings show: %d" % rows.size())
	var first: Control = rows[0]
	_check(_row_text(first) == "Dash: `dash` isn't in the input map", "One line per finding")
	_check(first.tooltip_text.contains("brief.md:12"), "The evidence is in the tooltip")
	_check(first.tooltip_text.contains("The player has no way to dash."), "And the player's view")
	_check(_button(first, "Ask agent to fix") != null, "Each finding has Ask agent to fix")
	var more := _button(card, "Show all 7")
	_check(more != null, "The rest wait behind Show all")
	more.pressed.emit()
	_check((card.call("finding_rows") as Array).size() == 7, "Show all shows every finding")
	_check((card.get("deeper") as Button).is_visible_in_tree(), "Look deeper is offered")

	# Ask agent to fix fills the composer and sends nothing.
	var sent_before: int = (job.get("lines") as Array).size()
	_button(first, "Ask agent to fix").pressed.emit()
	var prompt: TextEdit = dock.get("_composer").get("prompt")
	_check(prompt.text.begins_with("The design check found: Dash"), "The request is ready")
	_check((job.get("lines") as Array).size() == sent_before, "Nothing is sent until the user does")
	_check(_button(first, "Asked") != null, "The finding says it was asked")

	# Look deeper: the designer agent reads; Joinery's findings stay meanwhile.
	(card.get("deeper") as Button).pressed.emit()
	var start := _sent(job, "design_start")
	_check(bool(start.get("deep", false)), "Look deeper asks for the agent pass")
	_check(card.get("state") == "reading", "The card follows the agent")
	_check((card.get("stop") as Button).visible, "Stop shows while the agent reads")
	_check(
		not (card.get("deeper") as Button).is_visible_in_tree(),
		"No second Look deeper while it reads"
	)
	backend.call(
		"_handle",
		{
			"kind": "design",
			"design_id": "d1",
			"origin": "user",
			"deep": true,
			"state": "reading",
			"report": _fixture.call("report")
		}
	)
	(card.get("stop") as Button).pressed.emit()
	_check(_sent(job, "design_cancel").get("design_id") == "d1", "Stop names the run")
	backend.call(
		"_handle",
		{
			"kind": "design",
			"design_id": "d1",
			"origin": "user",
			"deep": true,
			"state": "done",
			"dropped": 2,
			"report": _fixture.call("report", true)
		}
	)
	_check(card.get("state") == "ready", "The agent pass is over")
	_check(
		(card.get("title_label") as Label).text == "8 design problems",
		"The agent's finding joins Joinery's"
	)
	_check(not (card.get("deeper") as Button).is_visible_in_tree(), "Look deeper is spent")
	_check(card.call("step_text").contains("high"), "The severity line follows")
	_check(
		(card.get("_step") as Label).tooltip_text.contains("2 of the agent's findings"),
		"Dropped findings are counted, quietly"
	)

	# Close; a clean check after accepted changes doesn't bring the card back.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("design_card") == null, "Dismissed")
	backend.call(
		"_handle",
		{
			"kind": "design",
			"design_id": "d2",
			"origin": "after_change",
			"deep": false,
			"state": "done",
			"report": _fixture.call("clean")
		}
	)
	_check(tasks.get("design_card") == null, "A clean check after changes stays quiet")
	backend.call(
		"_handle",
		{
			"kind": "design",
			"design_id": "d3",
			"origin": "after_change",
			"deep": false,
			"state": "done",
			"report": _fixture.call("report")
		}
	)
	card = tasks.get("design_card")
	_check(card != null, "A check after changes that finds something shows the card")
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame

	# Settings: Design check opens the card; a clean project says what was read.
	_clean = true
	var settings: Control = dock.get("_settings")
	settings.call("set_open", true)
	var row := _button(settings, "Design check")
	_check(row != null, "Settings has Design check")
	row.pressed.emit()
	await process_frame
	card = tasks.get("design_card")
	_check(card != null and not settings.visible, "Settings opens the card")
	_check(
		(card.get("title_label") as Label).text == "No design problems found", "A clean headline"
	)
	_check(
		card.call("step_text") == "Checked the brief, 6 input actions, 25 scripts and 8 scenes",
		"It says what it read: " + card.call("step_text")
	)
	_check((card.call("finding_rows") as Array).is_empty(), "No rows")

	backend.queue_free()
	dock.queue_free()
	await process_frame
	await _check_setting()
	print("design card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _toml() -> String:
	return FileAccess.get_file_as_string("res://joinery.toml")


## The "Check the design" picker: unknown until the project answers, Off by default, and a
## choice saved through the real `joinery design --every` into joinery.toml.
func _check_setting() -> void:
	var setting: SETTING = SETTING.new()
	_check(setting.picker.disabled, "Unknown until the project answers")
	setting.apply_status({"check_every": 7})
	_check(setting.picker.get_item_text(setting.picker.selected) == "Every 7 changes", "Hand-set")
	setting.apply_status({"error": "joinery.toml can't be read"})
	_check(setting.reason.visible, "A failure says why")
	setting.free()
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var host := Control.new()
	root.add_child(host)
	setting = SETTING.new()
	setting.cli_prefix = PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	host.add_child(setting)
	var answered := func() -> bool: return not setting.picker.disabled
	_check(await _until(answered), "The real command answers")
	_check(setting.picker.selected == 0, "Off by default")
	setting.picker.select(2)
	setting.picker.item_selected.emit(2)
	var saved := func() -> bool: return _toml().contains("design_check_every = 3")
	_check(await _until(saved), "Every 3 changes saves design_check_every = 3")
	_check(await _until(answered), "And the picker is free again")
	_check(_toml().contains("[tools]"), "The rest of joinery.toml stays")
	setting.picker.select(0)
	setting.picker.item_selected.emit(0)
	var off := func() -> bool: return _toml().contains("design_check_every = 0")
	_check(await _until(off), "Off saves 0")
	host.queue_free()
	await process_frame
