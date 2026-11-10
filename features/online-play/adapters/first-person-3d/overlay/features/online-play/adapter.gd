extends NetAdapter
## Online play for the first-person rooms. Switches and the door replicate
## through the game core. Reaching the exit is decided by the server, which
## sees every player walk in, so this tells the joining players that the level
## is complete and raises `GameEvents.level_completed` for them too (also for
## a player who joins after the end).

var _result: Dictionary = {}
var _announced: bool = false


func _ready() -> void:
	GameEvents.level_completed.connect(_on_level_completed)


func _setup() -> void:
	_result = {}
	_announced = false


func peer_ready(peer: int) -> void:
	if serving() and not _result.is_empty():
		send_to(peer, &"_completed", [_result])


func _on_level_completed(result: Dictionary) -> void:
	if not serving():
		return
	_result = result.duplicate(true)
	send_to_clients(&"_completed", [_result])


@rpc("authority", "call_remote", "reliable")
func _completed(result: Dictionary) -> void:
	if _announced or not has_level():
		return
	_announced = true
	level.set(&"completed", true)
	GameEvents.level_completed.emit(result)
