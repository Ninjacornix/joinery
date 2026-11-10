extends RefCounted
## The online save-load checks, run by online_test.gd once the autoloads
## exist. `Net` is reached through the node passed in, so this script also
## parses in projects without online play.

const LONG_MS := 30000
const CLIENT_STAYS_MS := 2500

var tree: SceneTree
var play: PlayHarness
var net: Node


func _init(scene_tree: SceneTree, harness: PlayHarness, net_node: Node) -> void:
	tree = scene_tree
	play = harness
	net = net_node


## Hosts, saves while the client plays, and checks the slot holds both players.
func host() -> void:
	await play.boot(true)
	play.expect(bool(net.call(&"is_server")), "the game hosts")
	play.expect(SaveGame.can_save(), "the host may save")
	var joined: bool = await _until(func() -> bool: return _client_id() != 0, "a client joins")
	if not joined:
		return
	var client: int = _client_id()
	var has_player: bool = str(ProjectSettings.get_setting("game/hooks/player_scene", "")) != ""
	if has_player:
		await _until(func() -> bool: return Game.players().size() == 2, "the client's player spawns")
	var saved: Dictionary = play.watch(SaveGame.saved)
	play.expect(SaveGame.save_slot(1) == OK, "the host saves while a client plays")
	await play.wait_watched(saved, 10)
	var data: Variant = JSON.parse_string(FileAccess.get_file_as_string(SaveGame.slot_path(1)))
	play.expect(data is Dictionary, "the host's save is readable")
	if data is Dictionary and has_player:
		var nodes: Dictionary = (data as Dictionary).get("nodes", {})
		play.expect(nodes.has("player"), "the host's player is in the save")
		play.expect(nodes.has("player_%d" % client), "the client's player is in the save")
	await _until(
		func() -> bool: return not (net.call(&"peers") as PackedInt32Array).has(client),
		"the client leaves"
	)
	play.expect(SaveGame.save_slot(1) == OK, "the host still saves after the client left")


## Joins, then checks every way to save or load is refused here.
func client() -> void:
	await play.boot(false)
	var loaded: bool = await _until(
		func() -> bool: return Game.current_level() != null, "the host's level loads here"
	)
	if not loaded:
		return
	play.expect(Game.session().is_online(), "the client plays an online session")
	play.expect(not SaveGame.can_save(), "an online client cannot save")
	play.expect(SaveGame.save_slot(1) == ERR_UNAUTHORIZED, "save_slot refuses the client")
	play.expect(SaveGame.autosave() == ERR_UNAUTHORIZED, "autosave refuses the client")
	var failed: Dictionary = play.watch(SaveGame.load_failed)
	play.expect(SaveGame.load_slot(1) == ERR_UNAUTHORIZED, "load_slot refuses the client")
	var args: Array = await play.wait_watched(failed, 5)
	play.expect(args == [1, SaveGame.REASON_CLIENT], "the client is told only the host can")
	SaveGame.request_autosave()
	play.expect(not SaveGame.autosave_pending(), "the client never queues an autosave")
	var saved: Dictionary = play.watch(SaveGame.saved)
	GameEvents.checkpoint_reached.emit("online_client")
	await play.physics_frames(10)
	play.expect(play.fire_count(saved) == 0, "a checkpoint on the client does not save")
	_stop(saved)
	for slot: int in SaveGame.SLOTS:
		play.expect(not SaveGame.has_slot(slot), "the client wrote no slot %d" % slot)
	Menus.open_pause()
	var page: Control = Menus.current_page()
	play.expect(page != null and page.name == &"PauseMenu", "the client opens the pause menu")
	if page != null:
		for id: String in ["save", "load"]:
			var button: Button = page.find_child(id, true, false) as Button
			play.expect(button != null and button.disabled, "%s is disabled on the client" % id)
	Menus.close_all()
	await _wait_ms(CLIENT_STAYS_MS)
	Game.quit_to_menu()
	await _wait_ms(500)


func _stop(record: Dictionary) -> void:
	var signal_ref: Signal = record["signal"]
	var catch: Variant = record.get("callable")
	if catch is Callable and signal_ref.is_connected(catch):
		signal_ref.disconnect(catch)
	record.erase("callable")


func _client_id() -> int:
	for id: int in net.call(&"peers") as PackedInt32Array:
		if id != 1:
			return id
	return 0


func _until(condition: Callable, message: String, limit_ms: int = LONG_MS) -> bool:
	var deadline: int = Time.get_ticks_msec() + limit_ms
	while not bool(condition.call()):
		if Time.get_ticks_msec() > deadline:
			play.expect(false, "%s (waited %d ms)" % [message, limit_ms])
			return false
		await tree.process_frame
	return true


func _wait_ms(ms: int) -> void:
	var deadline: int = Time.get_ticks_msec() + ms
	while Time.get_ticks_msec() < deadline:
		await tree.process_frame
