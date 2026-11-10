extends Node2D
## A tiny saveable world object for core tests: it counts `bump` actions and
## remembers which peer sent the last one.

@export var save_id: String = "probe"

var count: int = 0
var last_actor: int = 0
var spawn_note: String = ""


func _ready() -> void:
	add_to_group(&"saveable")


func bump(amount: int) -> void:
	count += amount
	last_actor = Game.actor_id()
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"count": count, "position": position}


func load_state(state: Dictionary) -> void:
	count = int(state.get("count", 0))
	position = state.get("position", Vector2.ZERO)
