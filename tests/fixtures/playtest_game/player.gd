extends CharacterBody2D
## Walks with move_left/right/up/down.

const SPEED := 200.0


func _physics_process(_delta: float) -> void:
	velocity = Input.get_vector(&"move_left", &"move_right", &"move_up", &"move_down") * SPEED
	move_and_slide()
