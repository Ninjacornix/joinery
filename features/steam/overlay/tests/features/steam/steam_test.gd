extends SceneTree
## Checks the Steam feature without the GodotSteam extension (stock Godot:
## every call is a quiet no-op), then with a fake Steam singleton (calls are
## routed, lobby signals re-emitted, achievements on level completion), then
## the bridge to online play when `NetTransport` exists: against stubs of
## online play's `Net` and `Lobby` when the feature is not applied, and against
## the real ones when it is.
## Run: godot --headless --fixed-fps 60 --script res://tests/features/steam/steam_test.gd
##        -- --autostart --settings-path=user://test_settings.cfg

const FAKE := "res://tests/features/steam/fake_steam.gd"
const TRANSPORT := "res://features/steam/steam_transport.gd"
const STEAM_ID := 76561190000000001
const FAKE_MY_ID := STEAM_ID
const STUB_NET := """extends Node

signal session_ended(reason: String)

var player_name: String = "Player"
var default_transport: Object = RefCounted.new()
var calls: Array = []
var _transport: Object = default_transport


func set_transport(next: Object) -> void:
	_transport = next if next != null else default_transport
	calls.append(["set_transport", next])


func transport() -> Object:
	return _transport


func host(port: int = 24565, max_clients: int = 7) -> Error:
	calls.append(["host", port, max_clients])
	var peer: Object = _transport.call(&"create_host", port, max_clients)
	return OK if peer != null else FAILED


func join(address: String, port: int = 24565) -> Error:
	calls.append(["join", address, port])
	var peer: Object = _transport.call(&"create_client", address, port)
	return OK if peer != null else FAILED


func leave() -> void:
	calls.append(["leave"])
	session_ended.emit("")
"""
const STUB_LOBBY := """extends Node

var _actions: Dictionary = {}


func add_action(
	id: StringName, label: String, action: Callable, order: int = 100,
	available: Callable = Callable()
) -> void:
	_actions[id] = {"id": id, "label": label, "action": action, "order": order, "enabled": available}


func remove_action(id: StringName) -> void:
	_actions.erase(id)


func actions() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for entry: Dictionary in _actions.values():
		var available: Callable = entry["enabled"]
		if not available.is_valid() or bool(available.call()):
			found.append(entry)
	return found
"""

var play: PlayHarness
var steam: Node


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	play = PlayHarness.new(self, "steam")
	steam = root.get_node_or_null(^"SteamService")
	play.expect(steam != null, "the SteamService autoload is loaded")
	if steam == null:
		play.finish()
		return
	await play.boot(true)
	_absent()
	await _present()
	await _embedded()
	if _net_transport_known():
		await _bridge()
		print("steam bridge: checked")
	else:
		play.expect(not steam.bridge_available(), "no bridge without online play")
	_unregister()
	play.expect(not steam.initialize(), "removing the singleton makes Steam unavailable")
	play.expect(steam.status == "not installed", "status returns to not installed")
	play.finish()


func _absent() -> void:
	play.expect(not Engine.has_singleton("Steam"), "stock Godot has no Steam singleton")
	play.expect(steam.available == false, "Steam is unavailable")
	play.expect(steam.status == "not installed", "status is 'not installed' (%s)" % steam.status)
	play.expect(steam.initialize() == false, "initialize() is false")
	play.expect(steam.unlock_achievement("ACH_WIN") == false, "unlock_achievement no-op")
	play.expect(steam.clear_achievement("ACH_WIN") == false, "clear_achievement no-op")
	play.expect(steam.set_stat_int("kills", 3) == false, "set_stat_int no-op")
	play.expect(steam.set_stat_float("time", 1.5) == false, "set_stat_float no-op")
	play.expect(steam.store_stats() == false, "store_stats no-op")
	play.expect(steam.set_rich_presence("status", "Playing") == false, "rich presence no-op")
	steam.clear_rich_presence()
	play.expect(steam.create_lobby(4) == false, "create_lobby no-op")
	play.expect(steam.create_lobby(4, true) == false, "public create_lobby no-op")
	play.expect(steam.join_lobby(42) == false, "join_lobby no-op")
	steam.leave_lobby()
	play.expect(steam.lobby_id == 0, "no lobby")
	play.expect(steam.persona_name() == "", "persona_name is empty")
	play.expect(steam.steam_id() == 0, "steam_id is 0")
	play.expect(not steam.bridge_available(), "no bridge without Steam")
	steam.join_friend()
	steam.invite_friends()
	play.events.level_completed.emit({"time": 1.0, "score": 0})
	play.expect(_entry_ids(&"main").filter(_is_steam_entry).is_empty(), "no Steam main entries")
	play.expect(_entry_ids(&"pause").filter(_is_steam_entry).is_empty(), "no Steam pause entries")
	play.expect(_lobby_ids().filter(_is_steam_entry).is_empty(), "no Steam online page entries")
	var net: Node = root.get_node_or_null(^"Net")
	if net != null:
		play.expect(not steam.bridged_to_net, "online play keeps its own transport")
		if net.has_method(&"transport"):
			play.expect(
				_script_path(net.call(&"transport")) != TRANSPORT,
				"online play keeps ENet without Steam"
			)


