extends RefCounted
## Turns input events into small dictionaries for a replay file and back.
##
## Only the kinds a game is played with are kept: keys, mouse buttons and motion, joypad
## buttons and axes, touch, and input actions (what test harnesses and the playtest bot
## send). Anything else (gestures, MIDI) is left out. Decoding builds events from a fixed
## list of fields, never from a class name in the file, so a replay a player sends
## cannot make anything but an input event.
##
## Positions are window coordinates, as the root window receives them.


static func encode(event: InputEvent) -> Dictionary:
	if event is InputEventKey:
		var key: InputEventKey = event
		return {
			"t": "key",
			"k": int(key.keycode),
			"p": int(key.physical_keycode),
			"u": key.unicode,
			"d": key.pressed,
			"e": key.echo,
			"m": _mods(key),
		}
	if event is InputEventMouseButton:
		var button: InputEventMouseButton = event
		return {
			"t": "mb",
			"b": int(button.button_index),
			"d": button.pressed,
			"x": button.position.x,
			"y": button.position.y,
			"bm": int(button.button_mask),
			"dc": button.double_click,
			"f": button.factor,
			"m": _mods(button),
		}
	if event is InputEventMouseMotion:
		var motion: InputEventMouseMotion = event
		return {
			"t": "mm",
			"x": motion.position.x,
			"y": motion.position.y,
			"rx": motion.relative.x,
			"ry": motion.relative.y,
			"bm": int(motion.button_mask),
			"m": _mods(motion),
		}
	if event is InputEventJoypadButton:
		var pad: InputEventJoypadButton = event
		return {
			"t": "jb",
			"dev": pad.device,
			"b": int(pad.button_index),
			"d": pad.pressed,
			"pr": pad.pressure,
		}
	if event is InputEventJoypadMotion:
		var axis: InputEventJoypadMotion = event
		return {"t": "jm", "dev": axis.device, "a": int(axis.axis), "v": axis.axis_value}
	if event is InputEventScreenTouch:
		var touch: InputEventScreenTouch = event
		return {
			"t": "touch",
			"i": touch.index,
			"x": touch.position.x,
			"y": touch.position.y,
			"d": touch.pressed,
			"dt": touch.double_tap,
		}
	if event is InputEventScreenDrag:
		var drag: InputEventScreenDrag = event
		return {
			"t": "drag",
			"i": drag.index,
			"x": drag.position.x,
			"y": drag.position.y,
			"rx": drag.relative.x,
			"ry": drag.relative.y,
		}
	if event is InputEventAction:
		var action: InputEventAction = event
		return {
			"t": "act",
			"a": String(action.action),
			"d": action.pressed,
			"s": action.strength,
		}
	return {}


## The event `data` describes, or null. `scale` maps recorded window positions to this
## window (1, 1 when the sizes match).
static func decode(data: Dictionary, scale: Vector2 = Vector2.ONE) -> InputEvent:
	match str(data.get("t", "")):
		"key":
			var key: InputEventKey = InputEventKey.new()
			key.keycode = int(data.get("k", 0)) as Key
			key.physical_keycode = int(data.get("p", 0)) as Key
			key.unicode = int(data.get("u", 0))
			key.pressed = bool(data.get("d", false))
			key.echo = bool(data.get("e", false))
			_set_mods(key, int(data.get("m", 0)))
			return key
		"mb":
			var button: InputEventMouseButton = InputEventMouseButton.new()
			button.button_index = int(data.get("b", 1)) as MouseButton
			button.pressed = bool(data.get("d", false))
			button.position = _point(data, "x", "y") * scale
			button.global_position = button.position
			button.button_mask = int(data.get("bm", 0)) as MouseButtonMask
			button.double_click = bool(data.get("dc", false))
			button.factor = float(data.get("f", 1.0))
			_set_mods(button, int(data.get("m", 0)))
			return button
		"mm":
			var motion: InputEventMouseMotion = InputEventMouseMotion.new()
			motion.position = _point(data, "x", "y") * scale
			motion.global_position = motion.position
			motion.relative = _point(data, "rx", "ry") * scale
			motion.screen_relative = motion.relative
			motion.button_mask = int(data.get("bm", 0)) as MouseButtonMask
			_set_mods(motion, int(data.get("m", 0)))
			return motion
		"jb":
			var pad: InputEventJoypadButton = InputEventJoypadButton.new()
			pad.device = int(data.get("dev", 0))
			pad.button_index = int(data.get("b", 0)) as JoyButton
			pad.pressed = bool(data.get("d", false))
			pad.pressure = float(data.get("pr", 0.0))
			return pad
		"jm":
			var axis: InputEventJoypadMotion = InputEventJoypadMotion.new()
			axis.device = int(data.get("dev", 0))
			axis.axis = int(data.get("a", 0)) as JoyAxis
			axis.axis_value = float(data.get("v", 0.0))
			return axis
		"touch":
			var touch: InputEventScreenTouch = InputEventScreenTouch.new()
			touch.index = int(data.get("i", 0))
			touch.position = _point(data, "x", "y") * scale
			touch.pressed = bool(data.get("d", false))
			touch.double_tap = bool(data.get("dt", false))
			return touch
		"drag":
			var drag: InputEventScreenDrag = InputEventScreenDrag.new()
			drag.index = int(data.get("i", 0))
			drag.position = _point(data, "x", "y") * scale
			drag.relative = _point(data, "rx", "ry") * scale
			drag.screen_relative = drag.relative
			return drag
		"act":
			var action_name: StringName = StringName(str(data.get("a", "")))
			if not InputMap.has_action(action_name):
				return null
			var action: InputEventAction = InputEventAction.new()
			action.action = action_name
			action.pressed = bool(data.get("d", false))
			action.strength = float(data.get("s", 1.0 if action.pressed else 0.0))
			return action
	return null


static func _mods(event: InputEventWithModifiers) -> int:
	return (
		int(event.shift_pressed)
		| int(event.ctrl_pressed) << 1
		| int(event.alt_pressed) << 2
		| int(event.meta_pressed) << 3
	)


static func _set_mods(event: InputEventWithModifiers, bits: int) -> void:
	event.shift_pressed = bits & 1 != 0
	event.ctrl_pressed = bits & 2 != 0
	event.alt_pressed = bits & 4 != 0
	event.meta_pressed = bits & 8 != 0


static func _point(data: Dictionary, x_key: String, y_key: String) -> Vector2:
	return Vector2(float(data.get(x_key, 0.0)), float(data.get(y_key, 0.0)))
