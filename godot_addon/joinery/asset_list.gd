@tool
extends ScrollContainer
## A selectable list of rows grouped under section titles, for the asset browser and its
## app filter. Rows are real controls, so they can stagger in and carry a kind icon, a
## rolling count and a readiness dot. The item API mirrors ItemList (add_item, select,
## get_selected_items, item_selected), and indexes count rows only, never section titles.

signal item_selected(index: int)

const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const _SELECTED_STATES := ["pressed", "hover_pressed"]

## Number of rows (section titles excluded).
var item_count: int:
	get:
		return _rows.size()

## True to grow with the rows instead of scrolling (short lists such as the app filter).
var fit_content := false:
	set(value):
		fit_content = value
		vertical_scroll_mode = (
			ScrollContainer.SCROLL_MODE_DISABLED if value else ScrollContainer.SCROLL_MODE_AUTO
		)

var _box: VBoxContainer
var _group: VBoxContainer
var _rows: Array[Button] = []
var _selected := -1
var _styling := false


## One row: a flat button with a left icon, a label, an optional count and a status dot.
class Row:
	extends Button
	var picture: TextureRect
	var title: Label
	var count: Label
	var dot: Control
	var metadata: Variant = null
	var text_value := ""


func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_box = VBoxContainer.new()
	_box.theme_type_variation = "JoineryStack"
	_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_box)


func _ready() -> void:
	UI_MOTION.rubber_band(self)


## Remove every row and section title.
func clear() -> void:
	for child: Node in _box.get_children():
		_box.remove_child(child)
		child.queue_free()
	_rows.clear()
	_group = null
	_selected = -1


## Start a new group under [param title] (a JoinerySection label lined up with row text).
func add_section(title: String) -> void:
	_group = VBoxContainer.new()
	_group.theme_type_variation = "JoineryTight"
	_box.add_child(_group)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(UI_COMPONENTS.label(title, "JoinerySection"))
	_group.add_child(inset)


## Add a row and return its index. [param icon_name] is a ui_theme icon ("" for none).
func add_item(text: String, icon_name := "") -> int:
	if _group == null:
		_group = VBoxContainer.new()
		_group.theme_type_variation = "JoineryTight"
		_box.add_child(_group)
	var row := Row.new()
	row.theme_type_variation = "JoineryRowButton"
	row.toggle_mode = true
	row.focus_mode = Control.FOCUS_ALL
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.text_value = text
	row.accessibility_name = text
	UI_COMPONENTS.bind_height(row, "md", false)
	UI_MOTION.press_feedback(row)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	row.add_child(line)
	row.picture = TextureRect.new()
	row.picture.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	row.picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.picture.visible = not icon_name.is_empty()
	if not icon_name.is_empty():
		UI_COMPONENTS.bind_icon(row.picture, icon_name, "texture")
	line.add_child(row.picture)
	row.title = UI_COMPONENTS.label(text)
	row.title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.title.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.add_child(row.title)
	row.count = UI_COMPONENTS.label("", "JoineryNumeric")
	row.count.mouse_filter = Control.MOUSE_FILTER_IGNORE
	# Never trimmed: a trimming label has no minimum width and the title would squeeze it out.
	row.count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.count.hide()
	line.add_child(row.count)
	row.dot = UI_COMPONENTS.status_dot()
	row.dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.dot.hide()
	line.add_child(row.dot)
	var index := _rows.size()
	row.pressed.connect(_on_row_pressed.bind(index))
	row.theme_changed.connect(_style_row.bind(row))
	_group.add_child(row)
	_rows.append(row)
	return index


func get_item_text(index: int) -> String:
	return (_rows[index] as Row).text_value


func set_item_metadata(index: int, value: Variant) -> void:
	(_rows[index] as Row).metadata = value


func get_item_metadata(index: int) -> Variant:
	return (_rows[index] as Row).metadata


func set_item_tooltip(index: int, text: String) -> void:
	_rows[index].tooltip_text = text


## A disabled row cannot be chosen; its title turns faint.
func set_item_disabled(index: int, disabled: bool) -> void:
	var row := _rows[index] as Row
	row.disabled = disabled
	row.title.theme_type_variation = "JoineryFaint" if disabled else ""


## Show [param value] at the row's end as a rolling count (tabular digits).
func set_item_count(index: int, value: int) -> void:
	var row := _rows[index] as Row
	row.count.show()
	if row.count.text.is_empty():
		# First value: set it plainly (roll_number would start an empty tween for it).
		row.count.text = "%d" % value
		row.count.set_meta("_joinery_value", float(value))
		return
	UI_MOTION.roll_number(row.count, value, "%d")


## Show a readiness dot ("ready", "working", "attention", "off"); "" hides it.
func set_item_status(index: int, state: String) -> void:
	var row := _rows[index] as Row
	row.dot.visible = not state.is_empty()
	if not state.is_empty():
		row.dot.set("state", state)


## Select a row without emitting [signal item_selected], like ItemList.select.
func select(index: int, _single := true) -> void:
	_selected = index if index >= 0 and index < _rows.size() else -1
	for position: int in _rows.size():
		_rows[position].set_pressed_no_signal(position == _selected)
	if _selected >= 0 and is_inside_tree():
		_reveal.call_deferred(_rows[_selected])


func deselect_all() -> void:
	select(-1)


func get_selected_items() -> PackedInt32Array:
	return PackedInt32Array([_selected] if _selected >= 0 else [])


## Bring every row and section title in one after another (motion rule 3).
func stagger() -> void:
	var nodes: Array = []
	for group: Node in _box.get_children():
		nodes.append_array(group.get_children())
	UI_MOTION.stagger(nodes)


## Scroll [param row] into view, unless the list was rebuilt before this deferred call ran.
func _reveal(row: Variant) -> void:
	if not is_instance_valid(row):
		return
	var control := row as Control
	if not control.is_queued_for_deletion() and is_ancestor_of(control):
		ensure_control_visible(control)


func _on_row_pressed(index: int) -> void:
	select(index)
	item_selected.emit(index)


## Selected rows take the accent tint (spec A1). The fill is read from the live theme each time
## it changes, so it follows the theme and accent; the guard stops the override from
## re-entering this handler through its own theme notification.
func _style_row(row: Row) -> void:
	if _styling or not row.is_inside_tree():
		return
	_styling = true
	row.begin_bulk_theme_override()
	for state: String in _SELECTED_STATES:
		row.remove_theme_stylebox_override(state)
	row.end_bulk_theme_override()
	var tint := row.get_theme_color("accent_tint", "Joinery")
	row.begin_bulk_theme_override()
	for state: String in _SELECTED_STATES:
		var box := row.get_theme_stylebox(state).duplicate() as StyleBox
		if box is StyleBoxFlat:
			(box as StyleBoxFlat).bg_color = tint
		row.add_theme_stylebox_override(state, box)
	row.end_bulk_theme_override()
	var inner := row.get_theme_stylebox("normal")
	var line := row.get_child(0) as Control
	line.offset_left = inner.get_margin(SIDE_LEFT)
	line.offset_right = -inner.get_margin(SIDE_RIGHT)
	row.picture.self_modulate = row.get_theme_color("text_muted", "Joinery")
	var side := float(row.get_theme_constant("icon_max_width"))
	row.picture.custom_minimum_size = Vector2(side, side)
	_styling = false
