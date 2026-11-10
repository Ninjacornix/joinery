extends RefCounted
## The checks behind controller_test.gd. Loaded once the game is running, so
## this file can use the autoloads. Works on every blueprint: checks that need
## a player, sticks or several actions run only where the game has them.

const Bindings := preload("res://features/controller/bindings.gd")
const DEVICE := 0
## A pad button no blueprint binds, used to switch to the pad without side
## effects.
const SPARE_BUTTON := JOY_BUTTON_MISC1

var tree: SceneTree
var h: PlayHarness


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run(phase: String) -> void:
	await h.boot()
	h.expect(Game.current_level() != null, "the level starts")
	if phase == "verify":
		_check_saved_bindings()
		_check_reset()
		return
	_check_pad_bindings()
	_check_menu_pad_events()
	await _check_device_switch()
	await _check_prompts()
	await _check_stick_moves_player()
	await _check_rumble()
	await _check_disconnect_pauses()
	await _check_menu_navigation()
	await _check_rebinding()
	await _check_settings_rows()
	await _check_back_out()


# --- Checks -----------------------------------------------------------------


func _check_pad_bindings() -> void:
	var actions: Array[StringName] = Controller.actions()
	h.expect(&"pause" in actions, "the core pause action is listed")
	for action: StringName in actions:
		h.expect(
			not Bindings.events_of(action, true).is_empty(),
			"%s has a pad binding" % action
		)
		var seen: Dictionary = {}
		for event: InputEvent in InputMap.action_get_events(action):
			var id: String = Bindings.identity(event)
			h.expect(not seen.has(id), "%s binds %s once" % [action, id])
			seen[id] = true
	h.expect(_has_pad_button(&"pause", JOY_BUTTON_START), "Start pauses")
	if InputMap.has_action(&"jump"):
		h.expect(_has_pad_button(&"jump", JOY_BUTTON_A), "A jumps")
	for action: StringName in [&"move_left", &"move_right", &"look_left"]:
		if InputMap.has_action(action):
			h.expect_near(
				InputMap.action_get_deadzone(action),
				0.25,
				0.001,
				"%s stick deadzone" % action
			)


func _check_menu_pad_events() -> void:
	h.expect(_has_pad_button(&"ui_accept", JOY_BUTTON_A), "A accepts in menus")
	h.expect(_has_pad_button(&"ui_cancel", JOY_BUTTON_B), "B goes back in menus")


func _check_device_switch() -> void:
	var record: Dictionary = h.watch(Controller.device_changed)
	await _pad_tap(SPARE_BUTTON)
	h.expect(Controller.using_pad, "a pad button switches to the pad")
	h.expect(Controller.active_device == DEVICE, "the pad that pressed is the active one")
	h.expect(Controller.cursor_hidden, "the mouse cursor hides while the pad is used")
	var args: Array = await h.wait_watched(record, 5)
	h.expect(args == [true], "device_changed(true) fires")

	record = h.watch(Controller.device_changed)
	await _key_tap(KEY_F9)
	h.expect(not Controller.using_pad, "a key switches back to keyboard and mouse")
	h.expect(not Controller.cursor_hidden, "the cursor shows again")
	args = await h.wait_watched(record, 5)
	h.expect(args == [false], "device_changed(false) fires")

	await _axis(JOY_AXIS_LEFT_X, 0.2)
	await _axis(JOY_AXIS_LEFT_X, 0.0)
	h.expect(not Controller.using_pad, "a resting stick does not switch to the pad")
	await _pad_tap(SPARE_BUTTON)
	h.mouse_motion(Vector2(40, 0))
	await h.physics_frames(1)
	h.expect(not Controller.using_pad, "moving the mouse switches back")
	await _pad_tap(SPARE_BUTTON)
	h.mouse_motion(Vector2(1, 0))
	await h.physics_frames(1)
	h.expect(Controller.using_pad, "a tiny mouse jitter keeps the pad")


