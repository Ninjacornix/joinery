extends Node
## Online play with Godot's high-level multiplayer: host a game, join one by
## address or from the LAN list, or run a dedicated server.
##
## A joining player first proves it runs the same game and protocol version
## (`online/protocol_version`) through SceneMultiplayer's authentication step;
## a mismatched player is refused with a reason and never enters the game.
## The level, players and world state are kept in step by the `Replication`
## child (net_replication.gd); `Lobby` holds names and ready flags.
##
## User arguments (after `--`): `--host`, `--join=<address>:<port>`,
## `--port=<n>`, `--name=<text>`, `--server` (dedicated, no local player),
## `--autostart` (start or join straight away), and for tests
## `--lan-port=<n>`, `--lan-broadcast=<address>`, `--protocol-version=<n>`.

signal hosted
signal joined
signal join_failed(reason: String)
signal peer_joined(id: int)
signal peer_left(id: int)
signal session_ended(reason: String)
## Server only: a player was turned away during the handshake.
signal refused(id: int, reason: String)

enum State { OFFLINE, HOSTING, CONNECTING, CONNECTED }

const Replication := preload("res://features/online-play/net_replication.gd")
## Optional per-starting-point behaviour, shipped by the feature's adapters:
## a `NetAdapter` (net_adapter.gd) that `Net` adds as its child `Adapter` on
## every peer, so the RPCs it declares reach the same node everywhere.
const ADAPTER := "res://features/online-play/adapter.gd"
const DEFAULT_PORT := 24565
const KICK_DELAY := 1.5
const AUTH_TIMEOUT := 5.0

var state: State = State.OFFLINE
## True for a dedicated server: it hosts, has no local player and no menus.
var dedicated: bool = false
var player_name: String = ""
var port: int = DEFAULT_PORT
var max_clients: int = 7
var lan_port: int = 47990
var lan_broadcast: String = "255.255.255.255"
## Why the last join failed or the last session ended ("" = it did not).
var last_reason: String = ""
var replication: Node = null
var lan: LanDiscovery = null
var adapter: Node = null

var _transport: NetTransport = EnetTransport.new()
var _protocol: int = 1
var _leaving: bool = false
var _pending: Dictionary = {}
var _args: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_protocol = int(ProjectSettings.get_setting("online/protocol_version", 1))
	port = int(ProjectSettings.get_setting("online/default_port", DEFAULT_PORT))
	max_clients = int(ProjectSettings.get_setting("online/max_clients", 7))
	lan_port = int(ProjectSettings.get_setting("online/lan_port", 47990))
	lan_broadcast = str(ProjectSettings.get_setting("online/lan_broadcast", "255.255.255.255"))
	_args = parse_args(OS.get_cmdline_user_args())
	port = int(_args.get("port", port))
	lan_port = int(_args.get("lan-port", lan_port))
	lan_broadcast = str(_args.get("lan-broadcast", lan_broadcast))
	_protocol = int(_args.get("protocol-version", _protocol))
	player_name = str(_args.get("name", Settings.get_value("online", "name", "")))
	if player_name.strip_edges().is_empty():
		player_name = tr("Player")
	dedicated = _args.has("server") or OS.has_feature("dedicated_server")
	if dedicated or _args.has("join"):
		Game.autostart = false
	replication = Replication.new()
	replication.name = "Replication"
	add_child(replication)
	lan = LanDiscovery.new()
	lan.name = "Lan"
	add_child(lan)
	if ResourceLoader.exists(ADAPTER):
		var script: Script = load(ADAPTER) as Script
		if script != null:
			adapter = script.new() as Node
		if adapter != null:
			adapter.name = "Adapter"
			add_child(adapter)
	var scene_multiplayer: SceneMultiplayer = multiplayer as SceneMultiplayer
	scene_multiplayer.auth_callback = _on_auth
	scene_multiplayer.auth_timeout = AUTH_TIMEOUT
	scene_multiplayer.peer_authenticating.connect(_on_peer_authenticating)
	scene_multiplayer.peer_authentication_failed.connect(_on_peer_authentication_failed)
	multiplayer.peer_connected.connect(_on_peer_connected)
	multiplayer.peer_disconnected.connect(_on_peer_disconnected)
	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)
	multiplayer.server_disconnected.connect(_on_server_disconnected)
	_add_menu_entries()
	_boot.call_deferred()


