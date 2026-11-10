extends Node
## Gamepad support. Knows whether the player is using a pad or the keyboard
## and mouse, names bindings for on-screen prompts ("A", "Space", "Left
## stick"), rumbles, hides the mouse cursor while a pad is in use, keeps a
## control focused in every open menu so a pad can always drive it, and pauses
## when the pad in use disconnects. Adds the "Controls" settings page, where
## any standard action can be rebound for keyboard and pad.
##
## Player settings (section `input` in `Settings`):
##   bindings        {action: [binding data]} for actions the player changed
##   deadzone        stick deadzone for every action bound to a stick
##   invert_look_y   bool, read by camera code
##   rumble          bool (default on)
##   pad_labels      "auto", "xbox", "playstation" or "nintendo"

signal device_changed(using_pad: bool)
## A binding changed (rebinding, reset, or loading saved bindings).
signal bindings_changed

const Bindings := preload("res://features/controller/bindings.gd")
## Loaded when the page opens (it uses this autoload by name).
const CONTROLS_PAGE := "res://features/controller/controls_page.gd"

const SECTION := "input"
const KEY_BINDINGS := "bindings"
const KEY_DEADZONE := "deadzone"
const KEY_INVERT_Y := "invert_look_y"
const KEY_RUMBLE := "rumble"
const KEY_LABELS := "pad_labels"
const DEFAULT_DEADZONE := 0.25
const DEADZONE_MIN := 0.05
const DEADZONE_MAX := 0.6
## Mouse motion shorter than this (pixels) does not switch away from the pad.
const MOUSE_SWITCH_DISTANCE := 3.0

## True while the player is using a gamepad (the last input came from one).
var using_pad: bool = false
## The pad that sent the last pad input, or -1.
var active_device: int = -1
## True while this node hid the mouse cursor for pad play.
var cursor_hidden: bool = false

var names: Bindings = Bindings.new()
## Project bindings of every vocabulary action as the game shipped them.
var _defaults: Dictionary = {}
var _default_deadzones: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_sticks_first()
	_remember_defaults()
	_add_menu_pad_events()
	apply_saved()
	Input.joy_connection_changed.connect(_on_joy_connection_changed)
	GameEvents.menu_opened.connect(_on_menu_opened)
	Settings.changed.connect(_on_settings_changed)
	Menus.add_settings_page(&"controls", "Controls", _build_controls_page, 40)
	var pads: Array[int] = Input.get_connected_joypads()
	if not pads.is_empty():
		active_device = pads[0]


func _input(event: InputEvent) -> void:
	if event is InputEventAction:
		return
	if _is_pad_press(event):
		active_device = event.device
		_set_using_pad(true)
		_ensure_menu_focus()
	elif _is_keyboard_or_mouse_use(event):
		_set_using_pad(false)


## On-screen text for `action` on the device in use: "A", "Space", "Left
## stick". Falls back to the other device's binding, then the action's name.
func prompt(action: StringName) -> String:
	if not InputMap.has_action(action):
		return names.action_label(action)
	var first: Array[InputEvent] = Bindings.events_of(action, using_pad)
	if first.is_empty():
		first = Bindings.events_of(action, not using_pad)
	if first.is_empty():
		return names.action_label(action)
	return names.prompt_text(first[0], pad_layout())


## The full name of one binding, such as "Left stick left" or "Space".
func event_text(event: InputEvent) -> String:
	return names.event_text(event, pad_layout())


## The layout pad buttons are named after: "xbox", "playstation" or "nintendo".
func pad_layout() -> String:
	var choice: String = str(Settings.get_value(SECTION, KEY_LABELS, Bindings.LAYOUT_AUTO))
	return Bindings.layout_for(choice, active_device)


## Vibrates the pad in use (strengths 0-1), unless the player turned rumble off.
func rumble(weak: float, strong: float, seconds: float) -> void:
	if not rumble_enabled():
		return
	var device: int = _rumble_device()
	if device < 0:
		return
	Input.start_joy_vibration(device, clampf(weak, 0.0, 1.0), clampf(strong, 0.0, 1.0), seconds)


func rumble_enabled() -> bool:
	return bool(Settings.get_value(SECTION, KEY_RUMBLE, true))


