@tool
extends PanelContainer
## A request's to-do (ADR 0060), above the footer with the agents at work. For a request with
## several steps Journeyman shows its steps and what "done" means before it changes anything.
## While it waits for Go every line is editable in place and has its own remove; Go starts the
## work with the list as left, Cancel drops it. Once the work runs the list is read-only and
## the "done when" items are checked after the change passes Joinery's checks: each row turns
## to Pass, Fail or Inconclusive with its evidence under it, and the header says "2 of 3 done".
## A Fail goes back to Journeyman once ("Fixing what didn't pass…") before the change opens.

signal go_pressed(line: Dictionary)
signal cancel_pressed
signal close_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## State -> the step line's words.
const WORDS := {
	"waiting": "Edit anything, then press Go.",
	"going": "Working through the steps…",
	"steps": "Making the steps that need nothing first at once…",
	"checking": "Checking what done means…",
	"fixing": "Fixing what didn't pass…",
	"cancelled": "Cancelled",
	"dropped": "Set aside for your new message",
}
## Result status -> the glyph (ui_components.gd status_icon) and the words for screen readers.
const LOOKS := {
	"pass": ["done", "Pass"],
	"fail": ["failed", "Fail"],
	"inconclusive": ["warning", "Inconclusive"],
}

var todo_id := ""
## waiting, going, checking, fixing, checked, cancelled or dropped.
var state := ""
var title_label: Label
var close: Button
var go: Button
var cancel: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _steps: VBoxContainer
var _done_title: Label
var _done: VBoxContainer
var _footer: HBoxContainer
## The agent's done-when items by id, with what was checked: {id: {text, kind, result?}}.
var _items: Dictionary = {}
var _order: Array[String] = []
var _step_texts: Array[String] = []