func _check_prompts() -> void:
	await _key_tap(KEY_F9)
	h.expect(Controller.prompt(&"pause") == "Escape", "keyboard prompt for pause is Escape")
	await _pad_tap(SPARE_BUTTON)
	h.expect(Controller.prompt(&"pause") == "Start", "pad prompt for pause is Start")
	if InputMap.has_action(&"jump"):
		h.expect(Controller.prompt(&"jump") == "A", "pad prompt for jump is A")
	if InputMap.has_action(&"move_left"):
		h.expect(
			Controller.prompt(&"move_left") == "Left stick",
			"pad prompt for moving is the left stick"
		)
	if InputMap.has_action(&"look_left"):
		h.expect(
			Controller.prompt(&"look_left") == "Right stick",
			"pad prompt for looking is the right stick"
		)
	Settings.set_value("input", "pad_labels", "playstation")
	h.expect(Controller.prompt(&"pause") == "Options", "PlayStation names: Options")
	if InputMap.has_action(&"jump"):
		h.expect(Controller.prompt(&"jump") == "Cross", "PlayStation names: Cross")
	Settings.set_value("input", "pad_labels", "nintendo")
	h.expect(Controller.prompt(&"pause") == "+", "Nintendo names: +")
	if InputMap.has_action(&"jump"):
		h.expect(Controller.prompt(&"jump") == "B", "Nintendo names: B for the bottom button")
	Settings.set_value("input", "pad_labels", "auto")
	var motion: InputEventJoypadMotion = InputEventJoypadMotion.new()
	motion.axis = JOY_AXIS_LEFT_X
	motion.axis_value = -1.0
	h.expect(
		Controller.event_text(motion) == "Left stick left",
		"stick bindings name their direction"
	)
	motion.axis = JOY_AXIS_TRIGGER_RIGHT
	motion.axis_value = 1.0
	h.expect(Controller.event_text(motion) == "RT", "the right trigger is RT")
	h.expect(Controller.event_text(null) == "Not bound", "a missing binding reads Not bound")
	h.expect(Controller.prompt(&"no_such_action") != "", "unknown actions still get a name")


func _check_stick_moves_player() -> void:
	var player: Node = Game.local_player()
	if player == null or not InputMap.has_action(&"move_right"):
		return
	await _axis(JOY_AXIS_LEFT_X, 0.2)
	h.expect_near(
		Input.get_action_strength(&"move_right"),
		0.0,
		0.001,
		"the deadzone hides a resting stick"
	)
	var start: Variant = _position(player)
	await _axis(JOY_AXIS_LEFT_X, 1.0)
	h.expect_near(
		Input.get_action_strength(&"move_right"),
		1.0,
		0.001,
		"a full stick presses move_right"
	)
	await h.physics_frames(24)
	var moved: Variant = _position(player)
	await _axis(JOY_AXIS_LEFT_X, 0.0)
	h.expect(not Input.is_action_pressed(&"move_right"), "a centred stick releases move_right")
	if player is Node2D:
		h.expect(
			(moved as Vector2).x - (start as Vector2).x > 8.0,
			"the left stick moves the player right (%s -> %s)" % [start, moved]
		)
	elif player is Node3D:
		h.expect(
			(moved as Vector3).distance_to(start as Vector3) > 0.3,
			"the left stick moves the player (%s -> %s)" % [start, moved]
		)
	var forward: StringName = &"move_forward" if InputMap.has_action(&"move_forward") else &"move_up"
	if InputMap.has_action(forward):
		await _axis(JOY_AXIS_LEFT_Y, -1.0)
		h.expect(Input.is_action_pressed(forward), "stick up presses %s" % forward)
		await _axis(JOY_AXIS_LEFT_Y, 0.0)
	if InputMap.has_action(&"look_right"):
		await _axis(JOY_AXIS_RIGHT_X, 1.0)
		h.expect(Input.is_action_pressed(&"look_right"), "the right stick presses look_right")
		await _axis(JOY_AXIS_RIGHT_X, 0.0)


