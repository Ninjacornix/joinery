@tool
extends ScrollContainer
## Journeyman settings page, shown in place of the chat. Every row changes something real and
## applies at once (no Save button); preferences are saved for this user, across projects.
## A failed write puts the control back and says so under the title.

signal closed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR_SCRIPT := preload("activity_indicator.gd")
const STYLE := preload("sidebar_style.gd")
const PATH := "Joinery/journeyman.cfg"
const TEXT_SIZES := UI_THEME.TEXT_SIZES
const MODES := ["Auto", "Fast", "Thorough"]
## What each reply mode does, shown as its tooltip.
const MODE_HINTS := [
	"The assistant picks how much to do for each request",
	"Short, quick replies for small changes",
	"Takes longer and works through bigger changes step by step",
]
const THEMES := {"match": "Match editor", "dark": "Dark", "light": "Light"}
## Page body width cap, in multiples of the font size.
const MAX_EM := 36.0
const TROUBLESHOOTING := [
	["Refresh models", "/models"],
	["Check installed apps", "/doctor"],
	["Check app connections", "/tools"],
	["Check assets", "/check"],
	["Verify recipes", "/rebuild"],
	["Supported assistants", "/agents"],
]
## Blank space right of the ink in the 16-unit chevron and disclosure glyphs (icons/ui), so
## every trailing control's ink ends on the content edge, where the switches end.
const CHEVRON_INK := 3.75 / 16.0
const DISCLOSURE_INK := 4.75 / 16.0
const BUTTON_STATES := ["normal", "hover", "pressed", "hover_pressed", "disabled", "focus"]
const COMPACT_TIP := "Keep the current assets and drop older restore data"

var dock: Control
var _body: VBoxContainer
var _spacer: Control
var _status: Label
var _theme_picker: OptionButton
var _theme_note: Control
var _accent_picker: OptionButton
var _text_picker: OptionButton
var _motion_switch: CheckButton
var _mode_picker: OptionButton
var _timings_switch: CheckButton
var _details_switch: CheckButton
var _reconnect: Button
var _history_row: Control
var _history_size: Label
var _history_button: MenuButton
var _history_indicator: Control
var _history_job: RefCounted
var _history_action := ""


## Add the menu button to the sidebar header and the hidden settings page below it.
static func attach(sidebar: Control, header: Control, content: Control) -> ScrollContainer:
	var page: ScrollContainer = load("res://addons/joinery/journeyman_settings.gd").new()
	page.dock = sidebar
	page.hide()
	var more := UI.icon_button("menu", "Journeyman settings")
	more.accessibility_name = "Open Journeyman settings"
	more.pressed.connect(page.set_open.bind(true))
	header.add_child(more)
	page.closed.connect(page.set_open.bind(false))
	content.add_child(page)
	return page


## Show the page in place of the header, chat, review and composer, then restore them.
func set_open(open: bool) -> void:
	var content := get_parent()
	if content == null:
		visible = open
		return
	for child: Node in content.get_children():
		if child == self or not child is CanvasItem:
			continue
		var item := child as CanvasItem
		if open:
			if not item.has_meta("before_settings"):
				item.set_meta("before_settings", item.visible)
			item.visible = false
		elif item.has_meta("before_settings"):
			item.visible = item.get_meta("before_settings")
			item.remove_meta("before_settings")
	visible = open
	if open:
		_status.hide()
		_sync_prefs()
		_refresh_rows()
		_history("usage")
		UI_MOTION.stagger(_body.get_children())
	elif _history_action != "compact":
		# Reading the size can stop; a running Compact finishes even with the page closed.
		_cancel_history()


## journeyman.cfg in the user config dir (the Project Manager reads the same file).
static func config_path() -> String:
	var override := str(Engine.get_meta("joinery_ui_config", ""))
	return override if not override.is_empty() else OS.get_config_dir().path_join(PATH)


