extends RefCounted
## The top-down arena checks: inject input through the core harness and check
## what the player would see happen. `play_test.gd` loads this file once the
## autoloads exist, so the game scripts it preloads can name them.

const Harness := preload("res://tests/support/play.gd")
const Level := preload("res://game/level.gd")
const Player := preload("res://game/player/player.gd")
const Chaser := preload("res://game/enemies/chaser.gd")
const Pickup := preload("res://game/pickups/pickup.gd")

## Counts what the level spawns, to prove waves go through `Game.spawn`.
class RecordingSession:
	extends GameSession

	var spawned: int = 0

	func spawn(scene: PackedScene, parent: Node, data: Dictionary) -> Node:
		spawned += 1
		return super.spawn(scene, parent, data)


const CLEAR := Vector2(960.0, 500.0)

var tree: SceneTree
var h: Harness
var game: Node
var events: Node
var settings: Node
var level: Level
var player: Player


func run(scene_tree: SceneTree) -> void:
	tree = scene_tree
	h = Harness.new(tree, "top-down")
	game = tree.root.get_node(^"Game")
	events = tree.root.get_node(^"GameEvents")
	settings = tree.root.get_node(^"Settings")
	await h.boot()
	_refresh()
	h.expect(level != null, "the level scene starts")
	h.expect(player != null, "a local player is spawned")
	if level == null or player == null:
		h.finish()
		return
	level.auto_waves = false
	await h.physics_frames(2)
	h.expect(level.wave == 0 and level.enemies().is_empty(), "no enemies before the first wave")
	h.expect(player.health == player.max_health, "the player starts at full health")
	h.expect(player.camera.is_current(), "the local player's camera is current")

	await _check_move()
	await _check_dash()
	await _check_attack()
	await _check_contact_damage()
	await _check_pickups()
	await _check_save_round_trip()
	await _check_pause()
	await _check_settings()
	_check_text_sizes()
	await _check_waves_and_win()
	await _check_fail()
	h.finish()


func _refresh() -> void:
	level = game.call(&"current_level") as Level
	player = game.call(&"local_player") as Player


func _place(at: Vector2) -> void:
	player.global_position = at
	player.velocity = Vector2.ZERO


func _clear_enemies() -> void:
	for node: Node in level.enemy_root.get_children():
		level.enemy_root.remove_child(node)
		node.queue_free()


## An enemy that stays still and harmless (a long spawn fade-in).
func _dormant_enemy(at: Vector2, variant: String = "chaser") -> Chaser:
	return level.spawn_enemy(at, variant, {"spawn_grace": 1000.0}) as Chaser


## An enemy that chases and hurts at once (no spawn fade-in).
func _harmful_enemy(at: Vector2) -> Chaser:
	return level.spawn_enemy(at, "chaser", {"spawn_grace": 0.0}) as Chaser


func _wait_until(condition: Callable, max_frames: int) -> bool:
	for _i: int in max_frames:
		if condition.call():
			return true
		await tree.physics_frame
	return bool(condition.call())


func _wait_vulnerable() -> void:
	await _wait_until(func() -> bool: return not player.is_invulnerable(), 120)


func _check_move() -> void:
	_place(CLEAR)
	await h.physics_frames(2)
	var start: Vector2 = player.global_position
	await h.hold(&"move_right", 30)
	var moved: Vector2 = player.global_position - start
	h.expect(moved.x > 80.0 and absf(moved.y) < 1.0, "holding move_right moves right")
	h.expect(player.facing.dot(Vector2.RIGHT) > 0.99, "the player faces the way it moves")
	await h.physics_frames(15)
	h.expect(player.velocity.length() < 1.0, "the player stops after move_right is released")

	start = player.global_position
	await h.hold(&"move_up", 20)
	h.expect(player.global_position.y < start.y - 40.0, "move_up moves up")
	await h.physics_frames(15)

	_place(CLEAR)
	h.press(&"move_right")
	h.press(&"move_down")
	await h.physics_frames(30)
	var speed: float = player.velocity.length()
	var heading: Vector2 = player.velocity.normalized()
	h.release(&"move_right")
	h.release(&"move_down")
	h.expect_near(speed, player.move_speed, 2.0, "diagonal movement is not faster than straight")
	h.expect(heading.dot(Vector2(1.0, 1.0).normalized()) > 0.99, "diagonal input moves diagonally")
	await h.physics_frames(15)