func _check_rumble() -> void:
	await _pad_tap(SPARE_BUTTON)
	Controller.rumble(0.3, 0.6, 0.5)
	var strength: Vector2 = Input.get_joy_vibration_strength(DEVICE)
	h.expect_near(strength.x, 0.3, 0.001, "rumble: weak motor")
	h.expect_near(strength.y, 0.6, 0.001, "rumble: strong motor")
	Settings.set_value("input", "rumble", false)
	h.expect(
		Input.get_joy_vibration_strength(DEVICE) == Vector2.ZERO,
		"turning rumble off stops it"
	)
	Controller.rumble(1.0, 1.0, 0.5)
	h.expect(Input.get_joy_vibration_strength(DEVICE) == Vector2.ZERO, "rumble off is honoured")
	Settings.set_value("input", "rumble", true)


func _check_disconnect_pauses() -> void:
	await _pad_tap(SPARE_BUTTON)
	h.expect(not Menus.is_open(), "no menu before the pad disconnects")
	Input.joy_connection_changed.emit(DEVICE, false)
	await h.process_frames(2)
	h.expect(Menus.current_menu() == &"pause", "losing the pad in use opens the pause menu")
	if Game.session().can_pause():
		h.expect(tree.paused, "losing the pad in use pauses the game")
	h.expect(Controller.active_device == -1, "no active pad after it disconnects")
	Input.joy_connection_changed.emit(DEVICE, true)
	await _pad_tap(JOY_BUTTON_B)
	await h.process_frames(2)
	h.expect(not Menus.is_open(), "B closes the pause menu")
	h.expect(not tree.paused, "the game runs again")


func _check_menu_navigation() -> void:
	await _pad_tap(JOY_BUTTON_START)
	await h.process_frames(2)
	h.expect(Menus.current_menu() == &"pause", "Start opens the pause menu")
	h.expect(_focused_name() == "resume", "Resume has focus (got %s)" % _focused_name())
	tree.root.gui_release_focus()
	await _pad_tap(JOY_BUTTON_DPAD_DOWN)
	var page: Control = Menus.current_page()
	var focus_owner: Control = h.focused()
	h.expect(
		focus_owner != null and page != null and page.is_ancestor_of(focus_owner),
		"a pad press gives a menu without focus its focus back"
	)
	h.expect(await _pad_focus("settings"), "the d-pad reaches Settings")
	await _pad_tap(JOY_BUTTON_A)
	await h.process_frames(2)
	h.expect(
		Menus.current_menu() == &"pause"
		and Menus.current_page().get_meta(&"menu", &"") == &"settings",
		"A opens Settings"
	)
	h.expect(await _pad_focus("controls"), "the d-pad reaches Controls")
	await _pad_tap(JOY_BUTTON_A)
	await h.process_frames(2)
	var controls: Node = _controls_page()
	h.expect(controls != null, "A opens the Controls page")
	if controls == null:
		return
	var first: StringName = Controller.actions()[0]
	h.expect(
		_focused_name() == "%s_keys" % first,
		"the first binding has focus (got %s)" % _focused_name()
	)
	h.expect_min_text(Menus.current_page(), "Controls page")
	for action: StringName in Controller.actions():
		var keys: Button = controls.find_child("%s_keys" % action, true, false) as Button
		var pad: Button = controls.find_child("%s_pad" % action, true, false) as Button
		h.expect(keys != null and pad != null, "%s has a row" % action)
		if pad != null:
			h.expect(
				pad.text == Controller.event_text(Controller.binding(action, true)),
				"%s pad text" % action
			)