## Load saved preferences and apply them to the sidebar.
func apply_all() -> void:
	var prefs := UI_THEME.prefs()
	_apply(
		{
			"mode": str(get_value("mode", "Auto")),
			"text_scale": float(TEXT_SIZES.get(prefs["text_size"], 1.0)),
			"show_timings": bool(get_value("show_timings", true)),
			"details_open": bool(get_value("details_open", false)),
		}
	)


## A saved [journeyman] value.
func get_value(key: String, fallback: Variant) -> Variant:
	var file := ConfigFile.new()
	file.load(config_path())
	return file.get_value("journeyman", key, fallback)


## Save a [journeyman] value. Returns OK or the write error.
func set_value(key: String, value: Variant) -> Error:
	var file := ConfigFile.new()
	var loaded := file.load(config_path())
	if loaded != OK and loaded != ERR_FILE_NOT_FOUND and loaded != ERR_FILE_CANT_OPEN:
		return loaded
	file.set_value("journeyman", key, value)
	DirAccess.make_dir_recursive_absolute(config_path().get_base_dir())
	return file.save(config_path())


func _ready() -> void:
	set_process(false)
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_THEME.watch(self)
	UI_MOTION.rubber_band(self)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(gutter)
	var column := HBoxContainer.new()
	gutter.add_child(column)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(_body)
	_spacer = Control.new()
	_spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	column.add_child(_spacer)
	_build_title()
	_build_appearance()
	_build_replies()
	_build_project()
	_build_troubleshooting()
	_on_theme_rebuilt()
	UI_THEME.bus().connect("changed", _on_pref_changed)


func _notification(what: int) -> void:
	if (what == NOTIFICATION_RESIZED or what == NOTIFICATION_VISIBILITY_CHANGED) and _body != null:
		_fit.call_deferred()
	elif what == NOTIFICATION_EXIT_TREE:
		_cancel_history()


## Called by UI_THEME.watch after a live re-theme.
func _on_theme_rebuilt() -> void:
	if _accent_picker == null:
		return
	var dark := UI_THEME.is_dark(self)
	for index: int in UI_THEME.ACCENTS.size():
		var key: String = UI_THEME.ACCENTS.keys()[index]
		var swatch: Color = UI_THEME.ACCENTS[key][0 if dark else 1]
		_accent_picker.set_item_icon(index, _swatch(swatch))
	_fit()


## Cap the body at MAX_EM. Stretch ratios share the width without raising the page's minimum
## size, so a narrow dock never gets pushed wider by its settings.
func _fit() -> void:
	var bar := get_v_scroll_bar()
	var available := size.x - UI_THEME.space(self, 3) - (bar.size.x if bar.visible else 0.0)
	var cap := MAX_EM * get_theme_default_font_size()
	_body.size_flags_stretch_ratio = maxf(1.0, minf(available, cap))
	_spacer.size_flags_stretch_ratio = maxf(0.0, available - cap)


func _build_title() -> void:
	var top := VBoxContainer.new()
	top.theme_type_variation = "JoineryTight"
	_body.add_child(top)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	top.add_child(row)
	var back := UI.icon_button("back", "Back to chat")
	back.accessibility_name = "Close settings and return to the chat"
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	back.shortcut = Shortcut.new()
	back.shortcut.events = [escape]
	back.shortcut_in_tooltip = false
	back.pressed.connect(func() -> void: closed.emit())
	row.add_child(back)
	var title := UI.label("Settings", "JoineryTitle")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	_status = UI.label("", "JoineryError", true)
	_status.hide()
	top.add_child(_inset(_status))


