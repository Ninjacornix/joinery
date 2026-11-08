@tool
extends RefCounted
## A selected scene object or file shown above the message. Click it to open the
## asset in its tool's viewer; × removes it from this message.

const UI_THEME := preload("res://addons/joinery/ui_theme.gd")


static func build(
	host: Control, target: Dictionary, opened: Callable, removed: Callable
) -> PanelContainer:
	var font_size := host.get_theme_default_font_size()
	var badge := PanelContainer.new()
	var focus := host.get_theme_color("focus", "Joinery")
	badge.add_theme_stylebox_override(
		"panel",
		UI_THEME.panel(
			host.get_theme_color("inset", "Joinery").lerp(focus, 0.08),
			host.get_theme_color("border", "Joinery").lerp(focus, 0.4),
			4,
			5
		)
	)
	var context := HBoxContainer.new()
	context.add_theme_constant_override("separation", 6)
	context.tooltip_text = "Open %s in its tool" % str(target["label"])
	context.mouse_filter = Control.MOUSE_FILTER_STOP
	context.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	var resource := str(target.get("resource", ""))
	var path := resource if not resource.is_empty() else str(target.get("source", ""))
	context.gui_input.connect(
		func(event: InputEvent) -> void:
			var click := event as InputEventMouseButton
			if (
				click != null
				and click.pressed
				and click.button_index == MOUSE_BUTTON_LEFT
				and not path.is_empty()
			):
				opened.call(path)
	)
	badge.add_child(context)
	var kind_icon := {"Audio": "AudioStreamPlayer", "Object": "PackedScene", "Image": "Image"}.get(
		str(target["kind"]), "File"
	)
	if host.has_theme_icon(kind_icon, "EditorIcons"):
		var icon := TextureRect.new()
		icon.texture = host.get_theme_icon(kind_icon, "EditorIcons")
		icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		icon.custom_minimum_size = Vector2.ONE * font_size
		icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
		context.add_child(icon)
	var label := Label.new()
	label.text = resource.get_file() if not resource.is_empty() else str(target["label"])
	var text_size := int(font_size * 0.85)
	label.add_theme_font_size_override("font_size", text_size)
	label.add_theme_color_override("font_color", host.get_theme_color("text", "Joinery"))
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var measured := (
		host
		. get_theme_default_font()
		. get_string_size(label.text, HORIZONTAL_ALIGNMENT_LEFT, -1, text_size)
		. x
	)
	# The slack covers the label's own padding, so short names never lose letters.
	label.custom_minimum_size.x = clampf(measured, 40, font_size * 12) + font_size * 0.6
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	context.add_child(label)
	var clear := Button.new()
	clear.text = "×"
	clear.flat = true
	clear.theme_type_variation = "JoineryIconButton"
	clear.custom_minimum_size = Vector2.ONE * font_size * 1.5
	clear.tooltip_text = "Clear selection for this message"
	clear.accessibility_name = "Clear target " + str(target["label"])
	clear.pressed.connect(removed)
	context.add_child(clear)
	return badge
