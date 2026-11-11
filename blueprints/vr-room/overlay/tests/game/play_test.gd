extends SceneTree
## Play test for the VR room on the desktop, driven by injected input.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##        -- --autostart --settings-path=<file>
##
## Positions and view angles are set directly only to stage a check; every
## outcome comes from input going through the real input map.

const TEST_NAME := "vr-room"
const EYE_HEIGHT := 1.6

var h: PlayHarness
var game: Node
var events: Node
var menus: Node
var settings: Node


func _initialize() -> void:
	h = PlayHarness.new(self, TEST_NAME)
	_run.call_deferred()


func _run() -> void:
	game = h.game
	events = h.events
	menus = h.menus
	settings = h.settings
	settings.call(&"set_value", "look", "sensitivity", 1.0)
	settings.call(&"set_value", "input", "invert_look_y", false)

	await h.boot()
	var level: Node = game.call(&"current_level")
	var player: CharacterBody3D = game.call(&"local_player") as CharacterBody3D
	h.expect(level != null, "the level started")
	h.expect(player != null, "a local player was spawned")
	if level == null or player == null:
		h.finish()
		return
	_check_vr_off(player)
	await h.physics_frames(5)
	await _check_look(player)
	await _check_walk(player)
	await _check_teleport_marks(level)
	await _check_case_is_closed(level, player)
	await _check_pick_up_and_drop(level, player)
	await _check_button(level, player)
	await _check_save_round_trip(level, player)
	await _check_pause(player)
	await _check_goal(level, player)
	await _check_restart()
	h.finish()


# --- staging helpers ---------------------------------------------------------


func _head(player: Node3D) -> Node3D:
	return player.get_node(^"Head") as Node3D


func _place(player: CharacterBody3D, at: Vector3, yaw: float, pitch: float = 0.0) -> void:
	player.global_position = at
	player.rotation = Vector3(0.0, yaw, 0.0)
	_head(player).rotation = Vector3(pitch, 0.0, 0.0)
	player.velocity = Vector3.ZERO


## Stands the player at `at` looking straight at `point`.
func _place_aiming(player: CharacterBody3D, at: Vector3, point: Vector3) -> void:
	var eye: Vector3 = at + Vector3(0.0, EYE_HEIGHT, 0.0)
	var d: Vector3 = point - eye
	_place(player, at, atan2(-d.x, -d.z), atan2(d.y, Vector2(d.x, d.z).length()))


# --- checks ------------------------------------------------------------------


## Without the VR feature, or without a headset, this is a desktop game.
func _check_vr_off(player: CharacterBody3D) -> void:
	h.expect(player.get_node(^"Head/Camera3D").current, "the desktop camera is current")
	var vr: Node = root.get_node_or_null(^"VR")
	if vr != null:
		h.expect(not bool(vr.call(&"is_active")), "no headset here: VR stays off")
		h.expect(player.get_node_or_null(^"XRRig") == null, "no XR rig on the desktop")


func _check_look(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)
	await h.physics_frames(2)
	h.mouse_motion(Vector2(100.0, 0.0))
	await h.physics_frames(1)
	h.expect_near(player.rotation.y, -0.25, 0.001, "mouse right turns the view right")
	h.mouse_motion(Vector2(0.0, -100.0))
	await h.physics_frames(1)
	h.expect_near(_head(player).rotation.x, 0.25, 0.001, "mouse up pitches the view up")
	h.mouse_motion(Vector2(0.0, -10000.0))
	await h.physics_frames(1)
	h.expect_near(_head(player).rotation.x, deg_to_rad(85.0), 0.0001, "pitch is clamped")
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)
	await h.hold(&"look_left", 20)
	h.expect(player.rotation.y > 0.5, "look_left turns the view left (%.2f)" % player.rotation.y)
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)


func _check_walk(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)
	await h.physics_frames(3)
	var start: Vector3 = player.global_position
	await h.hold(&"move_forward", 40)
	await h.physics_frames(10)
	var moved: Vector3 = player.global_position - start
	h.expect(moved.z < -1.0, "move_forward walks forward (dz %.2f)" % moved.z)
	h.expect(absf(moved.x) < 0.05, "and keeps a straight line (dx %.2f)" % moved.x)
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)
	await h.physics_frames(3)
	start = player.global_position
	await h.hold(&"move_right", 40)
	await h.physics_frames(10)
	h.expect((player.global_position - start).x > 1.0, "move_right steps right")
	h.expect(player.global_position.y > -0.1, "the floor holds the player")


## The level marks where a VR teleport may not land.
func _check_teleport_marks(level: Node) -> void:
	for path: NodePath in [^"Table", ^"CasePedestal", ^"ButtonPost", ^"Shelf", ^"Room/WallNorth"]:
		h.expect(level.get_node(path).is_in_group(&"no_teleport"), "%s is not a landing" % path)
	h.expect(not level.get_node(^"Room/Floor").is_in_group(&"no_teleport"), "the floor is")
	await h.physics_frames(1)


func _check_case_is_closed(level: Node, player: CharacterBody3D) -> void:
	var cube: Node3D = level.get_node(^"Pickups/Cube") as Node3D
	h.expect(not bool(level.get(&"case_open")), "the glass case starts closed")
	h.expect(not bool(cube.call(&"can_grab")), "the cube cannot be taken out of the closed case")
	_place_aiming(player, Vector3(-1.6, 0.0, 0.2), cube.global_position)
	await h.physics_frames(3)
	await h.tap(&"interact")
	h.expect(player.get(&"held") == null, "interact through the glass takes nothing")