func _check_rebinding() -> void:
	var controls: Node = _controls_page()
	if controls == null:
		return
	var action: StringName = _rebind_action()
	var before: InputEvent = Controller.binding(action, true)
	# Pad rebinding, started and finished with the pad.
	h.expect(await _pad_focus("%s_keys" % action), "the d-pad reaches %s's row" % action)
	await _pad_tap(JOY_BUTTON_DPAD_RIGHT)
	h.expect(
		_focused_name() == "%s_pad" % action,
		"d-pad right reaches %s's pad binding (got %s)" % [action, _focused_name()]
	)
	if _focused_name() != "%s_pad" % action:
		return
	await _pad_tap(JOY_BUTTON_A)
	h.expect(bool(controls.call(&"is_capturing")), "A on a binding waits for a new button")
	await _pad_tap(JOY_BUTTON_Y)
	h.expect(not bool(controls.call(&"is_capturing")), "a pad button ends the wait")
	h.expect(
		Bindings.identity(Controller.binding(action, true)) == "pad_button:3",
		"%s is now on Y" % action
	)
	h.expect(
		not _has_identity(action, Bindings.identity(before)),
		"%s's old pad binding is gone" % action
	)
	var pad_button: Button = controls.find_child("%s_pad" % action, true, false) as Button
	h.expect(pad_button.text == "Y", "the row shows Y (got %s)" % pad_button.text)
	h.expect(_saved().has(String(action)), "the new binding is saved")

	# Escape cancels a keyboard rebinding and keeps the page open.
	var key_before: String = Bindings.identity(Controller.binding(action, false))
	controls.call(&"start_capture", action, false)
	await h.process_frames(2)
	await _key_tap(KEY_ESCAPE)
	h.expect(not bool(controls.call(&"is_capturing")), "Escape cancels")
	h.expect(
		Bindings.identity(Controller.binding(action, false)) == key_before,
		"Escape changes nothing"
	)
	h.expect(_controls_page() != null, "Escape does not close the Controls page")

	# A key another action uses is swapped.
	var pair: Array[StringName] = _swap_pair()
	if pair.size() == 2:
		var mine: InputEvent = Controller.binding(pair[0], false)
		var theirs: InputEvent = Controller.binding(pair[1], false)
		controls.call(&"start_capture", pair[0], false)
		await h.process_frames(2)
		await _key_tap((theirs as InputEventKey).physical_keycode)
		h.expect(
			Bindings.identity(Controller.binding(pair[0], false)) == Bindings.identity(theirs),
			"%s took %s's key" % pair
		)
		h.expect(
			Bindings.identity(Controller.binding(pair[1], false)) == Bindings.identity(mine),
			"%s got %s's old key" % [pair[1], pair[0]]
		)
		var status: Label = controls.find_child("Status", true, false) as Label
		h.expect(status != null and not status.text.is_empty(), "the page says what was swapped")

	# Waiting too long gives up.
	controls.call(&"start_capture", action, true)
	await h.process_frames(int(6.0 * 60.0) + 10)
	h.expect(not bool(controls.call(&"is_capturing")), "a rebinding gives up after a few seconds")
	h.expect(
		Bindings.identity(Controller.binding(action, true)) == "pad_button:3",
		"giving up changes nothing"
	)


func _check_settings_rows() -> void:
	var controls: Node = _controls_page()
	if controls == null:
		return
	var slider: HSlider = controls.find_child("input_deadzone", true, false) as HSlider
	var stick_action: StringName = _stick_action()
	h.expect(
		(slider != null) == (stick_action != &""),
		"a deadzone slider exactly when sticks are bound"
	)
	if slider != null:
		slider.value = 0.4
		h.expect_near(
			InputMap.action_get_deadzone(stick_action),
			0.4,
			0.001,
			"the slider sets the deadzone"
		)
		h.expect_near(
			float(Settings.get_value("input", "deadzone")),
			0.4,
			0.001,
			"the deadzone is saved"
		)
	var invert: CheckButton = controls.find_child("input_invert_look_y", true, false) as CheckButton
	h.expect(
		(invert != null) == InputMap.has_action(&"look_up"),
		"invert look only with look actions"
	)
	if invert != null:
		invert.button_pressed = true
		h.expect(bool(Settings.get_value("input", "invert_look_y")), "invert look is saved")
		invert.button_pressed = false
	var rumble: CheckButton = controls.find_child("input_rumble", true, false) as CheckButton
	h.expect(rumble != null and rumble.button_pressed, "rumble is on by default")
	if rumble != null:
		rumble.button_pressed = false
		h.expect(not Controller.rumble_enabled(), "the toggle turns rumble off")
		rumble.button_pressed = true
	var labels: Button = controls.find_child("input_pad_labels", true, false) as Button
	h.expect(labels != null and labels.text == "Automatic", "button names start automatic")
	if labels != null:
		labels.pressed.emit()
		h.expect(
			str(Settings.get_value("input", "pad_labels")) == "xbox",
			"the button cycles the names"
		)
		h.expect(labels.text == "Xbox", "and shows the choice")
		Settings.set_value("input", "pad_labels", "auto")
	await _check_page_focus_paths(controls)


