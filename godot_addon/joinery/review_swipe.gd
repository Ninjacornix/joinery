@tool
extends Control
## Before and after of one picture in one frame, split by a line you can drag: the original
## left of it, the change right of it. Both are fitted into the same rectangle, so the line
## wipes between them. [member split] 1.0 shows only the original, 0.0 only the change; moving
## between them is a mask reveal (motion rule 17) that respects Reduce motion.

## The user dragged the line: the view is a comparison again.
signal dragged

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const GLANCE := preload("review_glance.gd")

## Share of the width showing the original, from the left (0..1).
var split := 0.5:
	set(value):
		split = clampf(value, 0.0, 1.0)
		queue_redraw()
var before: Texture2D
var after: Texture2D
var _dragging := false
var _tween: Tween


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	mouse_filter = Control.MOUSE_FILTER_PASS
	mouse_default_cursor_shape = Control.CURSOR_HSIZE
	clip_contents = true
	accessibility_name = "Before and after, split by a line you can drag"
	tooltip_text = "Drag to compare the original (left) with the change (right)"


## Show [param old] and [param new]; the line stays where it is.
func set_pictures(old: Texture2D, new: Texture2D) -> void:
	before = old
	after = new
	_sample()
	queue_redraw()


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED:
		_sample()


## Pixel art and small pictures blown up stay crisp; photos are smooth (review_glance.gd).
func _sample() -> void:
	var shown := after if after != null else before
	if shown != null:
		texture_filter = GLANCE.picture_filter(shown, fit(shown.get_size()).size)


## Move the line to [param value], animated with an ease-in-out unless motion is off.
func slide_to(value: float) -> void:
	if _tween != null:
		_tween.kill()
		_tween = null
	if not UI_MOTION.is_enabled() or not is_inside_tree() or _dragging:
		split = value
		return
	_tween = create_tween()
	_tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
	_tween.tween_property(self, "split", value, UI_MOTION.duration("base"))


## The rectangle a texture of [param texture_size] fills: fitted and centred.
func fit(texture_size: Vector2) -> Rect2:
	if texture_size.x <= 0.0 or texture_size.y <= 0.0 or size.x <= 0.0 or size.y <= 0.0:
		return Rect2()
	var scale := minf(size.x / texture_size.x, size.y / texture_size.y)
	var shown := texture_size * scale
	return Rect2((size - shown) / 2.0, shown)


func _draw() -> void:
	var line := size.x * split
	if before != null and split > 0.0:
		_draw_part(before, 0.0, line)
	if after != null and split < 1.0:
		_draw_part(after, line, size.x)
	if split <= 0.0 or split >= 1.0 or before == null or after == null:
		return
	# The line, with a small grip in the middle: the one thing to drag.
	var ink := UI_THEME.color(self, "text")
	var halo := UI_THEME.color(self, "surface_sunken")
	var width := maxf(1.0, UI_THEME.unit(self) / 4.0)
	draw_line(Vector2(line, 0.0), Vector2(line, size.y), halo, width * 3.0)
	draw_line(Vector2(line, 0.0), Vector2(line, size.y), ink, width)
	var grip := float(UI_THEME.space(self, 2))
	draw_circle(Vector2(line, size.y / 2.0), grip + width, halo)
	draw_circle(Vector2(line, size.y / 2.0), grip, ink)


## Draw the part of [param texture] between x [param from] and [param to] (in this control).
func _draw_part(texture: Texture2D, from: float, to: float) -> void:
	var rect := fit(texture.get_size())
	if not rect.has_area():
		return
	var left := clampf(from, rect.position.x, rect.end.x)
	var right := clampf(to, rect.position.x, rect.end.x)
	if right - left <= 0.0:
		return
	var shown := Rect2(left, rect.position.y, right - left, rect.size.y)
	var ratio := texture.get_size().x / rect.size.x
	var source := Rect2(
		(left - rect.position.x) * ratio, 0.0, shown.size.x * ratio, texture.get_size().y
	)
	draw_texture_rect_region(texture, shown, source)


func _gui_input(event: InputEvent) -> void:
	if before == null or after == null:
		return
	var button := event as InputEventMouseButton
	if button != null and button.button_index == MOUSE_BUTTON_LEFT:
		_dragging = button.pressed
		if button.pressed:
			_follow(button.position.x)
		accept_event()
		return
	var motion := event as InputEventMouseMotion
	if motion != null and _dragging:
		_follow(motion.position.x)
		accept_event()


func _follow(x: float) -> void:
	if _tween != null:
		_tween.kill()
		_tween = null
	# No tween while held (motion rule 14): the line follows the pointer exactly.
	split = x / maxf(1.0, size.x)
	dragged.emit()
