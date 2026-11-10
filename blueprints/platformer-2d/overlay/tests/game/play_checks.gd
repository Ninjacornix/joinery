extends RefCounted
## The checks behind play_test.gd. Loaded once the game is running, so this
## file and the game scripts it names can use the autoloads.

const Level := preload("res://game/level.gd")
const Player := preload("res://game/player/player.gd")
const Coin := preload("res://game/props/coin.gd")
const Checkpoint := preload("res://game/props/checkpoint.gd")
const GameplayPage := preload("res://game/ui/gameplay_page.gd")

var tree: SceneTree
var h: PlayHarness
var game: Node
var events: Node
var settings: Node
var menus: Node
var level: Level
var player: Player


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	game = h.game
	events = h.events
	settings = h.settings
	menus = h.menus
	settings.call(&"set_value", "platformer", "forgiving_jumps", false)
	settings.call(&"set_value", "platformer", "show_timer", true)
	await h.boot()
	_refresh()
	h.expect(level != null, "the level scene starts")
	h.expect(player != null, "a local player is spawned")
	if level == null or player == null:
		return

	await _check_start()
	await _check_run()
	await _check_jump_heights()
	await _check_coyote_time()
	await _check_jump_buffer()
	await _check_one_way()
	await _check_coin()
	await _check_checkpoint_and_spikes()
	await _check_pit()
	await _check_restart_action()
	await _check_save_round_trip()
	await _check_pause()
	await _check_gameplay_settings()
	_check_text_sizes()
	await _check_win()


func _refresh() -> void:
	level = game.call(&"current_level") as Level
	player = game.call(&"local_player") as Player


## Puts the player's feet at the bottom of `cell` and lets it settle.
func _place(cell: Vector2i, settle: int = 4) -> void:
	player.respawn(level.cell_bottom(cell))
	await h.physics_frames(settle)


func _wait_until(condition: Callable, max_frames: int) -> bool:
	for i: int in max_frames:
		if condition.call():
			return true
		await tree.physics_frame
	return bool(condition.call())


func _start_cell() -> Vector2i:
	for r: int in level.rows.size():
		var c: int = level.rows[r].find("P")
		if c >= 0:
			return Vector2i(c, r)
	return Vector2i(-1, -1)


## A ledge on the map: a free cell on solid ground whose right-hand side drops
## at least four tiles, with head room above.
func _find_ledge() -> Vector2i:
	for r: int in range(5, level.rows.size() - 4):
		for c: int in range(2, level.rows[r].length() - 4):
			var cell: Vector2i = Vector2i(c, r)
			if (
				_free(cell)
				and _free(cell + Vector2i.LEFT)
				and level.tile_at(cell + Vector2i.DOWN) == "#"
				and _free_box(c + 1, r, 3, 4)
				and _free_box(c - 1, r - 4, 5, 4)
			):
				return cell
	return Vector2i(-1, -1)


func _free(cell: Vector2i) -> bool:
	return level.tile_at(cell) == "."


func _free_box(c: int, r: int, width: int, height: int) -> bool:
	for dr: int in height:
		for dc: int in width:
			if not _free(Vector2i(c + dc, r + dr)):
				return false
	return true


func _check_start() -> void:
	await h.physics_frames(10)
	h.expect(player.is_on_floor(), "the player starts on the floor")
	var start: Vector2 = level.cell_bottom(_start_cell())
	h.expect(player.global_position.distance_to(start) < 1.0, "the player starts on the P tile")
	h.expect(level.total == 8 and level.score == 0, "the map places eight coins")
	h.expect(level.checkpoints.get_child_count() == 2, "the map places two checkpoints")
	h.expect(level.goals.get_child_count() == 1, "the map places the flag")
	h.expect(level.hazards.get_child_count() == 2, "spike tiles in a row form one hazard")
	h.expect(level.checkpoint == null, "the start is the first respawn point")
	var pages: Array[String] = []
	for page: Dictionary in menus.call(&"settings_pages"):
		pages.append(str(page["id"]))
	h.expect("gameplay" in pages, "the Gameplay settings page is registered at start-up")
	h.expect(player.camera.is_current(), "the player's camera is the current one")
	h.expect(player.camera.limit_right == int(level.bounds.end.x), "the camera stays inside the level")


