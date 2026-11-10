extends Node
## Who is in the online game: a name and a ready flag per player, kept by the
## server and sent to every player. The host starts once everyone is ready;
## a player who joins a game already running goes straight into the level.

signal players_changed

const LobbyPage := preload("res://features/online-play/ui/lobby_page.gd")
const NAME_LIMIT := 24

## {peer_id: {"name": String, "ready": bool}}; the server's copy is the truth.
var players: Dictionary = {}

var _names: Dictionary = {}
var _actions: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	Net.hosted.connect(_on_hosted)
	Net.joined.connect(_on_joined)


## Opens the online page (host, join, LAN list, players, ready, start).
func open_page() -> void:
	var content: Control = LobbyPage.new()
	var screen: Control = UiStyle.page("Play online", content, content.get(&"back_action"))
	screen.name = "LobbyPage"
	screen.set_meta(&"menu", &"online")
	Menus.open_page(screen)


## Adds a button to the page's "not connected" part, for example a platform
## feature's "Host on Steam". `available` (optional) returns whether to show it.
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
		if available.is_valid() and not bool(available.call()):
			continue
		found.append(entry)
	found.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool: return int(a["order"]) < int(b["order"])
	)
	return found


func reset() -> void:
	players.clear()
	_names.clear()
	players_changed.emit()


## Server: the name a peer sent in its handshake (used until it registers).
func note_name(id: int, player_name: String) -> void:
	_names[id] = clean_name(player_name)


## Server: drops a peer that left.
func forget(id: int) -> void:
	_names.erase(id)
	if players.erase(id) and Net.is_server():
		_broadcast()


func set_local_name(player_name: String) -> void:
	var cleaned: String = clean_name(player_name)
	if cleaned.is_empty():
		return
	Net.player_name = cleaned
	Settings.set_value("online", "name", cleaned)


## This player's ready flag, sent to the server.
func mark_ready(ready: bool) -> void:
	if Net.is_server():
		if players.has(1):
			(players[1] as Dictionary)["ready"] = ready
			_broadcast()
	elif Net.is_connected_to_game():
		set_ready.rpc_id(1, ready)


func is_ready(id: int) -> bool:
	return players.has(id) and bool((players[id] as Dictionary).get("ready", false))


func all_ready() -> bool:
	if players.is_empty():
		return false
	for id: Variant in players:
		if not is_ready(int(id)):
			return false
	return true


## The host may start when every player is ready and no level runs yet.
func can_start() -> bool:
	return Net.is_server() and all_ready() and Game.current_level() == null


## Host only: starts the level for everyone.
func start() -> Error:
	if not can_start():
		return ERR_UNAVAILABLE
	Game.start_game()
	return OK


static func clean_name(player_name: String) -> String:
	var cleaned: String = player_name.strip_edges().replace("\n", " ").replace("\t", " ")
	return cleaned.substr(0, NAME_LIMIT)


@rpc("any_peer", "call_remote", "reliable")
func register_player(player_name: String) -> void:
	if not multiplayer.is_server():
		return
	var id: int = multiplayer.get_remote_sender_id()
	var chosen: String = clean_name(player_name)
	if chosen.is_empty():
		chosen = str(_names.get(id, ""))
	if chosen.is_empty():
		chosen = tr("Player %d") % id
	var was_ready: bool = is_ready(id)
	players[id] = {"name": chosen, "ready": was_ready}
	_broadcast()


@rpc("any_peer", "call_remote", "reliable")
func set_ready(ready: bool) -> void:
	if not multiplayer.is_server():
		return
	var id: int = multiplayer.get_remote_sender_id()
	if not players.has(id):
		players[id] = {"name": str(_names.get(id, tr("Player %d") % id)), "ready": ready}
	else:
		(players[id] as Dictionary)["ready"] = ready
	_broadcast()


@rpc("authority", "call_remote", "reliable")
func _sync_players(data: Dictionary) -> void:
	players = data
	players_changed.emit()


func _on_hosted() -> void:
	players.clear()
	if not Net.dedicated:
		players[1] = {"name": Net.player_name, "ready": Net.has_arg("autostart")}
	_broadcast()


func _on_joined() -> void:
	register_player.rpc_id(1, Net.player_name)
	if Net.has_arg("autostart"):
		set_ready.rpc_id(1, true)


func _broadcast() -> void:
	players_changed.emit()
	if Net.is_server() and multiplayer.has_multiplayer_peer():
		_sync_players.rpc(players)
