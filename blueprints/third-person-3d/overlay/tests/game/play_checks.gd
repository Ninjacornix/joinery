extends RefCounted
## The third-person arena checks, run by play_test.gd once the autoloads
## exist. Injects input through the core harness and checks what the player
## would see happen.

const Harness := preload("res://tests/support/play.gd")
const Player := preload("res://game/player/player.gd")
const OrbitCamera := preload("res://game/player/orbit_camera.gd")
const Level := preload("res://game/level.gd")
const Coin := preload("res://game/props/coin.gd")
const Crate := preload("res://game/props/crate.gd")
const Lever := preload("res://game/props/lever.gd")
const Mover := preload("res://game/props/mover.gd")
const Plate := preload("res://game/props/pressure_plate.gd")
const Checkpoint := preload("res://game/props/checkpoint.gd")
const CameraSettingsPage := preload("res://game/ui/camera_settings_page.gd")
const AnimationChecks := preload("res://tests/game/animation_checks.gd")

var tree: SceneTree
var root: Window
var h: Harness
var game: Node
var events: Node
var settings: Node
var level: Level
var player: Player
var rig: OrbitCamera


func _init(p_tree: SceneTree) -> void:
	tree = p_tree
	root = p_tree.root


func run() -> void:
	h = Harness.new(tree, "third-person-3d")
	game = root.get_node(^"Game")
	events = root.get_node(^"GameEvents")
	settings = root.get_node(^"Settings")
	await h.boot()
	_refresh()
	h.expect(level != null, "the level scene starts")
	h.expect(player != null, "a local player is spawned")
	if level == null or player == null:
		h.finish()
		return
	await h.physics_frames(10)
	h.expect(player.is_on_floor(), "the player starts on the floor")

	await _check_camera()
	await _check_move()
	await _check_jump()
	await _check_coin()
	await _check_checkpoint_and_fall()
	await _check_carry_and_plate()
	await _check_lever()
	await _check_save_round_trip()
	await _check_pause()
	await _check_settings()
	_check_text_sizes()
	await _check_win()
	await _check_pause_restart()
	# Last: walking about collects coins and reaches checkpoints.
	_refresh()
	if player != null:
		await _check_animations()
	h.finish()


func _refresh() -> void:
	level = game.call(&"current_level") as Level
	player = game.call(&"local_player") as Player
	rig = player.camera_rig if player != null else null


func _place(at: Vector3, yaw: float = 0.0) -> void:
	player.respawn(Transform3D(Basis(Vector3.UP, yaw), at))


func _wait_until(condition: Callable, max_frames: int) -> bool:
	for i: int in max_frames:
		if condition.call():
			return true
		await tree.physics_frame
	return bool(condition.call())


func _check_camera() -> void:
	var yaw: float = rig.yaw
	await h.hold(&"look_right", 20)
	h.expect(rig.yaw < yaw - 0.3, "look_right orbits the camera to the right")
	yaw = rig.yaw
	await h.hold(&"look_left", 10)
	h.expect(rig.yaw > yaw + 0.15, "look_left orbits the camera to the left")
	var pitch: float = rig.pitch
	await h.hold(&"look_up", 10)
	h.expect(rig.pitch > pitch + 0.1, "look_up tilts the camera up")

	yaw = rig.yaw
	h.mouse_motion(Vector2(-80.0, 0.0))
	await h.physics_frames(2)
	h.expect(rig.yaw > yaw + 0.2, "mouse motion to the left yaws the camera left")
	pitch = rig.pitch
	h.mouse_motion(Vector2(0.0, 40.0))
	await h.physics_frames(2)
	h.expect(rig.pitch < pitch - 0.1, "mouse motion down pitches the camera down")
	h.mouse_motion(Vector2(0.0, 5000.0))
	await h.physics_frames(2)
	h.expect_near(rig.pitch, rig.min_pitch, 0.001, "pitch is clamped looking down")
	h.mouse_motion(Vector2(0.0, -5000.0))
	await h.physics_frames(2)
	h.expect_near(rig.pitch, rig.max_pitch, 0.001, "pitch is clamped looking up")
	rig.pitch = -0.3
	await h.physics_frames(2)
	var to_player: Vector3 = player.global_position - rig.camera.global_position
	var looking: float = to_player.normalized().dot(-rig.camera.global_basis.z)
	h.expect(looking > 0.8, "the camera looks at the player")


