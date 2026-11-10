@tool
extends VBoxContainer
## The "API key" page of the connection screen. Keys travel to the helper on stdin, never on
## the command line. Every field is a ui_components field: label above, one height, one inset.

signal back_requested
signal save_requested(settings: Dictionary)
signal environment_requested

const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
## Connection drivers, in picker order: metadata id, provider name, Service URL help,
## default Service URL ("" when the user must give one), Model placeholder.
const DRIVERS := [
	[
		"codex",
		"OpenAI",
		"OpenAI's address. Change it only for a server with the same API.",
		"https://api.openai.com/v1",
		"e.g. gpt-4.1",
	],
	[
		"opencode",
		"OpenAI-compatible server",
		"Address of your server, ending in /v1.",
		"",
		"e.g. provider/model-name",
	],
]
const UNAVAILABLE := "This connection is unavailable on this platform."
const NOT_INSTALLED := "Install the selected assistant to use this connection."

var _driver: OptionButton
var _saved: OptionButton
var _endpoint: LineEdit
var _model: LineEdit
var _key: LineEdit
## Under the key field: why Save is unavailable, or the result of the last check.
var _hint: Label
var _save: Button
var _environment: Button
var _indicator: Control
var _fields: Dictionary = {}
## Holds the Service URL: collapsed for a provider with a known address, open and
## headerless for a compatible server, which needs one.
var _advanced: UI_COMPONENTS.Section
var _profile_id := ""
var _driver_hints: Dictionary = {}
var _checking := false


func _ready() -> void:
	theme_type_variation = "JoinerySections"
	# Back sits above the title, flush with the column, so the title, the labels
	# and the field borders share one left edge.
	var top := VBoxContainer.new()
	top.theme_type_variation = "JoineryTight"
	add_child(top)
	var back := UI_COMPONENTS.link("Back")
	back.tooltip_text = "Back to the sign-in choices"
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI_COMPONENTS.bind_icon(back, "back")
	back.pressed.connect(func() -> void: back_requested.emit())
	top.add_child(back)
	top.add_child(UI_COMPONENTS.label("API key", "JoineryTitle"))

	var form := VBoxContainer.new()
	form.theme_type_variation = "JoineryStack"
	add_child(form)
	_saved = _picker()
	_saved.item_selected.connect(_choose_saved)
	_add_field(form, "saved", "Saved connection", _saved)
	_driver = _picker()
	for index: int in DRIVERS.size():
		_driver.add_item(str(DRIVERS[index][1]))
		_driver.set_item_metadata(index, DRIVERS[index][0])
	_driver.item_selected.connect(_choose_driver)
	_add_field(form, "driver", "Provider", _driver)
	_model = _line("e.g. provider/model-name", 256)
	_add_field(form, "model", "Model", _model)
	_key = _line("Paste the key", 8192)
	_key.secret = true
	var key_field := _add_field(form, "key", "API key", _key, "Saved privately on this computer.")
	_hint = UI_COMPONENTS.label("", "JoineryCaption", true)
	_hint.hide()
	key_field.add_child(_hint)
	_advanced = UI_COMPONENTS.section("Advanced", true)
	form.add_child(_advanced)
	_endpoint = _line("e.g. https://api.example.com/v1", 2048)
	_add_field(_advanced.body, "endpoint", "Service URL", _endpoint)

	var actions := VBoxContainer.new()
	actions.theme_type_variation = "JoineryStack"
	add_child(actions)
	_save = UI_COMPONENTS.primary("Save and connect")
	_save.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_save.pressed.connect(_submit)
	actions.add_child(_save)
	_indicator = INDICATOR.new()
	_indicator.anchor_left = 1.0
	_indicator.anchor_right = 1.0
	_indicator.anchor_top = 0.5
	_indicator.anchor_bottom = 0.5
	_indicator.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_indicator.grow_vertical = Control.GROW_DIRECTION_BOTH
	_save.add_child(_indicator)
	_environment = UI_COMPONENTS.secondary("Use the key already set on this computer")
	_environment.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_environment.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_environment.pressed.connect(func() -> void: environment_requested.emit())
	actions.add_child(_environment)
	_environment.hide()
	_save.theme_changed.connect(_place_indicator)


