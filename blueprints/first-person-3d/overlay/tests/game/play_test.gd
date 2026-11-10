extends SceneTree
## Play test for the first-person game, driven by injected input.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##        -- --autostart --settings-path=<file>
##
## Positions and view angles are set directly only to stage a check; every
## outcome comes from input going through the real input map.

const TEST_NAME := "first-person-3d"
const EYE_HEIGHT := 1.6
const PITCH_LIMIT := deg_to_rad(85.0)

var h: PlayHarness
var game: Node
var events: Node
var menus: Node
var settings: Node
var last_score: int = -1


func _initialize() -> void:
	h = PlayHarness.new(self, TEST_NAME)
	_run.call_deferred()


func _run() -> void:
	game = root.get_node(^"Game")
	events = root.get_node(^"GameEvents")
	menus = root.get_node(^"Menus")
	settings = root.get_node(^"Settings")
	events.connect(&"score_changed", func(value: int) -> void: last_score = value)
	_reset_look_settings()

	await h.boot()
	var level: Node = game.call(&"current_level")
	h.expect(level != null, "the level started")
	if level == null:
		h.finish()
		return
	var player: CharacterBody3D = game.call(&"local_player") as CharacterBody3D
	h.expect(player != null, "a local player was spawned")
	if player == null:
		h.finish()
		return
	h.expect(player.get_node(^"Head/Camera3D").current, "the player's camera is current")
	await h.physics_frames(5)

	await _check_look(player)
	await _check_walk(player)
	await _check_jump(player)
	await _check_interact(level, player)
	await _check_save_round_trip(level, player)
	await _check_pause_and_settings(player)
	await _check_restart(level)

	level = game.call(&"current_level")
	player = game.call(&"local_player") as CharacterBody3D
	if level != null and player != null:
		h.expect_min_text(player.get_node(^"Hud"), "HUD")
		await _check_play_through(level, player)
	h.finish()


func _reset_look_settings() -> void:
	settings.call(&"set_value", "look", "sensitivity", 1.0)
	settings.call(&"set_value", "look", "fov", 75.0)
	settings.call(&"set_value", "input", "invert_look_y", false)


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
	var yaw: float = atan2(-d.x, -d.z)
	var pitch: float = atan2(d.y, Vector2(d.x, d.z).length())
	_place(player, at, yaw, pitch)


func _lamp(switch: Node3D) -> Vector3:
	return (switch.get_node(^"Lamp") as Node3D).global_position


func _switch(level: Node, index: int) -> Node3D:
	return level.get_node(NodePath("Switches/Switch%d" % index)) as Node3D


func _yaw(player: Node3D) -> float:
	return player.rotation.y


func _pitch(player: Node3D) -> float:
	return _head(player).rotation.x


# --- checks ------------------------------------------------------------------

func _check_look(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.physics_frames(2)
	var yaw0: float = _yaw(player)
	h.mouse_motion(Vector2(100.0, 0.0))
	await h.physics_frames(1)
	h.expect_near(angle_difference(yaw0, _yaw(player)), -0.25, 0.001,
		"mouse right turns the view right")

	h.mouse_motion(Vector2(0.0, -100.0))
	await h.physics_frames(1)
	h.expect_near(_pitch(player), 0.25, 0.001, "mouse up pitches the view up")
	h.mouse_motion(Vector2(0.0, -5000.0))
	await h.physics_frames(1)
	h.expect_near(_pitch(player), PITCH_LIMIT, 0.0001, "pitch is clamped looking up")
	h.mouse_motion(Vector2(0.0, 10000.0))
	await h.physics_frames(1)
	h.expect_near(_pitch(player), -PITCH_LIMIT, 0.0001, "pitch is clamped looking down")

	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	settings.call(&"set_value", "input", "invert_look_y", true)
	h.mouse_motion(Vector2(0.0, -100.0))
	await h.physics_frames(1)
	h.expect_near(_pitch(player), -0.25, 0.001, "invert look Y flips the pitch")
	settings.call(&"set_value", "input", "invert_look_y", false)

	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	settings.call(&"set_value", "look", "sensitivity", 2.0)
	h.mouse_motion(Vector2(100.0, 0.0))
	await h.physics_frames(1)
	h.expect_near(_yaw(player), -0.5, 0.001, "mouse sensitivity setting scales the turn")
	settings.call(&"set_value", "look", "sensitivity", 1.0)

	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.hold(&"look_right", 30)
	h.expect(_yaw(player) < -1.0, "look_right turns the view right (yaw %.2f)" % _yaw(player))
	await h.hold(&"look_up", 20)
	h.expect(_pitch(player) > 0.5, "look_up pitches the view up (pitch %.2f)" % _pitch(player))
	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)


