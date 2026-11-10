@tool
extends RefCounted
## Builds the Joinery Theme from palette colours and size tokens. Internal to ui_theme.gd:
## screens call ui_theme.build(), never this script directly.

const UI_THEME := preload("ui_theme.gd")


## The full Theme: palette roles under "Joinery", base types and every type variation listed
## in ui_theme.build().
static func theme(context: Node, c: Dictionary) -> Theme:
	var t := Theme.new()
	for role: String in c:
		t.set_color(role, "Joinery", c[role])
	var m := _metrics(context)
	_text_types(t, c, m, context)
	_inputs(t, c, m)
	_buttons(t, c, m)
	# Collapsible section headers read exactly like the plain JoinerySection labels.
	t.set_font("font", "JoinerySectionButton", bold_font(context))
	_pickers(t, c, m, context)
	_switch(t, c, m)
	_checks(t, c, m)
	_scrollbars(t, c, m)
	_panels(t, c, m)
	_lists(t, c, m)
	return t


## The theme's default font for [param context] (the fallback font when headless).
static func default_font(context: Node) -> Font:
	if context is Control:
		return (context as Control).get_theme_default_font()
	if context is Window:
		return (context as Window).get_theme_default_font()
	return ThemeDB.fallback_font


## The editor bold font, or an emboldened default font.
static func bold_font(context: Node) -> Font:
	if context is Control and (context as Control).has_theme_font("bold", "EditorFonts"):
		return (context as Control).get_theme_font("bold", "EditorFonts")
	var bold := FontVariation.new()
	bold.base_font = default_font(context)
	bold.variation_embolden = 0.6
	return bold


static func _metrics(context: Node) -> Dictionary:
	var size := UI_THEME.font_size(context, "body")
	var u := maxf(size / 14.0, 1.0)
	var font := default_font(context)
	var line := font.get_height(size)
	var heights := {}
	for name: String in UI_THEME.CONTROL_H:
		heights[name] = roundf(size * float(UI_THEME.CONTROL_H[name]))
	return {
		"size": size,
		"u": u,
		"font": font,
		"line": line,
		"h": heights,
		"icon": roundf(int(UI_THEME.ICON["sm"]) * u),
		"ring": maxf(1.0, roundf(2 * u)),
	}


static func _sp(m: Dictionary, step: int) -> float:
	return roundf(int(UI_THEME.SPACE[step]) * float(m["u"]))


static func _rad(m: Dictionary, name: String) -> int:
	return roundi(int(UI_THEME.RADIUS[name]) * float(m["u"]))


## Transparent margin to the right of the chevron glyphs in icons/ui (about 4 of 16 units).
static func _glyph_inset(m: Dictionary) -> float:
	return roundf(4 * float(m["u"]))


static func _pad_v(m: Dictionary, height: String) -> float:
	return maxf(0.0, (float(m["h"][height]) - float(m["line"])) / 2.0)


static func _box(
	fill: Color, border: Color, width: float, corner: int, pad_h: float, pad_v: float
) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = fill
	box.border_color = border
	box.set_border_width_all(int(width))
	box.set_corner_radius_all(corner)
	box.content_margin_left = pad_h
	box.content_margin_right = pad_h
	box.content_margin_top = pad_v
	box.content_margin_bottom = pad_v
	return box


## The one field look shared by LineEdit, TextEdit and OptionButton: sunken fill, a hairline
## border, radius md and SPACE.3 side insets, so every input on every screen matches.
static func field_box(
	c: Dictionary, m: Dictionary, border: Color, fill_role := "surface_sunken"
) -> StyleBoxFlat:
	return _box(
		c[fill_role], border, maxf(1.0, roundf(m["u"])), _rad(m, "md"), _sp(m, 3), _pad_v(m, "md")
	)


static func _empty(pad_h: float, pad_v: float) -> StyleBoxEmpty:
	var box := StyleBoxEmpty.new()
	box.content_margin_left = pad_h
	box.content_margin_right = pad_h
	box.content_margin_top = pad_v
	box.content_margin_bottom = pad_v
	return box


## Focus ring drawn outside the control: 2u wide, expand 2u, radius md+2. Accent-filled
## controls get a 2u gap (expand 4u) so the ring stays visible against the fill.
static func _ring(c: Dictionary, m: Dictionary, gap := false) -> StyleBoxFlat:
	var expand: float = m["ring"] * (2.0 if gap else 1.0)
	var box := StyleBoxFlat.new()
	box.draw_center = false
	box.border_color = c["accent"]
	box.set_border_width_all(int(m["ring"]))
	box.set_corner_radius_all(_rad(m, "md") + int(expand))
	box.set_expand_margin_all(expand)
	return box


