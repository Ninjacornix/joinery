extends SceneTree
## The match card: "/match" (or Make it match in Settings) measures the project and shows one
## headline, a score per area and the top findings; Fix all and an area's Fix ask the chat host
## for the changes; the run's progress counts what is ready to review; Stop stops it; after an
## accept the project is checked again and the scores rise in place.

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
var _fixed := false
var _audits := 0


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


func _audit(args: PackedStringArray) -> Dictionary:
	_audits += 1
	_check(args.has("match") and args.has("--json"), "The card runs joinery match --json")
	return _fixture.call("audit", _fixed)


func _run() -> void:
	_fixture = load(get_script().resource_path.get_base_dir().path_join("match_fixture.gd"))
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	# The sidebar's usual width: below about 320 px the card shows one finding fewer.
	root.size = Vector2i(480, 760)
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
	var matcher: Node = tasks.get_node("Match")
	matcher.set("audit_runner", _audit)

	# "/match": the card shows with the audit at a glance.
	dock.call("run_command", "/match")
	await process_frame
	var card: Control = tasks.get("match_card")
	_check(card != null and tasks.visible, "The match card shows")
	_check(_audits == 1, "It measured the project once")
	_check(
		(card.get("title_label") as Label).text == "9 things drift from the art bible",
		"The headline says how much drifts: " + (card.get("title_label") as Label).text
	)
	var words: PackedStringArray = card.call("area_words")
	_check(
		(
			words
			== PackedStringArray(
				["Palette 72%", "Textures 80%", "Models 100%", "Lighting 0%", "Sound 70%"]
			)
		),
		"One score per area: " + ", ".join(words)
	)
	_check(card.call("step_text") == "4 high · 5 medium", "How serious, in one line")
	_check((card.call("finding_rows") as Array).size() == 3, "The top three findings show")
	var first: Control = (card.call("finding_rows") as Array)[0]
	_check(
		(first.find_children("", "Label", true, false)[0] as Label).text.begins_with(
			"wall.png  100% off-palette → ≤ 5%"
		),
		"A finding says what was measured and what the bible asks"
	)
	var more := _button(card, "Show all 9")
	_check(more != null, "The rest wait behind Show all")
	more.pressed.emit()
	_check((card.call("finding_rows") as Array).size() == 9, "Show all shows every finding")
	var fix_all: Button = card.get("fix_all")
	_check(
		fix_all.visible and fix_all.text == "Fix 8 things",
		"Fix all names how many: " + fix_all.text
	)
	_check(_button(card, "Fix") != null, "Each area that can be fixed has Fix")
	_check(card.get("_needs").text == "1 need you", "What Joinery can't fix is counted")
	_check(card.get("_kept").text == "2 kept as they are", "Final and excluded files are counted")
	_check((card.call("area_fix", "models") as Button).disabled, "A clean area has no Fix")

	# One area's Fix asks for that area only.
	(card.call("area_fix", "sound") as Button).pressed.emit()
	var sent := _sent(job, "match_fix")
	_check(sent.get("areas", []) == ["sound"], "An area's Fix sends its area")
	_check(card.get("state") == "fixing", "The card follows the run")
	_check(card.call("step_text") == "Planning the changes…", "It plans first")
	_check((card.get("stop") as Button).visible, "Stop shows while it runs")
	_check(not (card.get("_findings") as Control).visible, "The findings step back while it runs")
	_check((card.call("area_fix", "palette") as Button).disabled, "No Fix while it runs")
	_check(not (card.get("_areas") as Control).visible, "The scores step back while it runs")

	# The host plans three changes; one is ready to review.
	backend.call(
		"_handle", {"kind": "match", "state": "fixing", "tasks": _fixture.call("run_rows")}
	)
	_check(
		card.call("step_text") == "Making 3 changes… 1 ready to review",
		"It counts the changes: " + card.call("step_text")
	)
	_check((card.get("_review") as Button).visible, "Review shows once one is ready")
	backend.call(
		"_handle",
		{"kind": "task", "task_id": "t2", "state": "in_review", "title": "Match the lighting"}
	)
	_check(card.call("step_text") == "Making 3 changes… 2 ready to review", "A task's event counts")

	# Stop stops the run.
	(card.get("stop") as Button).pressed.emit()
	_check(_sent(job, "match_cancel").get("kind") == "match_cancel", "Stop sends match_cancel")
	backend.call(
		"_handle",
		{"kind": "match", "state": "done", "tasks": _fixture.call("run_rows", "in_review")}
	)
	_check(card.get("state") == "done", "The run is over")
	_check(
		card.call("step_text") == "3 ready to review",
		"It says what waits: " + card.call("step_text")
	)

	# Fix all, again: no areas means every area.
	card.call("show_audit", _fixture.call("audit"))
	(card.get("fix_all") as Button).pressed.emit()
	_check(_sent(job, "match_fix").get("areas", ["x"]) == [], "Fix all sends every area")
	backend.call(
		"_handle", {"kind": "match", "state": "fixing", "tasks": _fixture.call("run_rows")}
	)

	# An accept checks the project again; the scores rise in place.
	_fixed = true
	backend.call(
		"_handle", {"kind": "task", "task_id": "t1", "state": "accepted", "title": "Remap"}
	)
	_check(float(matcher.get("_recheck")) > 0.0, "An accept schedules a new check")
	matcher.set("_recheck", 0.01)
	await create_timer(0.1).timeout
	await process_frame
	_check(_audits == 2, "It measured again after the accept")
	words = card.call("area_words")
	_check(
		words[0] == "Palette 100%" and words[3] == "Lighting 100%",
		"Scores rose: " + ", ".join(words)
	)
	_check(
		(card.get("title_label") as Label).text == "1 thing drifts from the art bible",
		"The headline follows"
	)
	_check(not (card.get("fix_all") as Button).visible, "Nothing left to fix by itself")

	# Close, then Make it match from Settings opens it again.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("match_card") == null, "Dismissed")
	var settings: Control = dock.get("_settings")
	settings.call("set_open", true)
	var row := _button(settings, "Make it match")
	_check(row != null, "Settings has Make it match")
	row.pressed.emit()
	await process_frame
	_check(tasks.get("match_card") != null and not settings.visible, "Settings opens the card")
	_check(_audits == 3, "And measures again")

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("match card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