## `{"host": "", "join": "1.2.3.4:5", "port": "7", ...}` from `--key` and
## `--key=value` user arguments.
static func parse_args(arguments: PackedStringArray) -> Dictionary:
	var found: Dictionary = {}
	for argument: String in arguments:
		if not argument.begins_with("--"):
			continue
		var text: String = argument.substr(2)
		var cut: int = text.find("=")
		if cut < 0:
			found[text] = ""
		else:
			found[text.substr(0, cut)] = text.substr(cut + 1)
	return found


## True when the game was started with `--<name>` or `--<name>=…`.
func has_arg(arg_name: String) -> bool:
	return _args.has(arg_name)


func arg(arg_name: String, default: String = "") -> String:
	return str(_args.get(arg_name, default))


## Hosts a game: this instance is the server (and a player unless dedicated).
func host(host_port: int = DEFAULT_PORT, clients: int = 7) -> Error:
	if is_online():
		leave()
	var peer: MultiplayerPeer = _transport.create_host(host_port, clients)
	if peer == null:
		last_reason = tr("Could not host a game on port %d.") % host_port
		return _transport.last_error if _transport.last_error != OK else FAILED
	port = host_port
	max_clients = clients
	last_reason = ""
	_pending.clear()
	multiplayer.multiplayer_peer = peer
	state = State.HOSTING
	_begin_session()
	lan.start_beacon(lan_port, lan_broadcast, _beacon)
	hosted.emit()
	return OK


## Joins the game at `address`:`join_port`. `joined` or `join_failed` follows.
func join(address: String, join_port: int = DEFAULT_PORT) -> Error:
	if is_online():
		leave()
	var peer: MultiplayerPeer = _transport.create_client(address, join_port)
	if peer == null:
		var error: Error = _transport.last_error if _transport.last_error != OK else FAILED
		last_reason = tr("Could not connect to %s.") % address
		join_failed.emit(last_reason)
		return error
	last_reason = ""
	multiplayer.multiplayer_peer = peer
	state = State.CONNECTING
	_begin_session()
	return OK


## Ends the online session (the server ends it for everyone).
func leave() -> void:
	if _leaving or state == State.OFFLINE:
		return
	_leaving = true
	state = State.OFFLINE
	lan.stop()
	var peer: MultiplayerPeer = multiplayer.multiplayer_peer
	if peer != null and not peer is OfflineMultiplayerPeer:
		peer.close()
	multiplayer.multiplayer_peer = OfflineMultiplayerPeer.new()
	_pending.clear()
	replication.call(&"reset")
	Lobby.reset()
	Menus.remove_entry(&"pause", &"leave")
	if Game.session() is NetSession:
		Game.set_session(null)
	_leaving = false
	session_ended.emit(last_reason)


func is_online() -> bool:
	return state != State.OFFLINE


func is_server() -> bool:
	return state == State.HOSTING


func is_connected_to_game() -> bool:
	return state == State.HOSTING or state == State.CONNECTED


## Every peer in the game, this one included, sorted. Empty when offline.
func peers() -> PackedInt32Array:
	var ids: PackedInt32Array = PackedInt32Array()
	if not is_connected_to_game():
		return ids
	ids.append(multiplayer.get_unique_id())
	ids.append_array(multiplayer.get_peers())
	ids.sort()
	return ids


## Uses `transport` for the next host or join (null = ENet).
func set_transport(transport: NetTransport) -> void:
	_transport = transport if transport != null else EnetTransport.new()