static func _text_types(t: Theme, c: Dictionary, m: Dictionary, context: Node) -> void:
	var size: int = m["size"]
	t.set_stylebox("normal", "Label", StyleBoxEmpty.new())
	t.set_color("font_color", "Label", c["text"])
	var bold := bold_font(context)
	var semibold := FontVariation.new()
	semibold.base_font = m["font"]
	semibold.variation_embolden = 0.35
	var caption := roundi(size * float(UI_THEME.TYPE["caption"]))
	var labels := {
		"JoineryCaption": [caption, c["text_muted"], null],
		"JoineryFaint": [caption, c["text_faint"], null],
		"JoineryBodyLarge": [roundi(size * float(UI_THEME.TYPE["body_lg"])), c["text"], semibold],
		"JoineryTitle": [roundi(size * float(UI_THEME.TYPE["title"])), c["text"], bold],
		"JoineryDisplay": [roundi(size * float(UI_THEME.TYPE["display"])), c["text"], bold],
		"JoineryError": [size, c["danger"], null],
		"JoineryNumeric": [caption, c["text_muted"], UI_THEME.numeric_font(context)],
		"JoinerySection": [caption, c["text_muted"], bold],
		"JoineryMono": [size, c["text"], UI_THEME.mono_font(context)],
	}
	for type_name: String in labels:
		var spec: Array = labels[type_name]
		t.set_type_variation(type_name, "Label")
		t.set_font_size("font_size", type_name, spec[0])
		t.set_color("font_color", type_name, spec[1])
		if spec[2] != null:
			t.set_font("font", type_name, spec[2])
	t.set_stylebox("normal", "RichTextLabel", StyleBoxEmpty.new())
	t.set_stylebox("focus", "RichTextLabel", StyleBoxEmpty.new())
	t.set_color("default_color", "RichTextLabel", c["text"])
	t.set_color("selection_color", "RichTextLabel", c["accent_tint"])
	# Pending text (a request still sending, or not sent): faint, but still 4.5:1.
	t.set_type_variation("JoineryFaintRichText", "RichTextLabel")
	t.set_color("default_color", "JoineryFaintRichText", c["text_faint"])
	t.set_font("mono_font", "RichTextLabel", UI_THEME.mono_font(context))
	t.set_font("font", "CodeEdit", UI_THEME.mono_font(context))
	t.set_constant("line_separation", "RichTextLabel", roundi(3 * float(m["u"])))
	t.set_constant("paragraph_separation", "RichTextLabel", int(_sp(m, 2)))
	t.set_color("font_color", "TooltipLabel", c["text"])
	t.set_stylebox(
		"panel",
		"TooltipPanel",
		_box(c["surface_raised"], c["border_subtle"], 1, 0, _sp(m, 2), _sp(m, 1))
	)
	for sep: String in ["HSeparator", "VSeparator"]:
		var line := StyleBoxLine.new()
		line.color = c["border_subtle"]
		line.thickness = maxi(1, roundi(float(m["u"])))
		line.vertical = sep == "VSeparator"
		t.set_stylebox("separator", sep, line)
		t.set_constant("separation", sep, int(_sp(m, 2)))
	var boxes := {
		"JoineryTight": 1,
		"JoineryRowTight": 1,
		"JoineryFlush": 0,
		"JoineryRow": 2,
		"JoineryStack": 3,
		"JoinerySections": 5,
	}
	for type_name: String in boxes:
		t.set_type_variation(type_name, "BoxContainer")
		var step: int = boxes[type_name]
		t.set_constant("separation", type_name, int(_sp(m, step)) if step > 0 else 0)
	# A split between two columns (asset list | detail) keeps one SPACE.3 gutter.
	t.set_constant("separation", "HSplitContainer", int(_sp(m, 3)))
	t.set_type_variation("JoineryInset", "MarginContainer")
	t.set_type_variation("JoineryPage", "MarginContainer")
	t.set_type_variation("JoineryGutter", "MarginContainer")
	for side: String in ["left", "right", "top", "bottom"]:
		var inset := _sp(m, 2) if side in ["left", "right"] else 0.0
		t.set_constant("margin_" + side, "JoineryInset", int(inset))
		t.set_constant("margin_" + side, "JoineryPage", int(_sp(m, 3)))
		t.set_constant("margin_" + side, "JoineryGutter", int(_sp(m, 3) if side == "right" else 0))


