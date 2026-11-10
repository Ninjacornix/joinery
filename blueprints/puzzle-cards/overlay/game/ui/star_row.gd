extends Control
## A row of stars drawn as polygons (no images, no glyphs): filled ones are
## earned, outlined ones are not. `pop()` bounces them in, unless the player
## turned on reduced motion.

const POINTS := 5
const INNER_RATIO := 0.45
const FILLED := Color("#ffd75e")
const EMPTY := Color(1.0, 1.0, 1.0, 0.35)
const OVERSHOOT := 1.08

@export var max_stars: int = 3:
	set(value):
		max_stars = maxi(1, value)
		_resize()
@export var star_size: float = 32.0:
	set(value):
		star_size = maxf(4.0, value)
		_resize()

var stars: int = 0:
	set(value):
		stars = clampi(value, 0, max_stars)
		queue_redraw()


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_resize()


## Bounces the row in with a little overshoot.
func pop() -> void:
	pivot_offset = size * 0.5
	var settings: Node = get_tree().root.get_node_or_null(^"Settings")
	if settings != null and bool(settings.call(&"reduced_motion")):
		scale = Vector2.ONE
		return
	scale = Vector2(0.2, 0.2)
	var tween: Tween = create_tween()
	(
		tween
		. tween_property(self, ^"scale", Vector2.ONE * OVERSHOOT, 0.18)
		. set_trans(Tween.TRANS_BACK)
		. set_ease(Tween.EASE_OUT)
	)
	tween.tween_property(self, ^"scale", Vector2.ONE, 0.08)


func _draw() -> void:
	var gap: float = star_size * 0.25
	for i: int in max_stars:
		var centre: Vector2 = Vector2(star_size * 0.5 + i * (star_size + gap), size.y * 0.5)
		var shape: PackedVector2Array = _star(centre, star_size * 0.5)
		if i < stars:
			draw_colored_polygon(shape, FILLED)
		else:
			var outline: PackedVector2Array = shape.duplicate()
			outline.append(shape[0])
			draw_polyline(outline, EMPTY, maxf(1.5, star_size * 0.06), true)


func _star(centre: Vector2, radius: float) -> PackedVector2Array:
	var shape: PackedVector2Array = PackedVector2Array()
	for i: int in POINTS * 2:
		var r: float = radius if i % 2 == 0 else radius * INNER_RATIO
		var angle: float = -PI * 0.5 + i * PI / POINTS
		shape.append(centre + Vector2(cos(angle), sin(angle)) * r)
	return shape


func _resize() -> void:
	var gap: float = star_size * 0.25
	custom_minimum_size = Vector2(max_stars * star_size + (max_stars - 1) * gap, star_size)
	queue_redraw()
