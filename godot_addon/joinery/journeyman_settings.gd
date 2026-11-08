@tool
extends ScrollContainer
## Journeyman settings page, shown in place of the chat. Every row changes something
## real; preferences are saved for this user, across projects.

signal closed

const PATH := "Joinery/journeyman.cfg"
const TEXT_SIZES := {"Small": 0.9, "Normal": 1.0, "Large": 1.15}
const MODES := ["Auto", "Fast", "Thorough"]

var dock: Control
var _config := ConfigFile.new()


## Add the ⋯ button to the sidebar header and the hidden settings page below it.
static func attach(sidebar: Control, header: Control, content: Control) -> ScrollContainer:
	var page: ScrollContainer = load("res://addons/joinery/journeyman_settings.gd").new()
	page.dock = sidebar
	page.hide()
	var more := Button.new()
	more.text = "•••"
	more.flat = true
	if sidebar.has_theme_icon("GuiTabMenuHl", "EditorIcons"):
		more.icon = sidebar.get_theme_icon("GuiTabMenuHl", "EditorIcons")
		more.text = ""
	more.theme_type_variation = "JoineryIconButton"
	more.tooltip_text = "Journeyman settings"
	more.accessibility_name = "Open Journeyman settings"
	more.pressed.connect(page.set_open.bind(true))
	header.add_child(more)
	page.closed.connect(page.set_open.bind(false))
	content.add_child(page)
	return page


## Show the page in place of the chat, review and composer, then restore them.
func set_open(open: bool) -> void:
	var content := get_parent()
	var header := content.get_child(0)
	for child: Control in content.get_children():
		if child == header or child == self:
			continue
		if open:
			child.set_meta("before_settings", child.visible)
			child.visible = false
		else:
			child.visible = child.get_meta("before_settings", child.visible)
	visible = open


static func config_path() -> String:
	return OS.get_config_dir().path_join(PATH)


func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var body := VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", int(get_theme_default_font_size() * 0.5))
	add_child(body)
	var header := HBoxContainer.new()
	body.add_child(header)
	var back := Button.new()
	back.text = "‹  Back to chat"
	back.flat = true
	back.accessibility_name = "Close settings and return to the chat"
	back.pressed.connect(func() -> void: closed.emit())
	header.add_child(back)

	_section(body, "Agent")
	_actions(
		body,
		[
			["Reconnect", "reconnect_requested"],
			["Refresh models", "models_refresh_requested"],
			["Supported agents", "/agents"],
		]
	)
	_choice(
		body,
		"Start new requests in",
		MODES,
		str(get_value("mode", "Auto")),
		func(value: String) -> void:
			set_value("mode", value)
			_apply_mode(value)
	)

	_section(body, "Chat")
	_choice(
		body,
		"Text size",
		TEXT_SIZES.keys(),
		str(get_value("text_size", "Normal")),
		func(value: String) -> void:
			set_value("text_size", value)
			dock._transcript.set_text_scale(float(TEXT_SIZES[value]))
	)
	_toggle(
		body,
		"Show how long each reply took",
		bool(get_value("show_timings", true)),
		func(on: bool) -> void:
			set_value("show_timings", on)
			dock._transcript.set_show_timings(on)
	)

	_section(body, "Review")
	_toggle(
		body,
		"Open file details by default",
		bool(get_value("details_open", false)),
		func(on: bool) -> void:
			set_value("details_open", on)
			dock._details_open = on
	)

	_section(body, "Editor")
	_actions(body, [["Show or hide viewport controls", "/toolbar"], ["Focus mode", "/focus"]])

	_section(body, "Project")
	_note(body, "Art direction, tools and checks for the open project.")
	_actions(
		body,
		[
			["Review art bible", "/art"],
			["Project setup and tools", "/setup"],
			["Check project assets", "/check"],
			["Verify asset recipes", "/rebuild"],
			["Check installed tools", "/doctor"],
		]
	)
	_note(body, "Saved for you in " + config_path())


## Load saved preferences and apply them to the sidebar.
func apply_all() -> void:
	_config.load(config_path())
	_apply_mode(str(get_value("mode", "Auto")))
	dock._transcript.set_text_scale(float(TEXT_SIZES.get(get_value("text_size", "Normal"), 1.0)))
	dock._transcript.set_show_timings(bool(get_value("show_timings", true)))
	dock._details_open = bool(get_value("details_open", false))


func _apply_mode(value: String) -> void:
	var index := MODES.find(value)
	if index >= 0 and dock._mode.selected != index:
		dock._mode.select(index)
		dock._options_changed(index)


func get_value(key: String, fallback: Variant) -> Variant:
	return _config.get_value("journeyman", key, fallback)


func set_value(key: String, value: Variant) -> void:
	_config.set_value("journeyman", key, value)
	DirAccess.make_dir_recursive_absolute(config_path().get_base_dir())
	_config.save(config_path())


func _section(parent: Control, title: String) -> void:
	var gap := Control.new()
	gap.custom_minimum_size.y = get_theme_default_font_size() * 0.6
	parent.add_child(gap)
	var label := Label.new()
	label.text = title.to_upper()
	label.add_theme_font_size_override("font_size", int(get_theme_default_font_size() * 0.75))
	label.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	parent.add_child(label)


func _note(parent: Control, text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", int(get_theme_default_font_size() * 0.85))
	label.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	parent.add_child(label)
	return label


func _row(parent: Control, title: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	parent.add_child(row)
	var label := Label.new()
	label.text = title
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	row.add_child(label)
	return row


func _choice(
	parent: Control, title: String, options: Array, current: String, changed: Callable
) -> void:
	var picker := OptionButton.new()
	picker.theme_type_variation = "JoineryPicker"
	picker.accessibility_name = title
	for option: String in options:
		picker.add_item(option)
	picker.select(maxi(0, options.find(current)))
	picker.item_selected.connect(func(index: int) -> void: changed.call(str(options[index])))
	_row(parent, title).add_child(picker)


func _toggle(parent: Control, title: String, current: bool, changed: Callable) -> void:
	var check := CheckButton.new()
	check.button_pressed = current
	check.accessibility_name = title
	check.toggled.connect(func(on: bool) -> void: changed.call(on))
	_row(parent, title).add_child(check)


func _actions(parent: Control, items: Array) -> void:
	for item: Array in items:
		var button := Button.new()
		button.text = str(item[0])
		button.flat = true
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.accessibility_name = str(item[0])
		var target := str(item[1])
		button.pressed.connect(
			func() -> void:
				closed.emit()
				if target.begins_with("/"):
					dock._run_slash(target)
				else:
					dock.emit_signal(target)
		)
		parent.add_child(button)
