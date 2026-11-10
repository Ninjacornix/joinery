extends VBoxContainer
## The "Controls" settings page: one row per standard action with its
## keyboard-and-mouse binding and its pad binding. Choose a binding, then press
## the new key or button; Escape (or waiting a few seconds) cancels. A binding
## another action already uses is swapped with it. Also: stick deadzone,
## invert look, rumble, button names and a reset. Everything is saved at once.

const Bindings := preload("res://features/controller/bindings.gd")
## Seconds a rebinding waits for a key or button before giving up.
const CAPTURE_SECONDS := 6.0

var names: Bindings = Bindings.new()
var status_label: Label
var device_label: Label

var _buttons: Dictionary = {}
var _capture_action: StringName = &""
var _capture_pad: bool = false
var _capture_button: Button = null
var _capture_armed: bool = false
var _capture_left: float = 0.0
var _labels_button: Button = null
var _deadzone_slider: HSlider = null


func _init() -> void:
	name = "ControlsSettings"
	set_process(false)


func _ready() -> void:
	_build()
	Controller.bindings_changed.connect(_refresh)
	Controller.device_changed.connect(_on_device_changed)
	Input.joy_connection_changed.connect(_on_joy_connection_changed)
	_refresh()


func _exit_tree() -> void:
	_cancel_capture()


func _process(delta: float) -> void:
	if _capture_action == &"":
		set_process(false)
		return
	_capture_armed = true
	_capture_left -= delta
	if _capture_left <= 0.0:
		_cancel_capture()
		status_label.text = tr("No key or button pressed; nothing changed.")


func _input(event: InputEvent) -> void:
	if _capture_action == &"" or event is InputEventMouseMotion or event is InputEventAction:
		return
	get_viewport().set_input_as_handled()
	if not _capture_armed:
		return
	var key: InputEventKey = event as InputEventKey
	if key != null and key.pressed and not key.echo:
		if key.keycode == KEY_ESCAPE or key.physical_keycode == KEY_ESCAPE:
			_cancel_capture()
			status_label.text = tr("Nothing changed.")
			return
	if not _is_press(event) or Bindings.is_pad(event) != _capture_pad:
		return
	_finish_capture(event)


## Whether a rebinding is waiting for a key or button.
func is_capturing() -> bool:
	return _capture_action != &""


## Starts rebinding `action` for the pad (or keyboard and mouse), as choosing
## its button on the page does.
func start_capture(action: StringName, pad: bool) -> void:
	var button: Button = _buttons.get(_button_key(action, pad)) as Button
	if button == null:
		return
	_cancel_capture()
	_capture_action = action
	_capture_pad = pad
	_capture_button = button
	_capture_armed = false
	_capture_left = CAPTURE_SECONDS
	button.text = tr("Press a button…") if pad else tr("Press a key…")
	status_label.text = tr("Waiting for %s. Escape cancels.") % names.action_label(action)
	set_process(true)


func _build() -> void:
	add_child(UiStyle.hint("Choose a binding, then press the new key or button. Escape cancels."))
	device_label = UiStyle.hint("")
	device_label.name = "Device"
	device_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	add_child(device_label)
	status_label = UiStyle.hint("")
	status_label.name = "Status"
	status_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	add_child(status_label)

	var scroll: ScrollContainer = ScrollContainer.new()
	scroll.name = "Scroll"
	scroll.follow_focus = true
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.custom_minimum_size = Vector2(UiStyle.px(1080), UiStyle.px(520))
	add_child(scroll)
	var rows: VBoxContainer = VBoxContainer.new()
	rows.name = "Rows"
	rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(rows)

	var header: HBoxContainer = HBoxContainer.new()
	header.name = "Header"
	for text: String in [tr("Action"), tr("Keyboard and mouse"), tr("Controller")]:
		var heading: Label = Label.new()
		heading.theme_type_variation = &"HintLabel"
		heading.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		heading.text = text
		heading.custom_minimum_size = Vector2(UiStyle.px(320), 0)
		header.add_child(heading)
	rows.add_child(header)

	for action: StringName in Controller.actions():
		rows.add_child(_action_row(action))

	if _has_sticks():
		_deadzone_slider = UiStyle.slider(
			Controller.stick_deadzone(), Controller.DEADZONE_MIN, Controller.DEADZONE_MAX, 0.05
		)
		_deadzone_slider.name = "input_deadzone"
		_deadzone_slider.value_changed.connect(
			func(value: float) -> void: Controller.set_stick_deadzone(value)
		)
		rows.add_child(_named_row("Stick deadzone", _deadzone_slider))
	if InputMap.has_action(&"look_up") or InputMap.has_action(&"look_down"):
		var invert: CheckButton = UiStyle.toggle(Controller.invert_look_y())
		invert.name = "input_invert_look_y"
		invert.toggled.connect(
			func(on: bool) -> void: Settings.set_value(Controller.SECTION, Controller.KEY_INVERT_Y, on)
		)
		rows.add_child(_named_row("Invert look up and down", invert))
	var rumble: CheckButton = UiStyle.toggle(Controller.rumble_enabled())
	rumble.name = "input_rumble"
	rumble.toggled.connect(
		func(on: bool) -> void: Settings.set_value(Controller.SECTION, Controller.KEY_RUMBLE, on)
	)
	rows.add_child(_named_row("Rumble", rumble))
	_labels_button = UiStyle.button("")
	_labels_button.name = "input_pad_labels"
	_labels_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	_labels_button.custom_minimum_size = Vector2(UiStyle.px(330), UiStyle.px(64))
	_labels_button.pressed.connect(_next_layout)
	rows.add_child(_named_row("Button names", _labels_button))
	var reset: Button = UiStyle.button("Reset to defaults")
	reset.name = "ResetControls"
	reset.pressed.connect(_reset)
	rows.add_child(reset)
	_link_focus(rows)


