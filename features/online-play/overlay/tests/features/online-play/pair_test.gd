extends SceneTree
## Two real game processes on localhost: one hosts (or runs as a dedicated
## server), the other joins. Proves that the joining player's movement reaches
## the host, that a world action the client asks for runs on the server and
## reaches both, that a starting point's own objects replicate (through the
## optional `blueprint_check.gd` its adapter ships), and that a player who
## leaves is despawned on the host.
##
## Host:   --host --port=N --autostart [--lan-port=M --lan-broadcast=127.0.0.1]
## Server: --server --port=N
## Client: --join=127.0.0.1:N   or   --find-lan --lan-port=M   (finds the host)
## Refusal: add --expect-refused to both, and --protocol-version=99 to the client.
##
## The processes coordinate through the probe's value: 42 client asks,
## 43 host answers, 44 client's starting-point check done, 45 host's done.
## The adapter's check (a `PairCheck`, see pair_check.gd) has four steps:
## `prepare` (every side, once the level and its own player are there),
## `client_check` (client, before 44), `host_check` (host or server, before
## 45) and `client_verify` (client, after 45).

const PROBE := "res://tests/features/online-play/net_probe.gd"
const PairCheck := preload("res://tests/features/online-play/pair_check.gd")
const CHECK := "res://tests/features/online-play/blueprint_check.gd"
const LONG_MS := 30000

var play: PlayHarness
var net: Node
var replication: Node
var check: PairCheck = null


func _initialize() -> void:
	Engine.max_fps = 120
	_run.call_deferred()


func _run() -> void:
	net = root.get_node(^"Net")
	replication = net.get(&"replication")
	var refused: bool = bool(net.call(&"has_arg", "expect-refused"))
	var role: String = "client"
	if net.call(&"has_arg", "server"):
		role = "server"
	elif net.call(&"has_arg", "host"):
		role = "host"
	play = PlayHarness.new(self, "online_" + role)
	if ResourceLoader.exists(CHECK):
		check = (load(CHECK) as Script).new() as PairCheck
		check.setup(play, self)
		print("online: starting-point checks loaded")
	if role == "server":
		await _server()
	elif role == "host":
		if refused:
			await _host_refusal()
		else:
			await _host()
	else:
		if refused:
			await _client_refusal()
		else:
			await _client()
	if check != null and not refused and play.failures.is_empty():
		print("online: starting-point checks passed")
	play.finish()


# --- Host and dedicated server ---------------------------------------------------


func _host() -> void:
	await play.boot(true)
	play.expect(bool(net.call(&"is_server")), "the game hosts")
	play.expect(not play.game.session().can_pause(), "an online session does not pause")
	var level: Node = play.game.current_level()
	_add_probe(level)
	if check != null:
		await check.prepare(level)
	if _has_player():
		var me: Node = play.game.local_player()
		play.expect(me != null and me.name == "1", "the host has its own player")
	await _serve(level)


func _server() -> void:
	await play.boot(false)
	play.expect(bool(net.get(&"dedicated")), "--server runs a dedicated server")
	play.expect(bool(net.call(&"is_server")), "the dedicated server hosts")
	await _until(
		func() -> bool: return play.game.current_level() != null,
		"a joining player starts the level"
	)
	var level: Node = play.game.current_level()
	if level == null:
		return
	_add_probe(level)
	if check != null:
		await check.prepare(level)
	play.expect(not play.menus.is_open(), "the dedicated server shows no menu")
	play.expect(play.game.local_player() == null, "the dedicated server has no player")
	await _serve(level)
	await _until(
		func() -> bool: return play.game.current_level() == null,
		"the empty server waits again"
	)