func _check_move() -> void:
	_place(Vector3(0.0, 0.05, 3.0))
	rig.yaw = 0.9
	await h.physics_frames(4)
	var start: Vector3 = player.global_position
	var forward: Vector3 = rig.flat_forward()
	await h.hold(&"move_forward", 30)
	var moved: Vector3 = player.global_position - start
	moved.y = 0.0
	h.expect(moved.length() > 1.5, "move_forward moves the player")
	h.expect(moved.normalized().dot(forward) > 0.95, "move_forward follows the camera forward")
	var facing: Vector3 = -player.pivot.global_basis.z
	h.expect(facing.dot(forward) > 0.9, "the player turns to face the way it runs")
	await h.physics_frames(20)

	start = player.global_position
	await h.hold(&"move_right", 20)
	moved = player.global_position - start
	moved.y = 0.0
	var right: Vector3 = forward.cross(Vector3.UP)
	h.expect(moved.normalized().dot(right) > 0.9, "move_right strafes to the camera's right")
	await h.physics_frames(20)

	_place(Vector3(0.0, 0.05, 3.0), PI / 2.0)
	await h.physics_frames(4)
	start = player.global_position
	h.press(&"sprint")
	await h.hold(&"move_forward", 40)
	h.release(&"sprint")
	var sprinted: float = (player.global_position - start).length()
	await h.physics_frames(20)
	_place(Vector3(0.0, 0.05, 3.0), PI / 2.0)
	await h.physics_frames(4)
	start = player.global_position
	await h.hold(&"move_forward", 40)
	var walked: float = (player.global_position - start).length()
	h.expect(sprinted > walked * 1.3, "sprint runs faster than walking")
	await h.physics_frames(20)


func _check_jump() -> void:
	_place(Vector3(0.0, 0.05, 3.0))
	await h.physics_frames(6)
	var ground: float = player.global_position.y
	var jumps: Array[int] = [0]
	var stretch: Array[float] = [1.0]
	var visual: Node3D = player.get_node(^"Pivot/Visual") as Node3D
	var on_jump: Callable = func() -> void:
		jumps[0] += 1
		stretch[0] = maxf(stretch[0], visual.scale.y)
	player.jumped.connect(on_jump)
	var short_peak: float = await _jump_peak(2)
	var landed: bool = await _wait_until(player.is_on_floor, 120)
	h.expect(landed, "the player lands after a hop")
	var tall_peak: float = await _jump_peak(30)
	await _wait_until(player.is_on_floor, 120)
	player.jumped.disconnect(on_jump)
	h.expect(jumps[0] == 2, "each jump press jumps once")
	h.expect(stretch[0] > 1.1, "the body stretches on take-off")
	await h.physics_frames(30)
	h.expect(
		visual.scale.is_equal_approx(Vector3.ONE) or visual.scale.distance_to(Vector3.ONE) < 0.02,
		"the squash and stretch settles back"
	)
	h.expect(short_peak > ground + 0.4, "a tapped jump raises the player")
	h.expect(tall_peak > short_peak + 0.8, "holding jump jumps higher than a tap")
	h.expect(tall_peak > ground + 2.5, "a held jump reaches platform height")
	h.expect_near(player.global_position.y, ground, 0.05, "the player lands back on the floor")


func _check_animations() -> void:
	var checks: AnimationChecks = AnimationChecks.new(h, tree)
	await checks.run(player, func() -> void: _place(Vector3(0.0, 0.05, 3.0)))


func _jump_peak(hold_frames: int) -> float:
	var peak: float = player.global_position.y
	h.press(&"jump")
	for i: int in 90:
		await tree.physics_frame
		if i == hold_frames:
			h.release(&"jump")
		peak = maxf(peak, player.global_position.y)
	h.release(&"jump")
	return peak


func _check_coin() -> void:
	var coin: Coin = level.coins.get_child(0) as Coin
	_place(coin.global_position + Vector3(0.0, -0.95, 2.5))
	await h.physics_frames(3)
	var scored: Dictionary = h.watch(events.score_changed)
	h.press(&"move_forward")
	var args: Array = await h.wait_watched(scored, 120)
	h.release(&"move_forward")
	h.expect(not args.is_empty(), "walking into a coin emits score_changed")
	h.expect(coin.collected, "walking into a coin collects it")
	h.expect(not coin.visible, "a collected coin disappears")
	h.expect(level.score == 1, "the level counts the coin")
	await h.physics_frames(20)


