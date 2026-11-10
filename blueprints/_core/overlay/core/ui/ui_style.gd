class_name UiStyle
extends RefCounted
## The menus' look, built in code: flat panels, a clear focus ring and text
## that stays readable. Sizes are given at 1080p and scaled to the project's
## viewport height, so `TEXT_MIN` matches the art bible's `min_text_px`.

const TEXT_MIN := 18
const TEXT_BODY := 28
const TEXT_TITLE := 48
const DESIGN_HEIGHT := 1080.0

const BACKDROP := Color("#15171e")
const DIM := Color(0.04, 0.05, 0.07, 0.72)
const PANEL := Color("#1f2330")
const PANEL_EDGE := Color("#3a4157")
const BUTTON := Color("#2c3244")
const BUTTON_HOVER := Color("#3a4259")
const BUTTON_PRESSED := Color("#4a5373")
const TEXT := Color("#eef1f6")
const TEXT_DIM := Color("#b9c1d0")
const TEXT_DISABLED := Color("#7d8597")
const ACCENT := Color("#f2b632")


static func viewport_height() -> float:
	return float(ProjectSettings.get_setting("display/window/size/viewport_height", 648))


## A size given at 1080p, in this project's viewport pixels (rounded up).
static func px(size_at_1080: float) -> int:
	return maxi(1, ceili(size_at_1080 * viewport_height() / DESIGN_HEIGHT))


## The smallest font size any menu text may use, in viewport pixels.
static func min_text_px() -> int:
	return px(TEXT_MIN)


static func build_theme() -> Theme:
	var theme: Theme = Theme.new()
	theme.default_font_size = px(TEXT_BODY)
	var radius: int = px(10)
	var pad: int = px(14)

	theme.set_stylebox(&"panel", &"PanelContainer", box(PANEL, radius, px(28), PANEL_EDGE, px(2)))
	theme.set_stylebox(&"panel", &"Panel", box(PANEL, radius, px(28), PANEL_EDGE, px(2)))

	var focus: StyleBoxFlat = box(Color(0, 0, 0, 0), radius, pad, ACCENT, px(4))
	focus.draw_center = false
	focus.expand_margin_left = px(3)
	focus.expand_margin_right = px(3)
	focus.expand_margin_top = px(3)
	focus.expand_margin_bottom = px(3)

	for type: StringName in [&"Button", &"CheckButton", &"OptionButton", &"MenuButton"]:
		theme.set_stylebox(&"normal", type, box(BUTTON, radius, pad))
		theme.set_stylebox(&"hover", type, box(BUTTON_HOVER, radius, pad))
		theme.set_stylebox(&"pressed", type, box(BUTTON_PRESSED, radius, pad))
		theme.set_stylebox(&"hover_pressed", type, box(BUTTON_PRESSED, radius, pad))
		theme.set_stylebox(&"disabled", type, box(PANEL, radius, pad, PANEL_EDGE, px(2)))
		theme.set_stylebox(&"focus", type, focus)
		theme.set_color(&"font_color", type, TEXT)
		theme.set_color(&"font_hover_color", type, TEXT)
		theme.set_color(&"font_pressed_color", type, TEXT)
		theme.set_color(&"font_hover_pressed_color", type, TEXT)
		theme.set_color(&"font_focus_color", type, TEXT)
		theme.set_color(&"font_disabled_color", type, TEXT_DISABLED)

	for type: StringName in [&"HSlider", &"LineEdit", &"SpinBox"]:
		theme.set_stylebox(&"focus", type, focus)
	theme.set_stylebox(&"slider", &"HSlider", box(BUTTON, px(4), px(4)))
	theme.set_stylebox(&"grabber_area", &"HSlider", box(ACCENT, px(4), px(4)))
	theme.set_stylebox(&"grabber_area_highlight", &"HSlider", box(ACCENT, px(4), px(4)))
	theme.set_stylebox(&"normal", &"LineEdit", box(BUTTON, radius, pad))
	theme.set_color(&"font_color", &"LineEdit", TEXT)

	theme.set_color(&"font_color", &"Label", TEXT)
	theme.set_type_variation(&"TitleLabel", &"Label")
	theme.set_font_size(&"font_size", &"TitleLabel", px(TEXT_TITLE))
	theme.set_color(&"font_color", &"TitleLabel", TEXT)
	theme.set_type_variation(&"HintLabel", &"Label")
	theme.set_font_size(&"font_size", &"HintLabel", px(TEXT_MIN + 4))
	theme.set_color(&"font_color", &"HintLabel", TEXT_DIM)

	theme.set_constant(&"separation", &"VBoxContainer", px(14))
	theme.set_constant(&"separation", &"HBoxContainer", px(20))
	return theme


static func box(
	color: Color, radius: int, padding: int, border: Color = Color(0, 0, 0, 0), width: int = 0
) -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = color
	style.set_corner_radius_all(radius)
	style.set_content_margin_all(padding)
	if width > 0:
		style.border_color = border
		style.set_border_width_all(width)
	return style


## A page title. `text` is an English msgid; the label translates it.
static func title(text: String) -> Label:
	var label: Label = Label.new()
	label.theme_type_variation = &"TitleLabel"
	label.text = text
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	return label


static func hint(text: String) -> Label:
	var label: Label = Label.new()
	label.theme_type_variation = &"HintLabel"
	label.text = text
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return label


static func button(text: String) -> Button:
	var item: Button = Button.new()
	item.text = text
	item.focus_mode = Control.FOCUS_ALL
	item.custom_minimum_size = Vector2(px(460), px(64))
	return item


static func slider(
	value: float, low: float = 0.0, high: float = 1.0, step: float = 0.05
) -> HSlider:
	var item: HSlider = HSlider.new()
	item.min_value = low
	item.max_value = high
	item.step = step
	item.value = value
	item.focus_mode = Control.FOCUS_ALL
	item.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	item.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	item.custom_minimum_size = Vector2(px(320), px(40))
	return item


static func toggle(on: bool) -> CheckButton:
	var item: CheckButton = CheckButton.new()
	item.button_pressed = on
	item.focus_mode = Control.FOCUS_ALL
	item.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return item


## A labelled row: "<text>   <control>".
static func row(text: String, control: Control) -> HBoxContainer:
	var line: HBoxContainer = HBoxContainer.new()
	var label: Label = Label.new()
	label.text = text
	label.custom_minimum_size = Vector2(px(300), 0)
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(label)
	line.add_child(control)
	return line


## A full-screen page: dimmed backdrop, centred panel with a title, `content`,
## and a Back button when `back` is valid.
static func page(heading: String, content: Control, back: Callable = Callable()) -> Control:
	var screen: Control = Control.new()
	screen.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	screen.mouse_filter = Control.MOUSE_FILTER_STOP
	var shade: ColorRect = ColorRect.new()
	shade.color = DIM
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	screen.add_child(shade)
	var center: CenterContainer = CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	screen.add_child(center)
	var panel: PanelContainer = PanelContainer.new()
	panel.name = "Panel"
	center.add_child(panel)
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "Column"
	panel.add_child(column)
	if not heading.is_empty():
		column.add_child(title(heading))
	if content != null:
		column.add_child(content)
	if back.is_valid():
		var back_button: Button = button("Back")
		back_button.name = "Back"
		back_button.pressed.connect(back)
		column.add_child(back_button)
	return screen