static func _inputs(t: Theme, c: Dictionary, m: Dictionary) -> void:
	for type_name: String in [
		"LineEdit", "TextEdit", "JoineryLineEditError", "JoineryTextEditError"
	]:
		var error := type_name.ends_with("Error")
		var border: Color = c["danger"] if error else c["border_strong"]
		t.set_stylebox("normal", type_name, field_box(c, m, border))
		t.set_stylebox("read_only", type_name, field_box(c, m, c["border_subtle"], "surface"))
		t.set_stylebox("focus", type_name, _ring(c, m))
		t.set_color("font_color", type_name, c["text"])
		t.set_color("font_placeholder_color", type_name, c["text_faint"])
		t.set_color("font_readonly_color", type_name, c["text_muted"])
		t.set_color("font_uneditable_color", type_name, c["text_muted"])
		t.set_color("font_selected_color", type_name, c["text"])
		t.set_color("caret_color", type_name, c["accent"])
		t.set_color("selection_color", type_name, c["accent_tint"])
		t.set_color("clear_button_color", type_name, c["text_muted"])
		t.set_color("clear_button_color_pressed", type_name, c["text"])
	t.set_type_variation("JoineryLineEditError", "LineEdit")
	t.set_type_variation("JoineryTextEditError", "TextEdit")
	t.set_constant("line_spacing", "TextEdit", roundi(3 * float(m["u"])))
	t.set_color("current_line_color", "CodeEdit", Color.TRANSPARENT)


static func _look(c: Dictionary, m: Dictionary, overrides: Dictionary) -> Dictionary:
	var look := {
		"fill": Color.TRANSPARENT,
		"hover": c["surface_raised"],
		"pressed": c["surface_raised"],
		"disabled": Color.TRANSPARENT,
		"border": Color.TRANSPARENT,
		"width": 0.0,
		"radius": _rad(m, "md"),
		"pad_h": _sp(m, 3),
		"pad_v": _pad_v(m, "md"),
		"ink": c["text"],
		"ink_hover": c["text"],
		"ink_pressed": c["text"],
		"ink_disabled": c["text_faint"],
		"disabled_border": Color.TRANSPARENT,
		"icon": c["text_muted"],
		"icon_hover": c["text"],
		"expand_left": 0.0,
		"expand_right": 0.0,
		"ring_gap": false,
	}
	look.merge(overrides, true)
	return look


static func _button_type(
	t: Theme, type_name: String, c: Dictionary, m: Dictionary, look: Dictionary
) -> void:
	var states := {
		"normal": [look["fill"], look["ink"], look["icon"]],
		"hover": [look["hover"], look["ink_hover"], look["icon_hover"]],
		"pressed": [look["pressed"], look["ink_pressed"], look["icon_hover"]],
		"hover_pressed": [look["pressed"], look["ink_pressed"], look["icon_hover"]],
		"disabled": [look["disabled"], look["ink_disabled"], look["ink_disabled"]],
	}
	for state: String in states:
		var spec: Array = states[state]
		var box := _box(
			spec[0], look["border"], look["width"], look["radius"], look["pad_h"], look["pad_v"]
		)
		box.content_margin_left = look["pad_h"] - float(look["expand_left"])
		box.expand_margin_left = look["expand_left"]
		box.content_margin_right = look["pad_h"] - float(look["expand_right"])
		box.expand_margin_right = look["expand_right"]
		if state == "disabled" and look["width"] > 0:
			box.border_color = c["border_subtle"]
		elif state == "disabled" and look["disabled_border"].a > 0.0:
			# An accent action that cannot act yet: a neutral raised fill with a hairline, so it
			# reads as off rather than as a faded accent; the border sits inside the insets.
			box.border_color = look["disabled_border"]
			box.set_border_width_all(int(maxf(1.0, roundf(m["u"]))))
		t.set_stylebox(state, type_name, box)
		var prefix := "font_" + ("" if state == "normal" else state + "_")
		t.set_color(prefix + "color", type_name, spec[1])
		var icon_name := "icon_" + state + "_color"
		t.set_color(icon_name, type_name, spec[2])
	t.set_color("font_focus_color", type_name, look["ink"])
	t.set_color("icon_focus_color", type_name, look["icon"])
	t.set_stylebox("focus", type_name, _ring(c, m, look["ring_gap"]))
	t.set_constant("h_separation", type_name, int(_sp(m, 2)))
	t.set_constant("icon_max_width", type_name, int(m["icon"]))


