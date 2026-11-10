extends Area2D
## A row of spikes. The node sits on the bottom-left corner of its first
## tile; `tiles` sets how many tiles wide it is. Touching it emits `touched`,
## and the level sends the player back to the last checkpoint.

signal touched(body: Node2D)

const COLOUR := Color("#d8dde6")
const SHADE := Color("#8c95a6")

## Width in tiles.
@export_range(1, 64) var tiles: int = 1
@export var tile_size: int = 16
## Height of the spike points, in pixels.
@export var height: float = 8.0

@onready var shape: CollisionShape2D = $Shape
@onready var points: Polygon2D = $Points
@onready var base: Polygon2D = $Base


func _ready() -> void:
	_build()
	body_entered.connect(_on_body_entered)


func width() -> float:
	return float(tiles * tile_size)


func _build() -> void:
	var box: RectangleShape2D = RectangleShape2D.new()
	# A little smaller than the drawing, so grazing a tip is forgiven.
	box.size = Vector2(width() - 4.0, height * 0.6)
	shape.shape = box
	shape.position = Vector2(width() * 0.5, -box.size.y * 0.5)
	var outline: PackedVector2Array = PackedVector2Array([Vector2(0.0, 0.0)])
	var half: float = float(tile_size) * 0.25
	var count: int = tiles * 2
	for i: int in count:
		var left: float = float(i) * half * 2.0
		outline.append(Vector2(left + half, -height))
		outline.append(Vector2(left + half * 2.0, 0.0))
	points.polygon = outline
	points.color = COLOUR
	base.polygon = PackedVector2Array([
		Vector2(0.0, -2.0), Vector2(width(), -2.0), Vector2(width(), 0.0), Vector2(0.0, 0.0)
	])
	base.color = SHADE


func _on_body_entered(body: Node2D) -> void:
	if body.is_in_group(&"player") and body.is_multiplayer_authority():
		touched.emit(body)
