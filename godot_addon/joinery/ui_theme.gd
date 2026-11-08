@tool
extends RefCounted
## Local native-control tokens. Keep Godot's fonts, scaling and input semantics.


static func palette(context: Control) -> Dictionary:
	var dark := true
	if context.has_theme_color("base_color", "Editor"):
		dark = context.get_theme_color("base_color", "Editor").get_luminance() < 0.45
	return {
		"surface": Color("202123") if dark else Color("f7f6f3"),
		"inset": Color("18191b") if dark else Color("ffffff"),
		"raised": Color("2b2d30") if dark else Color("eeece6"),
		"text": Color("eeede8") if dark else Color("25262a"),
		"secondary": Color("b9b9b3") if dark else Color("575950"),
		"border": Color("777a82") if dark else Color("85877f"),
		"accent": Color("e6b66b") if dark else Color("875309"),
		"primary": Color("e6b66b") if dark else Color("875309"),
		"on_primary": Color("222018") if dark else Color("ffffff"),
	}


static func panel(
	color: Color, border: Color, padding: float, radius: float, width: int = 1
) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = color
	box.border_color = border
	box.set_border_width_all(width)
	box.set_corner_radius_all(int(radius))
	box.set_content_margin_all(padding)
	return box


static func build(context: Control) -> Theme:
	var colors := palette(context)
	var result := Theme.new()
	var unit := maxf(context.get_theme_default_font_size() / 14.0, 1.0)
	result.default_font_size = context.get_theme_default_font_size()
	for name: String in colors:
		result.set_color(name, "Joinery", colors[name])
	result.set_color("font_color", "Label", colors["text"])
	result.set_color("default_color", "RichTextLabel", colors["text"])
	result.set_color("font_color", "LineEdit", colors["text"])
	result.set_color("font_placeholder_color", "LineEdit", colors["secondary"])
	result.set_color("caret_color", "LineEdit", colors["accent"])
	result.set_stylebox(
		"normal", "LineEdit", panel(colors["inset"], colors["border"], 10 * unit, 8 * unit)
	)
	var focus := panel(Color.TRANSPARENT, colors["accent"], 0, 8 * unit, maxi(2, int(2 * unit)))
	result.set_stylebox("focus", "LineEdit", focus)
	result.set_stylebox("focus", "Button", focus)
	result.set_stylebox("focus", "ItemList", focus)
	result.set_stylebox(
		"normal", "RichTextLabel", panel(colors["inset"], Color.TRANSPARENT, 12 * unit, 8 * unit, 0)
	)
	result.set_constant("line_separation", "RichTextLabel", int(3 * unit))
	result.set_constant("paragraph_separation", "RichTextLabel", int(8 * unit))
	for state: String in ["normal", "hover", "pressed", "disabled"]:
		var fill: Color = colors["raised"] if state in ["hover", "pressed"] else colors["surface"]
		result.set_stylebox(state, "Button", panel(fill, colors["border"], 9 * unit, 7 * unit))
		result.set_color("font_" + state + "_color", "Button", colors["text"])
	result.set_color("font_color", "Button", colors["text"])
	result.set_color("font_disabled_color", "Button", colors["secondary"])
	result.set_type_variation("JoineryPrimaryButton", "Button")
	result.set_stylebox(
		"focus",
		"JoineryPrimaryButton",
		panel(Color.TRANSPARENT, colors["on_primary"], 0, 7 * unit, maxi(2, int(2 * unit)))
	)
	for state: String in ["normal", "hover", "pressed"]:
		var fill: Color = colors["primary"]
		if state == "hover":
			fill = fill.lightened(0.08)
		elif state == "pressed":
			fill = fill.darkened(0.08)
		result.set_stylebox(state, "JoineryPrimaryButton", panel(fill, fill, 9 * unit, 7 * unit))
		result.set_color("font_" + state + "_color", "JoineryPrimaryButton", colors["on_primary"])
	result.set_color("font_color", "JoineryPrimaryButton", colors["on_primary"])
	result.set_color("font_color", "ItemList", colors["text"])
	result.set_color("font_selected_color", "ItemList", colors["text"])
	result.set_stylebox(
		"panel", "ItemList", panel(colors["inset"], Color.TRANSPARENT, 4 * unit, 6 * unit, 0)
	)
	var selected: Color = colors["inset"].lerp(colors["accent"], 0.18)
	result.set_stylebox(
		"selected", "ItemList", panel(selected, Color.TRANSPARENT, 4 * unit, 4 * unit, 0)
	)
	result.set_stylebox(
		"selected_focus", "ItemList", panel(selected, colors["accent"], 4 * unit, 4 * unit)
	)
	result.set_constant("v_separation", "ItemList", int(8 * unit))
	return result
