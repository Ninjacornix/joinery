extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the third-person arena: the joining player's coin, lever
## pull and crate pick-up run on the server and come back (the crate in the
## joining player's own hands); its copy of the crate follows the server
## instead of simulating; collecting the last coins on the server wins the
## level for the joining player too.


func client_check(level: Node) -> void:
	var crate: RigidBody3D = level.get_node(^"Props/Crate") as RigidBody3D
	play.expect(crate.freeze, "the client's crate follows the server instead of simulating")
	var coin: Node = level.get_node(^"Coins/Coin1")
	play.game.request_action(coin, &"collect", [])
	await until(
		func() -> bool: return bool(coin.get(&"collected")),
		"the server collects the client's coin"
	)
	# The coin and the level's score replicate as separate messages.
	await until(func() -> bool: return int(level.get(&"score")) == 1, "the client counts the coin")
	var lever: Node = level.get_node(^"Props/StepLever")
	play.game.request_action(lever, &"interact", [])
	await until(func() -> bool: return bool(lever.get(&"on")), "the client's lever pull comes back")
	var mine: Node = play.game.local_player()
	play.game.request_action(crate, &"interact", [])
	await until(
		func() -> bool: return crate.get(&"carrier") == mine,
		"the client picks up the crate"
	)
	play.expect(
		mine != null and mine.get(&"carrying") == crate, "the client's player carries the crate"
	)
	play.game.request_action(crate, &"interact", [])
	await until(
		func() -> bool: return crate.get(&"carrier") == null,
		"the client puts the crate down"
	)
	play.expect(crate.freeze, "the put-down crate still follows the server")


func host_check(level: Node) -> void:
	var coin: Node = level.get_node(^"Coins/Coin1")
	play.expect(bool(coin.get(&"collected")), "the client's coin is collected on the server")
	var lever: Node = level.get_node(^"Props/StepLever")
	play.expect(bool(lever.get(&"on")), "the client's lever is on, on the server")
	var crate: RigidBody3D = level.get_node(^"Props/Crate") as RigidBody3D
	play.expect(crate.get(&"carrier") == null, "the server's crate is put down")
	play.expect(not crate.freeze, "the server simulates the crate")
	for other: Node in level.get_node(^"Coins").get_children():
		play.game.request_action(other, &"collect", [])
	play.expect(bool(level.get(&"completed")), "every coin wins the level on the server")


func client_verify(level: Node) -> void:
	await until(
		func() -> bool: return bool(level.get(&"completed")),
		"the server's coins win the level here"
	)
	await until(
		func() -> bool: return int(level.get(&"score")) == int(level.get(&"total")),
		"the client counts every coin"
	)