## Explicit pad and arrow-key paths through the page: up and down stay in the
## keyboard or the controller column, left and right switch column within a
## row, and the settings below are one list ending at Back. Godot's guess by
## geometry can jump from a binding into a wide toggle below it.
func _link_focus(rows: Control) -> void:
	var grid: Array[Array] = []
	for action: StringName in Controller.actions():
		grid.append([_buttons[_button_key(action, false)], _buttons[_button_key(action, true)]])
	var extras: Array[Control] = []
	for line: Node in rows.get_children():
		if line == rows.get_child(0):
			continue
		var target: Control = _focus_target(line)
		if target != null and not _in_grid(grid, target):
			extras.append(target)
	var back: Control = null
	if get_parent() != null:
		back = get_parent().get_node_or_null(^"Back") as Control
	if back != null:
		extras.append(back)
	for i: int in grid.size():
		var keys: Control = grid[i][0]
		var pad: Control = grid[i][1]
		_link(keys, &"focus_neighbor_right", pad)
		_link(pad, &"focus_neighbor_left", keys)
		_link(keys, &"focus_neighbor_left", keys)
		_link(pad, &"focus_neighbor_right", pad)
		if i > 0:
			_link(keys, &"focus_neighbor_top", grid[i - 1][0])
			_link(pad, &"focus_neighbor_top", grid[i - 1][1])
		var below_keys: Control = grid[i + 1][0] if i + 1 < grid.size() else _first(extras)
		var below_pad: Control = grid[i + 1][1] if i + 1 < grid.size() else _first(extras)
		_link(keys, &"focus_neighbor_bottom", below_keys)
		_link(pad, &"focus_neighbor_bottom", below_pad)
	for j: int in extras.size():
		var item: Control = extras[j]
		var above: Control = extras[j - 1] if j > 0 else (grid.back()[0] if not grid.is_empty() else null)
		_link(item, &"focus_neighbor_top", above)
		if j + 1 < extras.size():
			_link(item, &"focus_neighbor_bottom", extras[j + 1])
		if not item is Range:
			# Sliders use left and right to change their value.
			_link(item, &"focus_neighbor_left", item)
			_link(item, &"focus_neighbor_right", item)


func _focus_target(node: Node) -> Control:
	if node is Control and (node as Control).focus_mode == Control.FOCUS_ALL:
		return node as Control
	for child: Node in node.get_children():
		var found: Control = _focus_target(child)
		if found != null:
			return found
	return null


static func _in_grid(grid: Array[Array], control: Control) -> bool:
	for pair: Array in grid:
		if control in pair:
			return true
	return false


static func _first(controls: Array[Control]) -> Control:
	return controls[0] if not controls.is_empty() else null


static func _link(from: Control, side: StringName, to: Control) -> void:
	if from != null and to != null:
		from.set(side, from.get_path_to(to))


