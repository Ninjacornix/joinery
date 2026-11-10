extends Node
## A tiny world object for the online tests: `ping` changes it through
## `Game.request_action`, so it runs on the server and its state reaches
## every player, whatever the starting point.

@export var save_id: String = "net_probe"

var value: int = 0
var actor: int = 0
## Every state this node took, in order, as `"<value>@<actor>"`.
var history: PackedStringArray = PackedStringArray()


func _init() -> void:
	name = "NetProbe"
	add_to_group(&"saveable")


func ping(new_value: int) -> void:
	value = new_value
	actor = Game.actor_id()
	history.append("%d@%d" % [value, actor])
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"value": value, "actor": actor}


func load_state(state: Dictionary) -> void:
	value = int(state.get("value", value))
	actor = int(state.get("actor", actor))
	history.append("%d@%d" % [value, actor])
