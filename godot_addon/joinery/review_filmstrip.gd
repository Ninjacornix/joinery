@tool
extends ScrollContainer
## A change with several items reviews as a filmstrip under the preview: one small tile per
## item, the most important first, the shown one ringed in the accent. A tile is the item's
## picture when there is one, else its kind's glyph (a sound) or its file type (".gd").
## Left and Right move between tiles when the strip has focus.

## The user chose the tile at [param index].
signal chosen(index: int)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
## Tile side, in control heights ("lg").
const TILE := 1.25

var _row: HBoxContainer
var _tiles: Array[Tile] = []
var _selected := 0


## One tile: draws its picture covered and clipped to its corners, or a glyph, or a word.
class Tile:
	extends Button
	var picture: Texture2D
	var glyph := ""
	var word := ""

	func _init() -> void:
		toggle_mode = true
		focus_mode = Control.FOCUS_ALL
		flat = true
		clip_contents = true
		mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		# The ring is the focus mark; the theme's focus box would draw a second one.
		for state: String in ["focus", "normal", "hover", "pressed", "hover_pressed"]:
			add_theme_stylebox_override(state, StyleBoxEmpty.new())

	func _draw() -> void:
		var rect := Rect2(Vector2.ZERO, size)
		var corner := float(UI_THEME.radius(self, "sm"))
		var ground := StyleBoxFlat.new()
		ground.bg_color = UI_THEME.color(self, "surface_sunken")
		ground.set_corner_radius_all(int(corner))
		draw_style_box(ground, rect)
		var inner := rect.grow(-UI_THEME.unit(self) / 4.0)
		if picture != null and picture.get_width() > 0:
			var scale := maxf(
				inner.size.x / picture.get_width(), inner.size.y / picture.get_height()
			)
			var region := Rect2(Vector2.ZERO, inner.size / scale)
			region.position = (picture.get_size() - region.size) / 2.0
			draw_texture_rect_region(picture, inner, region)
		elif not word.is_empty():
			var font := UI_THEME.mono_font(self)
			var font_size := UI_THEME.font_size(self, "caption")
			var width := font.get_string_size(word, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
			var base := (size.y + font.get_ascent(font_size) - font.get_descent(font_size)) / 2.0
			draw_string(
				font,
				Vector2((size.x - width) / 2.0, base),
				word,
				HORIZONTAL_ALIGNMENT_LEFT,
				-1,
				font_size,
				UI_THEME.color(self, "text_muted")
			)
		var ring := StyleBoxFlat.new()
		ring.draw_center = false
		ring.set_corner_radius_all(int(corner))
		var hovered := is_hovered() and not button_pressed
		ring.border_color = UI_THEME.color(
			self, "accent" if button_pressed else ("border_strong" if hovered else "border_subtle")
		)
		ring.set_border_width_all(
			maxi(1, int(UI_THEME.unit(self) / (2.0 if button_pressed else 4.0)))
		)
		draw_style_box(ring, rect)
		if has_focus() and not button_pressed:
			ring.border_color = UI_THEME.color(self, "accent")
			draw_style_box(ring, rect)


func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	vertical_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Items in this change"
	_row = HBoxContainer.new()
	_row.theme_type_variation = "JoineryRowTight"
	add_child(_row)
	UI_MOTION.rubber_band(self)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_size_tiles()


## One tile per item: [{name, picture?, glyph?, word?}]. The first is selected.
func show_items(items: Array) -> void:
	for tile: Tile in _tiles:
		tile.queue_free()
	_tiles.clear()
	var group := ButtonGroup.new()
	for index: int in items.size():
		var item: Dictionary = items[index]
		var tile := Tile.new()
		tile.button_group = group
		tile.picture = item.get("picture") as Texture2D
		tile.glyph = str(item.get("glyph", ""))
		if not tile.glyph.is_empty() and tile.picture == null:
			# The kind's glyph, muted, in the middle of the tile.
			var mark := TextureRect.new()
			mark.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
			mark.mouse_filter = Control.MOUSE_FILTER_IGNORE
			mark.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
			UI.bind_icon(mark, tile.glyph, "texture")
			mark.theme_changed.connect(
				func() -> void: mark.self_modulate = UI_THEME.color(mark, "text_muted")
			)
			tile.add_child(mark)
		tile.word = str(item.get("word", ""))
		tile.tooltip_text = str(item.get("name", ""))
		tile.accessibility_name = str(item.get("name", ""))
		tile.pressed.connect(_on_pressed.bind(index))
		tile.focus_entered.connect(func() -> void: ensure_control_visible(tile))
		tile.mouse_entered.connect(tile.queue_redraw)
		tile.mouse_exited.connect(tile.queue_redraw)
		UI_MOTION.press_feedback(tile)
		_row.add_child(tile)
		_tiles.append(tile)
	_size_tiles()
	select(0)


## Ring the tile at [param index] (no signal).
func select(index: int) -> void:
	if index < 0 or index >= _tiles.size():
		return
	_selected = index
	_tiles[index].set_pressed_no_signal(true)
	for tile: Tile in _tiles:
		tile.queue_redraw()


## Give the tile at [param index] its picture once it is ready.
func set_picture(index: int, picture: Texture2D) -> void:
	if index >= 0 and index < _tiles.size():
		_tiles[index].picture = picture
		_tiles[index].queue_redraw()


## The index of the ringed tile.
func selected() -> int:
	return _selected


## The tiles, left to right.
func tiles() -> Array[Tile]:
	return _tiles.duplicate()


func _on_pressed(index: int) -> void:
	select(index)
	chosen.emit(index)


func _gui_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or _tiles.is_empty():
		return
	var step := 0
	if key.keycode == KEY_LEFT:
		step = -1
	elif key.keycode == KEY_RIGHT:
		step = 1
	if step == 0:
		return
	var next := clampi(_selected + step, 0, _tiles.size() - 1)
	if next != _selected:
		_tiles[next].grab_focus()
		_on_pressed(next)
	accept_event()


func _size_tiles() -> void:
	if not is_inside_tree():
		return
	var side := ceilf(UI_THEME.control_h(self, "lg") * TILE)
	for tile: Tile in _tiles:
		tile.custom_minimum_size = Vector2(side, side)
	custom_minimum_size.y = side
