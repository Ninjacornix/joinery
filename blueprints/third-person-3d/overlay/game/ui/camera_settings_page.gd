extends RefCounted
## The "Camera" settings page: mouse sensitivity, field of view and inverted
## vertical look, styled like the core pages (`Menus` adds the title and the
## Back button). Values live in Settings section "camera"; the orbit camera
## listens to `Settings.changed` and applies them at once.

const SECTION := "camera"
const DEFAULT_SENSITIVITY := 1.0
const DEFAULT_FOV := 70.0


static func build() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "CameraSettings"
	var sensitivity: float = float(
		Settings.get_value(SECTION, "mouse_sensitivity", DEFAULT_SENSITIVITY)
	)
	column.add_child(
		_slider_row("Mouse sensitivity", "mouse_sensitivity", sensitivity, 0.2, 3.0, 0.1)
	)
	var fov: float = float(Settings.get_value(SECTION, "fov", DEFAULT_FOV))
	column.add_child(_slider_row("Field of view", "fov", fov, 55.0, 100.0, 1.0))
	var invert: CheckButton = UiStyle.toggle(bool(Settings.get_value(SECTION, "invert_y", false)))
	invert.name = "camera_invert_y"
	invert.toggled.connect(func(on: bool) -> void: Settings.set_value(SECTION, "invert_y", on))
	column.add_child(UiStyle.row("Invert vertical look", invert))
	return column


static func _slider_row(
	text: String, key: String, value: float, low: float, high: float, step: float
) -> Control:
	var control: HSlider = UiStyle.slider(value, low, high, step)
	control.name = "camera_%s" % key
	control.value_changed.connect(
		func(changed: float) -> void: Settings.set_value(SECTION, key, changed)
	)
	return UiStyle.row(text, control)