func _check_dash() -> void:
	_place(CLEAR)
	player.face(Vector2.RIGHT)
	await h.physics_frames(2)
	var dashes: Dictionary = h.watch(player.dashed)
	h.press(&"move_right")
	await h.physics_frames(12)
	var at: Vector2 = player.global_position
	await h.physics_frames(9)
	var walked: float = player.global_position.x - at.x
	at = player.global_position
	h.press(&"dash")
	await h.physics_frames(2)
	h.expect(
		player.is_dashing() and player.is_invulnerable(), "a dash is under way and cannot be hurt"
	)
	h.release(&"dash")
	await h.physics_frames(7)
	var dashed: float = player.global_position.x - at.x
	h.expect(
		dashed > walked * 2.0,
		"dash moves much faster than walking (%.0f vs %.0f px)" % [dashed, walked]
	)
	h.expect(player.dash_ready() < 1.0, "the dash is cooling down")
	await h.tap(&"dash")
	h.expect(h.fire_count(dashes) == 1, "dash does nothing while cooling down")
	h.release(&"move_right")
	var cooled: bool = await _wait_until(func() -> bool: return player.dash_ready() >= 1.0, 90)
	h.expect(cooled, "the dash cools down")
	await h.tap(&"dash")
	h.expect(h.fire_count(dashes) == 2, "dash works again after the cooldown")
	h.unwatch(dashes)
	await h.physics_frames(30)


func _check_attack() -> void:
	_clear_enemies()
	_place(CLEAR)
	player.face(Vector2.RIGHT)
	var front: Chaser = _dormant_enemy(CLEAR + Vector2(50.0, 0.0))
	var behind: Chaser = _dormant_enemy(CLEAR - Vector2(50.0, 0.0))
	await h.physics_frames(3)
	var score: int = level.score
	var swings: Dictionary = h.watch(player.attacked)
	await h.tap(&"attack")
	h.expect(front.health == front.max_health - 1, "attack damages the enemy in front")
	h.expect(behind.health == behind.max_health, "attack does not reach the enemy behind")
	h.expect(player.health == player.max_health, "enemies fading in do not hurt")
	await h.tap(&"attack")
	h.expect(h.fire_count(swings) == 1, "attack waits for its cooldown")
	h.unwatch(swings)

	await h.physics_frames(20)
	front.global_position = CLEAR + Vector2(50.0, 0.0)
	await h.physics_frames(2)
	var gone: Dictionary = h.watch(front.defeated)
	await h.tap(&"attack")
	h.expect(h.fire_count(gone) == 1, "a second hit defeats a chaser")
	h.unwatch(gone)
	await h.physics_frames(1)
	h.expect(not is_instance_valid(front), "a defeated enemy is removed")
	h.expect(level.score == score + 10, "a defeated chaser scores 10")
	h.expect(behind.health == behind.max_health, "the enemy behind is still untouched")

	await h.physics_frames(20)
	player.face(Vector2.LEFT)
	await h.tap(&"attack")
	h.expect(behind.health == behind.max_health - 1, "turning around hits the other enemy")
	_clear_enemies()
	await h.physics_frames(2)


func _check_contact_damage() -> void:
	_place(CLEAR)
	await h.physics_frames(2)
	var hurt_frames: Array[int] = []
	var on_hurt: Callable = func(_amount: int) -> void:
		hurt_frames.append(Engine.get_physics_frames())
	player.hurt.connect(on_hurt)
	var enemy: Chaser = _harmful_enemy(CLEAR + Vector2(90.0, 0.0))
	var first: bool = await _wait_until(func() -> bool: return hurt_frames.size() >= 1, 90)
	h.expect(first, "a chasing enemy reaches and hurts the player")
	h.expect(player.health == player.max_health - 1, "enemy contact costs one health")
	h.expect(player.is_invulnerable(), "the player is invulnerable right after a hit")
	var second: bool = await _wait_until(func() -> bool: return hurt_frames.size() >= 2, 150)
	player.hurt.disconnect(on_hurt)
	h.expect(second, "staying in contact hurts again after the invulnerable window")
	if second:
		var gap: int = hurt_frames[1] - hurt_frames[0]
		h.expect(gap >= 55, "no damage during the invulnerable window (%d frames apart)" % gap)
	h.expect(is_instance_valid(enemy), "the chaser is still there")
	_clear_enemies()
	player.heal(player.max_health)
	h.expect(player.health == player.max_health, "heal restores health")
	await _wait_vulnerable()


