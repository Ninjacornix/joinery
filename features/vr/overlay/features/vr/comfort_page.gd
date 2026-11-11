extends RefCounted
## The "VR comfort" settings page. Every control writes to `Settings`
## (section `vr`) as soon as it changes, so the choice is kept between
## sessions and reaches the rig at once.

const Comfort := preload("res://features/vr/comfort.gd")


static func build() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "VrComfort"
	var status: Label = UiStyle.hint(_status_text())
	status.name = "Status"
	column.add_child(status)
	column.add_child(_toggle("Smooth movement", "smooth_move"))
	column.add_child(
		_slider("Movement speed", "move_speed", Comfort.SPEED_MIN, Comfort.SPEED_MAX, 0.25)
	)
	column.add_child(_snap_choice())
	column.add_child(_toggle("Teleport", "teleport"))
	column.add_child(_slider("Comfort vignette", "vignette", 0.0, 1.0, 0.1))
	column.add_child(_toggle("Seated play", "seated"))
	column.add_child(_toggle("Left-handed", "left_handed"))
	return column


static func _status_text() -> String:
	if VR.is_active():
		return "Headset in use."
	return "No headset found: these settings apply when you play in VR."


static func _value(key: String) -> Variant:
	return VR.setting(key)


static func _save(key: String, value: Variant) -> void:
	VR.set_setting(key, value)


static func _toggle(text: String, key: String) -> Control:
	var control: CheckButton = UiStyle.toggle(bool(_value(key)))
	control.name = "vr_%s" % key
	control.toggled.connect(func(on: bool) -> void: _save(key, on))
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "vr_%s_row" % key
	return line


static func _slider(text: String, key: String, low: float, high: float, step: float) -> Control:
	var control: HSlider = UiStyle.slider(float(_value(key)), low, high, step)
	control.name = "vr_%s" % key
	control.value_changed.connect(func(value: float) -> void: _save(key, value))
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "vr_%s_row" % key
	return line


static func _snap_choice() -> Control:
	var control: OptionButton = OptionButton.new()
	control.name = "vr_snap_turn"
	control.focus_mode = Control.FOCUS_ALL
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var current: int = int(_value("snap_turn"))
	for angle: int in Comfort.SNAP_ANGLES:
		control.add_item(String.num_int64(angle) + "°", angle)
		if angle == current:
			control.select(control.item_count - 1)
	if control.selected < 0:
		control.select(Comfort.SNAP_ANGLES.find(Comfort.SNAP_DEFAULT))
	control.item_selected.connect(
		func(index: int) -> void: _save("snap_turn", control.get_item_id(index))
	)
	var line: HBoxContainer = UiStyle.row("Snap turn", control)
	line.name = "vr_snap_turn_row"
	return line
