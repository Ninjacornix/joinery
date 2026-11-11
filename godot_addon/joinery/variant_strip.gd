@tool
extends VBoxContainer
## Variants side by side (ADR 0042): one request made a few ways at once, one tile per way
## (variant_way.gd) in a row that wraps on narrow panels, each with its picture, sound or
## changed lines and its one-line direction. One look, one click: Use this keeps that way and
## discards the others; Mix asks for one more way from notes on these ("shape from 2, colour
## from 3"); Discard all ends it. After the pick the strip says, in one line, what was kept.
##
## It hears the host through [method on_event]: the group's own event ({kind: "variants",
## group_id, title, state, picked, ways: [{task_id, variant, direction, state,
## transaction_id}]}) and its ways' task events (task, file_preview, review_media,
## review_notes). The sidebar card and the Review tab each hold one.

signal picked(task_id: String)
signal mix_requested(note: String)
signal discard_all_requested
signal open_requested(transaction_id: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const WAY := preload("variant_way.gd")
## A tile is at least this many large control heights wide before the row wraps.
const TILE_MIN := 2.6

var group_id := ""
var title := ""
## working, picking, picked or discarded.
var state := "working"
var picked_id := ""
## Smaller pictures, for the strip above a way's full review.
var compact := false
var mix_button: Button
var discard_button: Button
var mix_field: LineEdit
var mix_go: Button
var _grid: GridContainer
var _footer: HBoxContainer
var _mix_row: HBoxContainer
var _result: Label
var _ways: Dictionary = {}
var _order: Array[String] = []
## Each way's change as last heard: {task id: file_preview, merged with later media}.
var _previews: Dictionary = {}


func _init(identifier := "") -> void:
	group_id = identifier
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Ways to compare"
	_grid = GridContainer.new()
	_grid.columns = 3
	_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_grid)
	_mix_row = HBoxContainer.new()
	_mix_row.theme_type_variation = "JoineryRowTight"
	_mix_row.hide()
	add_child(_mix_row)
	mix_field = LineEdit.new()
	mix_field.placeholder_text = "Shape from 2, colour from 3"
	mix_field.accessibility_name = "What to take from which way"
	mix_field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	mix_field.text_submitted.connect(func(_text: String) -> void: _send_mix())
	_mix_row.add_child(mix_field)
	mix_go = UI.secondary("Make it")
	mix_go.tooltip_text = "Start one more way that combines these"
	UI.bind_height(mix_go, "sm")
	UI_MOTION.press_feedback(mix_go)
	mix_go.pressed.connect(_send_mix)
	_mix_row.add_child(mix_go)
	_footer = HBoxContainer.new()
	_footer.theme_type_variation = "JoineryRowTight"
	add_child(_footer)
	mix_button = UI.quiet("Mix…")
	mix_button.tooltip_text = "Combine ways: say what to take from which"
	UI.bind_height(mix_button, "sm")
	mix_button.pressed.connect(toggle_mix)
	_footer.add_child(mix_button)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_footer.add_child(push)
	discard_button = UI.quiet("Discard all")
	discard_button.tooltip_text = "Throw every way away. Your files stay as they are."
	UI.bind_height(discard_button, "sm")
	UI_MOTION.press_feedback(discard_button)
	discard_button.pressed.connect(_discard_all)
	_footer.add_child(discard_button)
	_result = UI.label("", "JoineryCaption", true)
	_result.hide()
	add_child(_result)
	resized.connect(_fit)


## One event from the host about this group or one of its ways.
func on_event(event: Dictionary) -> void:
	var task_id := str(event.get("task_id", ""))
	match str(event.get("kind", "")):
		"variants":
			show_group(event)
		"task":
			var way := way(task_id)
			if way == null and not str(event.get("variant_group", "")).is_empty():
				way = _add(task_id, int(event.get("variant", 0)), str(event.get("direction", "")))
			if way != null:
				way.call("apply", event)
				_sync()
		"file_preview":
			_previews[task_id] = event.duplicate(true)
			_show_change(task_id)
		"review_media", "review_notes":
			if _previews.has(task_id):
				for key: String in event:
					if key != "kind":
						_previews[task_id][key] = event[key]
				_show_change(task_id)


