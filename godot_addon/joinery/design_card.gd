@tool
extends PanelContainer
## The design check (`/design`): what breaks the game or goes against its own brief, at a
## glance. One headline ("3 design problems"), one line per finding with its severity dot and
## "Ask agent to fix", the evidence in the line's tooltip. Nothing here changes the game: Ask
## agent to fix puts a ready request in the composer, which the user sends as a normal
## reviewed change. "Look deeper" asks the designer agent for judgement calls; each one it
## makes cites evidence or is dropped before it gets here.

signal fix_requested(finding: Dictionary)
signal deeper_requested
signal recheck_requested
signal stop_pressed
signal close_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## Severity -> status dot state.
const DOTS := {"high": "attention", "medium": "working", "low": "off"}
## Findings shown before "Show all".
const FIRST_FINDINGS := 5

## checking, reading (the agent pass), ready or failed.
var state := "checking"
var report: Dictionary = {}
var title_label: Label
var close: Button
var stop: Button
var deeper: Button
var again: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _findings: VBoxContainer
var _more: Button
var _foot: HBoxContainer
## Finding ids already asked about this session: their link reads "Asked".
var _asked: Dictionary = {}


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Design check"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	title_label = UI.label("Checking the design")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	stop = _small_icon("stop", "Stop the design agent")
	stop.pressed.connect(_on_stop)
	stop.hide()
	head.add_child(stop)
	close = _small_icon("close", "Dismiss")
	close.pressed.connect(func() -> void: close_pressed.emit())
	head.add_child(close)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	column.add_child(line)
	_dots = INDICATOR.new()
	_dots.set("running", true)
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label("Reading the brief, scenes and scripts…", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_step.mouse_filter = Control.MOUSE_FILTER_PASS
	line.add_child(_step)
	again = UI.quiet_link("Check again")
	UI.bind_height(again, "xs")
	again.pressed.connect(func() -> void: recheck_requested.emit())
	again.hide()
	line.add_child(again)
	_findings = VBoxContainer.new()
	_findings.theme_type_variation = "JoineryTight"
	_findings.accessibility_name = "Design problems"
	_findings.hide()
	column.add_child(_findings)
	_more = UI.quiet_link("")
	UI.bind_height(_more, "xs")
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.hide()
	column.add_child(_more)
	_foot = HBoxContainer.new()
	_foot.theme_type_variation = "JoineryRow"
	_foot.hide()
	column.add_child(_foot)
	deeper = UI.secondary("Look deeper")
	deeper.tooltip_text = "An agent reads the design for problems the checks can't see"
	deeper.accessibility_name = "Ask the design agent to look deeper"
	UI.bind_height(deeper, "sm")
	UI_MOTION.press_feedback(deeper)
	deeper.pressed.connect(func() -> void: deeper_requested.emit())
	_foot.add_child(deeper)


## Joinery's checks are running (no model).
func set_checking() -> void:
	state = "checking"
	_working("Reading the brief, scenes and scripts…")
	_foot.hide()
	again.hide()


## The designer agent is reading; Joinery's findings stay on the card meanwhile.
func set_reading() -> void:
	state = "reading"
	_working("The design agent is reading the game…")
	_foot.hide()
	again.hide()
	stop.disabled = false
	stop.show()
	close.hide()


## `joinery design --json`, or the report in a design event: headline and findings.
func show_report(data: Dictionary, agent_ran := false, dropped := 0) -> void:
	report = data
	state = "ready"
	stop.hide()
	close.show()
	var findings: Array = data.get("findings", [])
	title_label.text = headline(data)
	title_label.tooltip_text = title_label.text
	if findings.is_empty():
		_settle("done", "Checked " + read_line(data))
	else:
		var serious := 0
		for item: Variant in findings:
			serious += int(str((item as Dictionary).get("severity", "")) == "high")
		_settle("warning" if serious > 0 else "done", severity_line(findings))
	_step.tooltip_text = "Checked " + read_line(data)
	if dropped > 0:
		_step.tooltip_text += (
			"\n%d of the agent's findings had no evidence and were left out." % dropped
		)
	_foot.visible = not agent_ran
	again.visible = true
	_fill(findings)


func apply_failed(message: String) -> void:
	state = "failed"
	stop.hide()
	close.show()
	_settle("warning", message)
	again.visible = true


## "No design problems found" or "3 design problems".
static func headline(data: Dictionary) -> String:
	var count := (data.get("findings", []) as Array).size()
	if count == 0:
		return "No design problems found"
	return "%d design %s" % [count, "problem" if count == 1 else "problems"]


## "2 high · 1 medium": how serious, at a glance.
static func severity_line(findings: Array) -> String:
	var counts := {"high": 0, "medium": 0, "low": 0}
	for item: Variant in findings:
		var severity := str((item as Dictionary).get("severity", "low"))
		counts[severity] = int(counts.get(severity, 0)) + 1
	var words := PackedStringArray()
	for severity: String in ["high", "medium", "low"]:
		if int(counts[severity]) > 0:
			words.append("%d %s" % [int(counts[severity]), severity])
	return " · ".join(words)


## What the check read: "the brief, 6 input actions, 25 scripts and 8 scenes".
static func read_line(data: Dictionary) -> String:
	var parts := PackedStringArray()
	for item: Variant in data.get("read", []):
		var text := str(item)
		parts.append("the brief" if text == "design/brief.md" else text.get_file())
	if parts.is_empty():
		return "the project"
	if parts.size() == 1:
		return parts[0]
	return ", ".join(parts.slice(0, parts.size() - 1)) + " and " + parts[parts.size() - 1]


## A finding's place as a person reads it: "brief.md:10", "level.tscn (Player)".
static func where(finding: Dictionary) -> String:
	var places := PackedStringArray()
	for place: Variant in finding.get("evidence", []):
		var entry := place as Dictionary
		var text := str(entry.get("path", "")).get_file()
		if int(entry.get("line", 0)) > 0:
			text += ":%d" % int(entry["line"])
		if not str(entry.get("node", "")).is_empty():
			text += " (%s)" % str(entry["node"])
		places.append(text)
	return ", ".join(places)


## The visible finding rows.
func finding_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _findings.get_children():
		if child is Control and (child as Control).visible and not child.is_queued_for_deletion():
			rows.append(child as Control)
	return rows


func step_text() -> String:
	return _step.text


func _fill(findings: Array) -> void:
	for child: Node in _findings.get_children():
		child.queue_free()
	var shown: Array = []
	for index: int in findings.size():
		var row := _row(findings[index] as Dictionary)
		row.visible = index < FIRST_FINDINGS
		_findings.add_child(row)
		if row.visible:
			shown.append(row)
	_findings.visible = not findings.is_empty()
	_more.text = "Show all %d" % findings.size()
	_more.visible = findings.size() > FIRST_FINDINGS
	UI_MOTION.stagger(shown)


## One line: the severity dot, the problem, and Ask agent to fix. The evidence, what the
## player experiences and why are in the tooltip.
func _row(item: Dictionary) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	UI.bind_height(row, "xs", false)
	var severity := str(item.get("severity", "low"))
	row.add_child(UI.status_dot(str(DOTS.get(severity, "off"))))
	var agent := str(item.get("source", "check")) == "agent"
	var words := UI.label(str(item.get("title", "")), "JoineryCaption")
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	row.tooltip_text = (
		"%s · %s%s\n%s\n%s"
		% [
			severity.capitalize(),
			where(item),
			" · found by the design agent" if agent else "",
			str(item.get("player", "")),
			str(item.get("why", "")),
		]
	)
	words.tooltip_text = row.tooltip_text
	row.accessibility_name = "%s, %s: %s" % [severity, str(item.get("title", "")), where(item)]
	row.add_child(words)
	# Shown only while its row is pointed at, so the problems lead and five rows of links
	# don't shout.
	var ask := UI.quiet("Ask agent to fix")
	STYLE.hug_right(ask)
	UI.bind_height(ask, "xs")
	ask.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	ask.tooltip_text = "Put a request to fix this in the chat, ready to edit or send"
	ask.accessibility_name = "Ask the agent to fix: " + str(item.get("title", ""))
	var id := str(item.get("id", ""))
	if _asked.has(id):
		_mark_asked(ask)
	UI_MOTION.press_feedback(ask)
	ask.pressed.connect(
		func() -> void:
			_asked[id] = true
			_mark_asked(ask)
			fix_requested.emit(item)
	)
	row.add_child(ask)
	STYLE.reveal_on_hover(row, ask)
	return row


func _mark_asked(ask: Button) -> void:
	ask.text = "Asked"
	ask.tooltip_text = "The request is in the chat; ask again to put it back"


func _show_all() -> void:
	var shown: Array = []
	for child: Node in _findings.get_children():
		if child is Control and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _working(words: String) -> void:
	_dots.show()
	_dots.set("running", true)
	_status.hide()
	_step.text = words
	_step.tooltip_text = words


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words


func _on_stop() -> void:
	if stop.disabled:
		return
	stop.disabled = true
	_step.text = "Stopping…"
	stop_pressed.emit()


func _small_icon(icon_name: String, tooltip: String) -> Button:
	var button := UI.icon_button(icon_name, tooltip)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(button)
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "sm"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button
