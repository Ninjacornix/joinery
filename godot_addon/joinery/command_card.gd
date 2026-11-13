@tool
extends PanelContainer
## A project command's form (ADR 0077): its title, what it does, one field per parameter and
## "Ask Journeyman". Text, whole numbers and numbers are text fields, a choice is a picker, yes
## or no is a switch; a parameter's default is filled in and its description is the field's
## caption. Joinery checks the values when it fills in the request (`joinery command render`),
## and a wrong one comes back on the card's error line. Enter in a text field asks too.

signal run_requested(values: Dictionary)
signal close_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")

## The command as `joinery command list --json` gives it.
var command: Dictionary = {}
var title_label: Label
var close: Button
var ask: Button
var _about: Label
var _fields: VBoxContainer
var _error: Label
## {parameter name: [type, control]}.
var _inputs: Dictionary = {}


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Command"
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
	var header := VBoxContainer.new()
	header.theme_type_variation = "JoineryTight"
	column.add_child(header)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	header.add_child(head)
	title_label = STYLE.card_title("")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	close = UI.icon_button("close", "Dismiss")
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(close)
	STYLE.on_theme_overrides(
		close,
		func() -> void:
			var side := float(UI_THEME.control_h(close, "sm"))
			close.custom_minimum_size = Vector2(side, side)
	)
	close.pressed.connect(func() -> void: close_pressed.emit())
	head.add_child(close)
	_about = UI.label("", "JoineryCaption", true)
	header.add_child(_about)
	_fields = VBoxContainer.new()
	_fields.theme_type_variation = "JoineryStack"
	column.add_child(_fields)
	_error = UI.label("", "JoineryError", true)
	_error.hide()
	column.add_child(_error)
	var actions := HBoxContainer.new()
	actions.theme_type_variation = "JoineryRow"
	actions.alignment = BoxContainer.ALIGNMENT_END
	column.add_child(actions)
	ask = UI.primary("Ask Journeyman")
	UI_MOTION.press_feedback(ask)
	ask.pressed.connect(_ask)
	actions.add_child(ask)


## Show [param entry] ({name, title, description, parameters}) with [param given] filled in.
func show_command(entry: Dictionary, given: Dictionary = {}) -> void:
	command = entry
	var name := str(entry.get("name", ""))
	title_label.text = str(entry.get("title", name))
	accessibility_name = title_label.text
	var about := str(entry.get("description", ""))
	_about.text = "/" + name + (" · " + about if about != title_label.text else "")
	for child: Node in _fields.get_children():
		_fields.remove_child(child)
		child.queue_free()
	_inputs.clear()
	for parameter: Variant in entry.get("parameters", []):
		if parameter is Dictionary:
			_add_field(parameter, given)
	ask.tooltip_text = "Fill in /%s and send it to Journeyman" % name
	show_error("")
	set_running(false)
	_focus_first.call_deferred()


## The values in the form: {name: String or bool}; empty text fields are left out.
func values() -> Dictionary:
	var result := {}
	for name: String in _inputs:
		var kind: String = _inputs[name][0]
		var control: Control = _inputs[name][1]
		if kind == "boolean":
			result[name] = (control as CheckButton).button_pressed
		elif kind == "choice":
			var picker := control as OptionButton
			result[name] = picker.get_item_text(picker.selected)
		else:
			var text := (control as LineEdit).text.strip_edges()
			if not text.is_empty():
				result[name] = text
	return result


func show_error(text: String) -> void:
	_error.text = text
	_error.visible = not text.is_empty()


## While Joinery fills in the request: Ask rests.
func set_running(on: bool) -> void:
	ask.disabled = on
	ask.text = "Filling in…" if on else "Ask Journeyman"


func _add_field(parameter: Dictionary, given: Dictionary) -> void:
	var name := str(parameter.get("name", ""))
	var kind := str(parameter.get("type", "string"))
	var value: Variant = given.get(name, parameter.get("default"))
	var control: Control
	if kind == "boolean":
		var toggle := UI.switch()
		toggle.button_pressed = value is bool and value
		control = toggle
	elif kind == "choice":
		var choices: Array = parameter.get("choices", [])
		var picker := UI.picker(choices)
		picker.selected = maxi(0, choices.find(str(value)))
		control = picker
	else:
		var line := LineEdit.new()
		line.text = "" if value == null else _plain(value)
		line.placeholder_text = (
			"A whole number" if kind == "integer" else "A number" if kind == "number" else ""
		)
		line.text_submitted.connect(func(_text: String) -> void: _ask())
		control = line
	var title := name.replace("_", " ").capitalize()
	if not bool(parameter.get("required", false)) and kind == "string":
		title += " (optional)" if value == null else ""
	var field := UI.field(title, control, str(parameter.get("description", "")))
	field.name = "Field_" + name
	if kind == "boolean":
		# A switch sits on its title's row, at the trailing edge.
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryRow"
		field.remove_child(field.label)
		field.remove_child(control)
		field.label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		field.label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		control.size_flags_horizontal = Control.SIZE_SHRINK_END
		row.add_child(field.label)
		row.add_child(control)
		field.add_child(row)
		field.move_child(row, 0)
	_fields.add_child(field)
	_inputs[name] = [kind, control]


## 2.5 stays 2.5 and 3.0 shows as 3.
static func _plain(value: Variant) -> String:
	if value is float and is_equal_approx(value, roundf(value)):
		return str(int(value))
	return str(value)


func _ask() -> void:
	if ask.disabled:
		return
	run_requested.emit(values())


func _focus_first() -> void:
	for name: String in _inputs:
		var control: Control = _inputs[name][1]
		if control is LineEdit and control.is_inside_tree():
			control.grab_focus()
			return