func _check_run() -> void:
	await _place(_start_cell())
	var start: Vector2 = player.global_position
	h.press(&"move_right")
	await h.physics_frames(30)
	h.expect_near(player.velocity.x, player.run_speed, 0.5, "holding move_right reaches running speed")
	h.release(&"move_right")
	var released_at: float = player.global_position.x
	h.expect(released_at - start.x > 40.0, "holding move_right moves the player right")
	h.expect(player.visual.scale.x > 0.0, "the player faces right")
	await h.physics_frames(15)
	h.expect(absf(player.velocity.x) < 0.01, "the player stops when move_right is released")
	h.expect(player.global_position.x - released_at < 12.0, "the player stops quickly")
	h.expect(player.is_on_floor(), "running stays on the floor")

	var before: float = player.global_position.x
	await h.hold(&"move_left", 20)
	h.expect(player.global_position.x < before - 20.0, "holding move_left moves the player left")
	await h.physics_frames(15)
	h.expect(player.visual.scale.x < 0.0, "the player turns to face left")


func _check_jump_heights() -> void:
	await _place(_start_cell(), 6)
	var ground: float = player.global_position.y
	var record: Dictionary = h.watch(player.jumped)
	var tap_peak: float = await _jump_peak(2)
	h.expect(await _wait_until(player.is_on_floor, 120), "the player lands after a hop")
	var hold_peak: float = await _jump_peak(40)
	h.expect(await _wait_until(player.is_on_floor, 120), "the player lands after a full jump")
	await h.physics_frames(2)
	h.expect(h.fire_count(record) == 2, "each jump press jumps once")
	_unwatch(record)
	var tap_height: float = ground - tap_peak
	var hold_height: float = ground - hold_peak
	h.expect(tap_height > 4.0, "a tapped jump leaves the ground")
	h.expect(hold_height > tap_height + 20.0, "holding jump jumps higher than a tap")
	h.expect(hold_height > 48.0, "a held jump clears three tiles")
	h.expect_near(player.global_position.y, ground, 0.5, "the player lands back on the floor")


## Presses jump for `hold_frames` and returns the highest point (smallest y).
func _jump_peak(hold_frames: int) -> float:
	var peak: float = player.global_position.y
	h.press(&"jump")
	for i: int in 90:
		await tree.physics_frame
		if i == hold_frames:
			h.release(&"jump")
		peak = minf(peak, player.global_position.y)
	h.release(&"jump")
	return peak


func _check_coyote_time() -> void:
	var ledge: Vector2i = _find_ledge()
	h.expect(ledge.x >= 0, "the map has a ledge to run off")
	if ledge.x < 0:
		return
	h.expect(await _ledge_jump(ledge, 2), "jumping just after running off a ledge still jumps")
	h.expect(not await _ledge_jump(ledge, 8), "jumping well after leaving a ledge does not jump")


## Runs off `ledge`, waits `frames_after` physics frames in the air, presses
## jump and returns whether the player jumped.
func _ledge_jump(ledge: Vector2i, frames_after: int) -> bool:
	await _place(ledge + Vector2i.LEFT, 6)
	var record: Dictionary = h.watch(player.jumped)
	h.press(&"move_right")
	var left: bool = await _wait_until(func() -> bool: return not player.is_on_floor(), 90)
	h.expect(left, "the player runs off the ledge")
	h.expect(h.fire_count(record) == 0, "running off a ledge is not a jump")
	await h.physics_frames(frames_after)
	h.press(&"jump")
	await h.physics_frames(3)
	var jumped: bool = h.fire_count(record) > 0 and player.velocity.y < 0.0
	h.release(&"jump")
	h.release(&"move_right")
	await _wait_until(player.is_on_floor, 180)
	await h.physics_frames(12)
	if not jumped:
		h.expect(h.fire_count(record) == 0, "a late press in the air is not kept until landing")
	_unwatch(record)
	return jumped


## Stops recording a signal started with `h.watch`.
func _unwatch(record: Dictionary) -> void:
	var signal_ref: Signal = record["signal"]
	var catch: Variant = record.get("callable")
	if catch is Callable and signal_ref.is_connected(catch):
		signal_ref.disconnect(catch)
	# The recorder holds the record, so drop it to break the cycle.
	record.erase("callable")