func _serve(level: Node) -> void:
	await _until(func() -> bool: return _client_id() != 0, "a client joins and loads the level")
	var client: int = _client_id()
	if client == 0:
		return
	if _has_player():
		await _until(
			func() -> bool: return _player_of(client) != null,
			"the client's player spawns"
		)
		var other: Node = _player_of(client)
		if other != null:
			play.expect(other.get_multiplayer_authority() == client, "the client owns its player")
			var start: Vector3 = _flat(other)
			var step: float = _step(other)
			var other_id: int = other.get_instance_id()
			await _until(
				func() -> bool: return _moved(other_id, start, step),
				"the client's movement reaches the host"
			)
		var expected: int = 1 if bool(net.get(&"dedicated")) else 2
		play.expect(play.game.players().size() == expected, "one player per peer on the host")
	var probe: Node = level.get_node_or_null(^"NetProbe")
	await _until(
		func() -> bool: return int(probe.get(&"value")) == 42,
		"the client's action runs on the server"
	)
	play.expect(int(probe.get(&"actor")) == client, "the action ran on behalf of the client")
	play.game.request_action(probe, &"ping", [43])
	await _until(
		func() -> bool: return int(probe.get(&"value")) == 44,
		"the client finished its checks"
	)
	if check != null:
		await check.host_check(level)
	play.game.request_action(probe, &"ping", [45])
	await _until(
		func() -> bool: return not (net.call(&"peers") as PackedInt32Array).has(client),
		"the client leaves"
	)
	if _has_player():
		await _until(
			func() -> bool: return _player_of(client) == null,
			"the leaving player is despawned on the host"
		)


func _host_refusal() -> void:
	var watched: Dictionary = play.watch(net.refused)
	await play.boot(true)
	await _until(
		func() -> bool: return play.fire_count(watched) > 0,
		"the mismatched client is refused"
	)
	_unwatch(watched)
	var args: Array = watched["args"]
	if args.size() == 2:
		play.expect(str(args[1]).contains("different version"), "the refusal names the version")
	await _wait_ms(2500)
	play.expect((net.call(&"peers") as PackedInt32Array).size() == 1, "nobody joined")


# --- Client -----------------------------------------------------------------------


func _client() -> void:
	await play.boot(false)
	if net.call(&"has_arg", "find-lan"):
		await _find_and_join()
	await _until(
		func() -> bool: return play.game.current_level() != null,
		"the host's level loads here"
	)
	var level: Node = play.game.current_level()
	if level == null:
		return
	play.expect(play.game.session().is_online(), "the client plays an online session")
	play.expect(not play.game.session().is_authority(), "the client is not the authority")
	_add_probe(level)
	var me: int = root.multiplayer.get_unique_id()
	if _has_player():
		await _until(
			func() -> bool: return play.game.local_player() != null,
			"the server spawns this player"
		)
		var mine: Node = play.game.local_player()
		if mine == null:
			return
		play.expect(mine.name == str(me), "the player is named after its peer")
		if bool(_server_has_player()):
			await _until(
				func() -> bool: return _player_of(1) != null,
				"the host's player is visible"
			)
		if check != null:
			await check.prepare(level)
		await play.physics_frames(10)
		var start: Vector3 = _flat(mine)
		await play.hold(&"move_right", 30)
		play.expect(_flat(mine).distance_to(start) > _step(mine), "move_right moves this player")
	elif check != null:
		await check.prepare(level)
	var probe: Node = level.get_node_or_null(^"NetProbe")
	play.game.request_action(probe, &"ping", [42])
	play.expect(int(probe.get(&"value")) != 42, "a client's action does not run locally first")
	await _until(
		func() -> bool: return int(probe.get(&"value")) >= 42,
		"the server's result comes back"
	)
	var seen: PackedStringArray = probe.get(&"history")
	play.expect("42@%d" % me in seen, "the server ran it for this player: %s" % [seen])
	await _until(
		func() -> bool: return int(probe.get(&"value")) == 43,
		"the host's own action reaches the client"
	)
	seen = probe.get(&"history")
	play.expect("43@1" in seen, "the host's action ran as the host: %s" % [seen])
	if check != null:
		await check.client_check(level)
	play.game.request_action(probe, &"ping", [44])
	await _until(
		func() -> bool: return int(probe.get(&"value")) == 45,
		"the host finished its checks"
	)
	if check != null:
		await check.client_verify(level)
	await _check_pause()
	play.game.quit_to_menu()
	play.expect(not bool(net.call(&"is_online")), "leaving ends the online session")
	play.expect(not play.game.session().is_online(), "the menu runs a local session again")
	await _wait_ms(500)


