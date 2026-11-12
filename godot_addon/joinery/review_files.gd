@tool
extends ScrollContainer
## A change with several files reviews as a list beside the preview (review_split.gd): one row
## per file, the model or source first with its textures and part views grouped under it, text
## last. A row is a small square thumbnail (the file's picture, else its kind's glyph or its file
## type), the full name (wrapped at its underscores when it must, never cut short) and a quiet
## mark, New, Changed or Removed, beside the name when there is room, else under it. The shown
## row is filled with the accent tint. Up and Down move through the rows once the review
## has the focus (review_panel.gd); a long list scrolls, keeping the shown row in view.

## The user chose the row at [param index].
signal chosen(index: int)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const GLANCE := preload("review_glance.gd")
const MARKS := {"new": "New", "changed": "Changed", "removed": "Removed"}

var _column: VBoxContainer
var _rows: Array[Row] = []
var _selected := 0


## One file: its thumbnail, its name and its mark, drawn on a quiet row that fills with the
## accent tint while it is the one on show.
class Row:
	extends Button
	var picture: Texture2D
	var glyph := ""
	var word := ""
	var mark := ""
	## True for a model's render: the thumbnail shows the model itself, not the ground around it.
	var framed := false
	## 0 for a file of its own, 1 for one grouped under the model above it.
	var depth := 0
	var thumb: Control
	var name_label: Label
	var mark_label: Label
	var _content: HBoxContainer
	## The name, with the mark under it when the row is too narrow for both on one line.
	var _text: VBoxContainer
	var _indent: Control
	var _icon: TextureRect

	func _init() -> void:
		toggle_mode = true
		focus_mode = Control.FOCUS_ALL
		flat = true
		clip_contents = true
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		alignment = HORIZONTAL_ALIGNMENT_LEFT
		# The fill and the ring are drawn here; the theme's boxes would draw a second set.
		for state: String in ["focus", "normal", "hover", "pressed", "hover_pressed"]:
			add_theme_stylebox_override(state, StyleBoxEmpty.new())
		_content = HBoxContainer.new()
		_content.theme_type_variation = "JoineryRow"
		_content.mouse_filter = Control.MOUSE_FILTER_IGNORE
		add_child(_content)
		_indent = Control.new()
		_indent.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_content.add_child(_indent)
		thumb = Control.new()
		thumb.mouse_filter = Control.MOUSE_FILTER_IGNORE
		thumb.clip_contents = true
		thumb.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		thumb.draw.connect(_draw_thumb)
		_content.add_child(thumb)
		_icon = TextureRect.new()
		_icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
		_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_icon.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		_icon.hide()
		thumb.add_child(_icon)
		_text = VBoxContainer.new()
		_text.theme_type_variation = "JoineryFlush"
		_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		_text.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_content.add_child(_text)
		name_label = Label.new()
		name_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		name_label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		name_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_text.add_child(name_label)
		mark_label = UI.label("", "JoineryCaption")
		mark_label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		mark_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		mark_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_text.add_child(mark_label)
		_content.minimum_size_changed.connect(_fit)
		resized.connect(_fit)
		toggled.connect(func(_on: bool) -> void: _tint())
		focus_entered.connect(queue_redraw)
		focus_exited.connect(queue_redraw)
		mouse_entered.connect(queue_redraw)
		mouse_exited.connect(queue_redraw)

	func _notification(what: int) -> void:
		if what == NOTIFICATION_THEME_CHANGED:
			_size_parts()

	## Fill the row: [param spec] is {name, picture?, glyph?, word?, mark?, depth?}.
	func fill(spec: Dictionary) -> void:
		var plain := str(spec.get("name", ""))
		# A long file name may break after its underscores and dashes, not mid-word.
		name_label.text = plain.replace("_", "_\u200b").replace("-", "-\u200b")
		tooltip_text = str(spec.get("tooltip", plain))
		accessibility_name = plain
		glyph = str(spec.get("glyph", ""))
		word = str(spec.get("word", ""))
		mark = str(spec.get("mark", ""))
		depth = int(spec.get("depth", 0))
		framed = bool(spec.get("framed", false))
		mark_label.text = MARKS.get(mark, "")
		mark_label.visible = not mark_label.text.is_empty()
		if not glyph.is_empty():
			UI.bind_icon(_icon, glyph, "texture")
		set_picture(spec.get("picture") as Texture2D)
		_size_parts()

	## Set the thumbnail picture, sampled crisp or smooth for the square it covers.
	func set_picture(value: Texture2D) -> void:
		picture = value
		_icon.visible = picture == null and not glyph.is_empty()
		if picture != null and picture.get_width() > 0 and picture.get_height() > 0:
			var side := thumb.custom_minimum_size.x
			var region := _region(Vector2(side, side))
			var shown := picture.get_size() * side / maxf(region.size.x, 1.0)
			thumb.texture_filter = GLANCE.picture_filter(picture, shown)
		thumb.queue_redraw()

	## The part of the picture the thumbnail shows, in its pixels: for a model's render the
	## model with a little room around it (it may reach past the picture's edges, where the
	## render's ground carries on), else the centre that covers [param rect].
	func _region(rect: Vector2) -> Rect2:
		if framed:
			return GLANCE.subject(picture)
		var scale := maxf(rect.x / picture.get_width(), rect.y / picture.get_height())
		var region := Rect2(Vector2.ZERO, rect / scale)
		region.position = (picture.get_size() - region.size) / 2.0
		return region

	func _size_parts() -> void:
		if not is_inside_tree():
			return
		var side := float(UI_THEME.control_h(self, "md"))
		thumb.custom_minimum_size = Vector2(side, side)
		_indent.custom_minimum_size.x = float(UI_THEME.space(self, 4)) * depth
		_indent.visible = depth > 0
		_tint()
		set_picture(picture)
		_fit()

	## The row is as tall as its content (a name may wrap to a second line) plus its padding.
	func _fit() -> void:
		if not is_inside_tree():
			return
		var pad := Vector2(UI_THEME.space(self, 2), UI_THEME.space(self, 1))
		_content.position = pad
		_content.size = Vector2(maxf(size.x - pad.x * 2.0, 0.0), _content.size.y)
		_place_mark()
		var tall := _content.get_combined_minimum_size().y + pad.y * 2.0
		_content.size.y = tall - pad.y * 2.0
		if not is_equal_approx(custom_minimum_size.y, tall):
			custom_minimum_size.y = tall

	## The mark beside the name when both fit on one line, else under the name. Decided from
	## the row's width alone, so moving the mark never changes the answer.
	func _place_mark() -> void:
		var gap := float(_content.get_theme_constant("separation"))
		var room := _content.size.x - thumb.custom_minimum_size.x - gap
		if _indent.visible:
			room -= _indent.custom_minimum_size.x + gap
		var font := name_label.get_theme_font("font")
		var font_size := name_label.get_theme_font_size("font_size")
		var wide := (
			font.get_string_size(name_label.text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
		)
		var inline := wide + gap + mark_label.get_combined_minimum_size().x <= room
		var host: Container = _content if inline else _text
		if mark_label.get_parent() != host:
			mark_label.reparent(host, false)

	## The file's name as shown, without the break points.
	func plain_name() -> String:
		return name_label.text.replace("\u200b", "")

	func _tint() -> void:
		if not is_inside_tree():
			return
		mark_label.theme_type_variation = (
			"JoineryAccentCaption"
			if mark == "new"
			else ("JoineryDangerCaption" if mark == "removed" else "JoineryFaint")
		)
		_icon.self_modulate = UI_THEME.color(self, "text_muted")
		queue_redraw()

	func _draw() -> void:
		var rect := Rect2(Vector2.ZERO, size)
		var box := StyleBoxFlat.new()
		box.set_corner_radius_all(UI_THEME.radius(self, "sm"))
		var hovered := is_hovered() and not button_pressed
		if button_pressed:
			box.bg_color = UI_THEME.color(self, "accent_tint")
		elif hovered:
			box.bg_color = UI_THEME.color(self, "surface_raised")
		else:
			box.draw_center = false
		if has_focus():
			box.border_color = UI_THEME.color(self, "accent")
			box.set_border_width_all(maxi(1, int(UI_THEME.unit(self))))
		draw_style_box(box, rect)

	## The thumbnail: the picture covering a rounded square, else the glyph or the file type.
	func _draw_thumb() -> void:
		var rect := Rect2(Vector2.ZERO, thumb.size)
		# Inner corners: the row's radius less its padding (visual rule 14).
		var corner := maxi(
			UI_THEME.radius(self, "xs"), UI_THEME.radius(self, "sm") - UI_THEME.space(self, 1)
		)
		var ground := StyleBoxFlat.new()
		ground.bg_color = UI_THEME.color(self, "surface_sunken")
		var has_picture := picture != null and picture.get_width() > 0
		if has_picture and framed and GLANCE.ground(picture).a > 0.0:
			# The render's own ground carries on where the framed square runs past its edges.
			ground.bg_color = GLANCE.ground(picture)
		ground.set_corner_radius_all(corner)
		thumb.draw_style_box(ground, rect)
		if has_picture:
			var region := _region(rect.size)
			var inside := region.intersection(Rect2(Vector2.ZERO, picture.get_size()))
			if inside.has_area():
				var scale := rect.size.x / region.size.x
				var shown := Rect2(
					rect.position + (inside.position - region.position) * scale, inside.size * scale
				)
				thumb.draw_texture_rect_region(picture, shown, inside)
		elif not word.is_empty() and glyph.is_empty():
			var font := UI_THEME.mono_font(self)
			var font_size := UI_THEME.font_size(self, "caption")
			var width := font.get_string_size(word, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
			var base := (
				(rect.size.y + font.get_ascent(font_size) - font.get_descent(font_size)) / 2.0
			)
			thumb.draw_string(
				font,
				Vector2((rect.size.x - width) / 2.0, base),
				word,
				HORIZONTAL_ALIGNMENT_LEFT,
				-1,
				font_size,
				UI_THEME.color(self, "text_muted")
			)


func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	accessibility_name = "Files in this change"
	_column = VBoxContainer.new()
	_column.theme_type_variation = "JoineryFlush"
	_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_column)
	UI_MOTION.rubber_band(self)


## One row per file: [{name, tooltip?, picture?, glyph?, word?, mark?, depth?}]. The first is
## selected; the rows come in one after another.
func show_items(items: Array) -> void:
	for row: Row in _rows:
		row.queue_free()
	_rows.clear()
	var group := ButtonGroup.new()
	for index: int in items.size():
		var row := Row.new()
		row.button_group = group
		_column.add_child(row)
		row.fill(items[index] as Dictionary)
		row.pressed.connect(_on_pressed.bind(index))
		row.focus_entered.connect(func() -> void: ensure_control_visible(row))
		UI_MOTION.press_feedback(row)
		_rows.append(row)
	scroll_vertical = 0
	select(0)
	UI_MOTION.stagger(_rows)


## Mark the row at [param index] as the one on show and scroll it into view (no signal).
func select(index: int) -> void:
	if index < 0 or index >= _rows.size():
		return
	_selected = index
	for row: Row in _rows:
		row.set_pressed_no_signal(row == _rows[index])
		row.queue_redraw()
	_reveal.call_deferred(_rows[index])


## Give the row at [param index] its picture once it is ready.
func set_picture(index: int, picture: Texture2D) -> void:
	if index >= 0 and index < _rows.size():
		_rows[index].set_picture(picture)


## The index of the row on show.
func selected() -> int:
	return _selected


## The rows, top to bottom.
func rows() -> Array[Row]:
	return _rows.duplicate()


## The height of one row, for a list folded to a few rows (review_split.gd).
func row_height() -> float:
	if _rows.is_empty():
		return float(UI_THEME.control_h(self, "lg")) if is_inside_tree() else 0.0
	return _rows[0].get_combined_minimum_size().y


## The height every row together needs.
func content_height() -> float:
	return _column.get_combined_minimum_size().y


## Show the row [param step] away from the one on show (Up and Down). True when it moved.
func step(by: int) -> bool:
	var next := clampi(_selected + by, 0, _rows.size() - 1)
	if _rows.is_empty() or next == _selected:
		return false
	_on_pressed(next)
	return true


func _on_pressed(index: int) -> void:
	select(index)
	chosen.emit(index)


func _reveal(row: Row) -> void:
	if is_instance_valid(row) and row.is_inside_tree():
		ensure_control_visible(row)


func _gui_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or _rows.is_empty():
		return
	var by := {KEY_UP: -1, KEY_DOWN: 1}.get(key.keycode, 0) as int
	if by != 0 and step(by):
		_rows[_selected].grab_focus()
		accept_event()
