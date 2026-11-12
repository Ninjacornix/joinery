@tool
extends VBoxContainer
## What a scene variant overrides, under its picture in the review (ADR 0066): one line per
## overridden property ("Sprite · modulate", the base's value, then the variant's), a colour
## swatch beside colour values, then the nodes and connections it adds. Only differences are
## listed; a variant that changes nothing yet says so. Long lists stop at MAX_ROWS with a
## count of the rest, which Details' file diff holds in full.
##
## The item's `variant` is {base, overrides: [{node, property, base, value}],
## added: [{node, type}], connections: [text], count}.
##
## It also has the variant's pictures drawn (review_scene_shot.gd, [method draw_pictures]):
## an item whose pictures can't be drawn gets their `error` instead of its before and after,
## and [signal pictures_failed] asks the stage to show it again.

## A variant's pictures couldn't be drawn; its item now carries the reason as `error`.
signal pictures_failed

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const SCENE_SHOT := preload("review_scene_shot.gd")
## Rows shown before "and N more".
const MAX_ROWS := 8
## Characters of a value before it is shortened.
const VALUE_CHARS := 36

var _head: Label
var _rows: VBoxContainer
var _more: Label
## The variant on show, so the same one isn't rebuilt.
var _shown := {}
var _shots: SCENE_SHOT


func _init() -> void:
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "What the variant changes"
	_head = UI.label("", "JoinerySection")
	add_child(_head)
	_rows = VBoxContainer.new()
	_rows.theme_type_variation = "JoineryTight"
	add_child(_rows)
	_more = UI.label("", "JoineryCaption")
	add_child(_more)
	_shots = SCENE_SHOT.new()
	_shots.failed.connect(_on_failed)
	add_child(_shots)
	hide()


## Draw the pictures of the scene variants among [param items] whose files aren't there yet.
func draw_pictures(items: Array) -> void:
	for item: Variant in items:
		if not item is Dictionary or not item.has("scenes") or item.has("error"):
			continue
		for side: String in ["before", "after"]:
			if item.has(side) and not FileAccess.file_exists(str(item[side])):
				_shots.request(item)
				break


## The review closed: drop pictures still to draw and hide.
func forget() -> void:
	_shots.cancel()
	show_variant({})


## The pictures drawer (review_scene_shot.gd).
func shots() -> SCENE_SHOT:
	return _shots


## Show [param variant] (an item's `variant`), or hide when it is empty.
func show_variant(variant: Dictionary) -> void:
	if variant.is_empty():
		_shown = {}
		hide()
		return
	if variant == _shown and visible:
		return
	_shown = variant.duplicate(true)
	var base := str(variant.get("base", "")).get_file()
	_head.text = "Differs from %s" % base
	_head.tooltip_text = str(variant.get("base", ""))
	for child: Node in _rows.get_children():
		_rows.remove_child(child)
		child.queue_free()
	var lines: Array[Control] = []
	for entry: Variant in variant.get("overrides", []):
		if entry is Dictionary:
			lines.append(_override_row(entry))
	for entry: Variant in variant.get("added", []):
		if entry is Dictionary:
			lines.append(_added_row(str(entry.get("node", "")), str(entry.get("type", ""))))
	for entry: Variant in variant.get("connections", []):
		lines.append(_added_row(str(entry), "connection"))
	var total := maxi(int(variant.get("count", lines.size())), lines.size())
	for index: int in mini(lines.size(), MAX_ROWS):
		_rows.add_child(lines[index])
	for index: int in range(MAX_ROWS, lines.size()):
		lines[index].free()
	_more.visible = total > MAX_ROWS or lines.is_empty()
	if lines.is_empty():
		_more.text = "Nothing yet: it plays exactly like %s." % base
	else:
		_more.text = "and %d more in Details" % (total - MAX_ROWS)
	var was_hidden := not visible
	show()
	if was_hidden:
		UI_MOTION.stagger(_rows.get_children())


## The rows on show, as plain text ("Sprite · modulate: Color(1, 1, 1, 1) → …"), for tests
## and the screen reader.
func row_texts() -> PackedStringArray:
	var found := PackedStringArray()
	for row: Node in _rows.get_children():
		found.append(str(row.get_meta("text", "")))
	return found


func _override_row(entry: Dictionary) -> Control:
	var node := str(entry.get("node", "."))
	var where := "Root" if node == "." else node.get_file()
	var property := str(entry.get("property", ""))
	var old := str(entry.get("base", ""))
	var new := str(entry.get("value", ""))
	var old_said := str(entry.get("base_said", old))
	var new_said := str(entry.get("said", new))
	if old_said.is_empty():
		old_said = "default"
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	var name := UI.label("%s · %s" % [where, property])
	name.tooltip_text = node if node != "." else "The variant's root"
	name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	name.size_flags_stretch_ratio = 0.9
	row.add_child(name)
	var values := HBoxContainer.new()
	values.theme_type_variation = "JoineryRowTight"
	values.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	values.alignment = BoxContainer.ALIGNMENT_END
	row.add_child(values)
	_value(values, old, old_said, "JoineryCaption")
	values.add_child(UI.label("→", "JoineryCaption"))
	_value(values, new, new_said, "JoineryNumeric")
	var text := "%s: %s → %s" % [name.text, old_said, new_said]
	row.set_meta("text", text)
	row.accessibility_name = text
	return row


func _added_row(what: String, kind: String) -> Control:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	var name := UI.label("Adds " + (what.get_file() if kind != "connection" else what))
	name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	name.tooltip_text = what
	row.add_child(name)
	if kind != "connection":
		row.add_child(UI.label(kind.get_file().get_basename(), "JoineryCaption"))
	var text := "Adds %s (%s)" % [what, kind]
	row.set_meta("text", text)
	row.accessibility_name = text
	return row


## One value: a swatch for a colour, then how it reads ([param text]), shortened, with the
## value as Godot writes it ([param raw]) in the tooltip.
func _value(box: HBoxContainer, raw: String, text: String, variation: String) -> void:
	var colour: Variant = _colour(raw)
	if colour is Color:
		var swatch := Panel.new()
		var style := StyleBoxFlat.new()
		style.bg_color = colour
		style.set_corner_radius_all(UI_THEME.radius(self, "xs"))
		style.border_color = Color(0.5, 0.5, 0.5, 0.5)
		style.set_border_width_all(1)
		swatch.add_theme_stylebox_override("panel", style)
		swatch.custom_minimum_size = Vector2.ONE * UI_THEME.dot_px(self) * 1.5
		swatch.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		box.add_child(swatch)
	var shown := text if text.length() <= VALUE_CHARS else text.left(VALUE_CHARS - 1) + "…"
	var label := UI.label(shown, variation)
	label.tooltip_text = raw if not raw.is_empty() else "The engine's default"
	box.add_child(label)


func _on_failed(item: Dictionary, reason: String) -> void:
	item.erase("before")
	item.erase("after")
	item["error"] = reason
	pictures_failed.emit()


static func _colour(text: String) -> Variant:
	if not text.begins_with("Color("):
		return null
	var value: Variant = str_to_var(text)
	return value if value is Color else null
