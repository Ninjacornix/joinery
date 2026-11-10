@tool
extends StaticBody2D
## A solid greybox block: a wall or a pillar. Set `size` and the colours in
## the inspector; the collision box follows the size, centred on the node.
## Replace it with your own art when the art bible is approved.

@export var size: Vector2 = Vector2(128.0, 128.0):
	set(value):
		size = value.max(Vector2(4.0, 4.0))
		_sync_shape()
		queue_redraw()
@export var colour: Color = Color("#3d4457"):
	set(value):
		colour = value
		queue_redraw()
@export var edge_colour: Color = Color("#5c6680"):
	set(value):
		edge_colour = value
		queue_redraw()

var _shape: CollisionShape2D = null


func _ready() -> void:
	_shape = CollisionShape2D.new()
	_shape.name = "Box"
	_shape.shape = RectangleShape2D.new()
	add_child(_shape)
	_sync_shape()


## The block's rectangle in its parent's space.
func rect() -> Rect2:
	return Rect2(position - size * 0.5, size)


func _draw() -> void:
	var box: Rect2 = Rect2(-size * 0.5, size)
	draw_rect(box, colour)
	draw_rect(box.grow(-3.0), edge_colour, false, 4.0)


func _sync_shape() -> void:
	if _shape != null and _shape.shape is RectangleShape2D:
		(_shape.shape as RectangleShape2D).size = size
