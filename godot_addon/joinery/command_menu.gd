@tool
extends PanelContainer
## Slash command menu: a popover above the composer that grows from it at 0.95 scale and
## shrinks back into it. Rows show the command in a column as wide as the longest one and a
## short description; the highlighted row has the accent tint. Everyday commands show first;
## troubleshooting commands appear when the typed text starts one, or under a
## "Troubleshooting" divider when opened from /help.

signal chosen(command: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const SLASH := preload("slash_commands.gd")
## Popup height cap as a share of the viewport height.
const MAX_HEIGHT := 0.4

## Commands in the order they are shown.
var matches: Array[String] = []
var anchor: Control
var _scroll: ScrollContainer
var _list: VBoxContainer
var _rows: Array[PanelContainer] = []
var _index := 0
var _semibold: FontVariation
var _closing := false


func _init() -> void:
	theme_type_variation = "JoineryPopup"
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(_scroll)
	_list = VBoxContainer.new()
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_list)
	accessibility_name = "Commands"


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and not _rows.is_empty():
		_style_rows()


## Show the commands that match [param text] ("/" shows the everyday ones). With
## [param help], show every command with troubleshooting under its divider. Returns true
## when the menu is open.
func open_for(text: String, help := false, local_only := false) -> bool:
	var typed := text.to_lower()
	var everyday: Array[String] = []
	var fixes: Array[String] = []
	for command: String in SLASH.COMMANDS:
		if local_only and not SLASH.is_local(command):
			continue
		var fix := SLASH.TROUBLESHOOTING.has(command)
		if help:
			(fixes if fix else everyday).append(command)
		elif command.begins_with(typed) and (not fix or typed.length() > 1):
			(fixes if fix else everyday).append(command)
	var opening := not visible or _closing
	_build(everyday, fixes)
	if matches.is_empty():
		close()
		return false
	place()
	if opening:
		_closing = false
		UI_MOTION.enter(self, anchor)
	return true


## Close the menu, shrinking back into the composer.
func close() -> void:
	if visible and not _closing:
		_closing = true
		UI_MOTION.exit(self, anchor, func() -> void: _closing = false)


## Move the highlight by [param step] rows, wrapping around.
func move(step: int) -> void:
	if matches.is_empty():
		return
	_select(posmod(_index + step, matches.size()))


## The highlighted command, or "".
func current() -> String:
	return matches[_index] if _index < matches.size() else ""


## Size the menu to its rows (capped) and put it just above [param anchor], full width.
func place() -> void:
	if anchor == null or not anchor.is_inside_tree():
		return
	var rect := anchor.get_global_transform_with_canvas() * Rect2(Vector2.ZERO, anchor.size)
	var content := _list.get_combined_minimum_size().y
	var box := get_theme_stylebox("panel")
	var chrome := box.get_minimum_size().y if box != null else 0.0
	var height := minf(content + chrome, get_viewport_rect().size.y * MAX_HEIGHT)
	size = Vector2(rect.size.x, height)
	var gap := UI_THEME.space(self, 1)
	global_position = Vector2(rect.position.x, maxf(gap, rect.position.y - height - gap))


func _build(everyday: Array[String], fixes: Array[String]) -> void:
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	_rows.clear()
	matches.clear()
	for command: String in everyday:
		_add_row(command)
	if not fixes.is_empty():
		if not everyday.is_empty():
			var divider := MarginContainer.new()
			divider.theme_type_variation = "JoineryInset"
			divider.add_child(UI.label("Troubleshooting", "JoinerySection"))
			_list.add_child(divider)
		for command: String in fixes:
			_add_row(command)
	_style_rows()
	_select(0)


func _add_row(command: String) -> void:
	var row := PanelContainer.new()
	row.mouse_filter = Control.MOUSE_FILTER_STOP
	row.accessibility_name = command + ", " + str(SLASH.COMMANDS[command][0])
	var box := HBoxContainer.new()
	box.theme_type_variation = "JoineryRow"
	box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(box)
	var name := UI.label(command)
	name.set_meta("command_column", true)
	box.add_child(name)
	var description := UI.label(str(SLASH.COMMANDS[command][0]), "JoineryCaption")
	description.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(description)
	var index := matches.size()
	row.gui_input.connect(_on_row_input.bind(index))
	_list.add_child(row)
	_rows.append(row)
	matches.append(command)


func _on_row_input(event: InputEvent, index: int) -> void:
	if event is InputEventMouseMotion and index != _index:
		_select(index)
	var click := event as InputEventMouseButton
	if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
		_select(index)
		chosen.emit(matches[index])
		accept_event()


func _select(index: int) -> void:
	_index = clampi(index, 0, maxi(0, matches.size() - 1))
	for row_index: int in _rows.size():
		_rows[row_index].add_theme_stylebox_override(
			"panel", _row_style(row_index == _index and not matches.is_empty())
		)
	if _index < _rows.size() and _rows[_index].is_inside_tree():
		_scroll.ensure_control_visible(_rows[_index])


func _style_rows() -> void:
	if _semibold == null:
		_semibold = FontVariation.new()
		_semibold.variation_embolden = 0.35
	_semibold.base_font = get_theme_default_font()
	var widest := 0.0
	for row: PanelContainer in _rows:
		var name: Label = row.get_child(0).get_child(0)
		name.add_theme_font_override("font", _semibold)
		var size := name.get_theme_font_size("font_size")
		widest = maxf(widest, _semibold.get_string_size(name.text, 0, -1, size).x)
	for row: PanelContainer in _rows:
		row.get_child(0).get_child(0).custom_minimum_size.x = ceilf(widest)
	_select(_index)


func _row_style(selected: bool) -> StyleBox:
	var box: StyleBox
	if selected:
		var flat := StyleBoxFlat.new()
		flat.bg_color = get_theme_color("accent_tint", "Joinery")
		flat.set_corner_radius_all(UI_THEME.radius(self, "md"))
		box = flat
	else:
		box = StyleBoxEmpty.new()
	var side := float(UI_THEME.space(self, 2))
	var top := float(UI_THEME.space(self, 1))
	box.content_margin_left = side
	box.content_margin_right = side
	box.content_margin_top = top
	box.content_margin_bottom = top
	return box
