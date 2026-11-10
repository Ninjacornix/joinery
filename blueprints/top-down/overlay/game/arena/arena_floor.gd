@tool
extends Node2D
## The arena floor: a flat rectangle with a faint grid, drawn in code so the
## greybox needs no texture. `size` also sets the camera limits.

@export var size: Vector2 = Vector2(1920.0, 1200.0):
	set(value):
		size = value.max(Vector2(64.0, 64.0))
		queue_redraw()
@export var colour: Color = Color("#232733"):
	set(value):
		colour = value
		queue_redraw()
@export var grid_colour: Color = Color("#2b3040"):
	set(value):
		grid_colour = value
		queue_redraw()
@export var grid_step: float = 80.0:
	set(value):
		grid_step = maxf(value, 8.0)
		queue_redraw()


## The floor in global coordinates.
func global_rect() -> Rect2:
	return Rect2(global_position, size)


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), colour)
	var x: float = grid_step
	while x < size.x:
		draw_line(Vector2(x, 0.0), Vector2(x, size.y), grid_colour, 2.0)
		x += grid_step
	var y: float = grid_step
	while y < size.y:
		draw_line(Vector2(0.0, y), Vector2(size.x, y), grid_colour, 2.0)
		y += grid_step
