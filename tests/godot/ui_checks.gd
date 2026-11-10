extends RefCounted
## Automated design checks run on whatever Joinery screen is currently shown.
##
## Walk the visible Control tree below one or more roots and report:
##   clipped   text cut short, trimmed with an ellipsis or drawn past its rect
##   contrast  WCAG contrast below 4.5:1 (body) or 3:1 (large text)
##   overflow  a control reaching outside its parent or the window
##   target    an interactive control smaller than 24x24 logical pixels
## Each finding prints one line: "UI-CHECK <screen> <kind> <node path> <detail>".
## Sizes are compared in logical pixels: the editor's interface scale is removed.
##
## Two things are allowed on purpose and never reported:
##   - text trimmed with an ellipsis when the control (or a near ancestor) carries the full
##     text as a tooltip, such as a long file name on a chip;
##   - ALLOWED entries: stock Godot controls Joinery does not style, each with its reason.

const MIN_TARGET := 24.0
const BODY_CONTRAST := 4.5
const LARGE_CONTRAST := 3.0
const TOLERANCE := 1.0
## Known findings in stock Godot UI, as {kind, class, text, reason}, and optionally the class
## of an ancestor the control must sit in ("inside").
const ALLOWED := [
	{
		"kind": "contrast",
		"class": "Button",
		"text": "Perspective",
		"reason": "Stock Godot 3D viewport menu; Joinery does not restyle the viewport chrome",
	},
	{
		"kind": "overflow",
		"class": "SceneTreeEditor",
		"text": "",
		"reason": "Stock Godot Scene dock: its tree draws into the dock margin by design",
	},
	{
		"kind": "overflow",
		"class": "Tree",
		"text": "",
		"inside": "FileSystemDock",
		"reason": "Stock Godot FileSystem dock in a side column: its tree fills the dock margin",
	},
	{
		"kind": "overflow",
		"class": "EditorInspector",
		"text": "",
		"reason": "Stock Godot Inspector in a side column: it fills the dock margin by design",
	},
]
## How far up the tree a tooltip may sit and still explain an ellipsis.
const TOOLTIP_DEPTH := 4
const INTERACTIVE := [
	"BaseButton", "LineEdit", "TextEdit", "Slider", "SpinBox", "ItemList", "Tree", "TabBar"
]
const BACKGROUND_STYLES := {
	"PanelContainer": "panel",
	"Panel": "panel",
	"TabContainer": "panel",
	"ScrollContainer": "panel",
	"ItemList": "panel",
	"Tree": "panel",
	"PopupPanel": "panel",
	"Label": "normal",
	"LineEdit": "normal",
	"TextEdit": "normal",
	"RichTextLabel": "normal",
}

## The rendered frame of the screen being checked, when one is available.
## Contrast reads the real background from it; styleboxes are the fallback.
static var _frame: Image
static var _frame_viewport: Viewport


## Check every visible control under `roots` and print one line per finding.
## `window` bounds the overflow check; an empty rect uses each root's viewport.
## `frame` is the rendered image of `viewport`, used to read real backgrounds.
static func run(
	screen: String,
	roots: Array,
	window: Rect2 = Rect2(),
	frame: Image = null,
	viewport: Viewport = null
) -> Array[String]:
	_frame = frame
	_frame_viewport = viewport
	var findings: Array[String] = []
	for root: Variant in roots:
		if not root is Node or not is_instance_valid(root):
			continue
		var bounds := window
		if bounds.size == Vector2.ZERO:
			bounds = (root as Node).get_viewport().get_visible_rect()
		_walk(screen, root, root, bounds, Rect2(-1e7, -1e7, 2e7, 2e7), findings)
	_frame = null
	_frame_viewport = null
	for line: String in findings:
		print(line)
	print("UI-CHECKED %s %d" % [screen, findings.size()])
	return findings


static func _walk(
	screen: String, root: Node, node: Node, window: Rect2, clip: Rect2, findings: Array[String]
) -> void:
	if node != root and node is Window:
		return  # Separate windows are separate screens.
	if node is CanvasItem and not (node as CanvasItem).visible:
		return
	var inner := clip
	if node is Control:
		var control := node as Control
		var rect := control.get_global_rect()
		if rect.size.x <= 0.0 or rect.size.y <= 0.0:
			return
		if not rect.intersects(clip, true):
			return  # Scrolled out of view: not shown, not judged.
		if control.modulate.a <= 0.01 or control.self_modulate.a <= 0.01:
			return
		_check(screen, root, control, window, clip, findings)
		if control.clip_contents or control is ScrollContainer:
			inner = clip.intersection(rect)
	for child: Node in node.get_children():
		_walk(screen, root, child, window, inner, findings)