func invert_look_y() -> bool:
	return bool(Settings.get_value(SECTION, KEY_INVERT_Y, false))


## The vocabulary actions this project has, in display order.
func actions() -> Array[StringName]:
	return Bindings.project_actions()


## The first binding of `action` for the pad (or keyboard and mouse), or null.
func binding(action: StringName, pad: bool) -> InputEvent:
	var found: Array[InputEvent] = Bindings.events_of(action, pad)
	return found[0] if not found.is_empty() else null


## Makes `event` the first `pad` (or keyboard and mouse) binding of `action`,
## replacing the old first one. When another action already uses `event`, the
## two swap: that action gets the old binding. Saves the change. Returns the
## action it swapped with, or &"".
func bind(action: StringName, event: InputEvent, pad: bool) -> StringName:
	var made: InputEvent = Bindings.binding_from(event)
	if made == null or not InputMap.has_action(action) or Bindings.is_pad(made) != pad:
		return &""
	var old: InputEvent = binding(action, pad)
	var wanted: String = Bindings.identity(made)
	if old != null and Bindings.identity(old) == wanted:
		return &""
	var swapped: StringName = &""
	for other: StringName in actions():
		if other == action:
			continue
		var clash: InputEvent = _find(other, wanted)
		if clash == null:
			continue
		swapped = other
		_replace(other, clash, old.duplicate() as InputEvent if old != null else null)
	_drop_duplicates(action, made)
	_replace(action, old, made)
	_save_bindings()
	bindings_changed.emit()
	return swapped


## Puts every binding back the way the game shipped it and forgets the
## player's changes, deadzone included.
func reset_bindings() -> void:
	for action: StringName in _defaults:
		if not InputMap.has_action(action):
			continue
		InputMap.action_erase_events(action)
		for event: InputEvent in _defaults[action]:
			InputMap.action_add_event(action, event)
		InputMap.action_set_deadzone(action, float(_default_deadzones[action]))
	Settings.set_value(SECTION, KEY_BINDINGS, {})
	if Settings.has_value(SECTION, KEY_DEADZONE):
		Settings.set_value(SECTION, KEY_DEADZONE, stick_deadzone_default())
	bindings_changed.emit()


## Applies saved bindings and deadzone to the input map. Runs on start.
func apply_saved() -> void:
	var saved: Variant = Settings.get_value(SECTION, KEY_BINDINGS, {})
	if saved is Dictionary:
		for action_name: Variant in (saved as Dictionary).keys():
			var action: StringName = StringName(str(action_name))
			if not _defaults.has(action) or not InputMap.has_action(action):
				continue
			var list: Variant = (saved as Dictionary)[action_name]
			if not list is Array:
				continue
			var events: Array[InputEvent] = []
			for data: Variant in list:
				var event: InputEvent = Bindings.from_data(data)
				if event != null:
					events.append(event)
			InputMap.action_erase_events(action)
			for event: InputEvent in events:
				InputMap.action_add_event(action, event)
	if Settings.has_value(SECTION, KEY_DEADZONE):
		_apply_deadzone(float(Settings.get_value(SECTION, KEY_DEADZONE)))
	bindings_changed.emit()


## The stick deadzone in use (of the first action bound to a stick).
func stick_deadzone() -> float:
	for action: StringName in actions():
		if _has_stick(action):
			return InputMap.action_get_deadzone(action)
	return DEFAULT_DEADZONE


## The stick deadzone the game shipped with.
func stick_deadzone_default() -> float:
	for action: StringName in actions():
		if _has_stick_in(_defaults.get(action, [])):
			return float(_default_deadzones[action])
	return DEFAULT_DEADZONE


func set_stick_deadzone(value: float) -> void:
	Settings.set_value(SECTION, KEY_DEADZONE, clampf(value, DEADZONE_MIN, DEADZONE_MAX))


## Gives focus to the first control of the open menu when nothing in it has
## focus, so a pad can always drive the menu.
func focus_menu() -> void:
	var page: Control = Menus.current_page()
	if page == null or not page.is_visible_in_tree():
		return
	var owner_control: Control = get_viewport().gui_get_focus_owner()
	if owner_control != null and page.is_ancestor_of(owner_control):
		return
	var target: Control = _first_focusable(page)
	if target != null:
		target.grab_focus()


