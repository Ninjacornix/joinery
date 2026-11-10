extends RefCounted
## The core settings pages. Each builder returns the page content; `Menus`
## wraps it in a titled panel with a Back button. Every control writes to
## `Settings` as soon as it changes.


static func video() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.add_child(_toggle_row("Full screen", "video", "fullscreen"))
	column.add_child(_toggle_row("Vertical sync", "video", "vsync"))
	return column


static func audio() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.add_child(_slider_row("Master volume", "audio", "master"))
	column.add_child(_slider_row("Music volume", "audio", "music"))
	column.add_child(_slider_row("Effects volume", "audio", "sfx"))
	return column


static func accessibility() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.add_child(_slider_row("Screen shake", "access", "screen_shake"))
	column.add_child(_toggle_row("Reduced motion", "access", "reduced_motion"))
	return column


static func _slider_row(text: String, section: String, key: String) -> Control:
	var control: HSlider = UiStyle.slider(float(Settings.get_value(section, key)))
	control.name = "%s_%s" % [section, key]
	control.value_changed.connect(
		func(value: float) -> void: Settings.set_value(section, key, value)
	)
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "%s_%s_row" % [section, key]
	return line


static func _toggle_row(text: String, section: String, key: String) -> Control:
	var control: CheckButton = UiStyle.toggle(bool(Settings.get_value(section, key)))
	control.name = "%s_%s" % [section, key]
	control.toggled.connect(func(on: bool) -> void: Settings.set_value(section, key, on))
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "%s_%s_row" % [section, key]
	return line