## The d-pad walks the binding grid and the settings below it in order.
func _check_page_focus_paths(controls: Node) -> void:
	var actions: Array[StringName] = Controller.actions()
	var first: Button = controls.find_child("%s_pad" % actions[0], true, false) as Button
	first.grab_focus()
	await h.process_frames(1)
	await _pad_tap(JOY_BUTTON_DPAD_LEFT)
	h.expect(_focused_name() == "%s_keys" % actions[0], "d-pad left goes to the keyboard column")
	await _pad_tap(JOY_BUTTON_DPAD_LEFT)
	h.expect(_focused_name() == "%s_keys" % actions[0], "d-pad left stops at the first column")
	for i: int in range(1, actions.size()):
		await _pad_tap(JOY_BUTTON_DPAD_DOWN)
		h.expect(
			_focused_name() == "%s_keys" % actions[i],
			"d-pad down reaches %s (got %s)" % [actions[i], _focused_name()]
		)
	var expected: Array[String] = []
	var below: Array[String] = [
		"input_deadzone", "input_invert_look_y", "input_rumble", "input_pad_labels",
		"ResetControls", "Back",
	]
	for name: String in below:
		if controls.find_child(name, true, false) != null or name == "Back":
			expected.append(name)
	for name: String in expected:
		await _pad_tap(JOY_BUTTON_DPAD_DOWN)
		h.expect(
			_focused_name() == name,
			"d-pad down reaches %s (got %s)" % [name, _focused_name()]
		)
	for i: int in range(expected.size() - 2, -1, -1):
		await _pad_tap(JOY_BUTTON_DPAD_UP)
		h.expect(
			_focused_name() == expected[i],
			"d-pad up reaches %s (got %s)" % [expected[i], _focused_name()]
		)
	await _pad_tap(JOY_BUTTON_DPAD_UP)
	h.expect(_focused_name() == "%s_keys" % actions.back(), "d-pad up returns to the last binding")


func _check_back_out() -> void:
	for _i: int in 3:
		if not Menus.is_open():
			break
		await _pad_tap(JOY_BUTTON_B)
		await h.process_frames(2)
	h.expect(not Menus.is_open(), "B backs out of every menu")
	h.expect(not tree.paused, "and the game runs again")


## Second process, same settings file: the bindings came back on start.
func _check_saved_bindings() -> void:
	var action: StringName = _rebind_action()
	h.expect(
		Bindings.identity(Controller.binding(action, true)) == "pad_button:3",
		"%s is still on Y after a restart" % action
	)
	var pair: Array[StringName] = _swap_pair_saved()
	if pair.size() == 2:
		h.expect(
			_saved().has(String(pair[0])) and _saved().has(String(pair[1])),
			"the swap is saved"
		)
	var stick_action: StringName = _stick_action()
	if stick_action != &"":
		h.expect_near(
			InputMap.action_get_deadzone(stick_action),
			0.4,
			0.001,
			"the deadzone survives a restart"
		)


func _check_reset() -> void:
	Controller.reset_bindings()
	var action: StringName = _rebind_action()
	h.expect(
		not _has_identity(action, "pad_button:3") or action == &"",
		"reset puts the default back"
	)
	h.expect(_saved().is_empty(), "reset forgets saved bindings")
	var expected: Dictionary = {
		&"pause": "pad_button:6", &"jump": "pad_button:0", &"move_left": "pad_axis:0:-1",
	}
	for name: StringName in expected:
		if InputMap.has_action(name):
			h.expect(
				_has_identity(name, str(expected[name])),
				"%s has its default pad binding" % name
			)
	var stick_action: StringName = _stick_action()
	if stick_action != &"":
		h.expect_near(
			InputMap.action_get_deadzone(stick_action),
			0.25,
			0.001,
			"reset restores the deadzone"
		)