static func _buttons(t: Theme, c: Dictionary, m: Dictionary) -> void:
	var raised: Color = c["surface_raised"]
	var secondary := {
		"fill": raised,
		"hover": raised.lerp(c["text"], 0.07),
		"pressed": raised.lerp(c["text"], 0.12),
		"border": c["border_subtle"],
		"icon": c["text"],
	}
	var square := (float(m["h"]["md"]) - float(m["icon"])) / 2.0
	var send_pad := (roundf(UI_THEME.SEND * float(m["u"])) - float(m["icon"])) / 2.0
	var types := {
		"Button": secondary,
		"JoinerySecondaryButton": secondary,
		"JoineryPrimaryButton":
		{
			"fill": c["accent"],
			"hover": c["accent_hover"],
			"pressed": c["accent_pressed"],
			"ink": c["on_accent"],
			"ink_hover": c["on_accent"],
			"ink_pressed": c["on_accent"],
			"icon": c["on_accent"],
			"icon_hover": c["on_accent"],
			"disabled": raised,
			"disabled_border": c["border_subtle"],
			"ink_disabled": c["text_muted"],
			"ring_gap": true,
		},
		"JoineryLinkButton":
		{
			"hover": Color.TRANSPARENT,
			"pressed": Color.TRANSPARENT,
			"pad_h": 0.0,
			"ink": c["accent"],
			"ink_hover": c["accent_hover"],
			"ink_pressed": c["accent_pressed"],
			"icon": c["accent"],
			"icon_hover": c["accent_hover"],
		},
		"JoineryQuietButton": {"ink": c["text_muted"], "pad_h": _sp(m, 2)},
		# A disclosure toggle ("Details"): no fill in any state, so an open toggle never looks
		# pressed, and no side inset, so its glyph starts on the content edge.
		"JoineryDisclosureButton":
		{
			"hover": Color.TRANSPARENT,
			"pressed": Color.TRANSPARENT,
			"pad_h": 0.0,
			"ink": c["text_muted"],
			"ink_hover": c["text"],
			"ink_pressed": c["text"],
			"icon": c["text_muted"],
			"icon_hover": c["text"],
		},
		"JoineryRowButton": {"pad_h": _sp(m, 2)},
		"JoineryIconButton": {"pad_h": square, "pad_v": square, "icon": c["text_muted"]},
		"JoinerySendButton":
		{
			"fill": c["accent"],
			"hover": c["accent_hover"],
			"pressed": c["accent_pressed"],
			"disabled": raised,
			"disabled_border": c["border_subtle"],
			"ink_disabled": c["text_muted"],
			"pad_h": send_pad,
			"pad_v": send_pad,
			"icon": c["on_accent"],
			"icon_hover": c["on_accent"],
			"ink": c["on_accent"],
			"ring_gap": true,
		},
		"JoineryStopButton":
		{
			"fill": raised,
			"hover": raised.lerp(c["text"], 0.07),
			"border": c["border_strong"],
			"width": maxf(1.0, roundf(m["u"])),
			"pad_h": send_pad - maxf(1.0, roundf(m["u"])),
			"pad_v": send_pad - maxf(1.0, roundf(m["u"])),
			"icon": c["text"],
		},
		"JoinerySegment":
		{
			"ink": c["text_muted"],
			"pressed": c["accent_tint"],
			"pad_h": _sp(m, 3),
			"pad_v": _pad_v(m, "sm"),
			"radius": _rad(m, "sm"),
		},
		"JoineryCardButton":
		{
			"fill": c["surface"],
			"hover": raised,
			"pressed": c["accent_tint"],
			"border": c["border_subtle"],
			"width": maxf(1.0, roundf(m["u"])),
			"radius": _rad(m, "lg"),
			"icon": c["text"],
		},
		"JoineryChipClose":
		{
			"pad_h": 0.0,
			"pad_v": 0.0,
			"radius": _rad(m, "xs"),
			"icon": c["text_muted"],
		},
		"JoinerySectionButton":
		{
			"hover": Color.TRANSPARENT,
			"pressed": Color.TRANSPARENT,
			"pad_h": 0.0,
			"ink": c["text_muted"],
			"ink_hover": c["text"],
			"pad_v": _pad_v(m, "xs"),
		},
	}
	for type_name: String in types:
		if type_name != "Button":
			t.set_type_variation(type_name, "Button")
		_button_type(t, type_name, c, m, _look(c, m, types[type_name]))
	var fill_pressed: Color = c["accent_tint"]
	var selected := _box(
		fill_pressed,
		c["accent"],
		roundf(2 * float(m["u"])),
		_rad(m, "lg"),
		_sp(m, 3),
		_pad_v(m, "md")
	)
	# Hovering the selected card keeps its accent border.
	t.set_stylebox("pressed", "JoineryCardButton", selected)
	t.set_stylebox("hover_pressed", "JoineryCardButton", selected)
	t.set_font_size(
		"font_size", "JoinerySectionButton", roundi(m["size"] * float(UI_THEME.TYPE["caption"]))
	)
	for type_name: String in ["CheckBox", "MenuButton", "LinkButton"]:
		var flat := {"pad_h": _sp(m, 1) if type_name == "MenuButton" else 0.0}
		if type_name == "CheckBox":
			# The box icon shows the state; a checked row draws no fill behind it.
			flat["pressed"] = Color.TRANSPARENT
		_button_type(t, type_name, c, m, _look(c, m, flat))


