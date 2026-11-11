@tool
extends Control
## Before and after of one picture in one frame, split by a line you can drag: the original
## left of it, the change right of it. Both are fitted into the same rectangle, so the line
## wipes between them. [member split] 1.0 shows only the original, 0.0 only the change; moving
## between them is a mask reveal (motion rule 17) that respects Reduce motion. A new picture
## (no original) is shown alone.
##
## The wheel or a pinch zooms about the pointer and a drag pans once zoomed; the original and
## the change share one view (review_zoom.gd), so they stay lined up. A double-click fits the
## picture again. Zoomed in, pixel art stays crisp (review_glance.gd's picture_filter). The line
## can still be dragged by its grip while zoomed.

## The user dragged the line: the view is a comparison again.
signal dragged

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const GLANCE := preload("review_glance.gd")
const ZOOM := preload("review_zoom.gd")

## Share of the width showing the original, from the left (0..1).
var split := 0.5:
	set(value):
		split = clampf(value, 0.0, 1.0)
		queue_redraw()
var before: Texture2D
var after: Texture2D
## The shared zoom and pan.
var view := ZOOM.new()
var _dragging := false
var _panning := false
var _tween: Tween


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	mouse_filter = Control.MOUSE_FILTER_STOP
	mouse_force_pass_scroll_events = false
	# A click gives the review its keys; the wheel or hovering never takes focus.
	focus_mode = Control.FOCUS_CLICK
	clip_contents = true
	accessibility_name = "Before and after, split by a line you can drag"


## Show [param old] and [param new]; the line stays where it is. A different picture starts
## fitted.
func set_pictures(old: Texture2D, new: Texture2D) -> void:
	if new != after or old != before:
		view.reset()
	before = old
	after = new
	mouse_default_cursor_shape = Control.CURSOR_HSIZE if paired() else Control.CURSOR_ARROW
	tooltip_text = (
		"Drag the line to compare. Scroll to zoom, double-click to fit."
		if paired()
		else "Scroll to zoom, drag to pan, double-click to fit."
	)
	_sample()
	queue_redraw()


## True with both an original and a change.
func paired() -> bool:
	return before != null and after != null


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED:
		view.reset()
		_sample()


## Pixel art and small pictures blown up stay crisp; photos are smooth (review_glance.gd).
func _sample() -> void:
	var shown := after if after != null else before
	if shown != null:
		texture_filter = GLANCE.picture_filter(shown, view.view(fit(shown.get_size())).size)


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


## The fitted rectangle of the picture on show (the change, else the original).
func picture_rect() -> Rect2:
	var shown := after if after != null else before
	return fit(shown.get_size()) if shown != null else Rect2()


## Zoom by [param factor] about [param at] (this control's pixels).
func zoom_at(at: Vector2, factor: float) -> void:
	view.zoom_at(at, factor, picture_rect())
	_sample()
	queue_redraw()


## Fit the picture again.
func reset_view() -> void:
	view.reset()
	_sample()
	queue_redraw()


func _draw() -> void:
	var line := size.x * split if paired() else (0.0 if after != null else size.x)
	# Each side's own background runs to its edge of the frame: no empty bands beside it.
	if before != null and line > 0.0:
		_draw_ground(GLANCE.ground(before), Rect2(0.0, 0.0, line, size.y))
	if after != null and line < size.x:
		_draw_ground(GLANCE.ground(after), Rect2(line, 0.0, size.x - line, size.y))
	if before != null and line > 0.0:
		_draw_part(before, 0.0, line)
	if after != null and line < size.x:
		_draw_part(after, line, size.x)
	if split <= 0.0 or split >= 1.0 or not paired():
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


## [param colour] over [param rect], rounded like the frame where it meets the frame's edge.
func _draw_ground(colour: Color, rect: Rect2) -> void:
	if colour.a <= 0.0 or not rect.has_area():
		return
	var box := StyleBoxFlat.new()
	box.bg_color = colour
	var corner := UI_THEME.radius(self, "md")
	var left := corner if rect.position.x <= 0.0 else 0
	var right := corner if rect.end.x >= size.x else 0
	box.corner_radius_top_left = left
	box.corner_radius_bottom_left = left
	box.corner_radius_top_right = right
	box.corner_radius_bottom_right = right
	draw_style_box(box, rect)


## Draw the part of [param texture] between x [param from] and [param to] (in this control),
## zoomed and panned like the other side.
func _draw_part(texture: Texture2D, from: float, to: float) -> void:
	var drawn := view.view(fit(texture.get_size()))
	var part := ZOOM.region(drawn, Rect2(from, 0.0, to - from, size.y), texture.get_size())
	if not part.is_empty():
		draw_texture_rect_region(texture, part["rect"], part["source"])


func _gui_input(event: InputEvent) -> void:
	if before == null and after == null:
		return
	var button := event as InputEventMouseButton
	if button != null:
		_on_button(button)
		return
	var motion := event as InputEventMouseMotion
	if motion != null and (_dragging or _panning):
		if _dragging:
			_follow(motion.position.x)
		else:
			view.pan(motion.relative, picture_rect())
			queue_redraw()
		accept_event()
		return
	var pinch := event as InputEventMagnifyGesture
	if pinch != null and pinch.factor > 0.0:
		zoom_at(pinch.position, pinch.factor)
		accept_event()
		return
	var scroll := event as InputEventPanGesture
	if scroll != null:
		if view.zoomed() and not scroll.ctrl_pressed:
			view.pan(-scroll.delta * 8.0, picture_rect())
			queue_redraw()
		else:
			zoom_at(scroll.position, exp(-scroll.delta.y * 0.05))
		accept_event()


func _on_button(button: InputEventMouseButton) -> void:
	match button.button_index:
		MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN:
			if button.pressed:
				var steps := maxf(button.factor, 1.0)
				var up := button.button_index == MOUSE_BUTTON_WHEEL_UP
				zoom_at(button.position, pow(ZOOM.STEP, steps if up else -steps))
			accept_event()
		MOUSE_BUTTON_LEFT:
			if button.pressed and button.double_click:
				reset_view()
			elif button.pressed:
				# Zoomed in, a drag pans unless it starts on the line's grip.
				var near := absf(button.position.x - size.x * split) <= _grab()
				_dragging = paired() and (not view.zoomed() or near)
				_panning = not _dragging and view.zoomed()
				if _dragging:
					_follow(button.position.x)
			else:
				_dragging = false
				_panning = false
			accept_event()


## How close to the line a press must be to take it while zoomed in.
func _grab() -> float:
	return float(UI_THEME.space(self, 4)) if is_inside_tree() else 16.0


func _follow(x: float) -> void:
	if _tween != null:
		_tween.kill()
		_tween = null
	# No tween while held (motion rule 14): the line follows the pointer exactly.
	split = x / maxf(1.0, size.x)
	dragged.emit()
