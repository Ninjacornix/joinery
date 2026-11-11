extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the VR room: the joining player's button press runs on
## the server and opens the glass case everywhere; when the server sees the
## cube on the table, the joining player gets `level_completed` as well.

var _finished: Dictionary = {}


func client_check(level: Node) -> void:
	_finished = play.watch(play.events.level_completed)
	var button: Node = level.get_node(^"PushButton")
	play.game.request_action(button, &"use", [])
	await until(
		func() -> bool: return bool(level.get(&"case_open")),
		"the client's button press opens the glass case here"
	)
	play.expect(bool(button.get(&"is_pressed")), "the button is pressed for the client")


func host_check(level: Node) -> void:
	play.expect(bool(level.get(&"case_open")), "the client's press opened the case on the server")
	# Held objects stay in each player's own game, so the server sets the cube
	# on the table itself: the server decides the win.
	var cube: RigidBody3D = level.get_node(^"Pickups/Cube") as RigidBody3D
	var table: Node3D = level.get_node(^"Table") as Node3D
	cube.global_position = table.global_position + Vector3(0.0, 0.85, 0.0)
	cube.linear_velocity = Vector3.ZERO
	await until(
		func() -> bool: return bool(level.get(&"completed")),
		"the cube on the table completes the level on the server"
	)


func client_verify(level: Node) -> void:
	await until(
		func() -> bool: return play.fire_count(_finished) > 0, "the client gets level_completed"
	)
	play.unwatch(_finished)
	play.expect(bool(level.get(&"completed")), "the level is complete for the client too")
	var page: Control = play.menus.current_page()
	play.expect(page != null and page.name == "WinScreen", "the client sees the win page")
	# The win page opens through the menus; close it as a player would, so
	# the pause checks that follow start from the game.
	play.menus.close_all()
	await tree.process_frame