func _check_checkpoint_and_fall() -> void:
	var pad: Checkpoint = level.checkpoints.get_node(^"Arena") as Checkpoint
	_place(pad.global_position + Vector3(0.0, 0.05, 3.0))
	await h.physics_frames(3)
	var reached: Dictionary = h.watch(events.checkpoint_reached)
	h.press(&"move_forward")
	var args: Array = await h.wait_watched(reached, 120)
	h.release(&"move_forward")
	h.expect(
		not args.is_empty() and str(args[0]) == "arena", "walking onto a checkpoint reaches it"
	)
	h.expect(level.checkpoint == pad, "the reached checkpoint becomes the respawn point")

	_place(Vector3(40.0, 2.0, -20.0))
	var target: Vector3 = pad.respawn_transform().origin
	var fell: Array[bool] = [false]
	var back: bool = await _wait_until(
		func() -> bool:
			fell[0] = fell[0] or player.global_position.y < 0.0
			return fell[0] and player.global_position.distance_to(target) < 0.6,
		300
	)
	h.expect(back, "falling off the arena respawns the player at the checkpoint")
	await h.physics_frames(10)
	h.expect(player.is_on_floor(), "the respawned player stands on the floor")


func _check_carry_and_plate() -> void:
	var crate: Crate = level.get_node(^"Props/Crate") as Crate
	var plate: Plate = level.get_node(^"Props/GatePlate") as Plate
	var gate: Mover = level.get_node(^"Movers/Gate") as Mover
	h.expect(not gate.active, "the gate starts closed")
	_place(crate.global_position + Vector3(0.0, -0.45, 1.6))
	await h.physics_frames(4)
	h.expect(player.interact_target == crate, "the crate in front is the interact target")
	h.expect(level.hud.prompt_label.visible, "the HUD shows an interact prompt")
	await h.tap(&"interact")
	await h.physics_frames(2)
	h.expect(crate.is_carried(), "interact picks up the crate")
	h.expect(player.carrying == crate, "the player holds the crate")

	var start: Vector3 = player.global_position
	await h.hold(&"move_forward", 20)
	await h.physics_frames(2)
	h.expect(player.global_position.distance_to(start) > 1.0, "the player can walk while carrying")
	var hands: Vector3 = player.carry_transform().origin
	h.expect(crate.global_position.distance_to(hands) < 0.3, "the crate follows the player")

	_place(plate.global_position + Vector3(0.0, 0.05, 1.2))
	await h.physics_frames(3)
	h.expect(not plate.pressed, "a carried crate does not press the plate")
	await h.tap(&"interact")
	await h.physics_frames(2)
	h.expect(not crate.is_carried(), "interact puts the crate down")
	h.expect(player.carrying == null, "the player's hands are free again")
	var opened: bool = await _wait_until(gate.is_open, 180)
	h.expect(plate.pressed, "the crate presses the plate")
	h.expect(opened, "the pressed plate opens the gate")


func _check_lever() -> void:
	var lever: Lever = level.get_node(^"Props/StepLever") as Lever
	var step: Mover = level.get_node(^"Movers/Step") as Mover
	_place(lever.global_position + Vector3(0.0, 0.05, 1.3))
	await h.physics_frames(4)
	h.expect(player.interact_target == lever, "the lever in front is the interact target")
	await h.tap(&"interact")
	await h.physics_frames(2)
	h.expect(lever.on, "interact pulls the lever")
	var raised: bool = await _wait_until(step.is_open, 180)
	h.expect(raised, "the lever raises the step")