func _picker() -> OptionButton:
	# A form picker looks like the fields under it: same fill, edge, inset and
	# focus ring, taken from the theme's LineEdit so a theme change follows.
	var picker := OptionButton.new()
	picker.fit_to_longest_item = false
	picker.clip_text = true
	picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	picker.theme_changed.connect(_style_as_field.bind(picker))
	return picker


func _style_as_field(picker: OptionButton) -> void:
	# Each override re-sends theme_changed; the meta keeps this from re-entering.
	if picker.has_meta("_joinery_styling"):
		return
	picker.set_meta("_joinery_styling", true)
	var normal := picker.get_theme_stylebox("normal", "LineEdit")
	var states := {
		"normal": normal,
		"hover": normal,
		"pressed": normal,
		"hover_pressed": normal,
		"disabled": picker.get_theme_stylebox("read_only", "LineEdit"),
		"focus": picker.get_theme_stylebox("focus", "LineEdit"),
	}
	for state: String in states:
		picker.add_theme_stylebox_override(state, states[state])
	for color: String in [
		"font_color", "font_hover_color", "font_pressed_color", "font_focus_color"
	]:
		picker.add_theme_color_override(color, picker.get_theme_color("font_color", "LineEdit"))
	picker.remove_meta("_joinery_styling")


func _line(placeholder: String, limit: int) -> LineEdit:
	var line := LineEdit.new()
	line.placeholder_text = placeholder
	line.max_length = limit
	line.text_changed.connect(func(_text: String) -> void: _clear_error(line))
	return line


func _add_field(
	parent: Control, key: String, title: String, control: Control, help := ""
) -> UI_COMPONENTS.Field:
	var field := UI_COMPONENTS.field(title, control, help)
	parent.add_child(field)
	_fields[key] = field
	return field


func _place_indicator() -> void:
	var inset := float(UI_THEME.space(_save, 3))
	_indicator.offset_right = -inset
	_indicator.offset_left = -inset


func _clear_error(control: Control) -> void:
	for field: UI_COMPONENTS.Field in _fields.values():
		if field.control == control:
			field.set_error("")


## Fill the form for [param agents] (status data keyed by id) with [param selected] chosen.
func configure(agents: Dictionary, selected: String) -> void:
	_saved.clear()
	_saved.add_item("New connection")
	_saved.set_item_metadata(0, {})
	for id: String in agents:
		var data: Dictionary = agents[id]
		if data.get("profile", false):
			_saved.add_item(str(data["model"]))
			_saved.set_item_metadata(_saved.item_count - 1, data)
	(_fields["saved"] as Control).visible = _saved.item_count > 1
	_driver_hints.clear()
	var first_available := 0
	var found_available := false
	for index: int in _driver.item_count:
		var id := str(_driver.get_item_metadata(index))
		var data: Dictionary = agents.get(id, {})
		var supported := bool(data.get("supported", true))
		var available := supported and bool(data.get("installed", false))
		_driver.set_item_disabled(index, not available)
		_driver_hints[index] = (
			str(data.get("reason", UNAVAILABLE)) if not supported else NOT_INSTALLED
		)
		_driver.set_item_tooltip(index, "" if available else str(_driver_hints[index]))
		if available and not found_available:
			first_available = index
			found_available = true
	_driver.select(first_available)
	_environment.visible = (
		agents.get(selected, {}).get("api_key_configured", false)
		and agents.get(selected, {}).get("supported", true)
	)
	_choose_saved(0)
	for index: int in _saved.item_count:
		if _saved.get_item_metadata(index).get("id", "") == selected:
			_saved.select(index)
			_choose_saved(index)
			break
	for field: UI_COMPONENTS.Field in _fields.values():
		field.set_error("")
	set_checking(false)
	_update_hint()