func transport() -> NetTransport:
	return _transport


func protocol_version() -> int:
	return _protocol


## Restarts the level for everyone (offline: just this game).
func restart_level() -> void:
	if is_online():
		replication.call(&"request_restart")
	else:
		Game.restart_level()


## Called by the replication node once a level is set up on this peer.
func level_setup(level: Node) -> void:
	if adapter != null and adapter.has_method(&"level_setup"):
		adapter.call(&"level_setup", level, multiplayer.is_server())


## Server: called by the replication node once `id` has the level loaded and
## has been sent the world snapshot.
func level_peer_ready(id: int) -> void:
	if adapter != null and adapter.has_method(&"peer_ready"):
		adapter.call(&"peer_ready", id)


## Server: extra `{level-relative path: state}` the adapter adds to the
## snapshot a joining player receives (nodes outside group `saveable`).
func snapshot_extra() -> Dictionary:
	if adapter != null and adapter.has_method(&"snapshot_extra"):
		var extra: Variant = adapter.call(&"snapshot_extra")
		if extra is Dictionary:
			return extra
	return {}


# --- Boot -------------------------------------------------------------------------


func _boot() -> void:
	if dedicated:
		GameEvents.menu_opened.connect(_close_menus.unbind(1))
		Menus.close_all()
		var error: Error = host(port, max_clients)
		if error != OK:
			print("Online: %s" % last_reason)
			get_tree().quit(1)
			return
		print("Online: dedicated server listening on port %d." % port)
	elif _args.has("host"):
		host(port, max_clients)
		if not _args.has("autostart"):
			Lobby.open_page()
	elif _args.has("join"):
		var target: Array = split_address(str(_args["join"]), port)
		join(str(target[0]), int(target[1]))
		if not _args.has("autostart"):
			Lobby.open_page()


## `["1.2.3.4", 24565]` from "1.2.3.4:24565", "1.2.3.4" or "[::1]:24565".
static func split_address(text: String, default_port: int) -> Array:
	var address: String = text.strip_edges()
	var chosen: int = default_port
	if address.begins_with("["):
		var close: int = address.find("]")
		if close > 0:
			var rest: String = address.substr(close + 1)
			if rest.begins_with(":") and rest.substr(1).is_valid_int():
				chosen = int(rest.substr(1))
			address = address.substr(1, close - 1)
	elif address.count(":") == 1:
		var cut: int = address.find(":")
		if address.substr(cut + 1).is_valid_int():
			chosen = int(address.substr(cut + 1))
		address = address.substr(0, cut)
	return [address, chosen]


func _close_menus() -> void:
	if dedicated:
		Menus.close_all.call_deferred()


func _add_menu_entries() -> void:
	Menus.add_entry(&"main", &"online", "Play online", func() -> void: Lobby.open_page(), 20)
	Menus.add_entry(&"pause", &"restart", "Restart", restart_level, 20)


func _begin_session() -> void:
	Game.set_session(NetSession.new(self))
	Menus.add_entry(&"pause", &"leave", "Leave game", func() -> void: Game.quit_to_menu(), 85)


func _beacon() -> Dictionary:
	return {
		"game": str(ProjectSettings.get_setting("application/config/name", "")),
		"name": player_name,
		"port": port,
		"players": Lobby.players.size(),
		"max": max_clients + (0 if dedicated else 1),
		"version": _protocol,
	}


# --- Handshake --------------------------------------------------------------------


func _on_peer_authenticating(id: int) -> void:
	if multiplayer.is_server():
		_pending[id] = true
		return
	var hello: Dictionary = {
		"game": str(ProjectSettings.get_setting("application/config/name", "")),
		"version": _protocol,
		"name": player_name,
	}
	(multiplayer as SceneMultiplayer).send_auth(id, JSON.stringify(hello).to_utf8_buffer())