func _check_save_round_trip() -> void:
	var state: Dictionary = game.call(&"capture_state")
	for id: String in ["level", "player", "coin_1", "coin_5", "crate", "step_lever"]:
		h.expect(state.has(id), "capture_state includes %s" % id)
	var crate_at: Vector3 = (level.get_node(^"Props/Crate") as Node3D).global_position

	var restarted: Dictionary = h.watch(events.level_started)
	game.call(&"restart_level")
	await h.wait_watched(restarted, 300)
	await h.physics_frames(2)
	_refresh()
	var coin: Coin = level.coins.get_child(0) as Coin
	var lever: Lever = level.get_node(^"Props/StepLever") as Lever
	var crate: Crate = level.get_node(^"Props/Crate") as Crate
	h.expect(not coin.collected and level.score == 0, "restart resets the coins")
	h.expect(not lever.on, "restart resets the lever")
	h.expect(level.checkpoint.get(&"checkpoint_id") == "start", "restart resets the checkpoint")

	game.call(&"apply_state", state)
	await h.physics_frames(2)
	h.expect(coin.collected and level.score == 1, "load_state restores collected coins")
	h.expect(lever.on, "load_state restores the lever")
	var step: Mover = level.get_node(^"Movers/Step") as Mover
	h.expect(step.is_open(), "load_state restores the raised step")
	h.expect(crate.global_position.distance_to(crate_at) < 0.2, "load_state restores the crate")
	h.expect(
		level.checkpoint.get(&"checkpoint_id") == "arena", "load_state restores the checkpoint"
	)
	var gate: Mover = level.get_node(^"Movers/Gate") as Mover
	h.expect(await _wait_until(gate.is_open, 180), "the restored crate holds the gate open")


## Every coin can be reached by running and jumping: each hop starts on one
## surface and must land on the next, driven only by move_forward and jump.
func _check_routes() -> void:
	var hops: Array[Array] = [
		["the low tower", Vector3(8.0, 0.05, -10.8), Vector3(8.0, 1.0, -14.0)],
		["the middle tower", Vector3(9.2, 1.05, -15.2), Vector3(10.5, 2.5, -18.0)],
		["the top tower", Vector3(10.0, 2.55, -19.2), Vector3(8.0, 4.0, -22.0)],
		["the first stone", Vector3(-11.5, 0.05, -17.0), Vector3(-15.5, 0.0, -17.0)],
		["the second stone", Vector3(-16.2, 0.05, -18.0), Vector3(-18.5, 0.5, -21.0)],
		["the third stone", Vector3(-18.5, 0.55, -22.0), Vector3(-18.0, 1.0, -25.5)],
		["the ledge from the raised step", Vector3(-6.6, 3.0, -28.0), Vector3(-9.5, 5.5, -28.0)],
	]
	for hop: Array in hops:
		var landed: bool = await _hop(hop[1] as Vector3, hop[2] as Vector3)
		h.expect(landed, "a running jump reaches %s" % str(hop[0]))
	var pen_coin: Coin = level.coins.get_node(^"Coin4") as Coin
	_place(Vector3(0.0, 0.05, -26.0))
	await h.physics_frames(3)
	h.press(&"move_forward")
	var got: bool = await _wait_until(func() -> bool: return pen_coin.collected, 150)
	h.release(&"move_forward")
	h.expect(got, "the open gate lets the player walk to the penned coin")
	await h.physics_frames(30)


func _hop(from: Vector3, to: Vector3) -> bool:
	var flat: Vector3 = Vector3(to.x - from.x, 0.0, to.z - from.z)
	_place(from, atan2(-flat.x, -flat.z))
	await h.physics_frames(4)
	h.press(&"move_forward")
	await h.physics_frames(3)
	h.press(&"jump")
	var airborne: bool = false
	for i: int in 150:
		await tree.physics_frame
		if i == 30:
			h.release(&"jump")
		# Let go of forward over the target, as a player would.
		var left: Vector2 = Vector2(
			to.x - player.global_position.x, to.z - player.global_position.z
		)
		if left.length() < 1.2:
			h.release(&"move_forward")
		if not player.is_on_floor():
			airborne = true
		elif airborne:
			break
	h.release(&"jump")
	h.release(&"move_forward")
	await h.physics_frames(20)
	var at: Vector3 = player.global_position
	var ok: bool = absf(at.y - to.y) < 0.2 and Vector2(at.x - to.x, at.z - to.z).length() < 1.7
	if not ok:
		print("hop from %s to %s ended at %s" % [from, to, at])
	return ok


func _check_pause() -> void:
	var menus: Node = root.get_node(^"Menus")
	_place(Vector3(0.0, 0.05, 3.0))
	await h.physics_frames(4)
	await h.tap(&"pause")
	await h.physics_frames(2)
	h.expect(tree.paused, "pause pauses the tree")
	h.expect(bool(menus.call(&"is_open")), "pause opens the pause menu")
	var at: Vector3 = player.global_position
	await h.hold(&"move_forward", 10)
	h.expect(
		player.global_position.distance_to(at) < 0.001, "the player does not move while paused"
	)
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	h.expect(not tree.paused, "Resume (ui_accept) unpauses")
	h.expect(not bool(menus.call(&"is_open")), "Resume closes the pause menu")


