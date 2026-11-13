@tool
extends MenuButton
## The composer's one options picker. Closed, it reads as the reply mode ("Auto", "Thorough")
## with a chevron that hugs the text; the chosen model shows in its tooltip. Its menu holds the
## modes as radio items, then a "Model" submenu when the provider offers a choice. How hard the
## model works follows from the mode, so there is no separate effort choice. A saved model the
## provider no longer lists stays visible as "Unavailable: …".

signal changed(selection: Dictionary)
## "Try 3 ways": make the typed message three ways side by side (ADR 0042).
signal try_ways

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const MODES := ["auto", "fast", "thorough"]
const MODE_NAMES := ["Auto", "Fast", "Thorough"]
const MODE_HINTS := ["Adapts to your request", "Favours short replies", "Favours complex work"]
## Closed label width cap, in multiples of the font size.
const MAX_EM := 16.0
const MODEL_ID := 100
const WAYS_ID := 200

var _models: Array = []
var _selection: Dictionary = {"mode": "auto", "model": "", "effort": ""}
var _last_reply := ""
var _model_menu: PopupMenu
## Choices in the model submenu, by item index.
var _model_ids: Array[String] = []


func _init() -> void:
	theme_type_variation = "JoineryPicker"
	flat = false
	clip_text = true
	text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	alignment = HORIZONTAL_ALIGNMENT_LEFT
	focus_mode = Control.FOCUS_ALL
	accessibility_name = "Reply mode and model"
	UI.bind_icon(self, "chevron")
	size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_model_menu = PopupMenu.new()
	_model_menu.id_pressed.connect(_on_model)
	get_popup().id_pressed.connect(_on_mode)
	_rebuild()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_fit()
	elif what == NOTIFICATION_PREDELETE:
		if is_instance_valid(_model_menu) and _model_menu.get_parent() == null:
			_model_menu.free()


## Models reported by the provider: [{id, name}].
func set_models(models: Array) -> void:
	_models = []
	for item: Variant in models:
		if item is Dictionary and not str(item.get("id", "")).is_empty():
			_models.append(item)
	_rebuild()


## Restore a saved {mode, model} without emitting [signal changed]. A saved effort is
## dropped: the mode decides it now.
func restore(selection: Dictionary) -> void:
	_selection = {
		"mode": str(selection.get("mode", "auto")),
		"model": str(selection.get("model", "")),
		"effort": "",
	}
	if not _selection["mode"] in MODES:
		_selection["mode"] = "auto"
	_rebuild()


## The {mode, model, effort} saved in this project for [param agent]. Each agent keeps its
## own: one agent's models mean nothing to another. A project saved before that has one
## choice for every agent ("assistant"); it is where each agent starts.
static func saved(settings: EditorSettings, agent: String) -> Dictionary:
	var shared: Dictionary = settings.get_project_metadata(
		"joinery", "assistant", {"mode": "auto", "model": "", "effort": ""}
	)
	return settings.get_project_metadata("joinery", "assistant_" + agent, shared)


static func save(settings: EditorSettings, agent: String, selection: Dictionary) -> void:
	settings.set_project_metadata("joinery", "assistant_" + agent, selection)


## Put [param agent]'s saved choice on the sidebar ([param dock]) and the chat bridge.
static func restore_saved(settings: EditorSettings, agent: String, dock: Node, chat: Node) -> void:
	var selection := saved(settings, agent)
	if dock.has_method("restore_selection_options"):
		dock.call("restore_selection_options", selection)
	chat.call("set_selection", selection)


## The current {mode, model, effort}; effort is always "" (the mode decides it).
func selection() -> Dictionary:
	return _selection.duplicate()


## Choose a mode ("auto", "fast", "thorough") and emit [signal changed] when it differs.
func set_mode(mode: String) -> void:
	var value := mode.to_lower()
	if value in MODES and value != _selection["mode"]:
		_selection["mode"] = value
		_rebuild()
		changed.emit(selection())