## The group's own event: its ways in order, their states, and the pick.
func show_group(event: Dictionary) -> void:
	group_id = str(event.get("group_id", group_id))
	title = str(event.get("title", title))
	for item: Variant in event.get("ways", []):
		if not item is Dictionary:
			continue
		var entry := item as Dictionary
		var id := str(entry.get("task_id", ""))
		var each := way(id)
		if each == null:
			each = _add(id, int(entry.get("variant", 0)), str(entry.get("direction", "")))
		each.call("apply", entry)
	var next := str(event.get("state", state))
	if next != state and next in ["picked", "discarded"]:
		_finish(next, str(event.get("picked", "")))
	elif state != "picking":
		state = next
	_sync()


## The tile of way [param task_id] (null when unknown).
func way(task_id: String) -> Control:
	var found: Control = _ways.get(task_id)
	return found if is_instance_valid(found) else null


## The ways' tiles, in the order they show.
func ways() -> Array[Control]:
	var found: Array[Control] = []
	for id: String in _order:
		if way(id) != null:
			found.append(way(id))
	return found


## True when [param task_id] is one of this group's ways.
func has_way(task_id: String) -> bool:
	return _ways.has(task_id)


## Ways whose agent works now (not those waiting for one).
func working_count() -> int:
	var count := 0
	for each: Control in ways():
		if str(each.get("state")) == "working":
			count += 1
	return count


## Open or close the Mix field.
func toggle_mix() -> void:
	if _mix_row.visible:
		UI_MOTION.exit(_mix_row, mix_button)
		return
	_mix_row.show()
	UI_MOTION.reveal(_mix_row)
	if mix_field.is_inside_tree():
		mix_field.grab_focus()


## The line the strip shows once a way was kept or all were discarded ("" before).
func result_text() -> String:
	return _result.text if _result.visible else ""


## Keep way [param task_id]: optimistic, the tile says so at once; the host confirms.
func pick(task_id: String) -> void:
	var chosen := way(task_id)
	if chosen == null or state != "working" or str(chosen.get("state")) != "in_review":
		return
	state = "picking"
	chosen.set("chosen", true)
	chosen.call("_restyle")
	(chosen.get("use_button") as Button).text = "Keeping…"
	_sync()
	picked.emit(task_id)


## The host refused the pick: back to choosing.
func pick_failed() -> void:
	if state != "picking":
		return
	state = "working"
	for each: Control in ways():
		each.set("chosen", false)
		each.call("_restyle")
		(each.get("use_button") as Button).text = "Use this"
	_sync()


func _add(task_id: String, number: int, direction: String) -> Control:
	var each: Control = WAY.new(task_id, number, direction)
	if compact:
		each.set("picture_max", 2.0)
	each.connect("use_pressed", pick.bind(task_id))
	each.connect("open_pressed", _open.bind(each))
	_ways[task_id] = each
	_order.append(task_id)
	_grid.add_child(each)
	UI_MOTION.enter(each)
	_fit()
	return each


func _open(each: Control) -> void:
	var id := str(each.get("transaction_id"))
	if not id.is_empty():
		open_requested.emit(id)


func _show_change(task_id: String) -> void:
	var each := way(task_id)
	if each != null:
		each.call("show_change", _previews[task_id])


func _send_mix() -> void:
	var note := mix_field.text.strip_edges()
	if note.is_empty() or state != "working":
		return
	mix_field.clear()
	_mix_row.hide()
	mix_requested.emit(note)


func _discard_all() -> void:
	if state != "working":
		return
	state = "picking"
	discard_button.text = "Discarding…"
	_sync()
	discard_all_requested.emit()


func _finish(next: String, chosen_id: String) -> void:
	state = next
	picked_id = chosen_id
	for each: Control in ways():
		each.call("stop_sound")
	var kept := way(chosen_id)
	if next == "picked" and kept != null:
		_result.text = "Kept %s. The other ways were discarded." % str(kept.call("note_text"))
	else:
		_result.text = "Discarded every way. Your files stay as they are."
	_grid.hide()
	_mix_row.hide()
	_footer.hide()
	_result.show()
	UI_MOTION.fade_in(_result)
	UI_MOTION.pulse(self)


func _sync() -> void:
	var open := state == "working"
	mix_button.disabled = not open
	discard_button.disabled = not open
	mix_go.disabled = not open
	for each: Control in ways():
		(each.get("use_button") as Button).disabled = not open


func _fit() -> void:
	if not is_inside_tree() or _order.is_empty():
		return
	var tile := float(UI_THEME.control_h(self, "lg")) * TILE_MIN
	var fits := maxi(1, int(size.x / maxf(tile, 1.0)))
	var columns := clampi(fits, 1, maxi(1, _order.size()))
	if columns == 3 and _order.size() == 4:
		columns = 2
	if _grid.columns != columns:
		_grid.columns = columns