static func _pickers(t: Theme, c: Dictionary, m: Dictionary, context: Node) -> void:
	var arrow := UI_THEME.icon(context, "chevron", c["text_muted"])
	# A form OptionButton is a field: the same box as LineEdit and TextEdit, a stronger border
	# on hover.
	var sunken: Color = c["surface_sunken"]
	var base := {
		"fill": sunken,
		"hover": sunken,
		"pressed": sunken,
		"disabled": c["surface"],
		"border": c["border_strong"],
		"width": maxf(1.0, roundf(m["u"])),
		"icon": c["text"],
	}
	_button_type(t, "OptionButton", c, m, _look(c, m, base))
	for state: String in ["hover", "pressed", "hover_pressed"]:
		var box := t.get_stylebox(state, "OptionButton") as StyleBoxFlat
		box.border_color = c["text_muted"]
	# The picker reads as plain text until pointed at: no inset on either side (the hover fill
	# extends outside instead), so its text lines up with labels and its chevron ends on the
	# row's trailing edge, like switches and row-button chevrons.
	var quiet := {
		"pad_h": _sp(m, 1),
		"pad_v": _pad_v(m, "sm"),
		"expand_left": _sp(m, 1),
		"expand_right": _sp(m, 1),
	}
	t.set_type_variation("JoineryPicker", "OptionButton")
	_button_type(t, "JoineryPicker", c, m, _look(c, m, quiet))
	for type_name: String in ["OptionButton", "JoineryPicker"]:
		t.set_icon("arrow", type_name, arrow)
		t.set_constant("modulate_arrow", type_name, 0)
		# OptionButton: the chevron ink sits SPACE.3 in, level with the text inset.
		var margin := _sp(m, 3) - _glyph_inset(m) if type_name == "OptionButton" else 0.0
		t.set_constant("arrow_margin", type_name, int(margin))
		t.set_color("icon_normal_color", type_name, c["text_muted"])


static func _switch(t: Theme, c: Dictionary, m: Dictionary) -> void:
	t.set_type_variation("JoinerySwitch", "CheckButton")
	var size: int = m["size"]
	var width := roundi(2.0 * size)
	var height := roundi(1.1 * size)
	var on := [c["accent"], c["on_accent"]]
	var off := [c["border_strong"], c["surface"]]
	var icons := {}
	for k: int in UI_THEME.SWITCH_FRAMES + 1:
		var progress := float(k) / UI_THEME.SWITCH_FRAMES
		var stretch := 0.12 * sin(PI * progress)
		var track: Color = (off[0] as Color).lerp(on[0], progress)
		var knob: Color = (off[1] as Color).lerp(on[1], progress)
		icons["frame_%d" % k] = switch_texture(width, height, progress, track, knob, stretch)
	icons["checked"] = icons["frame_%d" % UI_THEME.SWITCH_FRAMES]
	icons["unchecked"] = icons["frame_0"]
	icons["checked_disabled"] = switch_texture(width, height, 1.0, on[0], on[1], 0.0, 0.45)
	icons["unchecked_disabled"] = switch_texture(width, height, 0.0, off[0], off[1], 0.0, 0.45)
	for type_name: String in ["CheckButton", "JoinerySwitch"]:
		for name: String in icons:
			t.set_icon(name, type_name, icons[name])
			if not name.begins_with("frame"):
				t.set_icon(name + "_mirrored", type_name, icons[name])
		# The track ends where a chevron glyph's ink ends, so every trailing control in a
		# settings row shares one right edge.
		var pad := _empty(0, _pad_v(m, "md"))
		pad.content_margin_right = _glyph_inset(m)
		for state: String in ["normal", "hover", "pressed", "hover_pressed", "disabled"]:
			t.set_stylebox(state, type_name, pad)
		t.set_stylebox("focus", type_name, _ring(c, m))
		t.set_constant("h_separation", type_name, int(_sp(m, 2)))
		# CheckButton applies the Button icon cap to its switch, squashing the track.
		t.set_constant("icon_max_width", type_name, 0)
		for state: String in ["", "hover_", "pressed_", "hover_pressed_", "focus_"]:
			t.set_color("font_" + state + "color", type_name, c["text"])
		t.set_color("font_disabled_color", type_name, c["text_faint"])


