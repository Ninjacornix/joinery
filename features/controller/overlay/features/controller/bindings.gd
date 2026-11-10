extends RefCounted
## Helpers for input bindings: which actions can be rebound, how an event is
## named on screen, and how a binding is stored in the player's settings.
## Data helpers are static; the naming helpers are methods so their text goes
## through `tr()` (make one with `new()`).
## Names are plain text (no glyph images) and follow the pad's layout: Xbox
## (A, B, X, Y), PlayStation (Cross, Circle, Square, Triangle) or Nintendo
## (B, A, Y, X by position).

## The standard action vocabulary, in the order the Controls page lists it.
const ACTIONS: Array[StringName] = [
	&"move_left", &"move_right", &"move_up", &"move_down", &"move_forward", &"move_back",
	&"look_left", &"look_right", &"look_up", &"look_down",
	&"jump", &"crouch", &"sprint", &"dash", &"attack", &"interact", &"restart", &"pause",
]

const LAYOUT_AUTO := "auto"
const LAYOUT_XBOX := "xbox"
const LAYOUT_PLAYSTATION := "playstation"
const LAYOUT_NINTENDO := "nintendo"
const LAYOUTS: Array[String] = [LAYOUT_AUTO, LAYOUT_XBOX, LAYOUT_PLAYSTATION, LAYOUT_NINTENDO]

## A stick or trigger must move this far to count as a press (device switch,
## rebinding).
const AXIS_PRESS := 0.5
## Desktop display servers that can name a physical key in the player's layout.
const LAYOUT_AWARE_DISPLAYS: Array[String] = ["Windows", "macOS", "X11", "Wayland"]


## The vocabulary actions this project has, in display order.
static func project_actions() -> Array[StringName]:
	var found: Array[StringName] = []
	for action: StringName in ACTIONS:
		if InputMap.has_action(action):
			found.append(action)
	return found


static func is_pad(event: InputEvent) -> bool:
	return event is InputEventJoypadButton or event is InputEventJoypadMotion


static func is_keyboard(event: InputEvent) -> bool:
	return event is InputEventKey or event is InputEventMouseButton


## The events of `action` for one device class (pad, or keyboard and mouse).
static func events_of(action: StringName, pad: bool) -> Array[InputEvent]:
	var found: Array[InputEvent] = []
	if not InputMap.has_action(action):
		return found
	for event: InputEvent in InputMap.action_get_events(action):
		if (pad and is_pad(event)) or (not pad and is_keyboard(event)):
			found.append(event)
	return found


## What makes two events the same binding (device and pressure ignored).
static func identity(event: InputEvent) -> String:
	var key: InputEventKey = event as InputEventKey
	if key != null:
		var code: int = key.physical_keycode if key.physical_keycode != KEY_NONE else key.keycode
		return "key:%d" % code
	var mouse: InputEventMouseButton = event as InputEventMouseButton
	if mouse != null:
		return "mouse:%d" % mouse.button_index
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		return "pad_button:%d" % button.button_index
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		return "pad_axis:%d:%d" % [motion.axis, 1 if motion.axis_value >= 0.0 else -1]
	return ""


## A clean, device-independent copy of a pressed event, fit for the input map;
## null for events that cannot be a binding.
static func binding_from(event: InputEvent) -> InputEvent:
	var key: InputEventKey = event as InputEventKey
	if key != null:
		var made_key: InputEventKey = InputEventKey.new()
		if key.physical_keycode != KEY_NONE:
			made_key.physical_keycode = key.physical_keycode
		else:
			made_key.keycode = key.keycode
		return made_key if identity(made_key) != "key:0" else null
	var mouse: InputEventMouseButton = event as InputEventMouseButton
	if mouse != null:
		var made_mouse: InputEventMouseButton = InputEventMouseButton.new()
		made_mouse.button_index = mouse.button_index
		return made_mouse
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		var made_button: InputEventJoypadButton = InputEventJoypadButton.new()
		made_button.button_index = button.button_index
		made_button.device = -1
		return made_button
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		var made_motion: InputEventJoypadMotion = InputEventJoypadMotion.new()
		made_motion.axis = motion.axis
		made_motion.axis_value = 1.0 if motion.axis_value >= 0.0 else -1.0
		made_motion.device = -1
		return made_motion
	return null


## A binding as plain data for the settings file.
static func to_data(event: InputEvent) -> Dictionary:
	var key: InputEventKey = event as InputEventKey
	if key != null:
		return {"type": "key", "physical": int(key.physical_keycode), "keycode": int(key.keycode)}
	var mouse: InputEventMouseButton = event as InputEventMouseButton
	if mouse != null:
		return {"type": "mouse", "button": int(mouse.button_index)}
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		return {"type": "pad_button", "button": int(button.button_index)}
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		return {
			"type": "pad_axis",
			"axis": int(motion.axis),
			"value": 1.0 if motion.axis_value >= 0.0 else -1.0,
		}
	return {}