func _check_settings() -> void:
	settings.call(&"set_value", "camera", "fov", 90.0)
	h.expect_near(rig.camera.fov, 90.0, 0.01, "the field of view setting applies at once")
	settings.call(&"set_value", "camera", "invert_y", true)
	var pitch: float = rig.pitch
	rig.rotate_view(0.0, 0.1)
	h.expect(rig.pitch < pitch, "invert vertical look flips the pitch direction")
	settings.call(&"set_value", "camera", "invert_y", false)
	rig.pitch = -0.3

	var menus: Node = root.get_node(^"Menus")
	menus.call(&"open_settings")
	await h.physics_frames(2)
	h.expect(await h.focus_button("Camera"), "Settings lists a Camera page")
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	var page: Control = menus.call(&"current_page")
	if page == null:
		page = CameraSettingsPage.build()
		root.add_child(page)
	var slider: HSlider = page.find_child("camera_fov", true, false) as HSlider
	h.expect(slider != null, "the camera settings page has a field of view slider")
	if slider != null:
		h.expect_near(slider.value, 90.0, 0.01, "the slider shows the saved value")
		slider.value = 75.0
		var saved: float = float(settings.call(&"get_value", "camera", "fov", 0.0))
		h.expect_near(saved, 75.0, 0.01, "moving the slider saves the setting")
		h.expect_near(rig.camera.fov, 75.0, 0.01, "moving the slider updates the camera")
	h.expect_min_text(page, "camera settings page")
	menus.call(&"close_all")
	await h.physics_frames(2)
	settings.call(&"set_value", "camera", "fov", 70.0)


func _check_text_sizes() -> void:
	h.expect_min_text(level.hud.get_child(0), "HUD")


func _check_win() -> void:
	var done: Array = []
	var on_done: Callable = func(result: Dictionary) -> void: done.append(result)
	events.level_completed.connect(on_done)
	var start_time: float = level.elapsed
	await _check_routes()
	for node: Node in level.coins.get_children():
		var coin: Coin = node as Coin
		if coin.collected:
			continue
		_place(coin.global_position + Vector3(0.0, -0.95, 0.0))
		var got: bool = await _wait_until(func() -> bool: return coin.collected, 30)
		h.expect(got, "touching %s collects it" % coin.save_id)
	await h.physics_frames(2)
	events.level_completed.disconnect(on_done)
	h.expect(done.size() == 1, "collecting every coin completes the level once")
	if done.size() == 1:
		var result: Dictionary = done[0]
		h.expect(int(result.get("score", -1)) == 5, "the result reports five coins")
		h.expect(float(result.get("time", 0.0)) >= start_time, "the result reports the time")
	h.expect(level.hud.win_panel.visible, "the win screen shows")
	h.expect_min_text(level.hud.win_panel, "win screen")
	h.expect(h.focused() == level.hud.again_button, "Play again has focus on the win screen")
	h.expect(not player.controls_enabled, "the player stops taking input after the win")
	var at: Vector3 = player.global_position
	await h.hold(&"move_forward", 10)
	var drift: Vector2 = Vector2(player.global_position.x - at.x, player.global_position.z - at.z)
	h.expect(drift.length() < 0.01, "won players stay put")

	await h.physics_frames(2)
	var restarted: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	var args: Array = await h.wait_watched(restarted, 300)
	h.expect(not args.is_empty(), "Play again restarts the level")
	await h.physics_frames(2)
	_refresh()
	h.expect(level.score == 0 and not level.completed, "the restarted level starts fresh")
	h.expect(not level.hud.win_panel.visible, "the win screen is gone after Play again")


func _check_pause_restart() -> void:
	var coin: Coin = level.coins.get_child(0) as Coin
	_place(coin.global_position + Vector3(0.0, -0.95, 0.0))
	await _wait_until(func() -> bool: return coin.collected, 30)
	h.expect(level.score == 1, "a coin is collected before restarting")
	await h.tap(&"pause")
	h.expect(await h.focus_button("Restart"), "the pause menu offers Restart")
	var restarted: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	await h.wait_watched(restarted, 300)
	await h.physics_frames(2)
	_refresh()
	h.expect(not tree.paused, "Restart unpauses the game")
	h.expect(level.score == 0, "Restart from the pause menu resets the coins")
	h.expect(level.elapsed < 1.0, "Restart resets the timer")