func _build_appearance() -> void:
	var section := _section("Appearance")
	var prefs := UI_THEME.prefs()
	_theme_picker = UI.picker(THEMES.values())
	_flush_picker(_theme_picker)
	_theme_picker.select(THEMES.keys().find(prefs["theme"]))
	_theme_picker.item_selected.connect(
		func(index: int) -> void: _choose_pref(_theme_picker, "theme", THEMES.keys(), index)
	)
	section.body.add_child(UI.setting_row("Theme", _theme_picker))
	_theme_note = _inset(UI.label("Also changes the editor.", "JoineryCaption", true))
	_theme_note.visible = prefs["theme"] != "match"
	section.body.add_child(_theme_note)
	_accent_picker = UI.picker(UI_THEME.ACCENT_NAMES.values())
	_flush_picker(_accent_picker)
	_accent_picker.select(UI_THEME.ACCENTS.keys().find(prefs["accent"]))
	# Swatches show their own colour, not a tinted one.
	for state: String in ["normal", "hover", "pressed", "focus", "hover_pressed"]:
		_accent_picker.add_theme_color_override("icon_%s_color" % state, Color.WHITE)
	_accent_picker.item_selected.connect(
		func(index: int) -> void:
			_choose_pref(_accent_picker, "accent", UI_THEME.ACCENTS.keys(), index)
	)
	section.body.add_child(UI.setting_row("Accent", _accent_picker))
	_text_picker = UI.picker(TEXT_SIZES.keys())
	_flush_picker(_text_picker)
	_text_picker.select(TEXT_SIZES.keys().find(prefs["text_size"]))
	_text_picker.item_selected.connect(
		func(index: int) -> void:
			if _choose_pref(_text_picker, "text_size", TEXT_SIZES.keys(), index):
				_apply({"text_scale": float(TEXT_SIZES.values()[index])})
	)
	section.body.add_child(UI.setting_row("Text size", _text_picker))
	_motion_switch = UI.switch()
	_motion_switch.button_pressed = prefs["reduce_motion"] == "on"
	_motion_switch.toggled.connect(
		func(on: bool) -> void:
			if UI_THEME.set_pref("reduce_motion", "on" if on else "off") != OK:
				_motion_switch.set_pressed_no_signal(not on)
				_failed()
	)
	section.body.add_child(UI.setting_row("Reduce motion", _motion_switch))


func _build_replies() -> void:
	var section := _section("Replies")
	_mode_picker = UI.picker(MODES)
	_flush_picker(_mode_picker)
	for index: int in MODES.size():
		_mode_picker.set_item_tooltip(index, MODE_HINTS[index])
	_mode_picker.accessibility_name = "Reply mode"
	_mode_picker.select(maxi(0, MODES.find(str(get_value("mode", "Auto")))))
	_hint_mode()
	_mode_picker.item_selected.connect(
		func(index: int) -> void:
			_hint_mode()
			if _save(_mode_picker, "mode", MODES[index], MODES.find(get_value("mode", "Auto"))):
				_apply({"mode": MODES[index]})
	)
	section.body.add_child(UI.setting_row("Reply mode", _mode_picker))
	_timings_switch = _switch_row(section, "Show reply times", "show_timings", true)
	_details_switch = _switch_row(section, "Open review details", "details_open", false)


## The closed picker says what the chosen reply mode does.
func _hint_mode() -> void:
	var index := _mode_picker.selected
	_mode_picker.tooltip_text = (
		"How the assistant replies. " + MODE_HINTS[index]
		if index >= 0 and index < MODE_HINTS.size()
		else "How the assistant replies"
	)


func _build_project() -> void:
	var section := _section("Project")
	for item: Array in [["Art direction", "/art"], ["Project apps", "/setup"]]:
		var button := UI.row_button(str(item[0]))
		button.pressed.connect(_run.bind(str(item[1])))
		_flush_row(button)
		section.body.add_child(button)
	var tools := HBoxContainer.new()
	tools.theme_type_variation = "JoineryRow"
	# The dots show only while Compact runs; they stay hidden otherwise.
	_history_indicator = INDICATOR_SCRIPT.new()
	tools.add_child(_history_indicator)
	tools.add_child(_build_history_menu())
	_history_row = UI.setting_row("History", tools)
	section.body.add_child(_history_row)