func _on_auth(id: int, data: PackedByteArray) -> void:
	var parsed: Variant = JSON.parse_string(data.get_string_from_utf8())
	var message: Dictionary = parsed if parsed is Dictionary else {}
	var scene_multiplayer: SceneMultiplayer = multiplayer as SceneMultiplayer
	if multiplayer.is_server():
		var refusal: Dictionary = _check_hello(message)
		if refusal.is_empty():
			Lobby.note_name(id, str(message.get("name", "")))
			scene_multiplayer.send_auth(id, JSON.stringify({"ok": true}).to_utf8_buffer())
			scene_multiplayer.complete_auth(id)
		else:
			scene_multiplayer.send_auth(id, JSON.stringify(refusal).to_utf8_buffer())
			refused.emit(id, refusal_text(refusal))
			get_tree().create_timer(KICK_DELAY, true, false, true).timeout.connect(_kick.bind(id))
	elif bool(message.get("ok", false)):
		scene_multiplayer.complete_auth(id)
	else:
		_fail.call_deferred(refusal_text(message))


func _check_hello(hello: Dictionary) -> Dictionary:
	var game: String = str(ProjectSettings.get_setting("application/config/name", ""))
	if str(hello.get("game", "")) != game:
		return {"refused": "game"}
	var version: int = int(hello.get("version", -1))
	if version != _protocol:
		return {"refused": "version", "host": _protocol, "client": version}
	return {}


## The sentence shown for a refusal from `_check_hello`.
func refusal_text(refusal: Dictionary) -> String:
	match str(refusal.get("refused", "")):
		"version":
			return tr("The host runs a different version of the game (host %d, yours %d).") % [
				int(refusal.get("host", 0)), int(refusal.get("client", 0))
			]
		"game":
			return tr("The host is playing a different game.")
	return tr("The host did not let you in.")


func _kick(id: int) -> void:
	if _pending.has(id) and is_server():
		_pending.erase(id)
		(multiplayer as SceneMultiplayer).disconnect_peer(id)


func _on_peer_authentication_failed(id: int) -> void:
	if multiplayer.is_server():
		_pending.erase(id)
	elif state == State.CONNECTING and last_reason.is_empty():
		_fail.call_deferred(tr("The host did not answer."))


# --- Connection events ------------------------------------------------------------


func _on_peer_connected(id: int) -> void:
	_pending.erase(id)
	peer_joined.emit(id)
	if not is_server():
		return
	if dedicated and Game.current_level() == null:
		Game.start_game()
	else:
		replication.call(&"peer_joined", id)


func _on_peer_disconnected(id: int) -> void:
	_pending.erase(id)
	peer_left.emit(id)
	replication.call(&"peer_left", id)
	Lobby.forget(id)
	if dedicated and is_server() and multiplayer.get_peers().is_empty():
		_idle.call_deferred()


func _on_connected_to_server() -> void:
	state = State.CONNECTED
	joined.emit()


func _on_connection_failed() -> void:
	_fail.call_deferred(tr("Could not reach the host."))


func _on_server_disconnected() -> void:
	_end.call_deferred(tr("The host left the game."))


func _fail(reason: String) -> void:
	if state == State.OFFLINE:
		return
	last_reason = reason
	leave()
	join_failed.emit(reason)
	Menus.toast(reason, 4.0)


func _end(reason: String) -> void:
	if state == State.OFFLINE:
		return
	last_reason = reason
	var in_level: bool = Game.current_level() != null
	leave()
	if in_level:
		Game.quit_to_menu()
	Menus.toast(reason, 4.0)


## A dedicated server whose last player left goes back to waiting; the next
## player to join starts a fresh level.
func _idle() -> void:
	if not dedicated or not is_server() or not multiplayer.get_peers().is_empty():
		return
	replication.call(&"reset")
	var main_scene: String = str(
		ProjectSettings.get_setting("application/run/main_scene", "res://core/main.tscn")
	)
	get_tree().change_scene_to_file(main_scene)