func _check_pickups() -> void:
	var gem: Pickup = level.pickups.get_node(^"Gem2") as Pickup
	_place(gem.global_position - Vector2(80.0, 0.0))
	await h.physics_frames(2)
	var score: int = level.score
	h.press(&"move_right")
	var args: Array = await h.wait_for(events.score_changed, 90)
	h.release(&"move_right")
	h.expect(
		not args.is_empty() and int(args[0]) == score + 10, "walking over a gem adds 10 to the score"
	)
	h.expect(gem.collected and not gem.visible, "a collected gem disappears")
	h.expect(level.gems_taken == 1, "the level counts the gem")
	await h.physics_frames(10)

	var heart: Pickup = level.pickups.get_node(^"Heart1") as Pickup
	_place(heart.global_position)
	await h.physics_frames(5)
	h.expect(not heart.collected, "a heart stays put while health is full")
	player.take_damage(1)
	h.expect(player.health == player.max_health - 1, "damage lowers health")
	await h.physics_frames(3)
	h.expect(heart.collected, "a hurt player picks up the heart")
	h.expect(player.health == player.max_health, "the heart restores health")
	_place(CLEAR)
	await _wait_vulnerable()


func _check_save_round_trip() -> void:
	level.start_wave(1)
	await h.physics_frames(2)
	player.take_damage(1)
	_place(CLEAR + Vector2(40.0, 30.0))
	await h.physics_frames(2)
	var health: int = player.health
	var at: Vector2 = player.global_position
	var score: int = level.score
	var standing: int = level.enemies().size()
	h.expect(standing == 3, "wave 1 brings three enemies")
	var state: Dictionary = game.call(&"capture_state")
	for id: String in ["level", "player", "gem_1", "gem_2", "heart_1", "heart_2"]:
		h.expect(state.has(id), "capture_state includes %s" % id)

	game.call(&"restart_level")
	await h.wait_for(events.level_started, 300)
	await h.physics_frames(2)
	_refresh()
	level.auto_waves = false
	var gem: Pickup = level.pickups.get_node(^"Gem2") as Pickup
	var heart: Pickup = level.pickups.get_node(^"Heart1") as Pickup
	h.expect(level.score == 0 and level.wave == 0, "restart resets score and waves")
	h.expect(level.enemies().is_empty(), "restart clears the enemies")
	h.expect(not gem.collected and not heart.collected, "restart puts the pickups back")
	h.expect(player.health == player.max_health, "restart restores health")

	game.call(&"apply_state", state)
	await h.physics_frames(2)
	h.expect(level.score == score, "load_state restores the score")
	h.expect(level.wave == 1 and level.is_wave_running(), "load_state restores the running wave")
	h.expect(level.enemies().size() == standing, "load_state restores the enemies")
	h.expect(gem.collected and not gem.visible, "load_state restores a collected gem")
	h.expect(heart.collected, "load_state restores a used heart")
	h.expect(player.health == health, "load_state restores health")
	h.expect(player.global_position.distance_to(at) < 1.0, "load_state restores the player position")
	_clear_enemies()
	player.heal(player.max_health)
	await h.physics_frames(2)


func _check_pause() -> void:
	var menus: Node = tree.root.get_node(^"Menus")
	_place(CLEAR)
	await h.physics_frames(2)
	await h.tap(&"pause")
	await h.physics_frames(2)
	h.expect(tree.paused, "pause pauses the tree")
	h.expect(bool(menus.call(&"is_open")), "pause opens the pause menu")
	var at: Vector2 = player.global_position
	await h.hold(&"move_right", 10)
	h.expect(player.global_position.distance_to(at) < 0.001, "the player does not move while paused")
	await h.tap(&"ui_accept")
	await h.physics_frames(2)
	h.expect(not tree.paused, "Resume (ui_accept) unpauses")
	h.expect(not bool(menus.call(&"is_open")), "Resume closes the pause menu")


func _check_settings() -> void:
	settings.call(&"set_value", "access", "screen_shake", 0.0)
	await _wait_vulnerable()
	player.take_damage(1)
	var calm: float = await _max_shake(10)
	h.expect(calm < 0.01, "screen shake off keeps the camera still when hurt")
	settings.call(&"set_value", "access", "screen_shake", 1.0)
	await _wait_vulnerable()
	player.take_damage(1)
	var shaken: float = await _max_shake(10)
	h.expect(shaken > 1.0, "screen shake on shakes the camera when hurt")
	player.heal(player.max_health)

	var gem: Pickup = level.pickups.get_node(^"Gem3") as Pickup
	settings.call(&"set_value", "access", "reduced_motion", true)
	await h.physics_frames(2)
	h.expect(absf(gem.art.position.y) < 0.001, "reduced motion stops pickups bobbing")
	settings.call(&"set_value", "access", "reduced_motion", false)
	var bobbing: bool = await _wait_until(func() -> bool: return absf(gem.art.position.y) > 0.5, 60)
	h.expect(bobbing, "pickups bob again with reduced motion off")
	await _wait_vulnerable()