func _build_troubleshooting() -> void:
	var section := _section("Troubleshooting", true)
	_reconnect = UI.row_button("Reconnect")
	_reconnect.pressed.connect(_run.bind("reconnect"))
	_flush_row(_reconnect)
	section.body.add_child(_reconnect)
	for item: Array in TROUBLESHOOTING:
		var button := UI.row_button(str(item[0]))
		button.pressed.connect(_run.bind(str(item[1])))
		_flush_row(button)
		section.body.add_child(button)


## History reads like the other dropdowns: its size (rolling digits) and a chevron, with
## "Compact history" in its menu.
func _build_history_menu() -> MenuButton:
	_history_button = MenuButton.new()
	_history_button.theme_type_variation = "JoineryPicker"
	_history_button.flat = false
	_history_button.focus_mode = Control.FOCUS_ALL
	_history_button.accessibility_name = "History options"
	_history_button.tooltip_text = "Space used by restore data for past changes"
	UI.bind_height(_history_button, "md")
	UI_MOTION.press_feedback(_history_button)
	var menu := _history_button.get_popup()
	menu.add_item("Compact history", 0)
	menu.set_item_tooltip(0, COMPACT_TIP)
	menu.id_pressed.connect(func(_id: int) -> void: _history("compact"))
	_history_size = UI.label("", "JoineryNumeric")
	_history_size.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_history_button.add_child(_history_size)
	_history_size.minimum_size_changed.connect(_layout_history)
	_history_button.resized.connect(_layout_history)
	_history_button.draw.connect(_draw_history_arrow)
	_flush_picker(_history_button)
	STYLE.on_theme(_history_button, _layout_history)
	return _history_button


func _layout_history() -> void:
	var arrow := _history_button.get_theme_icon("arrow", "JoineryPicker")
	if arrow == null:
		return
	var text := _history_size.get_combined_minimum_size()
	var gap := float(UI_THEME.space(self, 2))
	var width := text.x + gap + arrow.get_width() - _ink(arrow.get_width(), CHEVRON_INK)
	_history_button.custom_minimum_size.x = ceilf(width)
	_history_size.size = text
	_history_size.position = Vector2(0.0, roundf((_history_button.size.y - text.y) / 2.0))
	_history_button.queue_redraw()


func _draw_history_arrow() -> void:
	var arrow := _history_button.get_theme_icon("arrow", "JoineryPicker")
	if arrow == null:
		return
	var at := Vector2(
		_history_button.size.x - arrow.get_width() + _ink(arrow.get_width(), CHEVRON_INK),
		roundf((_history_button.size.y - arrow.get_height()) / 2.0)
	)
	var tint := Color(1, 1, 1, 0.45) if _history_button.disabled else Color.WHITE
	_history_button.draw_texture(arrow, at, tint)


## Move a picker's chevron so its ink ends on the control's right edge (the switches' edge).
## The hover fill grows outward by the same amount, so the chevron keeps its padding.
func _flush_picker(picker: Button) -> void:
	STYLE.on_theme_overrides(
		picker,
		func() -> void:
			var variation := picker.theme_type_variation
			picker.remove_theme_constant_override("arrow_margin")
			var arrow := picker.get_theme_icon("arrow", variation)
			var ink := _ink(arrow.get_width() if arrow != null else 0.0, CHEVRON_INK)
			picker.add_theme_constant_override("arrow_margin", -ink)
			for state: String in BUTTON_STATES:
				picker.remove_theme_stylebox_override(state)
				var source := picker.get_theme_stylebox(state, variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				box.content_margin_right = 0.0
				if box is StyleBoxFlat:
					(box as StyleBoxFlat).expand_margin_right += UI_THEME.space(picker, 1) + ink
				picker.add_theme_stylebox_override(state, box)
	)


## Pull a row button's trailing chevron right so its ink ends on the content edge.
func _flush_row(button: Button) -> void:
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var variation := button.theme_type_variation
			var cap := button.get_theme_constant("icon_max_width", variation)
			var side := float(cap) if cap > 0 else float(UI_THEME.space(button, 4))
			var ink := _ink(side, DISCLOSURE_INK)
			for state: String in BUTTON_STATES:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state, variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				box.content_margin_right = maxf(0.0, box.content_margin_right - ink)
				button.add_theme_stylebox_override(state, box)
	)


