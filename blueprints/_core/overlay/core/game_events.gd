extends Node
## Game-wide signals. Levels, players, features and UI talk through these
## instead of holding references to each other.

@warning_ignore("unused_signal")
signal level_started(level: Node)
## `result` has at least `time` (seconds) and `score` (int).
@warning_ignore("unused_signal")
signal level_completed(result: Dictionary)
@warning_ignore("unused_signal")
signal level_failed(reason: String)
@warning_ignore("unused_signal")
signal checkpoint_reached(id: String)
@warning_ignore("unused_signal")
signal player_spawned(player: Node, peer_id: int)
@warning_ignore("unused_signal")
signal score_changed(value: int)
@warning_ignore("unused_signal")
signal paused_changed(paused: bool)
@warning_ignore("unused_signal")
signal menu_opened(menu: Control)
## A saveable world object changed state (after `Game.request_action`).
@warning_ignore("unused_signal")
signal world_changed(node: Node)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