func _check_jump_buffer() -> void:
	var start: Vector2i = _start_cell()
	var floor_y: float = level.cell_bottom(start).y
	# Pressed a few pixels above the floor: the jump happens on landing.
	await _place(start + Vector2i(0, -6), 0)
	var record: Dictionary = h.watch(player.jumped)
	var near: bool = await _wait_until(
		func() -> bool: return not player.is_on_floor() and player.global_position.y > floor_y - 14.0,
		120
	)
	h.expect(near, "the player falls towards the floor")
	h.press(&"jump")
	await h.physics_frames(1)
	h.release(&"jump")
	h.expect(h.fire_count(record) == 0, "there is no jump in mid-air")
	await h.physics_frames(10)
	h.expect(h.fire_count(record) == 1, "a jump pressed just before landing jumps on landing")
	_unwatch(record)
	await _wait_until(player.is_on_floor, 120)
	await h.physics_frames(4)

	# Pressed high above the floor: forgotten by the time the player lands.
	await _place(start + Vector2i(0, -12), 0)
	var early: Dictionary = h.watch(player.jumped)
	await _wait_until(func() -> bool: return player.global_position.y > floor_y - 100.0, 120)
	h.press(&"jump")
	await h.physics_frames(1)
	h.release(&"jump")
	await _wait_until(player.is_on_floor, 120)
	await h.physics_frames(20)
	h.expect(h.fire_count(early) == 0, "a jump pressed long before landing is not kept")
	_unwatch(early)
	h.expect(player.is_on_floor(), "the player stays on the floor")


func _check_one_way() -> void:
	var platform: Vector2i = Vector2i(-1, -1)
	for r: int in level.rows.size():
		var c: int = level.rows[r].find("-")
		if c >= 0 and _free(Vector2i(c, r - 1)):
			platform = Vector2i(c, r)
			break
	h.expect(platform.x >= 0, "the map has a one-way platform")
	if platform.x < 0:
		return
	await _place(platform + Vector2i.UP, 8)
	var top: float = level.cell_bottom(platform + Vector2i.UP).y
	h.expect(player.is_on_floor(), "the player stands on a one-way platform")
	h.expect(player.standing_on_one_way(), "the player knows the floor is one-way")
	h.expect_near(player.global_position.y, top, 0.5, "the platform holds the player up")
	await h.hold(&"move_down", 10)
	h.expect_near(player.global_position.y, top, 0.5, "holding move_down alone does not drop")
	h.press(&"move_down")
	await h.tap(&"jump")
	h.release(&"move_down")
	var dropped: bool = await _wait_until(
		func() -> bool: return player.global_position.y > top + 20.0, 60
	)
	h.expect(dropped, "move_down + jump drops through the platform")
	await _wait_until(player.is_on_floor, 120)
	await h.physics_frames(15)
	h.expect(player.global_position.y > top + 20.0, "the player lands below the platform")


func _check_coin() -> void:
	var coin: Coin = level.coins.get_node_or_null(^"Coin_9_14") as Coin
	h.expect(coin != null, "the first coin sits on the ground")
	if coin == null:
		return
	await _place(Vector2i(6, 14))
	h.press(&"move_right")
	var args: Array = await h.wait_for(events.score_changed, 120)
	h.release(&"move_right")
	h.expect(not args.is_empty() and int(args[0]) == 1, "running into a coin emits score_changed(1)")
	h.expect(coin.collected, "running into a coin collects it")
	h.expect(not coin.visible, "a collected coin disappears")
	h.expect(level.score == 1, "the level counts the coin")
	h.expect(level.hud.coins_label.text.contains("1 / 8"), "the HUD shows the coin count")
	await h.physics_frames(10)


func _check_checkpoint_and_spikes() -> void:
	var post: Checkpoint = level.checkpoints.get_child(0) as Checkpoint
	var post_cell: Vector2i = Vector2i(post.position / float(Level.TILE))
	await _place(Vector2i(post_cell.x - 2, post_cell.y - 1))
	h.press(&"move_right")
	var args: Array = await h.wait_for(events.checkpoint_reached, 120)
	h.release(&"move_right")
	h.expect(
		not args.is_empty() and str(args[0]) == "checkpoint_1",
		"running past a checkpoint reaches it"
	)
	h.expect(
		level.checkpoint == post and post.active, "the reached checkpoint becomes the respawn point"
	)
	await h.physics_frames(10)

	var spikes: Node2D = level.hazards.get_child(0) as Node2D
	var spike_cell: Vector2i = Vector2i(spikes.position / float(Level.TILE)) + Vector2i(1, -3)
	var falls: int = level.falls
	var record: Dictionary = h.watch(player.respawned)
	await _place(spike_cell, 0)
	var back: bool = await _wait_until(
		func() -> bool: return player.global_position.distance_to(post.respawn_position()) < 1.0, 120
	)
	h.expect(back, "touching spikes sends the player back to the checkpoint")
	# One respawn places the player above the spikes, the second sends it back.
	h.expect(h.fire_count(record) == 2, "the spikes respawn the player once")
	_unwatch(record)
	h.expect(level.falls == falls + 1, "the spikes count one fall")
	h.expect(level.hud.falls_label.text.contains(str(falls + 1)), "the HUD shows the falls")
	await h.physics_frames(10)
	h.expect(player.is_on_floor(), "the respawned player stands on the floor")


