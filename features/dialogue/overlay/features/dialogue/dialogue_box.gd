extends CanvasLayer
## The dialogue box: a panel along the bottom of the screen in the menus' style
## (UiStyle), with the speaker's name, the line, and the choices as buttons.
##
## Accessibility settings it follows (Settings section "access"):
##   subtitle_size   0.75-2.0: the text size, like subtitles
##   text_scale      0.8-2.0: on top of that, like the rest of the HUD
##   high_contrast   black panel, white edge and text
##   reduced_motion  each line shows at once instead of typing out
## The line is a polite live region, so screen readers read each new line.

signal advance_requested
signal choice_made(index: int)

const SECTION := "access"
const BASE_SIZE := 30
const NAME_SIZE := 26
## Characters a second while a line types out.
const TYPE_SPEED := 45.0
## Godot's AccessibilityServer.LIVE_POLITE.
const LIVE_POLITE := 1
## Meta the accessibility feature reads as a label's size before text size.
const BASE_SIZE_META := &"access_base_font_size"

var root: Control
var panel: PanelContainer
var speaker_label: Label
var text_label: Label
var choices: VBoxContainer
var hint: Label
var _typed: float = 0.0
## The mouse mode to give back when the box closes (-1: nothing to give back).
var _mouse_mode: int = -1


func _ready() -> void:
	layer = 80
	root = Control.new()
	root.name = "Root"
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.theme = UiStyle.build_theme()
	add_child(root)
	panel = PanelContainer.new()
	panel.name = "Panel"
	panel.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	panel.offset_bottom = -UiStyle.px(40)
	panel.mouse_filter = Control.MOUSE_FILTER_PASS
	root.gui_input.connect(_on_click)
	root.add_child(panel)
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "Column"
	panel.add_child(column)
	speaker_label = _label("Speaker", NAME_SIZE)
	speaker_label.add_theme_color_override(&"font_color", UiStyle.ACCENT)
	column.add_child(speaker_label)
	text_label = _label("Line", BASE_SIZE)
	text_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text_label.set(&"accessibility_live", LIVE_POLITE)
	column.add_child(text_label)
	choices = VBoxContainer.new()
	choices.name = "Choices"
	column.add_child(choices)
	hint = UiStyle.hint("Press Accept to continue")
	hint.name = "Hint"
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	column.add_child(hint)
	Settings.changed.connect(_on_settings_changed)
	_restyle()
	close()


func _process(delta: float) -> void:
	if not is_typing():
		return
	_typed += delta * TYPE_SPEED
	text_label.visible_characters = int(_typed)
	if not is_typing():
		finish_typing()


func _input(event: InputEvent) -> void:
	if not panel.visible or choices.get_child_count() > 0:
		return
	if event.is_echo():
		return
	if event.is_action_pressed(&"ui_accept") or _is_action(event, &"interact"):
		get_viewport().set_input_as_handled()
		advance_requested.emit()


func show_line(speaker: String, text: String) -> void:
	_clear_choices()
	speaker_label.text = speaker
	speaker_label.visible = not speaker.is_empty()
	text_label.text = text
	hint.visible = true
	_typed = 0.0
	text_label.visible_characters = -1 if Settings.reduced_motion() else 0
	_open()


## `options`: [{"index", "text"}], already translated.
func show_choices(options: Array[Dictionary]) -> void:
	_clear_choices()
	hint.visible = false
	finish_typing()
	var first: Button = null
	for option: Dictionary in options:
		var button: Button = UiStyle.button(str(option["text"]))
		button.name = "Choice%d" % int(option["index"])
		button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.pressed.connect(choice_made.emit.bind(int(option["index"])))
		choices.add_child(button)
		_size_label(button, BASE_SIZE)
		if first == null:
			first = button
	_open()
	if first != null:
		first.grab_focus.call_deferred()


func close() -> void:
	_clear_choices()
	panel.hide()
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if _mouse_mode >= 0:
		Input.mouse_mode = _mouse_mode as Input.MouseMode
		_mouse_mode = -1


## While open the box takes every click (a click anywhere advances a line) and
## shows the mouse, so a game that captures it still lets the player choose.
func _open() -> void:
	panel.show()
	root.mouse_filter = Control.MOUSE_FILTER_STOP
	if _mouse_mode < 0:
		_mouse_mode = Input.mouse_mode
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func is_typing() -> bool:
	return (
		panel.visible
		and text_label.visible_characters >= 0
		and text_label.visible_characters < text_label.get_total_character_count()
	)


func finish_typing() -> void:
	text_label.visible_characters = -1


## The buttons of the choices shown now.
func choice_buttons() -> Array[Button]:
	var found: Array[Button] = []
	for child: Node in choices.get_children():
		if child is Button and not child.is_queued_for_deletion():
			found.append(child as Button)
	return found


func _clear_choices() -> void:
	for child: Node in choices.get_children():
		choices.remove_child(child)
		child.queue_free()


func _label(label_name: String, size: int) -> Label:
	var label: Label = Label.new()
	label.name = label_name
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	_size_label(label, size)
	return label


## Text size: the subtitle size, then the text size on top (stored the way the
## accessibility feature stores a HUD label's size, so it never scales it twice).
func _size_label(control: Control, size: int) -> void:
	var subtitle: float = clampf(
		float(Settings.get_value(SECTION, "subtitle_size", 1.0)), 0.75, 2.0
	)
	var scale: float = clampf(float(Settings.get_value(SECTION, "text_scale", 1.0)), 0.8, 2.0)
	var base: int = UiStyle.px(size * subtitle)
	control.set_meta(BASE_SIZE_META, base)
	control.add_theme_font_size_override(&"font_size", roundi(base * scale))


func _restyle() -> void:
	var contrast: bool = bool(Settings.get_value(SECTION, "high_contrast", false))
	var edge: Color = Color.WHITE if contrast else UiStyle.PANEL_EDGE
	var fill: Color = Color.BLACK if contrast else UiStyle.PANEL
	var style: StyleBoxFlat = UiStyle.box(
		fill, UiStyle.px(10), UiStyle.px(24), edge, UiStyle.px(3 if contrast else 2)
	)
	panel.add_theme_stylebox_override(&"panel", style)
	panel.custom_minimum_size = Vector2(UiStyle.px(1100), 0)
	text_label.add_theme_color_override(&"font_color", Color.WHITE if contrast else UiStyle.TEXT)
	speaker_label.add_theme_color_override(
		&"font_color", Color.WHITE if contrast else UiStyle.ACCENT
	)
	_size_label(speaker_label, NAME_SIZE)
	_size_label(text_label, BASE_SIZE)
	for button: Button in choice_buttons():
		_size_label(button, BASE_SIZE)


func _on_settings_changed(section: String, _key: String) -> void:
	if section == SECTION:
		_restyle()
		if Settings.reduced_motion():
			finish_typing()


func _on_click(event: InputEvent) -> void:
	var click: InputEventMouseButton = event as InputEventMouseButton
	if click == null or not click.pressed or click.button_index != MOUSE_BUTTON_LEFT:
		return
	root.accept_event()
	if choices.get_child_count() == 0:
		advance_requested.emit()


static func _is_action(event: InputEvent, action: StringName) -> bool:
	return InputMap.has_action(action) and event.is_action_pressed(action)
