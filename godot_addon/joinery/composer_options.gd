@tool
extends MenuButton
## The composer's one options picker. Closed, it reads as the effective choice ("Auto",
## "Thorough · GPT-6.1 Sol · High") with a chevron that hugs the text. Its menu holds the
## modes as radio items, then "Model" and "Effort" submenus when the provider offers a choice.
## A saved model or effort the provider no longer lists stays visible as "Unavailable: …".

signal changed(selection: Dictionary)

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const MODES := ["auto", "fast", "thorough"]
const MODE_NAMES := ["Auto", "Fast", "Thorough"]
const MODE_HINTS := ["Adapts to your request", "Favours short replies", "Favours complex work"]
## Closed label width cap, in multiples of the font size.
const MAX_EM := 16.0
const MODEL_ID := 100
const EFFORT_ID := 200

var _models: Array = []
var _selection: Dictionary = {"mode": "auto", "model": "", "effort": ""}
var _model_menu: PopupMenu
var _effort_menu: PopupMenu
## Choices in the submenus, by item index: model ids and effort values.
var _model_ids: Array[String] = []
var _effort_ids: Array[String] = []


func _init() -> void:
	theme_type_variation = "JoineryPicker"
	flat = false
	clip_text = true
	text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	alignment = HORIZONTAL_ALIGNMENT_LEFT
	focus_mode = Control.FOCUS_ALL
	accessibility_name = "Reply mode and model"
	tooltip_text = "Reply mode and model"
	UI.bind_icon(self, "chevron")
	size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_model_menu = PopupMenu.new()
	_model_menu.id_pressed.connect(_on_model)
	_effort_menu = PopupMenu.new()
	_effort_menu.id_pressed.connect(_on_effort)
	get_popup().id_pressed.connect(_on_mode)
	_rebuild()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_fit()
	elif what == NOTIFICATION_PREDELETE:
		for menu: PopupMenu in [_model_menu, _effort_menu]:
			if is_instance_valid(menu) and menu.get_parent() == null:
				menu.free()


## Models reported by the provider: [{id, name, efforts?}].
func set_models(models: Array) -> void:
	_models = []
	for item: Variant in models:
		if item is Dictionary and not str(item.get("id", "")).is_empty():
			_models.append(item)
	_rebuild()


## Restore a saved {mode, model, effort} without emitting [signal changed].
func restore(selection: Dictionary) -> void:
	_selection = {
		"mode": str(selection.get("mode", "auto")),
		"model": str(selection.get("model", "")),
		"effort": str(selection.get("effort", "")),
	}
	if not _selection["mode"] in MODES:
		_selection["mode"] = "auto"
	_rebuild()


## The current {mode, model, effort}.
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
	if not str(event.get("effort", "")).is_empty():
		parts.append(str(event["effort"]).capitalize())
	tooltip_text = "Last reply: " + " · ".join(parts)


## The model submenu entries (model names, "Automatic" first), for tests and captures.
func model_items() -> Array[String]:
	return _items(_model_menu)


## The effort submenu entries ("Automatic" first).
func effort_items() -> Array[String]:
	return _items(_effort_menu)


## The closed label: the mode, then the chosen model and effort.
func summary() -> String:
	var parts: Array[String] = [MODE_NAMES[MODES.find(_selection["mode"])]]
	var model := str(_selection["model"])
	if not model.is_empty():
		var found := _model(model)
		parts.append(str(found.get("name", model)) if not found.is_empty() else model)
	var effort := str(_selection["effort"])
	if not effort.is_empty():
		parts.append(effort.capitalize())
	return " · ".join(parts)


func _rebuild() -> void:
	var popup := get_popup()
	for menu: PopupMenu in [_model_menu, _effort_menu]:
		if menu.get_parent() != null:
			menu.get_parent().remove_child(menu)
	popup.clear()
	for index: int in MODES.size():
		popup.add_radio_check_item(MODE_NAMES[index], index)
		popup.set_item_checked(index, MODES[index] == _selection["mode"])
		popup.set_item_tooltip(index, MODE_HINTS[index])
	_fill_models()
	_fill_efforts()
	var show_models := _model_ids.size() > 1
	var show_efforts := _effort_ids.size() > 1
	if show_models or show_efforts:
		popup.add_separator()
	if show_models:
		popup.add_submenu_node_item("Model", _model_menu, MODEL_ID)
	if show_efforts:
		popup.add_submenu_node_item("Effort", _effort_menu, EFFORT_ID)
	text = summary()
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


func _fill_efforts() -> void:
	_effort_menu.clear()
	_effort_ids.clear()
	var saved := str(_selection["effort"])
	var efforts: Array = _model(str(_selection["model"])).get("efforts", [])
	if efforts.is_empty() and saved.is_empty():
		return
	_add_choice(_effort_menu, _effort_ids, "Automatic", "", saved)
	for value: Variant in efforts:
		_add_choice(_effort_menu, _effort_ids, str(value).capitalize(), str(value), saved)
	if not saved.is_empty() and not efforts.has(saved):
		_add_choice(_effort_menu, _effort_ids, "Unavailable: " + saved, saved, saved)
		_effort_menu.set_item_disabled(_effort_menu.item_count - 1, true)


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
	if id >= 0 and id < MODES.size():
		set_mode(MODES[id])


func _on_model(id: int) -> void:
	if id < 0 or id >= _model_ids.size() or _model_ids[id] == _selection["model"]:
		return
	_selection["model"] = _model_ids[id]
	_selection["effort"] = ""
	_rebuild()
	changed.emit(selection())


func _on_effort(id: int) -> void:
	if id < 0 or id >= _effort_ids.size() or _effort_ids[id] == _selection["effort"]:
		return
	_selection["effort"] = _effort_ids[id]
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