func _build_controls_page() -> Control:
	var script: GDScript = load(CONTROLS_PAGE) as GDScript
	return script.new() as Control


## Puts stick and trigger bindings before pad buttons in every vocabulary
## action, so the first pad binding (the one prompts name and the Controls page
## rebinds) is "Left stick" rather than the d-pad. Order does not change what
## the bindings do.
func _sticks_first() -> void:
	for action: StringName in Bindings.ACTIONS:
		if not InputMap.has_action(action):
			continue
		var events: Array[InputEvent] = InputMap.action_get_events(action)
		var others: Array[InputEvent] = []
		var axes: Array[InputEvent] = []
		var buttons: Array[InputEvent] = []
		for event: InputEvent in events:
			if event is InputEventJoypadMotion:
				axes.append(event)
			elif event is InputEventJoypadButton:
				buttons.append(event)
			else:
				others.append(event)
		var ordered: Array[InputEvent] = others.duplicate()
		ordered.append_array(axes)
		ordered.append_array(buttons)
		if ordered == events:
			continue
		InputMap.action_erase_events(action)
		for event: InputEvent in ordered:
			InputMap.action_add_event(action, event)


func _remember_defaults() -> void:
	for action: StringName in Bindings.ACTIONS:
		if not InputMap.has_action(action):
			continue
		var events: Array[InputEvent] = []
		for event: InputEvent in InputMap.action_get_events(action):
			events.append(event.duplicate() as InputEvent)
		_defaults[action] = events
		_default_deadzones[action] = InputMap.action_get_deadzone(action)


## Godot's own ui_accept and ui_cancel have no pad buttons; menus need them.
## Added at run time only, so the project's ui_* actions stay Godot's.
func _add_menu_pad_events() -> void:
	for pair: Array in [[&"ui_accept", JOY_BUTTON_A], [&"ui_cancel", JOY_BUTTON_B]]:
		var action: StringName = pair[0]
		if not InputMap.has_action(action):
			continue
		var button: InputEventJoypadButton = InputEventJoypadButton.new()
		button.button_index = int(pair[1]) as JoyButton
		button.device = -1
		if _find(action, Bindings.identity(button)) == null:
			InputMap.action_add_event(action, button)


func _is_pad_press(event: InputEvent) -> bool:
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		return button.pressed
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		return absf(motion.axis_value) >= Bindings.AXIS_PRESS
	return false


func _is_keyboard_or_mouse_use(event: InputEvent) -> bool:
	if event is InputEventKey:
		return (event as InputEventKey).pressed
	if event is InputEventMouseButton:
		return (event as InputEventMouseButton).pressed
	var motion: InputEventMouseMotion = event as InputEventMouseMotion
	if motion != null:
		# Screen pixels: `relative` is scaled to the viewport and can be large
		# for a tiny real movement in a small or hidden window.
		return motion.screen_relative.length() >= MOUSE_SWITCH_DISTANCE
	return false


func _set_using_pad(on: bool) -> void:
	if on == using_pad:
		return
	using_pad = on
	_update_cursor()
	device_changed.emit(on)


## Hides a visible cursor while a pad is in use and shows it again after.
## A captured cursor (mouse look) is left alone.
func _update_cursor() -> void:
	if using_pad:
		if Input.mouse_mode == Input.MOUSE_MODE_VISIBLE:
			Input.mouse_mode = Input.MOUSE_MODE_HIDDEN
			cursor_hidden = true
	elif cursor_hidden:
		cursor_hidden = false
		if Input.mouse_mode == Input.MOUSE_MODE_HIDDEN:
			Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _ensure_menu_focus() -> void:
	if Menus.is_open():
		focus_menu()


func _on_menu_opened(_menu: Control) -> void:
	focus_menu.call_deferred()
	if using_pad:
		# Pages that release a captured mouse show the cursor; hide it again.
		_update_cursor.call_deferred()


