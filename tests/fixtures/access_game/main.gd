extends Control
## Planted on purpose: a flashing screen, and a state told by colour alone.

var ready_to_go: bool = false
var frame: int = 0
@onready var flash: ColorRect = $Flash
@onready var light: ColorRect = $Light


func _process(_delta: float) -> void:
	frame += 1
	# Black and white every 4 frames for 3 s: 7.5 flashes a second.
	flash.visible = frame < 180
	flash.color = Color.WHITE if (frame / 4) % 2 == 0 else Color.BLACK
	ready_to_go = frame % 120 < 60
	if ready_to_go:
		light.modulate = Color("#40a040")
	else:
		light.modulate = Color("#d04040")
	if Input.is_action_pressed(&"sprint"):
		frame += 1