func _max_shake(frames: int) -> float:
	var most: float = 0.0
	for _i: int in frames:
		await tree.process_frame
		most = maxf(most, player.camera.offset.length())
	return most


func _check_text_sizes() -> void:
	h.expect_min_text(level.hud.root, "HUD")
	h.expect(level.hud.score_label.text.contains(str(level.score)), "the HUD shows the score")


func _check_waves_and_win() -> void:
	var session: RecordingSession = RecordingSession.new()
	game.call(&"set_session", session)
	game.call(&"restart_level")
	await h.wait_for(events.level_started, 300)
	await h.physics_frames(2)
	_refresh()
	h.expect(level.auto_waves, "a fresh level runs its waves")
	var done: Dictionary = h.watch(events.level_completed)
	var expected: Array[int] = [3, 5, 8]
	var points: int = 0
	for index: int in expected.size():
		var number: int = index + 1
		var started: bool = await _wait_until(
			func() -> bool: return level.wave == number and level.is_wave_running(), 400
		)
		h.expect(started, "wave %d starts" % number)
		var count: int = level.enemies().size()
		h.expect(
			count == expected[index],
			"wave %d brings %d enemies (got %d)" % [number, expected[index], count]
		)
		h.expect(
			session.spawned >= _sum(expected, number),
			"wave %d is spawned through Game.spawn" % number
		)
		h.expect(not level.hud.wave_label.text.is_empty(), "the HUD shows the wave")
		for enemy: Node in level.enemies():
			points += int(enemy.get(&"points"))
			var from: Vector2 = (enemy as Node2D).global_position + Vector2.LEFT
			game.call(&"request_action", enemy, &"take_hit", [99, from])
		await h.physics_frames(2)
		if number < expected.size():
			h.expect(level.next_wave_in() > 0.0, "clearing wave %d queues the next" % number)
	var args: Array = await h.wait_watched(done, 30)
	h.expect(not args.is_empty(), "clearing the last wave completes the level")
	if not args.is_empty():
		var result: Dictionary = args[0]
		h.expect(int(result.get("waves", 0)) == 3, "the result reports three waves")
		h.expect(int(result.get("score", -1)) == points, "the result reports the score (%d)" % points)
		h.expect(result.has("time"), "the result reports the time")
	h.expect(level.completed and level.hud.is_end_visible(), "the win screen shows")
	h.expect(not player.controls_enabled, "the player stops taking input after the win")
	h.expect_min_text(level.hud.root, "win screen")
	game.call(&"set_session", null)

	await h.physics_frames(2)
	var restarted: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	var again: Array = await h.wait_watched(restarted, 300)
	h.expect(not again.is_empty(), "Play again restarts the level")
	await h.physics_frames(2)
	_refresh()
	h.expect(level.score == 0 and not level.completed, "the restarted level starts fresh")


func _sum(values: Array[int], count: int) -> int:
	var total: int = 0
	for i: int in count:
		total += values[i]
	return total


func _check_fail() -> void:
	level.auto_waves = false
	_place(CLEAR)
	player.health = 1
	await h.physics_frames(2)
	var failed: Dictionary = h.watch(events.level_failed)
	var enemy: Chaser = _harmful_enemy(CLEAR + Vector2(60.0, 0.0))
	var args: Array = await h.wait_watched(failed, 120)
	h.expect(not args.is_empty() and str(args[0]) == "defeated", "0 health fails the level")
	h.expect(not player.is_alive(), "the player is down")
	h.expect(level.failed and level.hud.is_end_visible(), "the lose screen shows")
	h.expect(level.hud.again_button.text == "Try again", "the lose screen offers Try again")
	h.expect(is_instance_valid(enemy) and enemy.frozen, "enemies stop when the player is down")
	var at: Vector2 = player.global_position
	await h.hold(&"move_right", 10)
	h.expect(player.global_position.distance_to(at) < 0.5, "a downed player cannot move")

	await h.physics_frames(2)
	var restarted: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	var again: Array = await h.wait_watched(restarted, 300)
	h.expect(not again.is_empty(), "Try again restarts the level")
	await h.physics_frames(2)
	_refresh()
	h.expect(not level.failed and player.health == player.max_health, "the retried level starts fresh")
