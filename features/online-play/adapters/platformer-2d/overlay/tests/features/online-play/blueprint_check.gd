extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the 2D platformer: a coin and a checkpoint the joining
## player takes reach the server, and the flag the server reaches finishes
## the level for the joining player too.


func client_check(level: Node) -> void:
	var coin: Node = _first_coin(level)
	play.game.request_action(coin, &"collect", [])
	play.expect(not bool(coin.get(&"collected")), "the coin waits for the server")
	await until(
		func() -> bool: return bool(coin.get(&"collected")),
		"the server collects the client's coin"
	)
	# The coin and the level's score replicate as separate messages.
	await until(func() -> bool: return int(level.get(&"score")) == 1, "the client counts the coin")
	var post: Node = level.get_node(^"Checkpoints/Checkpoint1")
	play.game.request_action(post, &"activate", [])
	await until(
		func() -> bool: return level.get(&"checkpoint") == post,
		"the client's checkpoint comes back active"
	)


func host_check(level: Node) -> void:
	var coin: Node = _first_coin(level)
	play.expect(bool(coin.get(&"collected")), "the client's coin is collected on the server")
	play.expect(int(level.get(&"score")) == 1, "the server counts the client's coin")
	var post: Node = level.get_node(^"Checkpoints/Checkpoint1")
	play.expect(level.get(&"checkpoint") == post, "the client's checkpoint is active on the server")
	var goal: Node = level.get_node(^"Goals").get_child(0)
	var finished: Dictionary = play.watch(play.events.level_completed)
	play.game.request_action(goal, &"reach", [])
	await play.wait_watched(finished, 30)
	play.expect(bool(level.get(&"completed")), "reaching the flag finishes the level on the server")
	var extra: Dictionary = adapter().call(&"snapshot_extra")
	var path: String = str(level.get_path_to(goal))
	play.expect(
		extra.has(path) and bool((extra[path] as Dictionary).get("done", false)),
		"a late joiner is sent the reached flag: %s" % [extra]
	)


func client_verify(level: Node) -> void:
	await until(
		func() -> bool: return bool(level.get(&"completed")),
		"the server's flag finishes the level here"
	)
	var mine: Node = play.game.local_player()
	play.expect(
		mine != null and not bool(mine.get(&"controls_enabled")), "the finished level stops this player"
	)


func _first_coin(level: Node) -> Node:
	return level.get_node(^"Coins").get_child(0)