func _present() -> void:
	var fake: Object = _register()
	ProjectSettings.set_setting("steam/app_id", 0)
	play.expect(not steam.initialize(), "app id 0 is not configured")
	play.expect(steam.status == "no app id", "status is 'no app id' (%s)" % steam.status)
	play.expect(fake.count("steamInitEx") == 0, "Steam is not started without an app id")

	ProjectSettings.set_setting("steam/app_id", 480)
	fake.init_status = 2
	fake.init_verbal = "Steam is not running"
	play.expect(not steam.initialize(), "a failed init is unavailable")
	play.expect(
		steam.status == "init failed: Steam is not running",
		"init failure status (%s)" % steam.status
	)
	play.expect(steam.unlock_achievement("ACH_WIN") == false, "calls stay no-ops after a failure")

	fake.init_status = 0
	play.expect(steam.initialize(), "initialize() starts Steam")
	play.expect(steam.available and steam.status == "ready", "status is ready (%s)" % steam.status)
	play.expect(fake.last("steamInitEx") == [true, 480, false], "init gets the app id")
	play.expect(steam.initialize(), "initialize() again is fine")
	play.expect(fake.count("steamInitEx") == 2, "a second initialize() does not start again")
	var before: int = fake.callbacks_run
	await play.process_frames(3)
	play.expect(
		fake.callbacks_run >= before + 2,
		"run_callbacks runs every frame (%d -> %d)" % [before, fake.callbacks_run]
	)

	play.expect(steam.unlock_achievement("ACH_WIN"), "unlock_achievement is routed")
	play.expect(fake.last("setAchievement") == ["ACH_WIN"], "setAchievement gets the id")
	play.expect(fake.count("storeStats") == 1, "unlocking stores stats")
	play.expect(steam.clear_achievement("ACH_WIN"), "clear_achievement is routed")
	play.expect(fake.last("clearAchievement") == ["ACH_WIN"], "clearAchievement gets the id")
	play.expect(not steam.unlock_achievement(""), "an empty achievement id is refused")
	play.expect(steam.set_stat_int("kills", 3), "set_stat_int is routed")
	play.expect(fake.last("setStatInt") == ["kills", 3], "setStatInt gets its values")
	play.expect(steam.set_stat_float("time", 1.5), "set_stat_float is routed")
	play.expect(fake.last("setStatFloat") == ["time", 1.5], "setStatFloat gets its values")
	play.expect(steam.store_stats(), "store_stats is routed")
	play.expect(steam.set_rich_presence("status", "In the forest"), "rich presence is routed")
	play.expect(
		fake.last("setRichPresence") == ["status", "In the forest"], "setRichPresence values"
	)
	steam.clear_rich_presence()
	play.expect(fake.count("clearRichPresence") == 1, "clearRichPresence is routed")
	play.expect(steam.persona_name() == "Fake Player", "persona_name reads Steam")
	play.expect(steam.steam_id() == STEAM_ID, "steam_id reads Steam")

	var created: Dictionary = play.watch(steam.lobby_created)
	var joined: Dictionary = play.watch(steam.lobby_joined)
	play.expect(steam.create_lobby(4), "create_lobby asks Steam")
	play.expect(fake.last("createLobby") == [1, 4], "a private lobby is friends only")
	var args: Array = await play.wait_watched(created, 30)
	play.expect(args == [7001], "lobby_created is re-emitted with the lobby id (%s)" % [args])
	await play.wait_watched(joined, 30)
	play.expect(steam.lobby_id == 7001, "the lobby is remembered")
	play.expect(not steam.bridged_to_net, "no online play bridge without a peer")
	play.expect(_entry_ids(&"pause").has("steam_invite"), "the pause menu can invite friends")
	steam.invite_friends()
	play.expect(fake.last("activateGameOverlayInviteDialog") == [7001], "invite opens the overlay")
	steam.leave_lobby()
	play.expect(fake.last("leaveLobby") == [7001], "leave_lobby leaves the Steam lobby")
	play.expect(steam.lobby_id == 0, "no lobby after leaving")

	play.expect(steam.create_lobby(8, true), "a public lobby")
	play.expect(fake.last("createLobby") == [2, 8], "public lobbies are public")
	await play.process_frames(2)
	steam.leave_lobby()
	play.expect(not steam.create_lobby(0), "a lobby needs a member")

	fake.create_result = 2
	var failed: Dictionary = play.watch(steam.lobby_failed)
	steam.create_lobby(4)
	args = await play.wait_watched(failed, 30)
	play.expect(args.size() == 1 and str(args[0]).contains("2"), "lobby_failed says why")
	fake.create_result = 1

	joined = play.watch(steam.lobby_joined)
	play.expect(steam.join_lobby(9001), "join_lobby asks Steam")
	args = await play.wait_watched(joined, 30)
	play.expect(args == [9001], "lobby_joined is re-emitted (%s)" % [args])
	fake.join_response = 4
	failed = play.watch(steam.lobby_failed)
	steam.join_lobby(9002)
	await play.wait_watched(failed, 30)
	play.expect(steam.lobby_id == 9001, "a refused join keeps the current lobby")
	fake.join_response = 1
	joined = play.watch(steam.lobby_joined)
	fake.join_requested.emit(9003, 12345)
	args = await play.wait_watched(joined, 30)
	play.expect(args == [9003], "a friend's invite joins their lobby (%s)" % [args])
	play.expect(fake.last("leaveLobby") == [9001], "joining another lobby leaves the old one")
	steam.leave_lobby()

	steam.join_friend()
	play.expect(fake.last("activateGameOverlay") == ["Friends"], "Join friend opens the overlay")

	ProjectSettings.set_setting("steam/achievement_on_complete", "ACH_LEVEL")
	play.events.level_completed.emit({"time": 12.0, "score": 3})
	play.expect(fake.last("setAchievement") == ["ACH_LEVEL"], "completing a level unlocks")
	ProjectSettings.set_setting("steam/achievement_on_complete", "")
	var unlocks: int = fake.count("setAchievement")
	play.events.level_completed.emit({"time": 12.0, "score": 3})
	play.expect(fake.count("setAchievement") == unlocks, "no achievement when none is set")