func _check_pick_up_and_drop(level: Node, player: CharacterBody3D) -> void:
	var ball: RigidBody3D = level.get_node(^"Pickups/Ball") as RigidBody3D
	_place_aiming(player, Vector3(1.6, 0.0, 0.8), ball.global_position)
	await h.physics_frames(3)
	h.expect(player.call(&"target") == ball, "the aim finds the ball")
	await h.tap(&"interact")
	h.expect(player.get(&"held") == ball, "interact picks up the ball")
	h.expect(bool(ball.call(&"is_held")) and ball.freeze, "a held ball is frozen to its holder")
	var hold: Node3D = player.get_node(^"Head/Camera3D/HoldPoint") as Node3D
	await h.hold(&"move_back", 30)
	await h.physics_frames(3)
	h.expect(
		ball.global_position.distance_to(hold.global_position) < 0.05,
		"the ball moves with the player"
	)
	var before: float = ball.global_position.y
	await h.tap(&"interact")
	h.expect(player.get(&"held") == null, "interact again lets go")
	h.expect(not ball.freeze, "a dropped ball is a free body again")
	await h.physics_frames(60)
	h.expect(ball.global_position.y < before - 0.5, "the dropped ball falls")
	h.expect(ball.global_position.y > 0.0, "and lands on the floor")


func _check_button(level: Node, player: CharacterBody3D) -> void:
	var button: Node3D = level.get_node(^"PushButton") as Node3D
	_place_aiming(player, Vector3(1.6, 0.0, 0.0), button.global_position + Vector3(0.0, 0.05, 0.0))
	await h.physics_frames(3)
	h.expect(player.call(&"target") == button, "the aim finds the button")
	await h.tap(&"interact")
	await h.physics_frames(40)
	h.expect(bool(button.get(&"is_pressed")), "interact presses the button")
	h.expect(bool(level.get(&"case_open")), "the button opens the glass case")
	var cube: Node3D = level.get_node(^"Pickups/Cube") as Node3D
	h.expect(bool(cube.call(&"can_grab")), "the cube can be taken now")


func _check_save_round_trip(level: Node, player: CharacterBody3D) -> void:
	var cube: RigidBody3D = level.get_node(^"Pickups/Cube") as RigidBody3D
	var state: Dictionary = game.call(&"capture_state")
	for id: String in ["level", "button", "cube", "ball", "can", "player"]:
		h.expect(state.has(id), "%s is saved" % id)
	var saved_cube: Transform3D = cube.global_transform
	cube.global_position += Vector3(0.5, 0.0, 0.0)
	game.call(&"apply_state", state)
	h.expect(
		cube.global_transform.origin.distance_to(saved_cube.origin) < 0.001,
		"loading puts the cube back"
	)
	h.expect(bool(level.get(&"case_open")), "the open case stays open after a load")
	await h.physics_frames(2)
	_place(player, Vector3(0.0, 0.0, 2.0), 0.0)


func _check_pause(player: CharacterBody3D) -> void:
	await h.tap(&"pause")
	h.expect(bool(menus.call(&"is_open")), "pause opens the menu")
	h.expect(not bool(player.call(&"can_control")), "the menu takes the controls")
	await h.tap(&"pause")
	h.expect(not bool(menus.call(&"is_open")), "pause again closes it")
	h.expect(bool(player.call(&"can_control")), "and gives the controls back")


func _check_goal(level: Node, player: CharacterBody3D) -> void:
	var cube: RigidBody3D = level.get_node(^"Pickups/Cube") as RigidBody3D
	var finished: Dictionary = h.watch(events.level_completed)
	_place_aiming(player, Vector3(-1.6, 0.0, -0.2), cube.global_position)
	await h.physics_frames(3)
	await h.tap(&"interact")
	h.expect(player.get(&"held") == cube, "interact takes the cube")
	h.expect(not bool(level.call(&"cube_on_table")), "a held cube is not on the table")
	var table: Node3D = level.get_node(^"Table") as Node3D
	_place_aiming(
		player, Vector3(0.0, 0.0, -1.85), table.global_position + Vector3(0.0, 0.75, -0.7)
	)
	await h.physics_frames(3)
	await h.tap(&"interact")
	var result: Array = await h.wait_watched(finished, 240)
	h.expect(bool(level.get(&"completed")), "the cube on the table completes the level")
	h.expect(
		result.size() == 1 and float((result[0] as Dictionary).get("time", 0.0)) > 0.0,
		"the result carries the time: %s" % [result]
	)
	await h.physics_frames(2)
	var page: Control = menus.call(&"current_page") as Control
	h.expect(page != null and page.name == "WinScreen", "the win page opens")
	h.expect(not bool(player.get(&"controls_enabled")), "the win stops the player")
	if page != null:
		h.expect_min_text(page, "win page")
		h.expect(
			page.find_child("PlayAgain", true, false) != null, "the win page offers Play again"
		)


func _check_restart() -> void:
	game.call(&"restart_level")
	await h.wait_for(events.level_started, 300)
	var level: Node = game.call(&"current_level")
	h.expect(level != null and not bool(level.get(&"completed")), "restart starts afresh")
	h.expect(level != null and not bool(level.get(&"case_open")), "with the case closed again")
	h.expect(not bool(menus.call(&"is_open")), "and no menu open")
