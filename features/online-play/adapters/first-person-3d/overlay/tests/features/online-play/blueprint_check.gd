extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the first-person rooms: the joining player's switches
## run on the server and open the door everywhere; when the joining player
## walks into the exit, the server completes the level and the joining player
## gets `level_completed` as well.


func client_check(level: Node) -> void:
	var door: Node = level.get_node(^"Door")
	var finished: Dictionary = play.watch(play.events.level_completed)
	for switch: Node in level.get_node(^"Switches").get_children():
		play.game.request_action(switch, &"use", [])
	await until(
		func() -> bool: return bool(door.get(&"is_open")),
		"the client's switches open the door"
	)
	for switch: Node in level.get_node(^"Switches").get_children():
		play.expect(bool(switch.get(&"on")), "%s is on for the client" % switch.name)
	var mine: Node3D = play.game.local_player() as Node3D
	var exit_zone: Node3D = level.get_node(^"ExitZone") as Node3D
	if mine != null:
		mine.global_position = exit_zone.global_position + Vector3(0.0, -0.9, 0.0)
	await until(
		func() -> bool: return bool(level.get(&"completed")),
		"walking into the exit completes the level here"
	)
	await until(
		func() -> bool: return play.fire_count(finished) > 0,
		"the client gets level_completed"
	)
	await play.wait_watched(finished, 1)
	var result: Array = finished["args"]
	play.expect(
		result.size() == 1 and int((result[0] as Dictionary).get("switches", 0)) == 3,
		"the result counts three switches: %s" % [result]
	)


func host_check(level: Node) -> void:
	play.expect(bool(level.get_node(^"Door").get(&"is_open")), "the door is open on the server")
	play.expect(bool(level.get(&"completed")), "the client's walk into the exit completed the level")
	for switch: Node in level.get_node(^"Switches").get_children():
		play.expect(bool(switch.get(&"locked")), "%s is locked once the door opened" % switch.name)