static func _panels(t: Theme, c: Dictionary, m: Dictionary) -> void:
	var hairline := maxf(1.0, roundf(m["u"]))
	var tile_pad := roundf(5 * float(m["u"]))
	var panels := {
		"JoineryCard": [c["surface"], c["border_subtle"], hairline, "lg", _sp(m, 3), _sp(m, 3)],
		"JoineryCardSelected":
		[c["accent_tint"], c["accent"], roundf(2 * float(m["u"])), "lg", _sp(m, 3), _sp(m, 3)],
		"JoineryBanner": [c["danger_tint"], Color.TRANSPARENT, 0.0, "md", _sp(m, 3), _sp(m, 3)],
		# Half a step vertically, so a 24 px remove target still fits CONTROL_H.sm.
		"JoineryChip": [c["accent_tint"], Color.TRANSPARENT, 0.0, "md", _sp(m, 2), _sp(m, 1) / 2.0],
		"JoineryPopup":
		[c["surface_raised"], c["border_subtle"], hairline, "lg", _sp(m, 1), _sp(m, 1)],
		# A card that explains something beside its trigger (the rail's readiness card).
		"JoineryHoverCard":
		[c["surface_raised"], c["border_subtle"], hairline, "lg", _sp(m, 3), _sp(m, 3)],
		"JoineryComposer":
		[c["surface_sunken"], c["border_strong"], hairline, "xl", _sp(m, 2), _sp(m, 2)],
		"JoineryComposerFocused":
		[c["surface_sunken"], c["accent"], hairline, "xl", _sp(m, 2), _sp(m, 2)],
		"JoineryBubble": [c["surface_raised"], Color.TRANSPARENT, 0.0, "xl", _sp(m, 3), _sp(m, 3)],
		"JoineryChangeCard":
		[c["surface_raised"], Color.TRANSPARENT, 0.0, "lg", _sp(m, 3), _sp(m, 3)],
		"JoineryMediaFrame": [c["surface_sunken"], Color.TRANSPARENT, 0.0, "md", 0.0, 0.0],
		"JoineryLoadingFrame":
		[c["surface_raised"], Color.TRANSPARENT, 0.0, "md", _sp(m, 3), _sp(m, 3)],
		"JoineryIconTile": [c["surface_raised"], Color.TRANSPARENT, 0.0, "xs", tile_pad, tile_pad],
		"JoineryDock": [c["surface"], Color.TRANSPARENT, 0.0, "", _sp(m, 3), _sp(m, 3)],
	}
	for type_name: String in panels:
		var spec: Array = panels[type_name]
		var corner := 0 if spec[3] == "" else _rad(m, spec[3])
		t.set_type_variation(type_name, "PanelContainer")
		t.set_stylebox(
			"panel", type_name, _box(spec[0], spec[1], spec[2], corner, spec[4], spec[5])
		)
	t.set_stylebox("panel", "ScrollContainer", StyleBoxEmpty.new())
	t.set_stylebox("focus", "ScrollContainer", StyleBoxEmpty.new())


