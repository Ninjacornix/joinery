@tool
extends PanelContainer
## One message waiting its turn (sidebar_queue.gd): an outlined card, quieter than a sent
## request, with the message text, Edit (put it back in the message box) and remove.

signal remove_pressed
signal edit_pressed

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")

var text := ""
var targets: Array = []
var text_label: Label
var edit: Button
var remove: Button


func _init(message := "", context: Array = []) -> void:
	text = message
	targets = context.duplicate(true)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Queued message: " + message
	# The request card's outline without its fill, SPACE.2 inside on every side.
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryChangeCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	add_child(row)
	text_label = UI.label(message, "", true)
	text_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	text_label.max_lines_visible = 3
	text_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	text_label.tooltip_text = message
	STYLE.on_theme_overrides(
		text_label,
		func() -> void:
			text_label.add_theme_color_override(
				"font_color", text_label.get_theme_color("text_muted", "Joinery")
			)
	)
	row.add_child(text_label)
	edit = UI.quiet("Edit")
	edit.tooltip_text = "Put this back in the message box"
	edit.accessibility_name = "Edit queued message"
	UI.bind_height(edit, "sm")
	edit.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	edit.pressed.connect(func() -> void: edit_pressed.emit())
	row.add_child(edit)
	remove = UI.icon_button("close", "Remove from the queue")
	remove.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	STYLE.on_theme_overrides(
		remove,
		func() -> void:
			var side := float(UI_THEME.control_h(remove, "sm"))
			remove.custom_minimum_size = Vector2(side, side)
	)
	remove.pressed.connect(func() -> void: remove_pressed.emit())
	row.add_child(remove)


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[text_label, "body"]]