func _client_refusal() -> void:
	var failed: Dictionary = play.watch(net.join_failed)
	await play.boot(false)
	await _until(func() -> bool: return play.fire_count(failed) > 0, "the join fails")
	_unwatch(failed)
	var args: Array = failed["args"]
	play.expect(
		args.size() == 1 and str(args[0]).contains("different version"),
		"the reason says the versions differ: %s" % [args]
	)
	play.expect(not bool(net.call(&"is_online")), "a refused client is offline")
	play.expect(play.game.current_level() == null, "a refused client never enters the level")


func _find_and_join() -> void:
	var browser: LanDiscovery = LanDiscovery.new()
	root.add_child(browser)
	play.expect(browser.start_listening(int(net.get(&"lan_port"))) == OK, "the LAN browser listens")
	await _until(
		func() -> bool: return not browser.hosts().is_empty(),
		"the host's LAN beacon is heard"
	)
	var hosts: Array[Dictionary] = browser.hosts()
	if hosts.is_empty():
		return
	var found: Dictionary = hosts[0]
	play.expect(bool(found["compatible"]), "the beacon is for this game and version")
	play.expect(str(found["address"]) == "127.0.0.1", "the beacon came from localhost")
	play.expect(int(found["port"]) == int(net.get(&"port")), "the beacon carries the game port")
	browser.stop()
	browser.queue_free()
	net.call(&"join", str(found["address"]), int(found["port"]))


## The pause action opens the pause menu with "Leave game", but an online
## game keeps running underneath.
func _check_pause() -> void:
	await play.tap(&"pause")
	play.expect(play.menus.is_open(), "pause opens the pause menu online")
	play.expect(not paused, "an online game does not pause")
	var ids: Array = []
	for entry: Dictionary in play.menus.entries(&"pause"):
		ids.append(entry["id"])
	play.expect(&"leave" in ids, "the pause menu offers Leave game: %s" % [ids])
	await play.tap(&"pause")
	play.expect(not play.menus.is_open(), "pause again closes the menu")


# --- Helpers ----------------------------------------------------------------------


func _add_probe(level: Node) -> void:
	if level != null and level.get_node_or_null(^"NetProbe") == null:
		level.add_child((load(PROBE) as Script).new() as Node)


func _has_player() -> bool:
	return not str(ProjectSettings.get_setting("game/hooks/player_scene", "")).is_empty()


func _server_has_player() -> bool:
	return _has_player() and (replication.get(&"level_peers") as Dictionary).has(1)


## The first other peer that has the level loaded, or 0.
func _client_id() -> int:
	for id: int in replication.call(&"ready_peers"):
		if id != 1:
			return id
	return 0


func _player_of(peer: int) -> Node:
	for player: Node in play.game.players():
		if player.name == str(peer):
			return player
	return null


## Horizontal position: x for 2D, x and z for 3D (falling does not count).
func _flat(node: Node) -> Vector3:
	if node is Node2D:
		return Vector3((node as Node2D).global_position.x, 0.0, 0.0)
	if node is Node3D:
		var at: Vector3 = (node as Node3D).global_position
		return Vector3(at.x, 0.0, at.z)
	return Vector3.ZERO


## True when the node with `id` still exists and is more than `step` away
## from `start` (horizontally).
func _moved(id: int, start: Vector3, step: float) -> bool:
	var node: Node = instance_from_id(id) as Node if is_instance_id_valid(id) else null
	return node != null and _flat(node).distance_to(start) > step


func _step(node: Node) -> float:
	return 4.0 if node is Node2D else 0.25


## Stops recording a `play.watch` record. Its callable refers back to the
## record, so the pair is only freed once that link is dropped here.
func _unwatch(record: Dictionary) -> void:
	var signal_ref: Signal = record["signal"]
	var catch: Variant = record.get("callable")
	if catch is Callable and signal_ref.is_connected(catch):
		signal_ref.disconnect(catch)
	record.erase("callable")


func _until(condition: Callable, message: String, limit_ms: int = LONG_MS) -> bool:
	var deadline: int = Time.get_ticks_msec() + limit_ms
	while not bool(condition.call()):
		if Time.get_ticks_msec() > deadline:
			play.expect(false, "%s (waited %d ms)" % [message, limit_ms])
			return false
		await process_frame
	return true


func _wait_ms(ms: int) -> void:
	var deadline: int = Time.get_ticks_msec() + ms
	while Time.get_ticks_msec() < deadline:
		await process_frame
