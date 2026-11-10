extends Area2D
## The goal flag. A player touching it finishes the level and the flag runs
## up the pole. The node sits on the ground at the foot of the pole.

signal reached(goal: Node)

## How far down the pole the flag waits until a player arrives.
@export var lowered_by: float = 22.0

var done: bool = false

@onready var flag: Polygon2D = $Flag


func _ready() -> void:
	flag.position.y = lowered_by
	body_entered.connect(_on_body_entered)


## Run through `Game.request_action` when a player touches the pole.
func reach() -> void:
	if done:
		return
	_finish()
	GameEvents.world_changed.emit(self)


## State another game instance needs to mirror this flag (online play).
func save_state() -> Dictionary:
	return {"done": done}


func load_state(state: Dictionary) -> void:
	if bool(state.get("done", false)) and not done:
		_finish()


func _finish() -> void:
	done = true
	flag.position.y = 0.0
	reached.emit(self)


func _on_body_entered(body: Node2D) -> void:
	if done or not body.is_in_group(&"player") or not body.is_multiplayer_authority():
		return
	Game.request_action(self, &"reach", [])
