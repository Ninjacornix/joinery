@tool
extends PanelContainer
## The sound pass, at a glance: one headline that says how much is missing ("11 sounds missing
## in level.tscn"), one row per kind of sound with what happens to it (missing, being made,
## ready to review, accepted), and Stop while the sounds are made. Each change has its own task
## card and its row in the Review tab; the sound designer's report goes to the transcript.

signal stop_pressed
signal close_pressed
signal review_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const STEPS := {
	"auditing": "Listening to the level…",
	"starting": "Listening to the level…",
}
## A task's state -> [the row's words, its dot].
const ROW_STATES := {
	"queued": ["Waiting", "off"],
	"working": ["Making…", "working"],
	"in_review": ["Ready to review", "ready"],
	"accepted": ["Accepted", "ready"],
	"discarded": ["Discarded", "off"],
	"done": ["Nothing to change", "off"],
	"failed": ["Didn't finish", "attention"],
	"stopped": ["Stopped", "off"],
}
## Most pressing first: a row covered by several tasks shows the first of these it has.
const ROW_ORDER: Array[String] = [
	"failed", "working", "queued", "in_review", "stopped", "discarded", "accepted", "done"
]
const FINAL := ["accepted", "discarded", "done", "failed", "stopped"]

## starting, auditing, making, done, failed or stopped.
var state := "starting"
var audit: Dictionary = {}
var summary := ""
var title_label: Label
var stop: Button
var close: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _review: Button
var _rows: VBoxContainer
var _row_nodes: Dictionary = {}
var _run: Array = []


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Sound pass"
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
	title_label = UI.label("Sound pass")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	stop = _small_icon("stop", "Stop the sound pass")
	stop.pressed.connect(_on_stop)
	head.add_child(stop)
	close = _small_icon("close", "Dismiss")
	close.pressed.connect(func() -> void: close_pressed.emit())
	close.hide()
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
	_step = UI.label(STEPS["starting"], "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	line.add_child(_step)
	_review = UI.link("Review")
	_review.accessibility_name = "Review the sounds in the Review tab"
	UI.bind_height(_review, "xs")
	_review.pressed.connect(func() -> void: review_pressed.emit())
	_review.hide()
	line.add_child(_review)
	_rows = VBoxContainer.new()
	_rows.theme_type_variation = "JoineryTight"
	_rows.accessibility_name = "Sounds by kind"
	_rows.hide()
	column.add_child(_rows)


## The host's sound_pass event: {state, audit, tasks, summary, message}.
func apply(event: Dictionary) -> void:
	var next := str(event.get("state", state))
	if event.get("tasks") is Array:
		_run = event["tasks"]
	if event.get("audit") is Dictionary:
		_show_audit(event["audit"])
	if not str(event.get("summary", "")).is_empty():
		summary = str(event["summary"])
	state = next
	match state:
		"starting", "auditing":
			_working(STEPS[state])
		"making":
			_working(making_line())
		"done":
			_finish("warning" if _failed_count() > 0 else "done", str(event.get("message", "")))
		"failed":
			_finish("failed", str(event.get("message", "The sound pass didn't run.")))
		"stopped":
			_finish("stopped", "Stopped. " + done_line() if not _run.is_empty() else "Stopped")
	_fill_rows()
	_review.visible = _ready_count() > 0


## A task of this pass changed state (sidebar_tasks.gd's task events).
func task_changed(task_id: String, task_state: String) -> bool:
	for row: Variant in _run:
		var entry := row as Dictionary
		if str(entry.get("task_id", "")) == task_id and not task_id.is_empty():
			entry["state"] = task_state
			if state == "making":
				_working(making_line())
			elif state == "done":
				_step.text = done_line()
			_fill_rows()
			_review.visible = _ready_count() > 0
			return true
	return false


## Still running: its Stop is live.
func working() -> bool:
	return state in ["starting", "auditing", "making"]


## Optimistic: "Stopping…" at once; the host confirms with "stopped".
func mark_stopping() -> bool:
	if not working() or stop.disabled:
		return false
	stop.disabled = true
	_step.text = "Stopping…"
	return true


func step_text() -> String:
	return _step.text


## Each row's words as shown ("Footsteps  3 sounds · Making…"), for checks.
func row_words() -> PackedStringArray:
	var words := PackedStringArray()
	for id: String in _row_nodes:
		var row: Dictionary = _row_nodes[id]
		words.append("%s  %s" % [(row["label"] as Label).text, (row["state"] as Label).text])
	return words


## "Making 5 changes… 2 ready to review".
func making_line() -> String:
	if _run.is_empty():
		return "Planning the changes…"
	var line := "Making %d %s…" % [_run.size(), "change" if _run.size() == 1 else "changes"]
	var ready := _ready_count()
	if ready > 0:
		line += " %d ready to review" % ready
	return line


func done_line() -> String:
	var parts := PackedStringArray()
	var ready := _ready_count()
	var accepted := _count("accepted")
	var failed := _failed_count()
	if ready > 0:
		parts.append("%d ready to review" % ready)
	if accepted > 0:
		parts.append("%d accepted" % accepted)
	if failed > 0:
		parts.append("%d didn't finish" % failed)
	if parts.is_empty():
		return "Nothing Joinery can place by itself."
	var text := ", ".join(parts)
	return text.left(1).to_upper() + text.substr(1)


func _show_audit(data: Dictionary) -> void:
	var first := audit.is_empty()
	audit = data
	title_label.text = str(data.get("headline", "Sound pass"))
	title_label.tooltip_text = title_label.text
	if first:
		UI_MOTION.fade_in(title_label)


## One row per kind of sound with something missing or being made.
func _fill_rows() -> void:
	var added: Array = []
	for category: Variant in audit.get("categories", []):
		var entry := category as Dictionary
		var id := str(entry.get("id", ""))
		var missing := int(entry.get("missing", 0))
		var tasks := _tasks_for(id)
		if missing == 0 and int(entry.get("findings", 0)) == 0 and tasks.is_empty():
			continue
		if not _row_nodes.has(id):
			_row_nodes[id] = _row(str(entry.get("label", id)))
			_rows.add_child(_row_nodes[id]["row"])
			added.append(_row_nodes[id]["row"])
		var row: Dictionary = _row_nodes[id]
		var words := _row_state(tasks, entry)
		(row["count"] as Label).text = (
			"%d %s" % [missing, "sound" if missing == 1 else "sounds"] if missing > 0 else ""
		)
		var state_label := row["state"] as Label
		if state_label.text != words[0] and not state_label.text.is_empty():
			UI_MOTION.flash(state_label)
		state_label.text = words[0]
		row["dot"].set("state", words[1])
		(row["row"] as Control).tooltip_text = _row_tooltip(id)
	_rows.visible = not _row_nodes.is_empty()
	UI_MOTION.stagger(added)


func _row(label_text: String) -> Dictionary:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	UI.bind_height(row, "xs", false)
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var dot := UI.status_dot("off")
	row.add_child(dot)
	# A narrow sidebar wraps the kind of sound onto a second line; the count and state stay.
	var label := UI.label(label_text, "", true)
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(label)
	var count := UI.label("", "JoineryNumeric")
	count.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.add_child(count)
	var words := UI.label("", "JoineryCaption")
	words.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	STYLE.on_theme(
		words, func() -> void: words.custom_minimum_size.x = roundf(UI_THEME.unit(words) * 104.0)
	)
	row.add_child(words)
	return {"row": row, "dot": dot, "label": label, "count": count, "state": words}


## [the row's words, its dot] for a kind of sound, from the tasks that cover it.
func _row_state(tasks: Array, entry: Dictionary) -> Array:
	if tasks.is_empty():
		if not bool(entry.get("placeable", true)) or state in ["done", "failed", "stopped"]:
			return ["Needs you", "attention"] if int(entry.get("findings", 0)) > 0 else ["", "off"]
		return ["Missing", "off"]
	var states := {}
	for task: Variant in tasks:
		states[str((task as Dictionary).get("state", "queued"))] = true
	for each: String in ROW_ORDER:
		if states.has(each):
			return ROW_STATES[each]
	return ROW_STATES["working"]


func _tasks_for(category: String) -> Array:
	var found: Array = []
	for row: Variant in _run:
		var entry := row as Dictionary
		if category in (entry.get("categories", [entry.get("category", "")]) as Array):
			found.append(entry)
	return found


func _row_tooltip(category: String) -> String:
	var lines := PackedStringArray()
	for item: Variant in audit.get("findings", []):
		var finding := item as Dictionary
		if str(finding.get("category", "")) != category:
			continue
		var line := (
			"%s: %s" % [str(finding.get("scene", "")).get_file(), finding.get("missing", "")]
		)
		if not str(finding.get("needs_you", "")).is_empty():
			line += " (needs you: %s)" % finding["needs_you"]
		elif not str(finding.get("evidence", "")).is_empty():
			line += " (%s)" % finding["evidence"]
		lines.append(line)
	return "\n".join(lines)


func _ready_count() -> int:
	return _count("in_review")


func _failed_count() -> int:
	return _count("failed") + _count("stopped")


func _count(task_state: String) -> int:
	var count := 0
	for row: Variant in _run:
		if str((row as Dictionary).get("state", "")) == task_state:
			count += 1
	return count


func _finish(kind: String, words: String) -> void:
	stop.hide()
	close.show()
	_settle(kind, words if not words.is_empty() else done_line())
	UI_MOTION.pulse(self)


func _working(words: String) -> void:
	_dots.show()
	_dots.set("running", true)
	_status.hide()
	stop.show()
	close.hide()
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
	if mark_stopping():
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