## The event stored by `to_data`, or null when the data is not a binding.
static func from_data(data: Variant) -> InputEvent:
	if not data is Dictionary:
		return null
	var entry: Dictionary = data
	match str(entry.get("type", "")):
		"key":
			var key: InputEventKey = InputEventKey.new()
			key.physical_keycode = int(entry.get("physical", 0)) as Key
			key.keycode = int(entry.get("keycode", 0)) as Key
			return key if identity(key) != "key:0" else null
		"mouse":
			var mouse: InputEventMouseButton = InputEventMouseButton.new()
			var index: int = int(entry.get("button", 0))
			if index <= 0:
				return null
			mouse.button_index = index as MouseButton
			return mouse
		"pad_button":
			var button: InputEventJoypadButton = InputEventJoypadButton.new()
			var number: int = int(entry.get("button", -1))
			if number < 0 or number >= JOY_BUTTON_SDL_MAX:
				return null
			button.button_index = number as JoyButton
			button.device = -1
			return button
		"pad_axis":
			var motion: InputEventJoypadMotion = InputEventJoypadMotion.new()
			var axis: int = int(entry.get("axis", -1))
			if axis < 0 or axis >= JOY_AXIS_SDL_MAX:
				return null
			motion.axis = axis as JoyAxis
			motion.axis_value = 1.0 if float(entry.get("value", 1.0)) >= 0.0 else -1.0
			motion.device = -1
			return motion
	return null


## The pad layout to name buttons by. `choice` is a `LAYOUTS` entry; "auto"
## guesses from the connected pad's name and falls back to Xbox names.
static func layout_for(choice: String, device: int) -> String:
	if choice != LAYOUT_AUTO and choice in LAYOUTS:
		return choice
	if device < 0:
		return LAYOUT_XBOX
	var pad_name: String = Input.get_joy_name(device).to_lower()
	for hint: String in ["playstation", "dualshock", "dualsense", "ps3", "ps4", "ps5", "sony"]:
		if hint in pad_name:
			return LAYOUT_PLAYSTATION
	for hint: String in ["nintendo", "switch", "joy-con", "joycon", "pro controller"]:
		if hint in pad_name:
			return LAYOUT_NINTENDO
	return LAYOUT_XBOX


## Full on-screen name of a binding, such as "Space", "Left stick left" or "RB".
func event_text(event: InputEvent, layout: String) -> String:
	if event == null:
		return tr("Not bound")
	var key: InputEventKey = event as InputEventKey
	if key != null:
		return key_text(key)
	var mouse: InputEventMouseButton = event as InputEventMouseButton
	if mouse != null:
		return mouse_text(mouse.button_index)
	var button: InputEventJoypadButton = event as InputEventJoypadButton
	if button != null:
		return button_text(button.button_index, layout)
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		return axis_text(motion.axis, motion.axis_value, layout)
	return event.as_text()


## Short prompt for a binding: like `event_text`, but a stick is named as a
## whole ("Left stick"), as a player reads it next to "Move" or "Look".
func prompt_text(event: InputEvent, layout: String) -> String:
	var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
	if motion != null:
		match motion.axis:
			JOY_AXIS_LEFT_X, JOY_AXIS_LEFT_Y:
				return tr("Left stick")
			JOY_AXIS_RIGHT_X, JOY_AXIS_RIGHT_Y:
				return tr("Right stick")
	return event_text(event, layout)


func key_text(key: InputEventKey) -> String:
	var code: Key = key.physical_keycode
	if code != KEY_NONE:
		if DisplayServer.get_name() in LAYOUT_AWARE_DISPLAYS:
			var local: Key = DisplayServer.keyboard_get_keycode_from_physical(code)
			if local != KEY_NONE:
				code = local
	else:
		code = key.keycode
	if code == KEY_NONE:
		code = key.key_label
	return OS.get_keycode_string(code)


func mouse_text(index: MouseButton) -> String:
	var known: Dictionary = {
		MOUSE_BUTTON_LEFT: tr("Left mouse button"),
		MOUSE_BUTTON_RIGHT: tr("Right mouse button"),
		MOUSE_BUTTON_MIDDLE: tr("Middle mouse button"),
		MOUSE_BUTTON_WHEEL_UP: tr("Mouse wheel up"),
		MOUSE_BUTTON_WHEEL_DOWN: tr("Mouse wheel down"),
	}
	if known.has(index):
		return str(known[index])
	return tr("Mouse button %d") % int(index)


func button_text(index: JoyButton, layout: String) -> String:
	var faces: Dictionary = _face_buttons(layout)
	if faces.has(index):
		return str(faces[index])
	var known: Dictionary = {
		JOY_BUTTON_DPAD_UP: tr("D-pad up"),
		JOY_BUTTON_DPAD_DOWN: tr("D-pad down"),
		JOY_BUTTON_DPAD_LEFT: tr("D-pad left"),
		JOY_BUTTON_DPAD_RIGHT: tr("D-pad right"),
		JOY_BUTTON_TOUCHPAD: tr("Touchpad"),
	}
	if known.has(index):
		return str(known[index])
	if index >= JOY_BUTTON_PADDLE1 and index <= JOY_BUTTON_PADDLE4:
		return tr("Paddle %d") % (int(index) - JOY_BUTTON_PADDLE1 + 1)
	return tr("Button %d") % int(index)


