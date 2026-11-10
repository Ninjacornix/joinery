extends AnimatableBody3D
## The exit door. Blocks the doorway while shut; slides up into the wall when
## opened. Its state is saved and replicated as {open, lift}.

signal opened

const LIFT_HEIGHT := 3.2
const LIFT_SPEED := 2.5

@export var save_id: String = "exit_door"

var is_open: bool = false
var _closed_y: float = 0.0
var _lift: float = 0.0


func _ready() -> void:
	add_to_group(&"saveable")
	_closed_y = position.y


func _physics_process(delta: float) -> void:
	var goal: float = LIFT_HEIGHT if is_open else 0.0
	if is_equal_approx(_lift, goal):
		return
	_lift = move_toward(_lift, goal, LIFT_SPEED * delta)
	position.y = _closed_y + _lift


## Opens the door. Safe to call more than once.
func open() -> void:
	if is_open:
		return
	is_open = true
	opened.emit()
	GameEvents.world_changed.emit(self)


func is_fully_open() -> bool:
	return is_open and is_equal_approx(_lift, LIFT_HEIGHT)


func save_state() -> Dictionary:
	return {"open": is_open}


func load_state(state: Dictionary) -> void:
	var want_open: bool = bool(state.get("open", is_open))
	if want_open and not is_open:
		is_open = true
		opened.emit()
	elif not want_open:
		is_open = false
	_lift = LIFT_HEIGHT if is_open else 0.0
	position.y = _closed_y + _lift