func _init(identifier := "") -> void:
	todo_id = identifier
	name = "Todo"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "To do"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryChangeCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	add_child(column)
	var top := VBoxContainer.new()
	top.theme_type_variation = "JoineryTight"
	column.add_child(top)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	top.add_child(head)
	title_label = STYLE.card_title("To do")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title_label)
	close = UI.icon_button("close", "Dismiss the to-do")
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close.pressed.connect(func() -> void: close_pressed.emit())
	UI_MOTION.press_feedback(close)
	close.hide()
	head.add_child(close)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	top.add_child(line)
	_dots = INDICATOR.new()
	_dots.hide()
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label("", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(_step)
	_steps = VBoxContainer.new()
	_steps.theme_type_variation = "JoineryTight"
	_steps.accessibility_name = "Steps"
	column.add_child(_steps)
	var done := VBoxContainer.new()
	done.theme_type_variation = "JoineryTight"
	column.add_child(done)
	_done_title = UI.label("Done when", "JoineryCaption")
	done.add_child(_done_title)
	_done = VBoxContainer.new()
	_done.theme_type_variation = "JoineryTight"
	_done.accessibility_name = "Done when"
	done.add_child(_done)
	_footer = HBoxContainer.new()
	_footer.theme_type_variation = "JoineryRow"
	column.add_child(_footer)
	go = UI.primary("Go")
	go.accessibility_name = "Go: start the work"
	UI.bind_height(go, "sm")
	UI_MOTION.press_feedback(go)
	go.pressed.connect(_on_go)
	_footer.add_child(go)
	cancel = UI.quiet("Cancel")
	UI.bind_height(cancel, "sm")
	UI_MOTION.press_feedback(cancel)
	cancel.pressed.connect(_on_cancel)
	_footer.add_child(cancel)
	_footer.hide()


## The host's to-do event: {todo_id, state, steps?, done_when?, results?, summary?}.
func apply(event: Dictionary) -> void:
	if event.get("steps") is Array:
		_step_texts.clear()
		for step: Variant in event["steps"]:
			_step_texts.append(str(step))
	if event.get("done_when") is Array:
		_items.clear()
		_order.clear()
		for item: Variant in event["done_when"]:
			var entry := item as Dictionary
			var id := str(entry.get("id", ""))
			_order.append(id)
			_items[id] = {"text": str(entry.get("text", "")), "kind": str(entry.get("kind", ""))}
	if event.get("results") is Array:
		for result: Variant in event["results"]:
			var entry := result as Dictionary
			var id := str(entry.get("id", ""))
			if _items.has(id):
				(_items[id] as Dictionary)["result"] = entry
	var next := str(event.get("state", state))
	var rebuilt := next != state or event.has("steps") or event.has("results")
	state = next
	if rebuilt:
		_build()
	match state:
		"checked":
			var summary := str(event.get("summary", ""))
			_settle(
				"done" if _fails() == 0 else "warning",
				summary if not summary.is_empty() else _summary()
			)
			UI_MOTION.pulse(self)
		"cancelled", "dropped":
			_settle("stopped", str(WORDS[state]))
		_:
			_dots.visible = state in ["going", "steps", "checking", "fixing"]
			_dots.set("running", _dots.visible)
			_status.hide()
			_step.text = str(WORDS.get(state, ""))
			if state == "steps" and event.get("together") is Array:
				_step.text = "Making %s at once…" % _step_list(event["together"] as Array)
			close.visible = false
	_footer.visible = state == "waiting"
	if state == "waiting":
		go.disabled = steps().is_empty()
		cancel.disabled = false


## Still waiting for Go, or working.
func working() -> bool:
	return state in ["waiting", "going", "steps", "checking", "fixing"]


## "step 2", "steps 1 and 3", "steps 1, 2 and 4".
static func _step_list(numbers: Array) -> String:
	var words := PackedStringArray()
	for number: Variant in numbers:
		words.append(str(int(number)))
	if words.size() == 1:
		return "step " + words[0]
	var last := words[words.size() - 1]
	words.resize(words.size() - 1)
	return "steps %s and %s" % [", ".join(words), last]


func step_text() -> String:
	return _step.text


## The steps as shown (edited text while waiting).
func steps() -> PackedStringArray:
	var found := PackedStringArray()
	for row: Node in _steps.get_children():
		if not _gone(row):
			found.append(_row_text(row))
	return found


## The done-when rows as shown: [{id, text, status?, evidence?}].
func done_rows() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for row: Node in _done.get_children():
		if _gone(row):
			continue
		var id := str(row.get_meta("item_id", ""))
		var entry := {"id": id, "text": _row_text(row)}
		var result: Variant = (_items.get(id, {}) as Dictionary).get("result")
		if result is Dictionary:
			entry["status"] = str((result as Dictionary).get("status", ""))
			entry["evidence"] = str((result as Dictionary).get("evidence", ""))
		found.append(entry)
	return found


## The rows' remove buttons (while waiting), steps first.
func remove_buttons() -> Array[Button]:
	var found: Array[Button] = []
	for list: Node in [_steps, _done]:
		for row: Node in list.get_children():
			var remove := row.find_child("Remove", true, false) as Button
			if remove != null and not _gone(row):
				found.append(remove)
	return found


func _build() -> void:
	var editable := state == "waiting"
	for list: Node in [_steps, _done]:
		for child: Node in list.get_children():
			list.remove_child(child)
			child.queue_free()
	var rows: Array = []
	for index: int in _step_texts.size():
		var row := _row(str(index + 1), _step_texts[index], editable, "Step %d" % (index + 1))
		_steps.add_child(row)
		rows.append(row)
	for id: String in _order:
		var item: Dictionary = _items[id]
		var row := _done_row(id, item, editable)
		_done.add_child(row)
		rows.append(row)
	_done_title.visible = not _order.is_empty()
	UI_MOTION.stagger(rows)


## A step: its number, then its words (a field while waiting) and remove.
func _row(mark: String, text: String, editable: bool, label: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	# The step's number, quiet: the words carry the meaning.
	var number := UI.label(mark, "JoineryCaption")
	number.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	number.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	STYLE.on_theme(
		number, func() -> void: number.custom_minimum_size.x = UI_THEME.unit(number) * 14.0
	)
	row.add_child(number)
	_words(row, text, editable, label, row)
	return row


## A done-when item: its state (an empty dot until checked, then Pass, Fail or Inconclusive),
## its words and, once checked, the evidence under them.
func _done_row(id: String, item: Dictionary, editable: bool) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	block.set_meta("item_id", id)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	block.add_child(row)
	var result: Variant = item.get("result")
	var status := str((result as Dictionary).get("status", "")) if result is Dictionary else ""
	var mark: Control
	if LOOKS.has(status):
		var icon := UI.status_icon(str(LOOKS[status][0]))
		icon.name = "Result"
		icon.tooltip_text = str(LOOKS[status][1])
		icon.accessibility_name = str(LOOKS[status][1])
		mark = icon
	else:
		var dot := UI.status_dot("working" if state == "checking" else "off")
		dot.tooltip_text = "Checked after the change"
		mark = dot
	STYLE.on_theme(mark, func() -> void: mark.custom_minimum_size.x = UI_THEME.unit(mark) * 14.0)
	row.add_child(mark)
	var words := _words(row, str(item.get("text", "")), editable, "Done when", block)
	if not status.is_empty():
		words.accessibility_name = "%s: %s" % [str(item.get("text", "")), str(LOOKS[status][1])]
		var evidence := UI.label(
			str((result as Dictionary).get("evidence", "")), "JoineryCaption", true
		)
		evidence.name = "Evidence"
		evidence.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var indent := MarginContainer.new()
		STYLE.on_theme_overrides(
			indent,
			func() -> void:
				indent.add_theme_constant_override(
					"margin_left", int(UI_THEME.unit(indent) * 14.0) + UI_THEME.space(indent, 1)
				)
		)
		indent.add_child(evidence)
		block.add_child(indent)
	return block


## The row's words: a borderless field to edit in place while waiting, else a label; and a
## remove button while waiting.
func _words(
	row: HBoxContainer, text: String, editable: bool, label: String, target: Control
) -> Control:
	var words: Control
	if editable:
		var field := LineEdit.new()
		field.name = "Words"
		field.text = text
		field.flat = true
		field.accessibility_name = label
		field.tooltip_text = text
		field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		UI.bind_height(field, "sm")
		words = field
	else:
		var shown := UI.label(text, "", true)
		shown.name = "Words"
		shown.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		shown.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		words = shown
	row.add_child(words)
	if editable:
		var remove := UI.icon_button("close", "Remove")
		remove.name = "Remove"
		remove.accessibility_name = "Remove: " + text
		remove.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		UI_MOTION.press_feedback(remove)
		STYLE.on_theme_overrides(
			remove,
			func() -> void:
				var side := float(UI_THEME.control_h(remove, "xs"))
				remove.custom_minimum_size = Vector2(side, side)
		)
		remove.pressed.connect(_remove.bind(target))
		row.add_child(remove)
	return words


## Remove [param target] (a step's row or a done-when item's block); Go needs a step left.
func _remove(target: Control) -> void:
	target.set_meta("removed", true)
	UI_MOTION.exit(target, null, target.queue_free)
	go.disabled = steps().is_empty()


static func _gone(row: Node) -> bool:
	return row.is_queued_for_deletion() or row.has_meta("removed")


static func _row_text(row: Node) -> String:
	var words := row.find_child("Words", true, false)
	if words is LineEdit:
		return (words as LineEdit).text.strip_edges()
	if words is Label:
		return (words as Label).text
	return ""


func _on_go() -> void:
	var done: Array = []
	for row: Dictionary in done_rows():
		if not str(row["text"]).is_empty():
			done.append({"id": row["id"], "text": row["text"]})
	var kept := PackedStringArray()
	for step: String in steps():
		if not step.is_empty():
			kept.append(step)
	if kept.is_empty():
		return
	go.disabled = true
	_step.text = "Starting…"
	go_pressed.emit({"todo_id": todo_id, "steps": Array(kept), "done_when": done})


func _on_cancel() -> void:
	cancel.disabled = true
	cancel_pressed.emit()


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words
	close.show()


func _fails() -> int:
	var count := 0
	for id: String in _order:
		var result: Variant = (_items[id] as Dictionary).get("result")
		if result is Dictionary and str((result as Dictionary).get("status", "")) == "fail":
			count += 1
	return count


func _summary() -> String:
	var passed := 0
	for id: String in _order:
		var result: Variant = (_items[id] as Dictionary).get("result")
		if result is Dictionary and str((result as Dictionary).get("status", "")) == "pass":
			passed += 1
	return "%d of %d done" % [passed, _order.size()]