func axis_text(axis: JoyAxis, value: float, layout: String) -> String:
	var side: int = 1 if value >= 0.0 else 0
	var sticks: Dictionary = {
		JOY_AXIS_LEFT_X: [tr("Left stick left"), tr("Left stick right")],
		JOY_AXIS_LEFT_Y: [tr("Left stick up"), tr("Left stick down")],
		JOY_AXIS_RIGHT_X: [tr("Right stick left"), tr("Right stick right")],
		JOY_AXIS_RIGHT_Y: [tr("Right stick up"), tr("Right stick down")],
	}
	if sticks.has(axis):
		return str((sticks[axis] as Array)[side])
	var triggers: Dictionary = {
		LAYOUT_PLAYSTATION: ["L2", "R2"],
		LAYOUT_NINTENDO: ["ZL", "ZR"],
		LAYOUT_XBOX: ["LT", "RT"],
	}
	var names: Array = triggers.get(layout, triggers[LAYOUT_XBOX])
	if axis == JOY_AXIS_TRIGGER_LEFT:
		return str(names[0])
	if axis == JOY_AXIS_TRIGGER_RIGHT:
		return str(names[1])
	return tr("Axis %d") % int(axis)


## Display name of a vocabulary action.
func action_label(action: StringName) -> String:
	var labels: Dictionary = {
		&"move_left": tr("Move left"),
		&"move_right": tr("Move right"),
		&"move_up": tr("Move up"),
		&"move_down": tr("Move down"),
		&"move_forward": tr("Move forward"),
		&"move_back": tr("Move back"),
		&"look_left": tr("Look left"),
		&"look_right": tr("Look right"),
		&"look_up": tr("Look up"),
		&"look_down": tr("Look down"),
		&"jump": tr("Jump"),
		&"crouch": tr("Crouch"),
		&"sprint": tr("Sprint"),
		&"dash": tr("Dash"),
		&"attack": tr("Attack"),
		&"interact": tr("Interact"),
		&"restart": tr("Restart"),
		&"pause": tr("Pause"),
	}
	return str(labels.get(action, String(action).capitalize()))


## Display name of a `LAYOUTS` entry.
func layout_label(layout: String) -> String:
	var labels: Dictionary = {
		LAYOUT_XBOX: tr("Xbox"),
		LAYOUT_PLAYSTATION: tr("PlayStation"),
		LAYOUT_NINTENDO: tr("Nintendo"),
	}
	return str(labels.get(layout, tr("Automatic")))


## Names of the face, shoulder and menu buttons for one pad layout.
func _face_buttons(layout: String) -> Dictionary:
	if layout == LAYOUT_PLAYSTATION:
		return {
			JOY_BUTTON_A: tr("Cross"),
			JOY_BUTTON_B: tr("Circle"),
			JOY_BUTTON_X: tr("Square"),
			JOY_BUTTON_Y: tr("Triangle"),
			JOY_BUTTON_BACK: tr("Share"),
			JOY_BUTTON_GUIDE: "PS",
			JOY_BUTTON_START: tr("Options"),
			JOY_BUTTON_LEFT_STICK: "L3",
			JOY_BUTTON_RIGHT_STICK: "R3",
			JOY_BUTTON_LEFT_SHOULDER: "L1",
			JOY_BUTTON_RIGHT_SHOULDER: "R1",
			JOY_BUTTON_MISC1: tr("Mute"),
		}
	if layout == LAYOUT_NINTENDO:
		# Godot numbers face buttons by position: 0 is the bottom one, which
		# Nintendo pads label B.
		return {
			JOY_BUTTON_A: "B",
			JOY_BUTTON_B: "A",
			JOY_BUTTON_X: "Y",
			JOY_BUTTON_Y: "X",
			JOY_BUTTON_BACK: "-",
			JOY_BUTTON_GUIDE: tr("Home"),
			JOY_BUTTON_START: "+",
			JOY_BUTTON_LEFT_STICK: tr("Left stick click"),
			JOY_BUTTON_RIGHT_STICK: tr("Right stick click"),
			JOY_BUTTON_LEFT_SHOULDER: "L",
			JOY_BUTTON_RIGHT_SHOULDER: "R",
			JOY_BUTTON_MISC1: tr("Capture"),
		}
	return {
		JOY_BUTTON_A: "A",
		JOY_BUTTON_B: "B",
		JOY_BUTTON_X: "X",
		JOY_BUTTON_Y: "Y",
		JOY_BUTTON_BACK: tr("Back"),
		JOY_BUTTON_GUIDE: tr("Guide"),
		JOY_BUTTON_START: tr("Start"),
		JOY_BUTTON_LEFT_STICK: tr("Left stick click"),
		JOY_BUTTON_RIGHT_STICK: tr("Right stick click"),
		JOY_BUTTON_LEFT_SHOULDER: "LB",
		JOY_BUTTON_RIGHT_SHOULDER: "RB",
		JOY_BUTTON_MISC1: tr("Share"),
	}
