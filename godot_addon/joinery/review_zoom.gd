@tool
extends RefCounted
## Zoom and pan of a picture in review, kept apart from drawing so the original and the change
## share one view: both are drawn into [method view] of the same fitted rectangle, so zooming
## or panning one moves the other with it. The wheel or a pinch zooms about the pointer, a drag
## pans, a double-click fits the picture again. Panning stops at the picture's edges.

## The furthest in, as a multiple of the fitted size.
const MAX_ZOOM := 16.0
## Zoom per wheel notch.
const STEP := 1.2

## 1.0 is the fitted picture.
var zoom := 1.0
## How far the zoomed picture is moved, in the control's pixels.
var offset := Vector2.ZERO


## True when the picture is shown larger than fitted.
func zoomed() -> bool:
	return zoom > 1.0 + 0.001


## Back to the fitted picture.
func reset() -> void:
	zoom = 1.0
	offset = Vector2.ZERO


## The rectangle the picture is drawn in, given its fitted rectangle [param fit].
func view(fit: Rect2) -> Rect2:
	if not fit.has_area():
		return fit
	var shown := fit.size * zoom
	return Rect2(fit.get_center() - shown / 2.0 + offset, shown)


## Zoom by [param factor] keeping the point under [param at] (control pixels) in place.
func zoom_at(at: Vector2, factor: float, fit: Rect2) -> void:
	if not fit.has_area() or factor <= 0.0:
		return
	var before := view(fit)
	var wanted := clampf(zoom * factor, 1.0, MAX_ZOOM)
	if is_equal_approx(wanted, zoom):
		return
	# Where the pointer sits on the picture, 0..1, stays under the pointer.
	var spot := (at - before.position) / before.size
	zoom = wanted
	var shown := fit.size * zoom
	var position := at - spot * shown
	offset = position - (fit.get_center() - shown / 2.0)
	_clamp(fit)


## Move the zoomed picture by [param delta] pixels.
func pan(delta: Vector2, fit: Rect2) -> void:
	if not zoomed():
		return
	offset += delta
	_clamp(fit)


## Keep the picture over its fitted rectangle: no empty band opens where it was.
func _clamp(fit: Rect2) -> void:
	if zoom <= 1.0:
		offset = Vector2.ZERO
		return
	var spare := (fit.size * zoom - fit.size) / 2.0
	offset = offset.clamp(-spare, spare)


## The part of [param texture] (of [param texture_size]) to draw so that it lands in
## [param target] when the whole picture is drawn in [param drawn]: {"rect", "source"}, or
## {} when nothing of it shows.
static func region(drawn: Rect2, target: Rect2, texture_size: Vector2) -> Dictionary:
	var visible := drawn.intersection(target)
	if not visible.has_area() or not drawn.has_area():
		return {}
	var ratio := texture_size / drawn.size
	var source := Rect2((visible.position - drawn.position) * ratio, visible.size * ratio)
	return {"rect": visible, "source": source}
