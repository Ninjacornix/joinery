@tool
extends BoxContainer
## The middle of the asset page (tool_workspace.gd): the preview, with the model's animations
## under it, and the parts list (parts_panel.gd) with a picked map above it. Wide, the list is
## a column beside the preview and fills its height; narrow, it sits under the preview, as
## tall as its rows, so the preview stays the largest thing on the page.

const UI_THEME := preload("ui_theme.gd")
## The list goes beside the preview from this many ems wide, in a column this many ems wide.
const SIDE_FROM_EM := 48.0
const SIDE_EM := 21.0

## The preview's own children are the workspace's: a model view, image, sound or empty state.
var preview: VBoxContainer
## The parts column; shown while the list has rows.
var side: VBoxContainer
var _parts: Control


func _init(parts: Control) -> void:
	_parts = parts
	vertical = true
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	column.size_flags_vertical = Control.SIZE_EXPAND_FILL
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(column)
	preview = VBoxContainer.new()
	preview.theme_type_variation = "JoineryTight"
	preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	preview.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(preview)
	column.add_child(parts.get("animations"))
	side = VBoxContainer.new()
	side.theme_type_variation = "JoineryTight"
	side.hide()
	add_child(side)
	side.add_child(parts.get("map_view"))
	side.add_child(parts)
	resized.connect(_layout)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		# An override changes the theme again: set it only when it differs.
		var gap := UI_THEME.space(self, 3)
		if get_theme_constant("separation") != gap:
			add_theme_constant_override("separation", gap)
		_layout()


## Show the parts column when the list has something to show.
func show_side(on: bool) -> void:
	side.visible = on
	_layout()


func _layout() -> void:
	var em := float(UI_THEME.font_size(self, "body"))
	var wide := size.x >= em * SIDE_FROM_EM
	if vertical == wide:
		vertical = not wide
	side.custom_minimum_size.x = roundf(em * SIDE_EM) if wide else 0.0
	side.size_flags_vertical = Control.SIZE_EXPAND_FILL if wide else Control.SIZE_FILL
	if _parts.get("fill") != wide:
		_parts.set("fill", wide)