static func _lists(t: Theme, c: Dictionary, m: Dictionary) -> void:
	# Popup menus and tooltips are separate windows; their corners can't show what is behind
	# them, so round corners left black wedges. They stay square.
	var popup := _box(c["surface_raised"], c["border_subtle"], 1, 0, _sp(m, 1), _sp(m, 1))
	t.set_stylebox("panel", "PopupMenu", popup)
	t.set_stylebox(
		"hover", "PopupMenu", _box(c["accent_tint"], Color.TRANSPARENT, 0, _rad(m, "md"), 0, 0)
	)
	var line := StyleBoxLine.new()
	line.color = c["border_subtle"]
	t.set_stylebox("separator", "PopupMenu", line)
	t.set_stylebox("labeled_separator_left", "PopupMenu", line)
	t.set_stylebox("labeled_separator_right", "PopupMenu", line)
	t.set_color("font_color", "PopupMenu", c["text"])
	t.set_color("font_hover_color", "PopupMenu", c["text"])
	t.set_color("font_disabled_color", "PopupMenu", c["text_faint"])
	t.set_color("font_separator_color", "PopupMenu", c["text_muted"])
	t.set_color("font_accelerator_color", "PopupMenu", c["text_muted"])
	t.set_constant("v_separation", "PopupMenu", int(_sp(m, 2)))
	t.set_constant("h_separation", "PopupMenu", int(_sp(m, 2)))
	t.set_constant("item_start_padding", "PopupMenu", int(_sp(m, 2)))
	t.set_constant("item_end_padding", "PopupMenu", int(_sp(m, 2)))
	t.set_color("font_color", "ItemList", c["text"])
	t.set_color("font_hovered_color", "ItemList", c["text"])
	t.set_color("font_selected_color", "ItemList", c["text"])
	t.set_stylebox(
		"panel",
		"ItemList",
		_box(c["surface_sunken"], Color.TRANSPARENT, 0, _rad(m, "md"), _sp(m, 1), _sp(m, 1))
	)
	var selected := _box(
		c["accent_tint"], Color.TRANSPARENT, 0, _rad(m, "md"), _sp(m, 1), _sp(m, 1)
	)
	t.set_stylebox("selected", "ItemList", selected)
	t.set_stylebox("selected_focus", "ItemList", selected)
	t.set_stylebox(
		"hovered",
		"ItemList",
		_box(c["surface_raised"], Color.TRANSPARENT, 0, _rad(m, "md"), _sp(m, 1), _sp(m, 1))
	)
	t.set_stylebox("focus", "ItemList", _ring(c, m))
	t.set_stylebox("cursor", "ItemList", StyleBoxEmpty.new())
	t.set_stylebox("cursor_unfocused", "ItemList", StyleBoxEmpty.new())
	t.set_constant("v_separation", "ItemList", int(_sp(m, 2)))
	t.set_constant("h_separation", "ItemList", int(_sp(m, 2)))
	var track := _box(
		c["border_subtle"], Color.TRANSPARENT, 0, _rad(m, "full"), 0, roundf(2 * float(m["u"]))
	)
	var filled := _box(
		c["accent"], Color.TRANSPARENT, 0, _rad(m, "full"), 0, roundf(2 * float(m["u"]))
	)
	t.set_stylebox("slider", "HSlider", track)
	t.set_stylebox("grabber_area", "HSlider", filled)
	t.set_stylebox("grabber_area_highlight", "HSlider", filled)


## A switch track and knob at [param progress] (0 off, 1 on), anti-aliased, with the knob
## stretched horizontally by [param stretch] (motion rule 5).
static func switch_texture(
	width: int,
	height: int,
	progress: float,
	track: Color,
	knob: Color,
	stretch: float,
	alpha := 1.0
) -> ImageTexture:
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	var half := height / 2.0
	var inset := maxf(2.0, roundf(height * 0.14))
	var knob_r := half - inset
	var knob_half := knob_r * (1.0 + stretch)
	var x := lerpf(inset + knob_half, width - inset - knob_half, progress)
	for py: int in height:
		for px: int in width:
			var point := Vector2(px + 0.5, py + 0.5)
			var track_d := _capsule(point, Vector2(width / 2.0, half), width / 2.0 - half, half)
			var knob_d := _capsule(point, Vector2(x, half), knob_half - knob_r, knob_r)
			var under := Color(track, track.a * alpha * clampf(0.5 - track_d, 0.0, 1.0))
			var over := Color(knob, knob.a * alpha * clampf(0.5 - knob_d, 0.0, 1.0))
			image.set_pixel(px, py, under.blend(over))
	return ImageTexture.create_from_image(image)