static func _check(
	screen: String, root: Node, control: Control, window: Rect2, clip: Rect2, found: Array[String]
) -> void:
	var path := _path(root, control)
	for detail: String in _clipping(control):
		if not (detail.ends_with("(ellipsis)") and _has_tooltip(control)):
			found.append(_line(screen, "clipped", path, detail))
	var contrast := _contrast(control)
	if not contrast.is_empty() and not _allowed("contrast", control):
		found.append(_line(screen, "contrast", path, contrast))
	var overflow := _overflow(control, window, clip)
	if not overflow.is_empty() and not _allowed("overflow", control):
		found.append(_line(screen, "overflow", path, overflow))
	var target := _target(control)
	if not target.is_empty():
		found.append(_line(screen, "target", path, target))


## True when the control or one of its near ancestors shows its full text as a tooltip.
static func _has_tooltip(control: Control) -> bool:
	var node: Node = control
	for _level: int in TOOLTIP_DEPTH:
		if node is Control and not (node as Control).tooltip_text.strip_edges().is_empty():
			return true
		node = node.get_parent()
		if node == null:
			break
	return false


static func _allowed(kind: String, control: Control) -> bool:
	var text := ""
	if control is Button:
		text = (control as Button).text
	elif control is Label:
		text = (control as Label).text
	for entry: Dictionary in ALLOWED:
		if entry["kind"] == kind and control.is_class(entry["class"]) and entry["text"] == text:
			if not entry.has("inside") or _inside(control, str(entry["inside"])):
				return true
	return false


static func _inside(control: Control, type: String) -> bool:
	var node := control.get_parent()
	while node != null:
		if node.is_class(type):
			return true
		node = node.get_parent()
	return false


static func _line(screen: String, kind: String, path: String, detail: String) -> String:
	return "UI-CHECK %s %s %s %s" % [screen, kind, path, detail.replace("\n", " ")]


## A stable, readable path: generated names become Class[index among same class].
static func _path(root: Node, node: Node) -> String:
	var parts: Array[String] = []
	var current := node
	while current != null:
		var label := str(current.name)
		if label.begins_with("@") or label.is_empty():
			var index := 0
			var parent := current.get_parent()
			if parent != null:
				for sibling: Node in parent.get_children(true):
					if sibling == current:
						break
					if sibling.get_class() == current.get_class():
						index += 1
			label = "%s[%d]" % [current.get_class(), index]
		parts.push_front(label.replace(" ", "_"))
		if current == root:
			break
		current = current.get_parent()
	return "/".join(parts)


static func scale_of(control: Control) -> float:
	var value := control.get_theme_default_base_scale()
	return value if value > 0.0 else 1.0


static func _quote(text: String) -> String:
	var short := text.replace("\n", "⏎").strip_edges()
	if short.length() > 48:
		short = short.substr(0, 47) + "…"
	return '"' + short + '"'


# --- clipped text ----------------------------------------------------------------


static func _clipping(control: Control) -> Array[String]:
	var result: Array[String] = []
	if control is Label:
		var detail := _label_clipping(control as Label)
		if not detail.is_empty():
			result.append(detail)
	elif control is Button:
		var detail := _button_clipping(control as Button)
		if not detail.is_empty():
			result.append(detail)
	elif control is ItemList:
		result.append_array(_item_list_clipping(control as ItemList))
	return result