func _check_walk(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.physics_frames(3)
	var start: Vector3 = player.global_position
	await h.hold(&"move_forward", 30)
	await h.physics_frames(10)
	var moved: Vector3 = player.global_position - start
	h.expect(moved.z < -1.0, "move_forward walks forward (dz %.2f)" % moved.z)
	h.expect(absf(moved.x) < 0.05, "move_forward keeps a straight line (dx %.2f)" % moved.x)

	_place(player, Vector3(0.0, 0.0, 4.0), PI * 0.5)
	await h.physics_frames(3)
	start = player.global_position
	await h.hold(&"move_forward", 30)
	await h.physics_frames(10)
	moved = player.global_position - start
	h.expect(moved.x < -1.0, "forward follows the view after turning left (dx %.2f)" % moved.x)

	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.physics_frames(3)
	start = player.global_position
	await h.hold(&"move_right", 30)
	await h.physics_frames(10)
	moved = player.global_position - start
	h.expect(moved.x > 1.0, "move_right strafes right (dx %.2f)" % moved.x)

	_place(player, Vector3(0.0, 0.0, 6.0), 0.0)
	await h.physics_frames(3)
	start = player.global_position
	await h.hold(&"move_back", 30)
	await h.physics_frames(10)
	var walked: float = (player.global_position - start).length()
	h.expect((player.global_position - start).z > 1.0, "move_back walks backwards")

	_place(player, Vector3(0.0, 0.0, 4.0), PI)
	await h.physics_frames(3)
	start = player.global_position
	h.press(&"sprint")
	await h.hold(&"move_forward", 30)
	h.release(&"sprint")
	await h.physics_frames(10)
	var sprinted: float = (player.global_position - start).length()
	h.expect(sprinted > walked + 0.5,
		"sprint covers more ground (%.2f vs %.2f)" % [sprinted, walked])


func _check_jump(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.physics_frames(5)
	h.expect(player.is_on_floor(), "the player stands on the floor")
	var ground: float = player.global_position.y
	await h.tap(&"jump")
	var highest: float = ground
	for _i: int in 90:
		await h.physics_frames(1)
		highest = maxf(highest, player.global_position.y)
	h.expect(highest > ground + 0.9, "jump rises (peak %.2f m)" % (highest - ground))
	h.expect(player.is_on_floor(), "the player lands after a jump")
	h.expect_near(player.global_position.y, ground, 0.05, "the player lands back on the floor")


func _check_interact(level: Node, player: CharacterBody3D) -> void:
	var switch: Node3D = _switch(level, 1)
	var lamp: Vector3 = _lamp(switch)
	h.expect(not bool(switch.get(&"on")), "switch 1 starts off")

	_place_aiming(player, Vector3(lamp.x, 0.0, lamp.z + 4.5), lamp)
	await h.physics_frames(2)
	h.expect(player.call(&"target") == null, "a switch out of range is not targeted")
	await h.tap(&"interact")
	h.expect(not bool(switch.get(&"on")), "interact out of range does nothing")

	_place_aiming(player, Vector3(lamp.x, 0.0, lamp.z + 1.8), lamp)
	player.rotation.y += PI
	await h.physics_frames(2)
	await h.tap(&"interact")
	h.expect(not bool(switch.get(&"on")), "interact while looking away does nothing")

	_place_aiming(player, Vector3(lamp.x, 0.0, lamp.z + 1.8), lamp)
	await h.physics_frames(2)
	h.expect(player.call(&"target") == switch, "the switch in view and range is targeted")
	var hint: Label = player.get_node(^"Hud").get(&"hint") as Label
	h.expect(hint != null and hint.visible and not hint.text.is_empty(),
		"the HUD shows a use hint over a switch")
	await h.tap(&"interact")
	h.expect(bool(switch.get(&"on")), "interact in range and in view turns the switch on")
	h.expect(last_score == 1, "score_changed reports one switch on (got %d)" % last_score)
	await h.tap(&"interact")
	h.expect(not bool(switch.get(&"on")), "interact again turns the switch off")
	await h.tap(&"interact")
	h.expect(bool(switch.get(&"on")), "and on again")
	h.expect(not bool(level.get(&"door").get(&"is_open")), "one switch does not open the door")


func _check_save_round_trip(level: Node, player: CharacterBody3D) -> void:
	var switch: Node3D = _switch(level, 1)
	var saved: Dictionary = game.call(&"capture_state")
	for id: String in ["player", "switch_1", "switch_2", "switch_3", "exit_door", "level"]:
		h.expect(saved.has(id), "capture_state includes %s" % id)
	var saved_position: Vector3 = player.global_position
	await h.tap(&"interact")
	h.expect(not bool(switch.get(&"on")), "the switch is off before loading")
	_place(player, Vector3(3.0, 0.0, 3.0), 1.0)
	await h.physics_frames(2)
	game.call(&"apply_state", saved)
	await h.physics_frames(1)
	h.expect(bool(switch.get(&"on")), "apply_state restores the switch")
	h.expect(player.global_position.distance_to(saved_position) < 0.05,
		"apply_state restores the player position")
	h.expect(last_score == 1, "loading reports the restored switch count")


func _check_pause_and_settings(player: CharacterBody3D) -> void:
	_place(player, Vector3(0.0, 0.0, 4.0), 0.0)
	await h.physics_frames(3)
	await h.tap(&"pause")
	h.expect(paused, "pause pauses the tree")
	h.expect(bool(menus.call(&"is_open")), "pause opens the pause menu")
	h.expect(h.focused_text() == "Resume", "Resume has focus (got '%s')" % h.focused_text())
	var before: Vector3 = player.global_position
	await h.hold(&"move_forward", 20)
	h.expect(player.global_position.distance_to(before) < 0.001,
		"the player does not move while paused")
	h.mouse_motion(Vector2(200.0, 0.0))
	await h.physics_frames(1)
	h.expect_near(_yaw(player), 0.0, 0.0001, "the view does not turn while paused")

	h.expect(await h.focus_button("Settings"), "the pause menu has Settings")
	await h.tap(&"ui_accept")
	h.expect(await h.focus_button("Camera"), "Settings lists the Camera page")
	await h.tap(&"ui_accept")
	var page: Control = menus.call(&"current_page")
	h.expect(page != null and page.name == "Settings_camera", "the Camera page opens")
	if page != null:
		h.expect_min_text(page, "Camera settings")
		var fov_slider: HSlider = page.find_child("look_fov", true, false) as HSlider
		h.expect(fov_slider != null, "the Camera page has a field of view slider")
		if fov_slider != null:
			fov_slider.value = 90.0
			var camera: Camera3D = player.get_node(^"Head/Camera3D")
			h.expect_near(camera.fov, 90.0, 0.01, "the field of view setting applies at once")
			h.expect_near(float(settings.call(&"get_value", "look", "fov", 0.0)), 90.0, 0.01,
				"the field of view is saved in Settings")
			fov_slider.value = 75.0
	for _i: int in 3:
		await h.tap(&"ui_cancel")
	h.expect(not paused, "backing out of the menus resumes the game")
	h.expect(not bool(menus.call(&"is_open")), "every menu is closed")

	await h.tap(&"pause")
	h.expect(paused, "pause again")
	await h.tap(&"ui_accept")
	h.expect(not paused, "Resume with ui_accept unpauses")
	await h.hold(&"move_forward", 20)
	h.expect(player.global_position.distance_to(before) > 0.3, "the player moves after resuming")


func _check_restart(level: Node) -> void:
	h.expect(bool(_switch(level, 1).get(&"on")), "switch 1 is on before the restart")
	await h.tap(&"pause")
	h.expect(await h.focus_button("Restart"), "the pause menu has Restart")
	h.press(&"ui_accept")
	await h.physics_frames(2)
	h.release(&"ui_accept")
	var started: Array = await h.wait_for(events.level_started, 240)
	h.expect(not started.is_empty(), "Restart starts the level again")
	await h.physics_frames(3)
	h.expect(not paused, "the restarted level is not paused")
	var fresh: Node = game.call(&"current_level")
	h.expect(fresh != null and fresh != level, "Restart loads a fresh level")
	if fresh == null:
		return
	h.expect(int(fresh.call(&"switches_on")) == 0, "Restart turns every switch off")
	var player: Node3D = game.call(&"local_player") as Node3D
	var spawn: Node3D = fresh.get_node(^"PlayerSpawn") as Node3D
	h.expect(player != null and player.global_position.distance_to(spawn.global_position) < 0.2,
		"Restart puts the player back at the spawn point")


func _check_play_through(level: Node, player: CharacterBody3D) -> void:
	var door: Node3D = level.get(&"door") as Node3D
	var completed: Array = []
	events.connect(&"level_completed", func(result: Dictionary) -> void: completed.append(result))

	var first: Node3D = _switch(level, 1)
	_place_aiming(player, Vector3(_lamp(first).x, 0.0, _lamp(first).z + 1.8), _lamp(first))
	await h.physics_frames(2)
	await h.tap(&"interact")
	h.expect(bool(first.get(&"on")), "play-through: switch 1 on")

	# Switch 2 stands on a ledge: walk at it and jump up.
	var second: Node3D = _switch(level, 2)
	_place(player, Vector3(second.global_position.x, 0.0, -2.0), 0.0)
	await h.physics_frames(3)
	h.press(&"move_forward")
	await h.physics_frames(40)
	h.expect(player.global_position.y < 0.2, "the ledge is too high to walk onto")
	await h.tap(&"jump")
	await h.physics_frames(40)
	h.release(&"move_forward")
	await h.physics_frames(20)
	h.expect(player.global_position.y > 0.8,
		"jumping lands the player on the ledge (y %.2f)" % player.global_position.y)
	_place_aiming(player, player.global_position, _lamp(second))
	await h.physics_frames(2)
	await h.tap(&"interact")
	h.expect(bool(second.get(&"on")), "play-through: switch 2 on")

	_place(player, Vector3(0.0, 0.0, -5.0), 0.0)
	await h.physics_frames(3)
	await h.hold(&"move_forward", 90)
	h.expect(player.global_position.z > -7.8,
		"the shut door blocks the doorway (z %.2f)" % player.global_position.z)
	h.expect(completed.is_empty(), "the level is not complete behind a shut door")
	h.expect(not bool(door.get(&"is_open")), "two switches leave the door shut")

	var third: Node3D = _switch(level, 3)
	_place_aiming(player, Vector3(_lamp(third).x, 0.0, _lamp(third).z + 1.8), _lamp(third))
	await h.physics_frames(2)
	await h.tap(&"interact")
	h.expect(bool(third.get(&"on")), "play-through: switch 3 on")
	h.expect(bool(door.get(&"is_open")), "the third switch opens the door")
	h.expect(last_score == 3, "score_changed reports three switches (got %d)" % last_score)
	await h.tap(&"interact")
	h.expect(bool(third.get(&"on")), "switches lock once the door is open")

	var saved: Dictionary = game.call(&"capture_state")
	door.call(&"load_state", {"open": false})
	h.expect(not bool(door.get(&"is_open")), "door state can be cleared")
	game.call(&"apply_state", saved)
	h.expect(bool(door.get(&"is_open")), "apply_state restores the open door")

	await h.physics_frames(90)
	h.expect(bool(door.call(&"is_fully_open")), "the door slides fully open")

	_place(player, Vector3(0.0, 0.0, -5.0), 0.0)
	await h.physics_frames(3)
	h.press(&"move_forward")
	var result: Array = await h.wait_for(events.level_completed, 360)
	h.release(&"move_forward")
	h.expect(not result.is_empty(), "walking through the exit completes the level")
	if not result.is_empty():
		var info: Dictionary = result[0]
		h.expect(float(info.get("time", 0.0)) > 0.0, "the result has a time")
		h.expect(int(info.get("score", -1)) == 3, "the result scores three switches")
		await _check_win_screen(player)

	# Loading a save from before the exit takes the win back.
	game.call(&"apply_state", saved)
	await h.physics_frames(2)
	var hud_win: Control = player.get_node(^"Hud").get(&"win_panel") as Control
	h.expect(hud_win != null and not hud_win.visible, "loading an earlier save hides the win screen")
	h.expect(bool(player.get(&"controls_enabled")), "loading an earlier save gives the controls back")
	h.expect(bool(root.get_node(^"FirstPersonLook").call(&"is_playing")),
		"loading an earlier save resumes play")


func _check_win_screen(player: CharacterBody3D) -> void:
	# Let the walk into the exit coast to a stop first.
	await h.physics_frames(30)
	var hud: Node = player.get_node(^"Hud")
	var win: Control = hud.get(&"win_panel") as Control
	h.expect(win != null and win.visible, "the win screen shows after the exit")
	h.expect(h.focused_text() == "Play again",
		"Play again has focus on the win screen (got '%s')" % h.focused_text())
	h.expect(not bool(player.get(&"controls_enabled")), "the win stops the player")
	var before: Vector3 = player.global_position
	var yaw0: float = _yaw(player)
	await h.hold(&"move_back", 20)
	h.mouse_motion(Vector2(200.0, 0.0))
	await h.physics_frames(1)
	h.expect(player.global_position.distance_to(before) < 0.05,
		"the player does not walk behind the win screen")
	h.expect_near(_yaw(player), yaw0, 0.0001, "the view does not turn behind the win screen")
