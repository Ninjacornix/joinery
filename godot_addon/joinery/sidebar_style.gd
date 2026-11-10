@tool
extends RefCounted
## Small styling helpers shared by the sidebar modules. Every value is read from the Joinery
## theme or the ui_theme tokens and re-applied on each theme change, so editor scale, theme
## and accent changes reach these controls live.

const UI_THEME := preload("ui_theme.gd")


## Give [param control] the [param variation] panel with its side padding set to SPACE
## [param step], so text inside a box starts on the same line as text outside boxes.
static func boxed(control: Control, variation: String, step := 2) -> void:
	on_theme_overrides(
		control,
		func() -> void:
			var source := control.get_theme_stylebox("panel", variation)
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			var pad := float(UI_THEME.space(control, step))
			box.content_margin_left = pad
			box.content_margin_right = pad
			control.add_theme_stylebox_override("panel", box)
	)


## Move a flat button's text onto the content edge: its left padding becomes an outward
## expansion of the hover fill, the same treatment the theme gives JoineryPicker.
static func hug_left(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			var variation := button.theme_type_variation
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				button.remove_theme_stylebox_override(state)
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				var source := button.get_theme_stylebox(state, variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				var pad := box.content_margin_left
				box.content_margin_left = 0.0
				if box is StyleBoxFlat:
					(box as StyleBoxFlat).expand_margin_left += pad
				button.add_theme_stylebox_override(state, box)
	)


## A quiet button that reads as a button at rest: a surface_raised fill (a step stronger on
## hover and press) at RADIUS.md, with SPACE.2 padding on both sides. Enabled text is the full
## text colour and hover adds a border_strong hairline, so it reads as clickable. Disabled
## keeps the same shape with the fill at half strength and text_faint text: only the contrast
## drops.
static func filled(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			var raised := button.get_theme_color("surface_raised", "Joinery")
			var ink := button.get_theme_color("text", "Joinery")
			var fills := {
				"normal": raised,
				"hover": raised.lerp(ink, 0.07),
				"pressed": raised.lerp(ink, 0.12),
				"hover_pressed": raised.lerp(ink, 0.12),
				"disabled": Color(raised, raised.a * 0.5),
			}
			var pad := float(UI_THEME.space(button, 2))
			var corner := UI_THEME.radius(button, "md")
			var edge := button.get_theme_color("border_strong", "Joinery")
			var hairline := maxi(roundi(UI_THEME.unit(button)), 1)
			for key: String in [
				"font_color",
				"font_hover_color",
				"font_pressed_color",
				"font_focus_color",
				"font_hover_pressed_color",
				"icon_normal_color",
				"icon_hover_color",
				"icon_pressed_color",
				"icon_focus_color"
			]:
				button.add_theme_color_override(key, ink)
			var faint := button.get_theme_color("text_faint", "Joinery")
			button.add_theme_color_override("font_disabled_color", faint)
			button.add_theme_color_override("icon_disabled_color", faint)
			for state: String in fills:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state, button.theme_type_variation)
				if not source is StyleBoxFlat:
					source = button.get_theme_stylebox("normal", button.theme_type_variation)
				if not source is StyleBoxFlat:
					continue
				var box := source.duplicate() as StyleBoxFlat
				box.bg_color = fills[state]
				box.content_margin_left = pad
				box.content_margin_right = pad
				box.expand_margin_left = 0.0
				box.set_corner_radius_all(corner)
				# Every state draws the same hairline (the fill colour at rest), so hover only
				# changes its colour and nothing moves.
				box.set_border_width_all(hairline)
				box.border_color = edge if state.begins_with("hover") else fills[state]
				button.add_theme_stylebox_override(state, box)
	)


## A filled button sitting on a raised card: its resting fill becomes the dock's ground
## (surface), so it stays visible on the card's surface_raised fill.
static func on_card(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			button.remove_theme_stylebox_override("normal")
			var source := button.get_theme_stylebox("normal", button.theme_type_variation)
			if source is StyleBoxFlat:
				var box := source.duplicate() as StyleBoxFlat
				box.bg_color = button.get_theme_color("surface", "Joinery")
				button.add_theme_stylebox_override("normal", box)
	)


## A square TextureRect for a 16u icon or thumbnail, tinted with the [param role] colour.
static func glyph(role := "text_muted") -> TextureRect:
	var picture := TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	picture.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	picture.set_meta("_joinery_role", role)
	on_theme(
		picture,
		func() -> void:
			var side := float(UI_THEME.space(picture, 4))
			picture.custom_minimum_size = Vector2(side, side)
			var tint := str(picture.get_meta("_joinery_role", ""))
			if not tint.is_empty():
				picture.self_modulate = picture.get_theme_color(tint, "Joinery")
	)
	return picture


## Set a glyph's icon and tint role ("" keeps the texture's own colours, for thumbnails).
static func set_glyph(picture: TextureRect, icon_name: String, role := "text_muted") -> void:
	picture.set_meta("_joinery_role", role)
	picture.set_meta("_joinery_glyph", icon_name)
	if picture.is_inside_tree():
		_apply_glyph(picture)
	if not picture.has_meta("_joinery_glyph_bound"):
		picture.set_meta("_joinery_glyph_bound", true)
		picture.theme_changed.connect(_apply_glyph.bind(picture))


## Run [param apply] now when [param control] is in the tree, and after every theme change.
static func on_theme(control: Control, apply: Callable) -> void:
	if control.is_inside_tree():
		apply.call()
	control.theme_changed.connect(apply)


## Like [method on_theme], for an [param apply] that adds theme overrides to [param control]
## itself: an override re-sends the theme-changed notification, so nested calls are skipped.
static func on_theme_overrides(control: Control, apply: Callable) -> void:
	var guarded := func() -> void:
		if control.has_meta("_joinery_restyling"):
			return
		control.set_meta("_joinery_restyling", true)
		apply.call()
		control.remove_meta("_joinery_restyling")
	on_theme(control, guarded)


static func _apply_glyph(picture: TextureRect) -> void:
	var icon_name := str(picture.get_meta("_joinery_glyph", ""))
	if not icon_name.is_empty():
		picture.texture = UI_THEME.icon(picture, icon_name)
	var role := str(picture.get_meta("_joinery_role", ""))
	picture.self_modulate = (
		picture.get_theme_color(role, "Joinery") if not role.is_empty() else Color.WHITE
	)