func _on_joy_connection_changed(device: int, connected: bool) -> void:
	if connected:
		if active_device < 0:
			active_device = device
		Menus.toast("Controller connected")
		return
	if device != active_device:
		return
	var was_using: bool = using_pad
	active_device = -1
	var pads: Array[int] = Input.get_connected_joypads()
	pads.erase(device)
	if not pads.is_empty():
		active_device = pads[0]
	Menus.toast("Controller disconnected")
	if was_using and Game.current_level() != null and not Menus.is_open():
		Menus.open_pause()


func _on_settings_changed(section: String, key: String) -> void:
	if section != SECTION:
		return
	if key == KEY_DEADZONE:
		_apply_deadzone(float(Settings.get_value(SECTION, KEY_DEADZONE)))
	elif key == KEY_RUMBLE and not rumble_enabled():
		for device: int in Input.get_connected_joypads():
			Input.stop_joy_vibration(device)
		if active_device >= 0:
			Input.stop_joy_vibration(active_device)


func _rumble_device() -> int:
	if active_device >= 0:
		return active_device
	var pads: Array[int] = Input.get_connected_joypads()
	return pads[0] if not pads.is_empty() else -1


func _apply_deadzone(value: float) -> void:
	var zone: float = clampf(value, DEADZONE_MIN, DEADZONE_MAX)
	for action: StringName in actions():
		if _has_stick(action):
			InputMap.action_set_deadzone(action, zone)


func _has_stick(action: StringName) -> bool:
	return _has_stick_in(InputMap.action_get_events(action))


func _has_stick_in(events: Array) -> bool:
	for event: Variant in events:
		var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
		if motion != null and motion.axis <= JOY_AXIS_RIGHT_Y:
			return true
	return false


func _find(action: StringName, wanted: String) -> InputEvent:
	for event: InputEvent in InputMap.action_get_events(action):
		if Bindings.identity(event) == wanted:
			return event
	return null


## Replaces `old` in `action` with `new_event`, keeping its place in the list.
## With no `old`, `new_event` goes first among its device's bindings; with no
## `new_event`, `old` is removed.
func _replace(action: StringName, old: InputEvent, new_event: InputEvent) -> void:
	var events: Array[InputEvent] = InputMap.action_get_events(action)
	var result: Array[InputEvent] = []
	var placed: bool = false
	for event: InputEvent in events:
		if old != null and event == old:
			if new_event != null:
				result.append(new_event)
			placed = true
		else:
			result.append(event)
	if not placed and new_event != null:
		var index: int = result.size()
		for i: int in result.size():
			if Bindings.is_pad(result[i]) == Bindings.is_pad(new_event):
				index = i
				break
		result.insert(index, new_event)
	InputMap.action_erase_events(action)
	for event: InputEvent in result:
		InputMap.action_add_event(action, event)


## Removes extra copies of `event` from `action` (it may already be a later
## binding of the same action).
func _drop_duplicates(action: StringName, event: InputEvent) -> void:
	var wanted: String = Bindings.identity(event)
	var first: InputEvent = binding(action, Bindings.is_pad(event))
	for existing: InputEvent in InputMap.action_get_events(action):
		if existing != first and Bindings.identity(existing) == wanted:
			InputMap.action_erase_event(action, existing)


## Saves the full binding list of every action that differs from the game's.
func _save_bindings() -> void:
	var changed: Dictionary = {}
	for action: StringName in actions():
		if not _defaults.has(action):
			continue
		var now: Array[String] = _identities(InputMap.action_get_events(action))
		var shipped: Array[String] = _identities(_defaults[action])
		if now == shipped:
			continue
		var data: Array = []
		for event: InputEvent in InputMap.action_get_events(action):
			var entry: Dictionary = Bindings.to_data(event)
			if not entry.is_empty():
				data.append(entry)
		changed[String(action)] = data
	Settings.set_value(SECTION, KEY_BINDINGS, changed)


func _identities(events: Array) -> Array[String]:
	var found: Array[String] = []
	for event: Variant in events:
		found.append(Bindings.identity(event as InputEvent))
	return found


func _first_focusable(node: Node) -> Control:
	for child: Node in node.get_children():
		if child is Control and not (child as Control).visible:
			continue
		if child is Control and (child as Control).focus_mode == Control.FOCUS_ALL:
			if not (child is BaseButton and (child as BaseButton).disabled):
				return child as Control
		var found: Control = _first_focusable(child)
		if found != null:
			return found
	return null
