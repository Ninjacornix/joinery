extends VBoxContainer
## The "Accessibility" settings page: every option the Accessibility autoload applies,
## the core's screen shake and reduced motion, and a way to the Controls page where keys
## and buttons are remapped (when controller support is in the game). Each control saves
## at once and carries the name a screen reader says.

const SECTION := "access"
const FILTER_NAMES := ["Off", "Protanopia", "Deuteranopia", "Tritanopia"]

var _scroll: ScrollContainer
var _rows: VBoxContainer


func _init() -> void:
	name = "AccessibilitySettings"


func _ready() -> void:
	_scroll = ScrollContainer.new()
	_scroll.name = "Scroll"
	_scroll.follow_focus = true
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.custom_minimum_size = Vector2(UiStyle.px(900), UiStyle.px(560))
	add_child(_scroll)
	_rows = VBoxContainer.new()
	_rows.name = "Rows"
	_rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_rows)
	_slider("Text size", "text_scale", 0.8, 2.0, 0.1)
	_toggle("High contrast", "high_contrast")
	_filter()
	_slider("Screen shake", "screen_shake", 0.0, 1.0, 0.05)
	_toggle("Reduced motion", "reduced_motion")
	_toggle("Reduce flashing", "reduce_flashes")
	_toggle("Subtitles", "subtitles")
	_slider("Subtitle size", "subtitle_size", 0.75, 2.0, 0.25)
	if not Accessibility.hold_actions().is_empty():
		_toggle("Toggle instead of hold", "toggle_actions")
	_slider("Game speed", "game_speed", 0.5, 1.0, 0.1)
	var controls := _controls_page()
	if not controls.is_empty():
		var remap: Button = UiStyle.button("Remap controls")
		remap.name = "access_remap"
		remap.pressed.connect(func() -> void: Menus.call(&"_open_settings_page", controls))
		_rows.add_child(remap)


func _value(key: String) -> Variant:
	return (
		Accessibility.value(key)
		if Accessibility.DEFAULTS.has(key)
		else Settings.get_value(SECTION, key)
	)


func _slider(text: String, key: String, low: float, high: float, step: float) -> void:
	var control: HSlider = UiStyle.slider(float(_value(key)), low, high, step)
	control.name = "access_%s" % key
	control.accessibility_name = tr(text)
	control.value_changed.connect(
		func(value: float) -> void: Settings.set_value(SECTION, key, value)
	)
	Settings.changed.connect(
		func(section: String, changed: String) -> void:
			if section == SECTION and changed == key and is_instance_valid(control):
				control.set_value_no_signal(float(_value(key)))
	)
	_row(text, control)


func _toggle(text: String, key: String) -> void:
	var control: CheckButton = UiStyle.toggle(bool(_value(key)))
	control.name = "access_%s" % key
	control.accessibility_name = tr(text)
	control.toggled.connect(func(on: bool) -> void: Settings.set_value(SECTION, key, on))
	_row(text, control)


func _filter() -> void:
	var choice := OptionButton.new()
	choice.name = "access_colour_filter"
	choice.accessibility_name = tr("Colour-blind filter")
	choice.focus_mode = Control.FOCUS_ALL
	choice.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	choice.custom_minimum_size = Vector2(UiStyle.px(320), UiStyle.px(64))
	var current: int = maxi(0, Accessibility.FILTERS.find(str(_value("colour_filter"))))
	for index: int in FILTER_NAMES.size():
		choice.add_item(FILTER_NAMES[index], index)
	choice.select(current)
	choice.item_selected.connect(
		func(index: int) -> void:
			Settings.set_value(SECTION, "colour_filter", Accessibility.FILTERS[index])
	)
	_row("Colour-blind filter", choice)


func _row(text: String, control: Control) -> void:
	var line: HBoxContainer = UiStyle.row(text, control)
	line.name = "%s_row" % control.name
	_rows.add_child(line)


## The settings page where controls are remapped, if the game has one.
static func _controls_page() -> StringName:
	for page: Dictionary in Menus.settings_pages():
		if StringName(page["id"]) == &"controls":
			return &"controls"
	return &""