func _check_pit() -> void:
	var pit: int = -1
	var bottom: String = level.rows[level.rows.size() - 1]
	for c: int in bottom.length():
		if bottom[c] == ".":
			pit = c
			break
	h.expect(pit >= 0, "the map has a pit")
	if pit < 0:
		return
	var falls: int = level.falls
	await _place(Vector2i(pit, level.rows.size() - 2), 0)
	var target: Vector2 = level.respawn_position()
	var back: bool = await _wait_until(
		func() -> bool: return player.global_position.distance_to(target) < 1.0, 120
	)
	h.expect(back, "falling into a pit sends the player back to the checkpoint")
	h.expect(level.falls == falls + 1, "the pit counts one fall")
	await h.physics_frames(10)


func _check_restart_action() -> void:
	await _place(Vector2i(45, 14))
	var falls: int = level.falls
	await h.tap(&"restart")
	await h.physics_frames(2)
	h.expect(
		player.global_position.distance_to(level.respawn_position()) < 1.0,
		"restart sends the player back to the checkpoint"
	)
	h.expect(level.falls == falls, "restarting from a checkpoint is not a fall")


func _check_save_round_trip() -> void:
	await _place(Vector2i(44, 14))
	var state: Dictionary = game.call(&"capture_state")
	for id: String in ["level", "player", "coin_9_14", "coin_74_8"]:
		h.expect(state.has(id), "capture_state includes %s" % id)
	var at: Vector2 = player.global_position
	var falls: int = level.falls
	h.expect(falls == 2, "two falls so far")

	game.call(&"restart_level")
	await h.wait_for(events.level_started, 300)
	await h.physics_frames(2)
	_refresh()
	var coin: Coin = level.coins.get_node(^"Coin_9_14") as Coin
	h.expect(not coin.collected and level.score == 0, "restart resets the coins")
	h.expect(level.checkpoint == null, "restart resets the checkpoint")
	h.expect(level.falls == 0, "restart resets the falls")
	h.expect(
		player.global_position.distance_to(level.start_position) < 1.0,
		"restart puts the player at the start"
	)

	var second: Checkpoint = level.checkpoints.get_child(1) as Checkpoint
	second.load_state({"active": true})
	h.expect(level.checkpoint == second, "a mirrored checkpoint state moves the respawn point")

	game.call(&"apply_state", state)
	await h.physics_frames(2)
	h.expect(coin.collected and level.score == 1, "load_state restores collected coins")
	h.expect(level.hud.coins_label.text.contains("1 / 8"), "the HUD shows the restored coins")
	h.expect(
		level.checkpoint != null and str(level.checkpoint.get(&"checkpoint_id")) == "checkpoint_1",
		"load_state restores the checkpoint"
	)
	h.expect(level.falls == falls, "load_state restores the falls")
	h.expect(player.global_position.distance_to(at) < 1.0, "load_state restores the player")
	await h.physics_frames(4)


func _check_pause() -> void:
	await _place(_start_cell())
	await h.tap(&"pause")
	await h.physics_frames(2)
	h.expect(tree.paused, "pause pauses the tree")
	h.expect(bool(menus.call(&"is_open")), "pause opens the pause menu")
	var at: Vector2 = player.global_position
	await h.hold(&"move_right", 10)
	h.expect(player.global_position.distance_to(at) < 0.001, "the player does not move while paused")
	var time: float = level.elapsed
	await h.physics_frames(10)
	h.expect_near(level.elapsed, time, 0.0001, "the timer stops while paused")
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	h.expect(not tree.paused, "Resume (ui_accept) unpauses")
	h.expect(not bool(menus.call(&"is_open")), "Resume closes the pause menu")

	# An online game keeps running under a menu: the keys that move through
	# the menu must not move the player too.
	var page: Control = Control.new()
	menus.call(&"open_page", page)
	await h.physics_frames(2)
	h.expect(not tree.paused, "a page opened over a running game does not pause it")
	at = player.global_position
	await h.hold(&"move_right", 10)
	await h.tap(&"jump")
	await h.physics_frames(4)
	h.expect(player.global_position.distance_to(at) < 0.5,
		"the player ignores input while a menu is open over a running game")
	menus.call(&"close_all")
	await h.physics_frames(2)