## Check boxes and radio buttons drawn from the palette, so an unchecked box reads on every
## surface: a border_strong outline (3:1) on the sunken fill; checked fills (box) or rings and
## dots (radio) in accent. Disabled states are the same shapes at reduced opacity.
static func _checks(t: Theme, c: Dictionary, m: Dictionary) -> void:
	var side := roundi(float(m["icon"]))
	var stroke := maxf(1.0, roundf(1.5 * float(m["u"])))
	var icons := {
		"unchecked": check_texture(side, false, false, c, stroke),
		"checked": check_texture(side, false, true, c, stroke),
		"radio_unchecked": check_texture(side, true, false, c, stroke),
		"radio_checked": check_texture(side, true, true, c, stroke),
	}
	for name: String in icons.keys():
		var radio := name.begins_with("radio")
		var on := name.ends_with("_checked") or name == "checked"
		icons[name + "_disabled"] = check_texture(side, radio, on, c, stroke, 0.45)
	for name: String in icons:
		t.set_icon(name, "CheckBox", icons[name])


## One check box or radio icon, [param side] pixels square, anti-aliased.
static func check_texture(
	side: int, radio: bool, on: bool, c: Dictionary, stroke: float, alpha := 1.0
) -> ImageTexture:
	var image := Image.create(side, side, false, Image.FORMAT_RGBA8)
	var half := side / 2.0
	var center := Vector2(half, half)
	var outline: Color = c["accent"] if on else c["border_strong"]
	var fill: Color = c["accent"] if on and not radio else c["surface_sunken"]
	var corner := maxf(2.0, roundf(side * 0.2))
	# The check mark: two strokes through these points (fractions of the side).
	var tick := [Vector2(0.27, 0.53), Vector2(0.43, 0.69), Vector2(0.74, 0.35)]
	for py: int in side:
		for px: int in side:
			var point := Vector2(px + 0.5, py + 0.5)
			var shape_d: float
			if radio:
				shape_d = point.distance_to(center) - (half - 0.5)
			else:
				var q := (point - center).abs() - Vector2(half - 0.5 - corner, half - 0.5 - corner)
				shape_d = Vector2(maxf(q.x, 0.0), maxf(q.y, 0.0)).length() - corner
			var inside := clampf(0.5 - shape_d, 0.0, 1.0)
			var ring := clampf(0.5 - absf(shape_d + stroke / 2.0) + stroke / 2.0, 0.0, 1.0)
			var colour := Color(fill, fill.a * inside).blend(Color(outline, outline.a * ring))
			if on and radio:
				var dot_d := point.distance_to(center) - side * 0.22
				colour = colour.blend(Color(c["accent"], clampf(0.5 - dot_d, 0.0, 1.0)))
			elif on:
				var mark_d := minf(
					_segment(point, tick[0] * side, tick[1] * side),
					_segment(point, tick[1] * side, tick[2] * side)
				)
				var mark := clampf(0.5 - (mark_d - stroke * 0.6), 0.0, 1.0)
				colour = colour.blend(Color(c["on_accent"], mark))
			colour.a *= alpha
			image.set_pixel(px, py, colour)
	return ImageTexture.create_from_image(image)


## Scroll bars: a thin border_strong thumb (3:1 on every surface) on a clear track, darker
## while pointed at or dragged.
static func _scrollbars(t: Theme, c: Dictionary, m: Dictionary) -> void:
	# Half the bar's width: 6u in all.
	var thickness := roundf(3 * float(m["u"]))
	var states := {
		"grabber": c["border_strong"],
		"grabber_highlight": c["text_muted"],
		"grabber_pressed": c["text"],
	}
	for type_name: String in ["VScrollBar", "HScrollBar"]:
		var track := _empty(thickness, thickness)
		t.set_stylebox("scroll", type_name, track)
		t.set_stylebox("scroll_focus", type_name, track)
		for state: String in states:
			var thumb := _box(
				states[state], Color.TRANSPARENT, 0, _rad(m, "full"), thickness, thickness
			)
			t.set_stylebox(state, type_name, thumb)


## Distance from [param point] to the segment [param a]–[param b].
static func _segment(point: Vector2, a: Vector2, b: Vector2) -> float:
	var along := clampf((point - a).dot(b - a) / (b - a).length_squared(), 0.0, 1.0)
	return point.distance_to(a + (b - a) * along)


## Signed distance from [param point] to a horizontal capsule.
static func _capsule(point: Vector2, center: Vector2, half_length: float, round_r: float) -> float:
	var dx := maxf(absf(point.x - center.x) - half_length, 0.0)
	return Vector2(dx, point.y - center.y).length() - round_r
