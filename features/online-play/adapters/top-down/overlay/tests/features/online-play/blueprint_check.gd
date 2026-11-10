extends "res://tests/features/online-play/pair_check.gd"
## Online checks for the top-down arena: the server's waves and score reach
## the joining player; the joining player's attack defeats a server enemy
## (it disappears everywhere and scores); a heart the joining player takes
## heals them on their own machine. With a host, the host clears every wave
## and the joining player sees the win; with a dedicated server, the joining
## player goes down alone and the server ends the fight as lost for them.
## Players start tough (`prepare`), so the waves cannot end the test early.

## Health for the players while the test waits on the other process: the
## waves keep coming, and an idle player must not go down by chance.
const TOUGH := 999

var _health_seen: PackedInt32Array = PackedInt32Array()


func prepare(_level: Node) -> void:
	var mine: Node = play.game.local_player()
	if mine != null:
		mine.set(&"max_health", TOUGH)
		mine.set(&"health", TOUGH)
	await tree.process_frame


func client_check(level: Node) -> void:
	await until(
		func() -> bool: return int(level.get(&"wave")) >= 1,
		"the server's first wave reaches the client"
	)
	await until(
		func() -> bool: return not (level.call(&"enemies") as Array).is_empty(),
		"the server's enemies appear here"
	)
	var enemy: Node2D = (level.call(&"enemies") as Array)[0] as Node2D
	var score: int = int(level.get(&"score"))
	var enemy_id: int = enemy.get_instance_id()
	play.game.request_action(enemy, &"take_hit", [99, enemy.global_position + Vector2(-8.0, 0.0)])
	enemy = null
	await until(
		func() -> bool: return not is_instance_id_valid(enemy_id),
		"the client's attack defeats a server enemy"
	)
	await until(
		func() -> bool: return int(level.get(&"score")) > score,
		"the defeat scores for the client"
	)
	var mine: Node = play.game.local_player()
	if mine == null:
		play.expect(false, "the client has a player")
		return
	var record: Callable = func(health: int, _max_health: int) -> void: _health_seen.append(health)
	mine.connect(&"health_changed", record)
	await until(
		func() -> bool: return bool(mine.call(&"take_damage", 2)),
		"the client's player can be hurt"
	)
	var heart: Node = level.get_node(^"Pickups/Heart1")
	play.game.request_action(heart, &"collect", [])
	await until(
		func() -> bool: return _healed(),
		"the server's heart heals this player: %s" % [_health_seen]
	)
	mine.disconnect(&"health_changed", record)


func host_check(level: Node) -> void:
	var client: Node = player_of(client_id())
	play.expect(client != null, "the client's player is on the server")
	var heart: Node = level.get_node(^"Pickups/Heart1")
	play.expect(bool(heart.get(&"collected")), "the client's heart is taken on the server")
	if not server_plays():
		return
	await until(func() -> bool: return _clear_wave(level), "the host clears every wave")
	play.expect(bool(level.get(&"completed")), "the arena is won on the server")


func client_verify(level: Node) -> void:
	if server_plays():
		await until(
			func() -> bool: return bool(level.get(&"completed")),
			"the server's win reaches the client"
		)
	else:
		var mine: Node = play.game.local_player()
		await until(
			func() -> bool:
				if mine != null and int(mine.get(&"health")) > 0:
					mine.set(&"health", mini(int(mine.get(&"health")), 1))
					mine.call(&"take_damage", 1)
				return bool(level.get(&"failed")),
			"the client going down alone loses the fight"
		)
	var hud: Node = level.get(&"hud")
	play.expect(bool(hud.call(&"is_end_visible")), "the end screen shows for the client")


## Defeats every standing enemy; true once the arena is won.
func _clear_wave(level: Node) -> bool:
	for enemy: Node in level.call(&"enemies"):
		play.game.request_action(enemy, &"take_hit", [99, (enemy as Node2D).global_position])
	return bool(level.get(&"completed"))


func _healed() -> bool:
	for i: int in range(1, _health_seen.size()):
		if _health_seen[i] > _health_seen[i - 1]:
			return true
	return false