func _action_row(action: StringName) -> HBoxContainer:
	var line: HBoxContainer = HBoxContainer.new()
	line.name = "%s_row" % action
	var label: Label = Label.new()
	label.text = names.action_label(action)
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.custom_minimum_size = Vector2(UiStyle.px(320), 0)
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(label)
	for pad: bool in [false, true]:
		var button: Button = UiStyle.button("")
		button.name = "%s_%s" % [action, "pad" if pad else "keys"]
		button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		button.custom_minimum_size = Vector2(UiStyle.px(330), UiStyle.px(64))
		button.pressed.connect(start_capture.bind(action, pad))
		_buttons[_button_key(action, pad)] = button
		line.add_child(button)
	return line


func _named_row(text: String, control: Control) -> HBoxContainer:
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "%s_row" % control.name
	return line


func _refresh() -> void:
	for action: StringName in Controller.actions():
		for pad: bool in [false, true]:
			var button: Button = _buttons.get(_button_key(action, pad)) as Button
			if button != null and not (action == _capture_action and pad == _capture_pad):
				button.text = Controller.event_text(Controller.binding(action, pad))
	if _deadzone_slider != null:
		_deadzone_slider.set_value_no_signal(Controller.stick_deadzone())
	var choice: String = str(
		Settings.get_value(Controller.SECTION, Controller.KEY_LABELS, Bindings.LAYOUT_AUTO)
	)
	_labels_button.text = names.layout_label(choice)
	_update_device_label()


func _update_device_label() -> void:
	var pads: Array[int] = Input.get_connected_joypads()
	if pads.is_empty():
		device_label.text = tr("No controller connected.")
		return
	var device: int = Controller.active_device if Controller.active_device >= 0 else pads[0]
	var pad_name: String = Input.get_joy_name(device)
	if pad_name.is_empty():
		pad_name = names.layout_label(Controller.pad_layout())
	device_label.text = tr("Controller: %s") % pad_name


func _finish_capture(event: InputEvent) -> void:
	var action: StringName = _capture_action
	var pad: bool = _capture_pad
	var button: Button = _capture_button
	_end_capture()
	var made: InputEvent = Bindings.binding_from(event)
	if made == null:
		_refresh()
		return
	var swapped: StringName = Controller.bind(action, made, pad)
	_refresh()
	if swapped != &"":
		status_label.text = tr("%s and %s swapped bindings.") % [
			names.action_label(action), names.action_label(swapped)
		]
	else:
		status_label.text = tr("%s: %s") % [names.action_label(action), Controller.event_text(made)]
	if is_instance_valid(button) and button.is_visible_in_tree():
		button.grab_focus()


func _cancel_capture() -> void:
	if _capture_action == &"":
		return
	_end_capture()
	_refresh()


func _end_capture() -> void:
	_capture_action = &""
	_capture_button = null
	_capture_armed = false
	set_process(false)


func _is_press(event: InputEvent) -> bool:
	var key: InputEventKey = event as InputEventKey
	if key != null:
		return key.pressed and not key.echo
	var mouse: InputEventMouseButton = event as InputEventMouseButton
	if mouse != null:
		return mouse.pressed and mouse.button_index in [
			MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE,
			MOUSE_BUTTON_XBUTTON1, MOUSE_BUTTON_XBUTTON2,
		]
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		return button.pressed
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		return absf(motion.axis_value) >= 0.6
	return false


func _next_layout() -> void:
	var choice: String = str(
		Settings.get_value(Controller.SECTION, Controller.KEY_LABELS, Bindings.LAYOUT_AUTO)
	)
	var index: int = Bindings.LAYOUTS.find(choice)
	var next: String = Bindings.LAYOUTS[(index + 1) % Bindings.LAYOUTS.size()]
	Settings.set_value(Controller.SECTION, Controller.KEY_LABELS, next)
	_refresh()


func _reset() -> void:
	_cancel_capture()
	Controller.reset_bindings()
	status_label.text = tr("Controls are back to the game's defaults.")


func _has_sticks() -> bool:
	for action: StringName in Controller.actions():
		for event: InputEvent in InputMap.action_get_events(action):
			if event is InputEventJoypadMotion:
				return true
	return false


func _on_device_changed(_using_pad: bool) -> void:
	_update_device_label()


func _on_joy_connection_changed(_device: int, _connected: bool) -> void:
	_update_device_label()


static func _button_key(action: StringName, pad: bool) -> String:
	return "%s/%s" % [action, "pad" if pad else "keys"]
