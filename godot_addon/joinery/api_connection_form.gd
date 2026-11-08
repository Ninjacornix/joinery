@tool
extends VBoxContainer
## API settings stay behind a submenu; keys travel to the helper on stdin.

signal back_requested
signal save_requested(settings: Dictionary)
signal environment_requested

var _driver: OptionButton
var _saved: OptionButton
var _endpoint: LineEdit
var _model: LineEdit
var _key: LineEdit
var _hint: Label
var _save: Button
var _environment: Button
var _profile_id := ""
var _driver_hints: Dictionary = {}


func _ready() -> void:
	add_theme_constant_override("separation", get_theme_default_font_size() / 2)
	var back := Button.new()
	back.text = "Back"
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back.pressed.connect(func() -> void: back_requested.emit())
	add_child(back)
	var heading := Label.new()
	heading.text = "API / additional model"
	heading.set_theme_type_variation("HeaderLarge")
	add_child(heading)
	_saved = OptionButton.new()
	_saved.item_selected.connect(_choose_saved)
	add_child(_saved)
	_label("Run through")
	_driver = OptionButton.new()
	_driver.add_item("Codex · Responses API")
	_driver.set_item_metadata(0, "codex")
	_driver.add_item("opencode · OpenAI-compatible API")
	_driver.set_item_metadata(1, "opencode")
	_driver.item_selected.connect(func(_index: int) -> void: _update_hint())
	add_child(_driver)
	_endpoint = _field("Endpoint", "https://api.example.com/v1")
	_endpoint.max_length = 2048
	_model = _field("Model", "Provider model ID")
	_model.max_length = 256
	_key = _field("API key", "API key")
	_key.secret = true
	_key.max_length = 8192
	_hint = Label.new()
	_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(_hint)
	_environment = Button.new()
	_environment.text = "Use configured environment key"
	_environment.pressed.connect(func() -> void: environment_requested.emit())
	add_child(_environment)
	_save = Button.new()
	_save.text = "Save and connect"
	_save.pressed.connect(_submit)
	add_child(_save)


func _label(text: String) -> void:
	var label := Label.new()
	label.text = text
	add_child(label)


func _field(label: String, placeholder: String) -> LineEdit:
	_label(label)
	var field := LineEdit.new()
	field.placeholder_text = placeholder
	add_child(field)
	return field


func configure(agents: Dictionary, selected: String) -> void:
	_saved.clear()
	_saved.add_item("New connection")
	_saved.set_item_metadata(0, {})
	for id: String in agents:
		var data: Dictionary = agents[id]
		if data.get("profile", false):
			_saved.add_item(str(data["model"]))
			_saved.set_item_metadata(_saved.item_count - 1, data)
	_saved.visible = _saved.item_count > 1
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
			str(data.get("reason", "This connection is unavailable on this platform."))
			if not supported else "Install the selected agent to use this connection."
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
	_update_hint()


func _choose_saved(index: int) -> void:
	var data: Dictionary = _saved.get_item_metadata(index)
	_profile_id = str(data.get("id", ""))
	_endpoint.text = str(data.get("endpoint", ""))
	_model.text = str(data.get("model", ""))
	_key.clear()
	_key.placeholder_text = "Leave blank to keep the saved key" if not data.is_empty() else "API key"
	if data.has("driver"):
		_driver.select(0 if data["driver"] == "codex" else 1)
	_update_hint()


func _update_hint() -> void:
	if _hint == null or _save == null:
		return
	_save.disabled = _driver.is_item_disabled(_driver.selected)
	_hint.text = (
		str(_driver_hints.get(_driver.selected, "Install the selected agent to use this connection."))
		if _save.disabled else
		"Use a Responses-compatible endpoint. The key is saved privately on this computer."
		if _driver.selected == 0 else
		"Use an OpenAI-compatible endpoint. The key is saved privately on this computer."
	)


func _submit() -> void:
	if not _save.disabled:
		save_requested.emit({
			"id": _profile_id, "driver": _driver.get_item_metadata(_driver.selected),
			"endpoint": _endpoint.text, "model": _model.text, "key": _key.text,
		})


func clear_key() -> void:
	_key.clear()