# --- Helpers ----------------------------------------------------------------


## The action whose pad binding the test changes: jump when the game has it.
func _rebind_action() -> StringName:
	return &"jump" if InputMap.has_action(&"jump") else &"pause"


## Two actions with different first keys, to test swapping, or [].
func _swap_pair() -> Array[StringName]:
	var found: Array[StringName] = []
	var seen: Dictionary = {}
	for action: StringName in Controller.actions():
		var key: InputEventKey = Controller.binding(action, false) as InputEventKey
		if key == null or key.physical_keycode == KEY_NONE or key.physical_keycode == KEY_ESCAPE:
			continue
		var id: String = Bindings.identity(key)
		if seen.has(id):
			continue
		seen[id] = true
		found.append(action)
		if found.size() == 2:
			return found
	return []


## The swapped pair as saved: actions in the saved bindings besides the pad one.
func _swap_pair_saved() -> Array[StringName]:
	var found: Array[StringName] = []
	for key: Variant in _saved().keys():
		if StringName(str(key)) != _rebind_action():
			found.append(StringName(str(key)))
	return found if found.size() == 2 else ([] as Array[StringName])


func _stick_action() -> StringName:
	for action: StringName in Controller.actions():
		for event: InputEvent in InputMap.action_get_events(action):
			var motion: InputEventJoypadMotion = event as InputEventJoypadMotion
			if motion != null and motion.axis <= JOY_AXIS_RIGHT_Y:
				return action
	return &""


func _saved() -> Dictionary:
	var saved: Variant = Settings.get_value("input", "bindings", {})
	return saved if saved is Dictionary else {}


func _controls_page() -> Node:
	var page: Control = Menus.current_page()
	if page == null:
		return null
	return page.find_child("ControlsSettings", true, false)


func _has_pad_button(action: StringName, button: JoyButton) -> bool:
	return _has_identity(action, "pad_button:%d" % button)


func _has_identity(action: StringName, id: String) -> bool:
	for event: InputEvent in InputMap.action_get_events(action):
		if Bindings.identity(event) == id:
			return true
	return false


func _focused_name() -> String:
	var control: Control = h.focused()
	return String(control.name) if control != null else ""


## Presses d-pad down until the focused control is named `target`.
func _pad_focus(target: String, limit: int = 40) -> bool:
	for _i: int in limit:
		if _focused_name() == target:
			return true
		await _pad_tap(JOY_BUTTON_DPAD_DOWN)
	return _focused_name() == target


func _position(node: Node) -> Variant:
	if node is Node2D:
		return (node as Node2D).global_position
	if node is Node3D:
		return (node as Node3D).global_position
	return null


func _send(event: InputEvent) -> void:
	Input.parse_input_event(event)
	Input.flush_buffered_events()


func _pad_button(button: JoyButton, pressed: bool) -> void:
	var event: InputEventJoypadButton = InputEventJoypadButton.new()
	event.device = DEVICE
	event.button_index = button
	event.pressed = pressed
	event.pressure = 1.0 if pressed else 0.0
	_send(event)


func _pad_tap(button: JoyButton) -> void:
	_pad_button(button, true)
	await h.process_frames(2)
	_pad_button(button, false)
	await h.process_frames(2)


func _axis(axis: JoyAxis, value: float) -> void:
	var event: InputEventJoypadMotion = InputEventJoypadMotion.new()
	event.device = DEVICE
	event.axis = axis
	event.axis_value = value
	_send(event)
	await h.physics_frames(1)


func _key_tap(code: Key) -> void:
	for pressed: bool in [true, false]:
		var event: InputEventKey = InputEventKey.new()
		event.keycode = code
		event.physical_keycode = code
		event.pressed = pressed
		_send(event)
		await h.process_frames(2)
