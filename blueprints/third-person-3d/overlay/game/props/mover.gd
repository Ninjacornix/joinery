extends AnimatableBody3D
## A gate, lift or step that slides between its placed (closed) position and
## an open offset while it is switched on.

signal arrived(open: bool)

@export var open_offset: Vector3 = Vector3(0.0, -4.0, 0.0)
@export var speed: float = 3.0

var active: bool = false

var _closed: Vector3
var _has_closed: bool = false
var _moving: bool = false


# Runs before any sibling's _ready, so levers and plates can switch it early.
func _enter_tree() -> void:
	if not _has_closed:
		_closed = position
		_has_closed = true


func set_active(on: bool, instant: bool = false) -> void:
	active = on
	if instant:
		position = _goal()
		_moving = false
	else:
		_moving = true


func is_open() -> bool:
	return active and position.is_equal_approx(_goal())


func _physics_process(delta: float) -> void:
	if not _moving:
		return
	position = position.move_toward(_goal(), speed * delta)
	if position.is_equal_approx(_goal()):
		_moving = false
		arrived.emit(active)


func _goal() -> Vector3:
	return _closed + open_offset if active else _closed