func _embedded() -> void:
	_unregister()
	ProjectSettings.set_setting("steam/embed_callbacks", true)
	var fake: Object = _register()
	play.expect(steam.initialize(), "a new singleton initialises")
	play.expect(fake.last("steamInitEx") == [true, 480, true], "embedded callbacks are asked for")
	await play.process_frames(3)
	play.expect(fake.callbacks_run == 0, "embedded callbacks are not run here")
	_unregister()
	ProjectSettings.set_setting("steam/embed_callbacks", false)
	_register()
	play.expect(steam.initialize(), "Steam starts again with callbacks run here")


func _bridge() -> void:
	var net: Node = root.get_node_or_null(^"Net")
	var stub: bool = net == null
	if stub:
		net = _stub_node("Net", STUB_NET)
	var fake: Object = Engine.get_singleton("Steam")
	play.expect(
		steam.bridge_available() == ClassDB.class_exists(&"SteamMultiplayerPeer"),
		"the bridge needs a Steam multiplayer peer"
	)
	if not stub:
		# The real Net: without a Steam peer a lobby leaves ENet in place.
		var enet: Object = net.call(&"transport")
		var created: Dictionary = play.watch(steam.lobby_created)
		steam.create_lobby(4)
		await play.wait_watched(created, 30)
		play.expect(not steam.bridged_to_net, "online play keeps ENet without a Steam peer")
		play.expect(net.call(&"transport") == enet, "Net still has its own transport")
		steam.leave_lobby()
		# A Steam peer that cannot start: Net refuses, the bridge puts ENet back.
		steam.transport_peer_factory = func(
			_as_host: bool, _address: String, _port: int, _max_clients: int
		) -> MultiplayerPeer:
			return null
		var failed: Dictionary = play.watch(steam.lobby_failed)
		steam.create_lobby(4)
		await play.wait_watched(failed, 30)
		play.expect(not steam.bridged_to_net, "a failed Steam host is not bridged")
		play.expect(net.call(&"transport") == enet, "the bridge restores online play's transport")
		play.expect(not bool(net.call(&"is_online")), "Net is not online after the failure")
		steam.leave_lobby()
		await _real_bridge(net, enet)
		steam.transport_peer_factory = Callable()
		steam.initialize()

	var made: Array = []
	var factory: Callable = func(
		as_host: bool, address: String, port: int, max_clients: int
	) -> MultiplayerPeer:
		made.append([as_host, address, port, max_clients])
		return OfflineMultiplayerPeer.new()
	var transport: Object = (load(TRANSPORT) as Script).new()
	var base: Script = (transport.get_script() as Script).get_base_script()
	play.expect(
		base != null and base.get_global_name() == &"NetTransport",
		"the Steam transport extends NetTransport"
	)
	play.expect(transport.call(&"create_host", 1, 3) == null, "no peer without the extension")
	play.expect(transport.call(&"describe") == "Steam", "the transport names itself")
	transport.set(&"peer_factory", factory)
	play.expect(transport.call(&"create_host", 1, 3) is MultiplayerPeer, "create_host makes a peer")
	play.expect(transport.call(&"create_client", "555", 1) is MultiplayerPeer, "create_client")
	play.expect(transport.call(&"create_client", "friend", 1) == null, "addresses are Steam ids")
	play.expect(made == [[true, "", 1, 3], [false, "555", 1, 0]], "factory calls %s" % [made])
	if not stub:
		return

	var lobby: Node = root.get_node_or_null(^"Lobby")
	if lobby == null:
		lobby = _stub_node("Lobby", STUB_LOBBY)
	play.expect(not steam.bridge_available(), "no bridge before a Steam peer can be made")
	play.expect(_lobby_ids().filter(_is_steam_entry).is_empty(), "no Steam page entries yet")
	steam.transport_peer_factory = factory
	play.expect(steam.initialize() and steam.bridge_available(), "the bridge is available")
	play.expect(net.get(&"player_name") == "Fake Player", "the Steam name is the online name")
	var previous: Object = RefCounted.new()
	net.call(&"set_transport", previous)
	(net.get(&"calls") as Array).clear()
	var ids: Array[String] = _lobby_ids()
	play.expect(ids.has("steam_host") and ids.has("steam_join"), "Steam page entries %s" % [ids])
	play.expect(_entry_ids(&"main").filter(_is_steam_entry).is_empty(), "not in the main menu")
	var bridged: Dictionary = play.watch(steam.bridged)
	steam.host_on_steam()
	var args: Array = await play.wait_watched(bridged, 30)
	play.expect(args == [true], "hosting a lobby hosts online play")
	var calls: Array = net.get(&"calls")
	play.expect(calls.size() == 2 and calls[1][0] == "host", "Net hosts (%s)" % [calls])
	play.expect(_script_path(net.call(&"transport")) == TRANSPORT, "Net uses the Steam transport")
	steam.leave_lobby()
	play.expect(net.call(&"transport") == previous, "leaving restores the previous transport")
	play.expect(not steam.bridged_to_net, "the bridge ends with the lobby")

	fake.lobby_owner = 555
	calls.clear()
	bridged = play.watch(steam.bridged)
	steam.join_lobby(8001)
	args = await play.wait_watched(bridged, 30)
	play.expect(args == [false], "joining a friend's lobby joins online play")
	play.expect(
		calls.size() == 2 and calls[1] == ["join", "555", 24565],
		"Net joins the owner (%s)" % [calls]
	)
	steam.leave_lobby()
	play.expect(calls.has(["leave"]), "leaving the lobby leaves online play")
	play.expect(net.call(&"transport") == previous, "the transport is restored after a join")

	# Online play ends on its own: the bridge ends and the lobby is left.
	fake.lobby_owner = FAKE_MY_ID
	bridged = play.watch(steam.bridged)
	steam.create_lobby(4)
	await play.wait_watched(bridged, 30)
	var lobby_was: int = steam.lobby_id
	net.call(&"leave")
	play.expect(not steam.bridged_to_net, "a session that ends ends the bridge")
	play.expect(fake.last("leaveLobby") == [lobby_was], "the Steam lobby is left with it")
	play.expect(steam.lobby_id == 0, "no lobby after the session ended")
	play.expect(net.call(&"transport") == previous, "online play gets its transport back")

	steam.transport_peer_factory = Callable()
	steam.initialize()
	play.expect(_lobby_ids().filter(_is_steam_entry).is_empty(), "Steam entries go with the bridge")
	net.queue_free()
	lobby.queue_free()