## Note the model that actually answered, for the tooltip.
func show_execution(event: Dictionary) -> void:
	var parts := [str(event.get("name", "Provider default"))]
	parts.append(str(event.get("mode", "auto")).capitalize())
	_last_reply = "Last reply: " + " · ".join(parts)
	_sync_tooltip()


## The chosen mode and model, then the route of the last reply when there was one.
func _sync_tooltip() -> void:
	tooltip_text = "Reply mode: %s. Model: %s." % [summary(), model_name()]
	if not _last_reply.is_empty():
		tooltip_text += "\n" + _last_reply


## The model submenu entries (model names, "Automatic" first), for tests and captures.
func model_items() -> Array[String]:
	return _items(_model_menu)


## The closed label: the mode only.
func summary() -> String:
	return MODE_NAMES[MODES.find(_selection["mode"])]


## The chosen model's name, or "Automatic".
func model_name() -> String:
	var model := str(_selection["model"])
	if model.is_empty():
		return "Automatic"
	var found := _model(model)
	return str(found.get("name", model)) if not found.is_empty() else model


func _rebuild() -> void:
	var popup := get_popup()
	if _model_menu.get_parent() != null:
		_model_menu.get_parent().remove_child(_model_menu)
	popup.clear()
	for index: int in MODES.size():
		popup.add_radio_check_item(MODE_NAMES[index], index)
		popup.set_item_checked(index, MODES[index] == _selection["mode"])
		popup.set_item_tooltip(index, MODE_HINTS[index])
	_fill_models()
	if _model_ids.size() > 1:
		popup.add_separator()
		popup.add_submenu_node_item("Model: " + model_name(), _model_menu, MODEL_ID)
	popup.add_separator()
	popup.add_item("Try 3 ways", WAYS_ID)
	popup.set_item_tooltip(
		popup.get_item_index(WAYS_ID),
		"Make this message three different ways side by side, then keep one"
	)
	text = summary()
	_sync_tooltip()
	_fit()


func _fill_models() -> void:
	_model_menu.clear()
	_model_ids.clear()
	_add_choice(_model_menu, _model_ids, "Automatic", "", _selection["model"])
	for item: Dictionary in _models:
		var id := str(item["id"])
		_add_choice(_model_menu, _model_ids, str(item.get("name", id)), id, _selection["model"])
	var saved := str(_selection["model"])
	if not saved.is_empty() and _model(saved).is_empty():
		_add_choice(_model_menu, _model_ids, "Unavailable: " + saved, saved, saved)
		_model_menu.set_item_disabled(_model_menu.item_count - 1, true)


func _add_choice(
	menu: PopupMenu, ids: Array[String], label: String, id: String, chosen: String
) -> void:
	menu.add_radio_check_item(label, ids.size())
	menu.set_item_checked(ids.size(), id == chosen)
	ids.append(id)


func _model(id: String) -> Dictionary:
	if id.is_empty():
		return {}
	for item: Dictionary in _models:
		if str(item["id"]) == id:
			return item
	return {}


func _on_mode(id: int) -> void:
	if id == WAYS_ID:
		try_ways.emit()
	elif id >= 0 and id < MODES.size():
		set_mode(MODES[id])


func _on_model(id: int) -> void:
	if id < 0 or id >= _model_ids.size() or _model_ids[id] == _selection["model"]:
		return
	_selection["model"] = _model_ids[id]
	_selection["effort"] = ""
	_last_reply = ""
	_rebuild()
	changed.emit(selection())


## Width follows the label so the chevron hugs it; past MAX_EM the label ellipsizes.
func _fit() -> void:
	if not is_inside_tree():
		return
	var font := get_theme_font("font")
	var size := get_theme_font_size("font_size")
	var box := get_theme_stylebox("normal")
	var chrome := box.get_minimum_size().x if box != null else 0.0
	if icon != null:
		chrome += icon.get_width() + get_theme_constant("h_separation")
	var width := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
	custom_minimum_size.x = ceilf(minf(width, MAX_EM * size) + chrome)


static func _items(menu: PopupMenu) -> Array[String]:
	var result: Array[String] = []
	for index: int in menu.item_count:
		result.append(menu.get_item_text(index))
	return result
