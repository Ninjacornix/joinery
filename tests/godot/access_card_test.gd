extends SceneTree
## The accessibility card: "/access" (or Accessibility check in Settings, or the Review tab's
## Game checks row) checks the game and shows one headline, a score per area and the top
## findings in their own words. Fix all asks the chat host for Joinery's own changes and
## opens the Accessibility feature's plan for what only a feature adds; the run counts what is
## ready to review; a check after accepted changes updates the card in place.

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
var _audits := 0
var _features: Array[String] = []


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
	_check(args.has("access") and args.has("--json"), "The card runs joinery access --json")
	return _fixture.call("access")


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
	var checker: Node = tasks.get_node("Access")
	checker.set("audit_runner", _audit)
	checker.set("feature_runner", func(feature: String) -> void: _features.append(feature))
	(tasks.get_node("Perf") as Node).set(
		"audit_runner", func(_args: PackedStringArray) -> Dictionary: return _fixture.call("perf")
	)

	# "/access": the card at a glance.
	dock.call("run_command", "/access")
	await process_frame
	var card: Control = tasks.get("access_card")
	_check(card != null and tasks.visible, "The accessibility card shows")
	_check(tasks.get("match_card") == null, "It is not the match card")
	_check(_audits == 1, "It checked the game once")
	var title := (card.get("title_label") as Label).text
	_check(title == "8 accessibility problems", "The headline counts the problems: " + title)
	var words: PackedStringArray = card.call("area_words")
	_check(words[0] == "Contrast 0%" and words[5] == "Screen readers 75%", ", ".join(words))
	_check(card.call("step_text") == "3 high · 4 medium · 1 low", "How serious, in one line")
	var first: Control = (card.call("finding_rows") as Array)[0]
	var row_text := (first.find_children("", "Label", true, false)[0] as Label).text
	_check(
		row_text.begins_with('main.tscn: "Score: 12" (#777777 on #888888)'),
		"A finding reads in its own words, with its file: " + row_text
	)
	var fix_all: Button = card.get("fix_all")
	_check(fix_all.text == "Fix 7 things", "Fix all names how many: " + fix_all.text)
	_check(card.get("_needs").text == "1 need you", "What Joinery can't fix is counted")

	# The same findings by Xbox Accessibility Guideline, with what Joinery checks.
	var guide: Control = card.get("guidelines")
	_check(guide != null and (guide.get("head") as Control).visible, "The guidelines are offered")
	_check(guide.call("coverage_text") == "9 of 23 checked", guide.call("coverage_text"))
	var listed: PackedStringArray = guide.call("row_words")
	_check(listed[0] == "101 Text display · 1 problem", "Failing first: %s" % listed)
	_check(listed[1] == "102 Contrast · 2 problems", "%s" % listed)
	_check(
		listed.has("107 Input · passes") and listed.has("118 Photosensitivity · not measured"),
		"%s" % listed
	)
	_check(
		(guide.call("left_text") as String).begins_with("A person judges the other 14: 105 Audio"),
		guide.call("left_text")
	)
	guide.call("set_open", true)
	_check(not (card.get("_areas") as Control).visible, "The list takes the scores' place")
	var opener := _button(guide, "102 Contrast")
	_check(opener != null, "A failing guideline opens")
	opener.button_pressed = true
	var shown := PackedStringArray()
	for label: Node in guide.find_children("", "Label", true, false):
		if (label as Label).is_visible_in_tree():
			shown.append((label as Label).text)
	_check(
		shown.has('main.tscn: "Score: 12" (#777777 on #888888) has contrast 1.3:1.'), "%s" % shown
	)
	(guide.get("toggle") as Button).button_pressed = false
	_check((card.get("_areas") as Control).visible, "Closing brings the scores back")
	_check((card.get("_more") as Control).visible, "and Show all")

	# A feature-only area: its Fix opens the feature's plan; nothing goes to the host.
	(card.call("area_fix", "captions") as Button).pressed.emit()
	_check(_features == ["accessibility"], "Captions' Fix opens the Accessibility plan")
	_check(_sent(job, "check_fix").get("areas", ["x"]) == ["captions"], "and asks for the rest")
	backend.call("_handle", {"kind": "access", "state": "done", "tasks": []})

	# Fix all: the host's changes, and the feature plan once.
	_features.clear()
	card.call("show_audit", _fixture.call("access"))
	fix_all.pressed.emit()
	var sent := _sent(job, "check_fix")
	_check(sent.get("check") == "access" and sent.get("areas") == [], "Fix all asks for all")
	_check(_features == ["accessibility"], "Fix all opens the feature's plan once")
	_check(card.get("state") == "fixing", "The card follows the run")
	backend.call(
		"_handle", {"kind": "access", "state": "fixing", "tasks": _fixture.call("run_rows")}
	)
	_check(
		card.call("step_text") == "Making 2 changes… 1 ready to review",
		"It counts the changes: " + card.call("step_text")
	)
	backend.call("_handle", {"kind": "perf", "state": "failed", "tasks": [], "message": "x"})
	_check(card.get("state") == "fixing", "Another check's events are not this card's")
	(card.get("stop") as Button).pressed.emit()
	_check(_sent(job, "check_cancel").get("check") == "access", "Stop sends check_cancel")
	backend.call(
		"_handle",
		{"kind": "access", "state": "done", "tasks": _fixture.call("run_rows", "accepted")}
	)
	_check(card.get("state") == "done", "The run is over")

	# A check after accepted changes: the scores rise in place.
	backend.call(
		"_handle",
		{"kind": "access", "state": "checked", "tasks": [], "audit": _fixture.call("access", true)}
	)
	title = (card.get("title_label") as Label).text
	_check(title == "No accessibility problems found", "The periodic check shows: " + title)
	_check(card.call("step_text") == "Checked 14 controls on 4 screens", card.call("step_text"))

	# Close; Settings and the Review tab's row open it again.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("access_card") == null, "Dismissed")
	var settings: Control = dock.get("_settings")
	settings.call("set_open", true)
	var row := _button(settings, "Accessibility check")
	_check(row != null, "Settings has Accessibility check")
	row.pressed.emit()
	await process_frame
	_check(tasks.get("access_card") != null and _audits == 2, "Settings opens and checks")
	_review_row(dock)

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("access card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The Review tab's Game checks row reads the saved reports and opens the cards.
func _review_row(dock: Control) -> void:
	var folder := ProjectSettings.globalize_path("res://.joinery/checks")
	DirAccess.make_dir_recursive_absolute(folder)
	var file := FileAccess.open(folder.path_join("access.json"), FileAccess.WRITE)
	file.store_string(JSON.stringify(_fixture.call("access")))
	file.close()
	var slot := VBoxContainer.new()
	root.add_child(slot)
	var row: Control = load("res://addons/joinery/review_checks.gd").call(
		"attach", slot, dock.get("_slash")
	)
	var status: Label = row.get("status")
	_check(status.text == "Accessibility 38%", "The row shows the last score: " + status.text)
	(row.get_node("Open_perf") as Button).pressed.emit()
	_check(dock.get("_tasks").get("perf_card") != null, "Its Performance button opens that card")
	slot.queue_free()
	DirAccess.remove_absolute(folder.path_join("access.json"))