static func _ink(side: float, ratio: float) -> int:
	return roundi(side * ratio)


func _section(title: String, collapsible := false) -> UI.Section:
	var section := UI.section(title, collapsible)
	var header := section.header
	section.remove_child(header)
	var inset := _inset(header)
	section.add_child(inset)
	section.move_child(inset, 0)
	_body.add_child(section)
	return section


func _inset(control: Control) -> MarginContainer:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(control)
	return inset


func _switch_row(section: UI.Section, title: String, key: String, fallback: bool) -> CheckButton:
	var check := UI.switch()
	check.button_pressed = bool(get_value(key, fallback))
	check.toggled.connect(
		func(on: bool) -> void:
			if _save(check, key, on, not on):
				_apply({key: on})
	)
	section.body.add_child(UI.setting_row(title, check))
	return check


## Save an appearance preference chosen in [param picker]; roll the picker back on failure.
func _choose_pref(picker: OptionButton, key: String, values: Array, index: int) -> bool:
	var previous := values.find(UI_THEME.prefs()[key])
	if UI_THEME.set_pref(key, str(values[index])) != OK:
		picker.select(previous)
		_failed()
		return false
	if key == "theme":
		_theme_note.visible = values[index] != "match"
		if _theme_note.visible:
			UI_MOTION.fade_in(_theme_note)
	_status.hide()
	return true


## Save a [journeyman] value set by [param control]; restore [param previous] on failure.
func _save(control: Control, key: String, value: Variant, previous: Variant) -> bool:
	if set_value(key, value) == OK:
		_status.hide()
		return true
	if control is OptionButton:
		(control as OptionButton).select(maxi(0, int(previous)))
	elif control is BaseButton:
		(control as BaseButton).set_pressed_no_signal(bool(previous))
	_failed()
	return false


func _on_pref_changed(_key: String, _value: String) -> void:
	_sync_prefs()


## Show the saved appearance values, however they were changed (here, another Joinery
## screen, or a script), without re-saving them.
func _sync_prefs() -> void:
	if _theme_picker == null:
		return
	var prefs := UI_THEME.prefs()
	_theme_picker.select(THEMES.keys().find(prefs["theme"]))
	_theme_note.visible = prefs["theme"] != "match"
	_accent_picker.select(UI_THEME.ACCENTS.keys().find(prefs["accent"]))
	_text_picker.select(TEXT_SIZES.keys().find(prefs["text_size"]))
	var reduced: bool = prefs["reduce_motion"] == "on"
	if _motion_switch.button_pressed != reduced:
		_motion_switch.set_pressed_no_signal(reduced)


func _failed() -> void:
	_status.text = "Couldn't save this setting. Check that your settings folder can be written."
	UI_MOTION.fade_in(_status)


func _apply(values: Dictionary) -> void:
	if dock != null and dock.has_method("apply_preferences"):
		dock.call("apply_preferences", values)


func _run(command: String) -> void:
	closed.emit()
	if dock == null:
		return
	if command == "reconnect":
		if dock.has_signal("reconnect_requested"):
			dock.emit_signal("reconnect_requested")
	elif dock.has_method("run_command"):
		dock.call("run_command", command)


func _refresh_rows() -> void:
	var state := str(dock.get("connection_state")) if dock != null else ""
	_reconnect.visible = state != "connected"