## The whole flow against the real `Net` and `Lobby`, with a peer that needs
## no network: the online page offers Steam, hosting a lobby hosts online
## play under the Steam name, and leaving the game ends the bridge.
func _real_bridge(net: Node, enet: Object) -> void:
	var fake: Object = Engine.get_singleton("Steam")
	steam.transport_peer_factory = func(
		_as_host: bool, _address: String, _port: int, _max_clients: int
	) -> MultiplayerPeer:
		return OfflineMultiplayerPeer.new()
	play.expect(steam.initialize() and steam.bridge_available(), "the real bridge is available")
	var ids: Array[String] = _lobby_ids()
	play.expect(ids.has("steam_host") and ids.has("steam_join"), "the online page offers Steam")
	play.expect(net.get(&"player_name") == "Fake Player", "the Steam name is the online name")
	var bridged: Dictionary = play.watch(steam.bridged)
	steam.host_on_steam()
	var args: Array = await play.wait_watched(bridged, 30)
	play.expect(args == [true], "hosting on Steam hosts online play (%s)" % [args])
	play.expect(bool(net.call(&"is_server")), "Net is the server")
	play.expect(_script_path(net.call(&"transport")) == TRANSPORT, "Net uses the Steam transport")
	var players: Dictionary = root.get_node(^"Lobby").get(&"players")
	play.expect(
		players.has(1) and str((players[1] as Dictionary).get("name")) == "Fake Player",
		"the host is listed under the Steam name (%s)" % [players]
	)
	var lobby_was: int = steam.lobby_id
	net.call(&"leave")
	play.expect(not steam.bridged_to_net, "leaving the game ends the bridge")
	play.expect(fake.last("leaveLobby") == [lobby_was], "and leaves the Steam lobby")
	play.expect(net.call(&"transport") == enet, "online play has ENet again")
	play.expect(not bool(net.call(&"is_online")), "Net is offline")