func _check_gameplay_settings() -> void:
	await h.tap(&"pause")
	await h.physics_frames(2)
	h.expect(await h.focus_button("Settings"), "the pause menu has Settings")
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	h.expect(await h.focus_button("Gameplay"), "Settings lists the Gameplay page")
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	var page: Control = menus.call(&"current_page") as Control
	var forgiving: CheckButton = page.find_child("ForgivingJumps", true, false) as CheckButton
	var timer: CheckButton = page.find_child("ShowTimer", true, false) as CheckButton
	h.expect(forgiving != null and timer != null, "the Gameplay page has its toggles")
	if forgiving == null or timer == null:
		menus.call(&"close_all")
		return
	h.expect(h.focused() == forgiving, "the first toggle takes focus")
	h.expect(not forgiving.button_pressed, "forgiving jumps start off")
	h.expect_min_text(page, "Gameplay settings page")
	await h.tap(&"ui_accept")
	await h.physics_frames(1)
	h.expect(
		bool(settings.call(&"get_value", "platformer", "forgiving_jumps", false)),
		"the toggle saves the setting"
	)
	h.expect_near(
		player.coyote_window(), player.coyote_time * 2.0, 0.0001,
		"forgiving jumps double the coyote time"
	)
	h.expect_near(
		player.buffer_window(), player.jump_buffer_time * 2.0, 0.0001,
		"forgiving jumps double the jump buffer"
	)
	timer.button_pressed = false
	h.expect(not level.hud.time_label.visible, "Show timer off hides the HUD timer")
	timer.button_pressed = true
	h.expect(level.hud.time_label.visible, "Show timer on shows it again")
	menus.call(&"close_all")
	await h.physics_frames(2)
	h.expect(not tree.paused, "closing the menus resumes the game")

	var ledge: Vector2i = _find_ledge()
	h.expect(await _ledge_jump(ledge, 8), "with forgiving jumps a late ledge jump still works")
	settings.call(&"set_value", "platformer", "forgiving_jumps", false)
	h.expect_near(
		player.coyote_window(), player.coyote_time, 0.0001, "turning it off restores the coyote time"
	)

	var built: Control = GameplayPage.build()
	tree.root.add_child(built)
	var fresh: CheckButton = built.find_child("ForgivingJumps", true, false) as CheckButton
	h.expect(fresh != null and not fresh.button_pressed, "a new page shows the saved value")
	built.queue_free()


func _check_text_sizes() -> void:
	h.expect_min_text(level.hud.root, "HUD")


func _check_win() -> void:
	var goal: Node2D = level.goals.get_child(0) as Node2D
	var goal_cell: Vector2i = Vector2i(goal.position / float(Level.TILE))
	var record: Dictionary = h.watch(events.level_completed)
	await _place(Vector2i(goal_cell.x - 2, goal_cell.y - 1))
	h.press(&"move_right")
	var args: Array = await h.wait_watched(record, 120)
	h.release(&"move_right")
	await h.physics_frames(2)
	h.expect(h.fire_count(record) == 1, "touching the flag completes the level once")
	if not args.is_empty():
		var result: Dictionary = args[0]
		h.expect(int(result.get("score", -1)) == level.score, "the result reports the score")
		h.expect(int(result.get("total", -1)) == 8, "the result reports the coin total")
		h.expect(float(result.get("time", 0.0)) > 0.0, "the result reports the time")
		h.expect(int(result.get("falls", -1)) == level.falls, "the result reports the falls")
	h.expect(level.completed, "the level is complete")
	h.expect(level.hud.win_panel.visible, "the win screen shows")
	h.expect_min_text(level.hud.win_panel, "win screen")
	h.expect(not player.controls_enabled, "the player stops taking input after the win")
	await h.physics_frames(20)
	var time: float = level.elapsed
	var at: Vector2 = player.global_position
	await h.hold(&"move_left", 10)
	h.expect(absf(player.global_position.x - at.x) < 0.5, "a finished player stays put")
	h.expect_near(level.elapsed, time, 0.0001, "the timer stops at the flag")

	await h.physics_frames(2)
	h.expect(h.focused_text() == "Play again", "Play again takes focus")
	var restarted: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	var started: Array = await h.wait_watched(restarted, 300)
	h.expect(not started.is_empty(), "Play again restarts the level")
	await h.physics_frames(2)
	_refresh()
	h.expect(level.score == 0 and not level.completed, "the restarted level starts fresh")
	h.expect(not level.hud.win_panel.visible, "the win screen is gone after Play again")
	h.expect(player.controls_enabled, "the new player takes input")