func _choose_saved(index: int) -> void:
	var data: Dictionary = _saved.get_item_metadata(index)
	_profile_id = str(data.get("id", ""))
	_endpoint.text = str(data.get("endpoint", ""))
	_model.text = str(data.get("model", ""))
	_key.clear()
	_key.placeholder_text = (
		"Leave blank to keep the saved key" if not data.is_empty() else "Paste the key"
	)
	if data.has("driver"):
		_driver.select(0 if data["driver"] == "codex" else 1)
	_choose_driver(_driver.selected)


## Show the chosen provider's defaults: its address (kept unless the user typed their
## own), its model example, and the Service URL only where it must be given.
func _choose_driver(index: int) -> void:
	var spec: Array = DRIVERS[clampi(index, 0, DRIVERS.size() - 1)]
	var url := _endpoint.text.strip_edges()
	var known := url.is_empty()
	for driver: Array in DRIVERS:
		known = known or url == str(driver[3])
	if known:
		_endpoint.text = str(spec[3])
	_model.placeholder_text = str(spec[4])
	var needs_url := str(spec[3]).is_empty()
	_advanced.header.visible = not needs_url
	if needs_url:
		_advanced.open = true
		_advanced.body.show()
	elif not (_advanced.header as Button).button_pressed:
		_advanced.open = false
		_advanced.body.hide()
	_update_hint()


func _update_hint() -> void:
	if _hint == null or _save == null:
		return
	var blocked := _driver.is_item_disabled(_driver.selected)
	_save.disabled = blocked or _checking
	_save.tooltip_text = str(_driver_hints.get(_driver.selected, NOT_INSTALLED)) if blocked else ""
	var help: Label = (_fields["endpoint"] as UI_COMPONENTS.Field).help
	help.text = str(DRIVERS[clampi(maxi(_driver.selected, 0), 0, DRIVERS.size() - 1)][2])
	help.show()
	if blocked:
		_set_hint(str(_driver_hints.get(_driver.selected, NOT_INSTALLED)), false)
	elif not _checking:
		_set_hint("", false)


func _set_hint(text: String, error: bool) -> void:
	_hint.text = text
	_hint.theme_type_variation = "JoineryError" if error else "JoineryCaption"
	_hint.visible = not text.is_empty()


## Show the helper's verdict on the last Save under the key field ("" clears it).
func show_result(text: String, ok := false) -> void:
	set_checking(false)
	_set_hint(text, not ok and not text.is_empty())


## While the key is being checked: Save disabled, "Checking key…" and the indicator.
func set_checking(on: bool) -> void:
	_checking = on
	_save.text = "Checking key…" if on else "Save and connect"
	_indicator.set("running", on)
	_place_indicator()
	if on:
		_set_hint("", false)
	_update_hint()


func _submit() -> void:
	if _save.disabled:
		return
	var problems := {}
	var url := _endpoint.text.strip_edges()
	if url.is_empty():
		problems["endpoint"] = "Enter the service URL"
	elif not (url.begins_with("https://") or url.begins_with("http://")):
		problems["endpoint"] = "Start the address with https://"
	if _model.text.strip_edges().is_empty():
		problems["model"] = "Enter the model ID"
	if _profile_id.is_empty() and _key.text.strip_edges().is_empty():
		problems["key"] = "Paste the API key"
	for key: String in ["endpoint", "model", "key"]:
		(_fields[key] as UI_COMPONENTS.Field).set_error(str(problems.get(key, "")))
	if not problems.is_empty():
		if problems.has("endpoint") and not _advanced.body.visible:
			_advanced.set_open(true)
		for key: String in ["model", "key", "endpoint"]:
			if problems.has(key):
				(_fields[key] as UI_COMPONENTS.Field).control.grab_focus()
				break
		return
	set_checking(true)
	(
		save_requested
		. emit(
			{
				"id": _profile_id,
				"driver": _driver.get_item_metadata(_driver.selected),
				"endpoint": url,
				"model": _model.text.strip_edges(),
				"key": _key.text,
			}
		)
	)


func clear_key() -> void:
	_key.clear()