func _stub_node(node_name: String, source: String) -> Node:
	var script: GDScript = GDScript.new()
	script.source_code = source
	play.expect(script.reload() == OK, "the stub %s compiles" % node_name)
	var node: Node = script.new() as Node
	node.name = node_name
	root.add_child(node)
	return node


func _register() -> Object:
	var fake: Object = (load(FAKE) as Script).new()
	Engine.register_singleton("Steam", fake)
	return fake


func _unregister() -> void:
	if not Engine.has_singleton("Steam"):
		return
	var fake: Object = Engine.get_singleton("Steam")
	Engine.unregister_singleton("Steam")
	steam.initialize()
	fake.free()


func _net_transport_known() -> bool:
	for info: Dictionary in ProjectSettings.get_global_class_list():
		if StringName(info.get("class", "")) == &"NetTransport":
			return true
	return false


func _entry_ids(menu: StringName) -> Array[String]:
	var ids: Array[String] = []
	for entry: Dictionary in play.menus.entries(menu):
		ids.append(str(entry["id"]))
	return ids


## Ids of the extra actions on online play's page (empty without a Lobby).
func _lobby_ids() -> Array[String]:
	var ids: Array[String] = []
	var lobby: Node = root.get_node_or_null(^"Lobby")
	if lobby == null or not lobby.has_method(&"actions"):
		return ids
	for entry: Dictionary in lobby.call(&"actions"):
		ids.append(str(entry["id"]))
	return ids


func _is_steam_entry(id: String) -> bool:
	return id.begins_with("steam_")


func _script_path(object: Variant) -> String:
	if not (object is Object) or (object as Object).get_script() == null:
		return ""
	return ((object as Object).get_script() as Script).resource_path
