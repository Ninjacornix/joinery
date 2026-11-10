extends Node
## Look settings for the first-person game: mouse sensitivity, field of view
## and invert look Y, stored through the core Settings autoload. Adds a
## "Camera" settings page and keeps the mouse captured only while playing.

signal look_changed

const SECTION_LOOK := "look"
const KEY_SENSITIVITY := "sensitivity"
const KEY_FOV := "fov"
const SECTION_INPUT := "input"
const KEY_INVERT_Y := "invert_look_y"

const SENSITIVITY_DEFAULT := 1.0
const SENSITIVITY_MIN := 0.2
const SENSITIVITY_MAX := 3.0
const FOV_DEFAULT := 75.0
const FOV_MIN := 60.0
const FOV_MAX := 100.0
## Radians of turn per pixel of mouse motion at sensitivity 1.
const MOUSE_RADIANS_PER_PIXEL := 0.0025
## Radians per second at full stick or key deflection at sensitivity 1.
const STICK_RADIANS_PER_SECOND := 3.0

var _playing: bool = false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	Settings.changed.connect(_on_settings_changed)
	GameEvents.level_started.connect(_on_level_started)
	GameEvents.level_completed.connect(_on_level_completed)
	GameEvents.paused_changed.connect(_on_paused_changed)
	GameEvents.menu_opened.connect(_on_menu_opened)
	Menus.add_settings_page(&"camera", "Camera", _build_page, 45)


func sensitivity() -> float:
	var value: float = float(Settings.get_value(SECTION_LOOK, KEY_SENSITIVITY, SENSITIVITY_DEFAULT))
	return clampf(value, SENSITIVITY_MIN, SENSITIVITY_MAX)


func fov() -> float:
	var value: float = float(Settings.get_value(SECTION_LOOK, KEY_FOV, FOV_DEFAULT))
	return clampf(value, FOV_MIN, FOV_MAX)


func invert_y() -> bool:
	return bool(Settings.get_value(SECTION_INPUT, KEY_INVERT_Y, false))


## Yaw and pitch change, in radians, for a mouse motion in pixels.
func mouse_delta(relative: Vector2) -> Vector2:
	var scale: float = MOUSE_RADIANS_PER_PIXEL * sensitivity()
	var pitch_sign: float = 1.0 if invert_y() else -1.0
	return Vector2(-relative.x * scale, relative.y * scale * pitch_sign)


## Yaw and pitch change, in radians, for look actions held over `delta` seconds.
func stick_delta(look: Vector2, delta: float) -> Vector2:
	var scale: float = STICK_RADIANS_PER_SECOND * sensitivity() * delta
	var pitch_sign: float = 1.0 if invert_y() else -1.0
	return Vector2(-look.x * scale, look.y * scale * pitch_sign)


func is_playing() -> bool:
	return _playing


## Marks the level as being played again (for example a save loaded over the
## win screen) and takes the mouse back once no menu is in the way.
func set_playing(on: bool) -> void:
	_playing = on
	_capture(on and not get_tree().paused and not Menus.is_open())


## True when mouse motion should turn the view: only while the mouse is
## captured, so moving a free cursor (after the win, or after switching
## windows) does not spin the camera. A headless run has no cursor to
## capture, so motion always counts there (automated play tests).
func mouse_look_enabled() -> bool:
	return Input.mouse_mode == Input.MOUSE_MODE_CAPTURED or DisplayServer.get_name() == "headless"


func _capture(on: bool) -> void:
	if DisplayServer.get_name() == "headless":
		return
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED if on else Input.MOUSE_MODE_VISIBLE


func _on_level_started(_level: Node) -> void:
	_playing = true
	_capture(true)


func _on_level_completed(_result: Dictionary) -> void:
	_playing = false
	_capture(false)


func _on_paused_changed(paused: bool) -> void:
	_capture(not paused and _playing)


func _on_menu_opened(_menu: Control) -> void:
	_playing = Game.current_level() != null
	_capture(false)


func _unhandled_input(event: InputEvent) -> void:
	# A click in the game window takes the mouse back after it was let go
	# (for example after switching windows).
	var click: InputEventMouseButton = event as InputEventMouseButton
	if click == null or not click.pressed or not _playing or get_tree().paused:
		return
	if Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
		_capture(true)


func _on_settings_changed(section: String, _key: String) -> void:
	if section == SECTION_LOOK or section == SECTION_INPUT:
		look_changed.emit()


func _build_page() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "CameraSettings"
	column.add_child(_slider_row("Mouse sensitivity", SECTION_LOOK, KEY_SENSITIVITY,
		sensitivity(), SENSITIVITY_MIN, SENSITIVITY_MAX, 0.1))
	column.add_child(_slider_row("Field of view", SECTION_LOOK, KEY_FOV,
		fov(), FOV_MIN, FOV_MAX, 1.0))
	var invert: CheckButton = UiStyle.toggle(invert_y())
	invert.name = "%s_%s" % [SECTION_INPUT, KEY_INVERT_Y]
	invert.toggled.connect(
		func(on: bool) -> void: Settings.set_value(SECTION_INPUT, KEY_INVERT_Y, on)
	)
	var line: HBoxContainer = UiStyle.row("Invert look up and down", invert)
	line.name = "%s_row" % invert.name
	column.add_child(line)
	return column


func _slider_row(
	text: String, section: String, key: String,
	value: float, low: float, high: float, step: float
) -> Control:
	var control: HSlider = UiStyle.slider(value, low, high, step)
	control.name = "%s_%s" % [section, key]
	control.value_changed.connect(
		func(changed_value: float) -> void: Settings.set_value(section, key, changed_value)
	)
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "%s_row" % control.name
	return line