static func _text_width(control: Control, text: String, font_type: String = "") -> float:
	var font := control.get_theme_font("font", font_type)
	var size := control.get_theme_font_size("font_size", font_type)
	if font == null:
		return 0.0
	var widest := 0.0
	for line: String in text.split("\n"):
		widest = maxf(widest, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	return widest


static func _label_clipping(label: Label) -> String:
	var text := label.text.to_upper() if label.uppercase else label.text
	if text.strip_edges().is_empty() or label.visible_ratio <= 0.0:
		return ""
	if label.autowrap_mode != TextServer.AUTOWRAP_OFF:
		var lines := label.get_line_count()
		var shown := label.get_visible_line_count()
		if label.max_lines_visible < 0 and shown < lines:
			return "%s shows %d of %d lines" % [_quote(text), shown, lines]
		return ""
	var style := label.get_theme_stylebox("normal")
	var available := label.size.x
	if style != null:
		available -= style.get_margin(SIDE_LEFT) + style.get_margin(SIDE_RIGHT)
	var font := label.get_theme_font("font")
	var size := label.get_theme_font_size("font_size")
	if label.label_settings != null:
		if label.label_settings.font != null:
			font = label.label_settings.font
		size = label.label_settings.font_size
	if font == null:
		return ""
	var needed := 0.0
	for line: String in text.split("\n"):
		needed = maxf(needed, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	if needed <= available + TOLERANCE:
		return ""
	var how := (
		"cut"
		if label.clip_text
		else (
			"ellipsis"
			if label.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING
			else "drawn outside"
		)
	)
	return "%s needs %dpx, has %dpx (%s)" % [_quote(text), ceili(needed), floori(available), how]


static func _button_clipping(button: Button) -> String:
	var text := button.text
	if button is OptionButton:
		var picker := button as OptionButton
		if picker.selected >= 0:
			text = picker.get_item_text(picker.selected)
	if text.strip_edges().is_empty() or button.autowrap_mode != TextServer.AUTOWRAP_OFF:
		return ""
	var style := button.get_theme_stylebox("normal")
	var available := button.size.x
	if style != null:
		available -= style.get_margin(SIDE_LEFT) + style.get_margin(SIDE_RIGHT)
	if button.icon != null:
		var icon_width := float(button.icon.get_width())
		var max_width := button.get_theme_constant("icon_max_width")
		if max_width > 0:
			icon_width = minf(icon_width, max_width)
		if button.expand_icon:
			icon_width = minf(icon_width, button.size.y)
		available -= icon_width + button.get_theme_constant("h_separation")
	if button is OptionButton:
		var arrow := button.get_theme_icon("arrow")
		if arrow != null:
			available -= arrow.get_width() + button.get_theme_constant("arrow_margin")
	var needed := _text_width(button, text)
	if needed <= available + TOLERANCE:
		return ""
	var how := (
		"cut"
		if button.clip_text
		else (
			"ellipsis"
			if button.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING
			else "drawn outside"
		)
	)
	return "%s needs %dpx, has %dpx (%s)" % [_quote(text), ceili(needed), floori(available), how]


static func _item_list_clipping(list: ItemList) -> Array[String]:
	var result: Array[String] = []
	if list.max_columns != 1 or list.fixed_column_width > 0:
		return result
	var style := list.get_theme_stylebox("panel")
	var available := list.size.x - list.get_theme_constant("h_separation")
	if style != null:
		available -= style.get_margin(SIDE_LEFT) + style.get_margin(SIDE_RIGHT)
	var bar := list.get_v_scroll_bar()
	if bar != null and bar.visible:
		available -= bar.size.x
	for index: int in list.item_count:
		var text := list.get_item_text(index)
		var needed := _text_width(list, text)
		var icon := list.get_item_icon(index)
		if icon != null:
			needed += icon.get_width() + list.get_theme_constant("h_separation")
		if needed > available + TOLERANCE:
			result.append(
				(
					"item %d %s needs %dpx, has %dpx"
					% [index, _quote(text), ceili(needed), floori(available)]
				)
			)
	return result


# --- contrast --------------------------------------------------------------------


static func _foreground(control: Control) -> Dictionary:
	## Return {"color", "size", "bold", "text"} for a control that draws text.
	var text := ""
	var color := Color.TRANSPARENT
	var font := control.get_theme_font("font")
	var size := control.get_theme_font_size("font_size")
	if control is Label:
		var label := control as Label
		text = label.text
		color = label.get_theme_color("font_color")
		if label.label_settings != null:
			color = label.label_settings.font_color
			size = label.label_settings.font_size
			if label.label_settings.font != null:
				font = label.label_settings.font
	elif control is Button:
		var button := control as Button
		if button.disabled:
			return {}  # WCAG exempts inactive controls.
		text = button.text
		if button is OptionButton and (button as OptionButton).selected >= 0:
			var picker := button as OptionButton
			text = picker.get_item_text(picker.selected)
		var state := "font_color"
		if button.button_pressed and button.toggle_mode:
			state = "font_pressed_color"
		elif button.has_focus():
			state = "font_focus_color"
		color = button.get_theme_color(state)
	elif control is LineEdit:
		var line := control as LineEdit
		text = line.text
		color = line.get_theme_color("font_color")
	elif control is RichTextLabel:
		var rich := control as RichTextLabel
		text = rich.get_parsed_text()
		color = rich.get_theme_color("default_color")
		font = rich.get_theme_font("normal_font")
		size = rich.get_theme_font_size("normal_font_size")
	if text.strip_edges().is_empty():
		return {}
	var bold := font != null and (font.get_font_style() & TextServer.FONT_BOLD) != 0
	return {"color": color, "size": size, "bold": bold, "text": text}


static func _drawn_background(control: Control) -> Color:
	## The fill a control paints behind its content, or transparent.
	var style_name := ""
	if control is Button:
		var button := control as Button
		if button.flat:
			return Color.TRANSPARENT
		style_name = "normal"
		if button.disabled:
			style_name = "disabled"
		elif button.button_pressed and button.toggle_mode:
			style_name = "pressed"
	else:
		for type_name: String in BACKGROUND_STYLES:
			if control.is_class(type_name):
				style_name = BACKGROUND_STYLES[type_name]
				break
	if style_name.is_empty() or not control.has_theme_stylebox(style_name):
		return Color.TRANSPARENT
	var style := control.get_theme_stylebox(style_name)
	if style is StyleBoxFlat and (style as StyleBoxFlat).draw_center:
		var fill := (style as StyleBoxFlat).bg_color
		fill.a *= control.self_modulate.a
		return fill
	return Color.TRANSPARENT


static func background(control: Control) -> Color:
	var sampled := _sampled_background(control)
	if sampled.a > 0.0:
		return sampled
	## Without a frame, composite the nearest panels until an opaque fill is reached.
	var layers: Array[Color] = []
	var node: Node = control
	while node != null:
		if node is Control:
			var fill := _drawn_background(node as Control)
			if fill.a > 0.0:
				layers.append(fill)
				if fill.a >= 0.999:
					break
		if node is Window:
			var window := node as Window
			if window.has_theme_stylebox("panel"):
				var style := window.get_theme_stylebox("panel")
				if style is StyleBoxFlat and (style as StyleBoxFlat).draw_center:
					layers.append((style as StyleBoxFlat).bg_color)
			break
		node = node.get_parent()
	var result := RenderingServer.get_default_clear_color()
	result.a = 1.0
	for index: int in range(layers.size() - 1, -1, -1):
		result = result.blend(layers[index])
	return result


## The most common colour inside the control's rect in the rendered frame: the
## fill actually behind its text, whatever drew it (siblings, window panels).
static func _sampled_background(control: Control) -> Color:
	if _frame == null or control.get_viewport() != _frame_viewport:
		return Color.TRANSPARENT
	var bounds := Rect2(Vector2.ZERO, Vector2(_frame.get_size()))
	var rect := control.get_global_rect().intersection(bounds)
	if rect.size.x < 2.0 or rect.size.y < 2.0:
		return Color.TRANSPARENT
	var votes := {}
	var columns := mini(48, int(rect.size.x))
	var rows := mini(16, int(rect.size.y))
	for row: int in rows:
		for column: int in columns:
			var x := int(rect.position.x + (column + 0.5) * rect.size.x / columns)
			var y := int(rect.position.y + (row + 0.5) * rect.size.y / rows)
			var pixel := _frame.get_pixel(
				clampi(x, 0, _frame.get_width() - 1), clampi(y, 0, _frame.get_height() - 1)
			)
			var key := pixel.to_html(false)
			votes[key] = int(votes.get(key, 0)) + 1
	var best := ""
	for key: String in votes:
		if best.is_empty() or int(votes[key]) > int(votes[best]):
			best = key
	return Color.html(best)


static func _linear(channel: float) -> float:
	if channel <= 0.04045:
		return channel / 12.92
	return pow((channel + 0.055) / 1.055, 2.4)


static func luminance(color: Color) -> float:
	return 0.2126 * _linear(color.r) + 0.7152 * _linear(color.g) + 0.0722 * _linear(color.b)


static func contrast_ratio(first: Color, second: Color) -> float:
	var a := luminance(first)
	var b := luminance(second)
	return (maxf(a, b) + 0.05) / (minf(a, b) + 0.05)


static func _contrast(control: Control) -> String:
	var foreground := _foreground(control)
	if foreground.is_empty():
		return ""
	var back := background(control)
	var ink: Color = foreground["color"]
	var alpha := ink.a
	var node: Node = control
	while node is CanvasItem:
		alpha *= (node as CanvasItem).modulate.a
		node = node.get_parent()
	alpha *= control.self_modulate.a
	ink.a = clampf(alpha, 0.0, 1.0)
	var shown := back.blend(ink)
	var ratio := contrast_ratio(shown, back)
	var logical := float(foreground["size"]) / scale_of(control)
	var large := logical >= 24.0 or (bool(foreground["bold"]) and logical >= 18.66)
	var required := LARGE_CONTRAST if large else BODY_CONTRAST
	if ratio + 0.005 >= required:
		return ""
	return (
		"%s %.2f:1 < %.1f:1 (text #%s on #%s, %dpx)"
		% [
			_quote(str(foreground["text"])),
			ratio,
			required,
			shown.to_html(false),
			back.to_html(false),
			roundi(logical),
		]
	)


# --- overflow --------------------------------------------------------------------


static func _overflow(control: Control, window: Rect2, clip: Rect2) -> String:
	var rect := control.get_global_rect()
	var parent := control.get_parent() as Control
	if parent != null and not control.top_level and not parent is ScrollContainer:
		var outer := parent.get_global_rect()
		var spill := _spill(rect, outer)
		if spill != Vector2.ZERO:
			return (
				"%s %s past parent %s by %dx%dpx"
				% [
					control.get_class(),
					_describe(control),
					parent.get_class(),
					int(spill.x),
					int(spill.y),
				]
			)
	var shown := rect.intersection(clip)
	if shown.size == Vector2.ZERO:
		return ""
	var outside := _spill(shown, window)
	if outside != Vector2.ZERO:
		return (
			"%s %s past window by %dx%dpx"
			% [control.get_class(), _describe(control), int(outside.x), int(outside.y)]
		)
	return ""


static func _spill(inner: Rect2, outer: Rect2) -> Vector2:
	var left := maxf(0.0, outer.position.x - inner.position.x)
	var right := maxf(0.0, inner.end.x - outer.end.x)
	var top := maxf(0.0, outer.position.y - inner.position.y)
	var bottom := maxf(0.0, inner.end.y - outer.end.y)
	var result := Vector2(maxf(left, right), maxf(top, bottom))
	if result.x <= TOLERANCE:
		result.x = 0.0
	if result.y <= TOLERANCE:
		result.y = 0.0
	return result.round()


static func _describe(control: Control) -> String:
	var text := ""
	if control is Label:
		text = (control as Label).text
	elif control is Button:
		text = (control as Button).text
	return _quote(text) if not text.strip_edges().is_empty() else ""


# --- target size -----------------------------------------------------------------


static func _target(control: Control) -> String:
	if control.mouse_filter == Control.MOUSE_FILTER_IGNORE:
		return ""
	if control is ScrollBar:
		return ""
	var interactive := false
	for type_name: String in INTERACTIVE:
		if control.is_class(type_name):
			interactive = true
			break
	if not interactive:
		return ""
	var logical := control.size / scale_of(control)
	if logical.x + 0.01 >= MIN_TARGET and logical.y + 0.01 >= MIN_TARGET:
		return ""
	return (
		"%s %s is %dx%d logical px (minimum %dx%d)"
		% [
			control.get_class(),
			_describe(control),
			roundi(logical.x),
			roundi(logical.y),
			int(MIN_TARGET),
			int(MIN_TARGET),
		]
	)


# --- capture ---------------------------------------------------------------------


## Save `viewport` as <screen>.png in JOINERY_CAPTURE_DIR, then check `roots`.
## Prints "VISUAL-CAPTURE <file>" so the runner can confirm every screen exists.
static func snapshot(screen: String, viewport: Viewport, roots: Array) -> bool:
	var frame: Image = null
	var directory := OS.get_environment("JOINERY_CAPTURE_DIR")
	if not directory.is_empty() and DisplayServer.get_name() != "headless":
		DirAccess.make_dir_recursive_absolute(directory)
		RenderingServer.force_draw()
		var image := viewport.get_texture().get_image()
		if image != null and not image.is_empty():
			var file := screen + ".png"
			if image.save_png(directory.path_join(file)) == OK:
				print("VISUAL-CAPTURE ", file)
				frame = image
	if frame == null:
		print("VISUAL-MISSING ", screen)
	run(screen, roots, viewport.get_visible_rect(), frame, viewport)
	return frame != null
