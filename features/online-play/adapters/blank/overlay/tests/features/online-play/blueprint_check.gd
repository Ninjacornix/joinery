extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the blank starting point: there is no player scene, so
## nobody is spawned, and both sides run the same level.


func client_check(level: Node) -> void:
	play.expect(play.game.players().is_empty(), "the blank level spawns no players")
	play.expect(level.scene_file_path == "res://game/level.tscn", "the client runs the host's level")


func host_check(level: Node) -> void:
	play.expect(play.game.players().is_empty(), "the blank level spawns no players on the server")
	play.expect(level.scene_file_path == "res://game/level.tscn", "the server runs the level")
	play.expect(client_id() != 0, "the client has the level loaded")