func _history(action: String) -> void:
	if _history_job != null:
		return
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", _engine_dir(), "joinery", "history", action]
	)
	if action == "usage":
		args.append("--json")
	args.append_array(["--project", project])
	_history_job = COMMAND_SCRIPT.new()
	if _history_job.start("uv", args) != OK:
		_history_job = null
		_history_unavailable()
		return
	_history_action = action
	_history_button.get_popup().set_item_disabled(0, true)
	if action == "compact":
		# Only a running Compact gets the dots; reading the size is quiet.
		_history_indicator.set("running", true)
		_history_size.text = "Compacting…"
	elif _history_size.text.is_empty():
		_history_size.text = "Checking…"
	set_process(true)


func _process(_delta: float) -> void:
	if _history_job == null:
		set_process(false)
		return
	var result: Dictionary = _history_job.poll()
	if result.is_empty():
		return
	_history_job = null
	set_process(false)
	_history_indicator.set("running", false)
	if int(result.get("code", 1)) != 0:
		if _history_action == "compact":
			_history_button.get_popup().set_item_disabled(0, false)
			_history_size.text = ""
			_status.text = "Couldn't compact history. Details are in Output."
			print("[Joinery] history compact: ", result.get("error", ""))
			UI_MOTION.fade_in(_status)
		else:
			_history_unavailable()
		return
	if _history_action == "compact":
		UI_MOTION.pulse(_history_row)
		_history("usage")
		return
	var bytes := _usage_bytes(str(result.get("output", "")))
	if bytes < 0:
		_history_unavailable()
		return
	_history_button.disabled = false
	_history_button.get_popup().set_item_disabled(0, false)
	_history_button.tooltip_text = "Space used by restore data for past changes"
	if _history_size.text.is_empty() or not _history_size.text[0].is_valid_int():
		_history_size.text = ""
	UI_MOTION.roll_number(_history_size, bytes, _human_size)


func _history_unavailable() -> void:
	_history_indicator.set("running", false)
	_history_size.text = "Unavailable"
	_history_button.disabled = true
	_history_button.queue_redraw()
	_history_button.tooltip_text = "History tools aren't available in this version of Joinery"


func _cancel_history() -> void:
	if _history_job != null:
		_history_job.cancel()
		_history_job = null
		_history_indicator.set("running", false)


## Total restore-data bytes from `joinery history usage --json` (the last JSON line), or -1.
static func _usage_bytes(output: String) -> int:
	var lines := output.split("\n", false)
	lines.reverse()
	for line: String in lines:
		if not line.strip_edges().begins_with("{"):
			continue
		var data: Variant = JSON.parse_string(line)
		if not data is Dictionary:
			continue
		for key: String in ["total_bytes", "bytes", "size_bytes", "total"]:
			var value: Variant = (data as Dictionary).get(key)
			if value is Dictionary:
				value = (value as Dictionary).get("bytes")
			if value is float or value is int:
				return int(value)
	return -1


## Bytes for people: "0 B", "512 KB", "340 MB", "1.2 GB".
static func _human_size(bytes: float) -> String:
	var units := ["B", "KB", "MB", "GB", "TB"]
	var value := bytes
	var index := 0
	while value >= 1000.0 and index < units.size() - 1:
		value /= 1000.0
		index += 1
	if index >= 3 and value < 10.0:
		return "%.1f %s" % [value, units[index]]
	return "%d %s" % [roundi(value), units[index]]


func _swatch(color_value: Color) -> ImageTexture:
	var side := maxi(4, roundi(12 * UI_THEME.unit(self)))
	var image := Image.create(side, side, false, Image.FORMAT_RGBA8)
	var half := side / 2.0
	for y: int in side:
		for x: int in side:
			var distance := Vector2(x + 0.5 - half, y + 0.5 - half).length()
			var cover := clampf(half - distance + 0.5, 0.0, 1.0)
			image.set_pixel(x, y, Color(color_value, cover))
	return ImageTexture.create_from_image(image)


func _engine_dir() -> String:
	var from_env := OS.get_environment("JOINERY_HOME")
	return (
		from_env
		if not from_env.is_empty()
		else ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	)
