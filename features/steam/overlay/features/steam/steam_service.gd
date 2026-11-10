# gdlint: disable=max-public-methods
extends Node
## Steam achievements, stats, rich presence and friend lobbies, through the
## GodotSteam extension. Joinery downloads the one pinned, hash-checked
## GodotSteam release into projects that have this feature (see
## `policy/godotsteam.toml` in Joinery); it never uploads or publishes
## anything to Steam.
##
## Without the extension (stock Godot) every method returns false, "" or 0 and
## nothing is printed apart from one verbose line, so the game runs the same
## with or without Steam. The singleton is held as a plain Object and called by
## method name, so this script parses on stock Godot.
##
## Checked against GodotSteam 4.23 (GDExtension): every singleton method this
## script calls is in METHODS, the init methods in INIT_METHODS, the signals
## and their argument counts in SIGNALS and SIGNAL_ARGS. A project with the
## extension installed can confirm them with
## `res://tests/features/steam/extension_check.gd`.
##
## Project settings: `steam/app_id` (0 = not configured), `steam/connect`
## ("steam" = online play can run over Steam lobbies, "direct" = ENet only),
## `steam/achievement_on_complete` (achievement unlocked when a level is
## completed, "" = none), `steam/embed_callbacks` (the extension runs Steam
## callbacks itself; otherwise this node calls `run_callbacks` every frame).
##
## With the online-play feature and `steam/connect` = "steam", a hosted or
## joined Steam lobby installs `steam_transport.gd` as `Net`'s transport when a
## Steam multiplayer peer is available; otherwise (or with "direct") online
## play keeps ENet. Then the online page gains "Host on Steam" and
## "Join friend", the player's Steam name is the default online name (a name
## the player typed still wins), and the bridge ends with the online session.

signal lobby_created(lobby_id: int)
signal lobby_joined(lobby_id: int)
signal lobby_failed(reason: String)
## Online play switched to Steam networking (`as_host` false = joined a friend).
signal bridged(as_host: bool)

const SINGLETON := "Steam"
const PEER_CLASS := &"SteamMultiplayerPeer"
const NET_TRANSPORT_CLASS := &"NetTransport"
const NET_PATH := ^"/root/Net"
const LOBBY_PATH := ^"/root/Lobby"
const MENUS_PATH := ^"/root/Menus"
const SETTINGS_PATH := ^"/root/Settings"
const TRANSPORT_PATH := "res://features/steam/steam_transport.gd"
const APP_ID_SETTING := "steam/app_id"
const COMPLETE_SETTING := "steam/achievement_on_complete"
const EMBED_SETTING := "steam/embed_callbacks"
const CONNECT_SETTING := "steam/connect"
const CONNECT_STEAM := "steam"
const CONNECT_DIRECT := "direct"
const PORT_SETTING := "online/default_port"
const MAX_CLIENTS_SETTING := "online/max_clients"
const DEFAULT_PORT := 24565
const DEFAULT_LOBBY_SIZE := 4

const STATUS_NOT_INSTALLED := "not installed"
const STATUS_NO_APP_ID := "no app id"
const STATUS_INIT_FAILED := "init failed: "
const STATUS_READY := "ready"

## Init methods tried in order; the first the singleton has is used. Both take
## (app_id, embed_callbacks) in GodotSteam 4.23.
const INIT_METHODS: Array[String] = ["steamInitEx", "steamInit"]
## Every other singleton method this script calls.
const METHODS: Array[String] = [
	"steamShutdown",
	"run_callbacks",
	"setAchievement",
	"clearAchievement",
	"setStatInt",
	"setStatFloat",
	"storeStats",
	"setRichPresence",
	"clearRichPresence",
	"createLobby",
	"joinLobby",
	"leaveLobby",
	"getLobbyOwner",
	"getPersonaName",
	"getSteamID",
	"activateGameOverlay",
	"activateGameOverlayInviteDialog",
]
## SteamMultiplayerPeer methods `steam_transport.gd` calls.
const PEER_METHODS: Array[String] = ["create_host", "create_client"]
## Steam lobby types (ELobbyType).
const LOBBY_FRIENDS_ONLY := 1
const LOBBY_PUBLIC := 2
## EResult OK, and EChatRoomEnterResponse success.
const RESULT_OK := 1
const ENTER_SUCCESS := 1
## Launch option Steam adds when a friend joins from the friends list.
const CONNECT_LOBBY_ARG := "+connect_lobby"
## Extension signal -> handler here.
const SIGNALS := {
	"lobby_created": &"_on_lobby_created",
	"lobby_joined": &"_on_lobby_joined",
	"join_requested": &"_on_join_requested",
}
## How many arguments each of those signals carries in GodotSteam 4.23.
const SIGNAL_ARGS := {
	"lobby_created": 2,
	"lobby_joined": 4,
	"join_requested": 2,
}

## True once Steam initialised.
var available: bool = false
## "not installed", "no app id", "init failed: …" or "ready".
var status: String = STATUS_NOT_INSTALLED
## The Steam lobby this game is in, 0 = none.
var lobby_id: int = 0
## True while online play runs over Steam networking.
var bridged_to_net: bool = false
## Join or host through `Net` automatically when a lobby is joined or created.
var auto_bridge: bool = true
## Makes the multiplayer peer for the Steam transport instead of the
## extension's `SteamMultiplayerPeer` (tests). See `steam_transport.gd`.
var transport_peer_factory: Callable = Callable()

var _steam: Object = null
var _embedded: bool = false
var _previous_transport: Variant = null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	set_process(false)
	var events: Node = get_node_or_null(^"/root/GameEvents")
	if events != null and events.has_signal(&"level_completed"):
		events.connect(&"level_completed", _on_level_completed)
	if initialize():
		_join_from_launch_options()


func _process(_delta: float) -> void:
	if not _embedded and _can(&"run_callbacks"):
		_steam.call(&"run_callbacks")


func _exit_tree() -> void:
	if available and _steam.has_method(&"steamShutdown"):
		_steam.call(&"steamShutdown")
	_release()


## Looks for the GodotSteam singleton and starts Steam with `steam/app_id`.
## Safe to call again (for example after the app id changes).
func initialize() -> bool:
	if available and _steam == _singleton():
		_hook_net()
		_offer_persona_name()
		_refresh_menu_entries()
		return true
	_release()
	var steam: Object = _singleton()
	if steam == null:
		status = STATUS_NOT_INSTALLED
		print_verbose("Steam: the GodotSteam extension is not installed; Steam calls do nothing.")
	elif _app_id() <= 0:
		status = STATUS_NO_APP_ID
		print_verbose("Steam: set steam/app_id to use Steam.")
	else:
		var problem: String = _start(steam)
		if problem.is_empty():
			_steam = steam
			available = true
			status = STATUS_READY
			for signal_name: String in SIGNALS:
				var handler: Callable = Callable(self, SIGNALS[signal_name])
				if steam.has_signal(signal_name) and not steam.is_connected(signal_name, handler):
					steam.connect(signal_name, handler)
		else:
			status = STATUS_INIT_FAILED + problem
			print_verbose("Steam: %s" % status)
	set_process(available and not _embedded)
	_hook_net()
	_offer_persona_name()
	_refresh_menu_entries()
	return available


## Unlocks an achievement and stores it so Steam shows it.
func unlock_achievement(id: String) -> bool:
	if not _can(&"setAchievement") or id.is_empty():
		return false
	return _ok(_steam.call(&"setAchievement", id)) and store_stats()


func clear_achievement(id: String) -> bool:
	if not _can(&"clearAchievement") or id.is_empty():
		return false
	return _ok(_steam.call(&"clearAchievement", id)) and store_stats()


func set_stat_int(stat: String, value: int) -> bool:
	if not _can(&"setStatInt") or stat.is_empty():
		return false
	return _ok(_steam.call(&"setStatInt", stat, value))


func set_stat_float(stat: String, value: float) -> bool:
	if not _can(&"setStatFloat") or stat.is_empty():
		return false
	return _ok(_steam.call(&"setStatFloat", stat, value))


## Sends changed stats and achievements to Steam.
func store_stats() -> bool:
	if not _can(&"storeStats"):
		return false
	return _ok(_steam.call(&"storeStats"))


func set_rich_presence(key: String, value: String) -> bool:
	if not _can(&"setRichPresence") or key.is_empty():
		return false
	return _ok(_steam.call(&"setRichPresence", key, value))


func clear_rich_presence() -> void:
	if _can(&"clearRichPresence"):
		_steam.call(&"clearRichPresence")


## Asks Steam for a lobby; `lobby_created` or `lobby_failed` follows.
## Not public = friends only.
func create_lobby(max_members: int, public: bool = false) -> bool:
	if not _can(&"createLobby") or max_members < 1:
		return false
	_steam.call(&"createLobby", LOBBY_PUBLIC if public else LOBBY_FRIENDS_ONLY, max_members)
	return true


## Asks Steam to join a lobby; `lobby_joined` or `lobby_failed` follows.
func join_lobby(id: int) -> bool:
	if not _can(&"joinLobby") or id <= 0:
		return false
	_steam.call(&"joinLobby", id)
	return true


func leave_lobby() -> void:
	if lobby_id != 0 and _can(&"leaveLobby"):
		_steam.call(&"leaveLobby", lobby_id)
	lobby_id = 0
	_unbridge()


## The player's Steam name, "" without Steam.
func persona_name() -> String:
	if not _can(&"getPersonaName"):
		return ""
	return str(_steam.call(&"getPersonaName"))


## The player's Steam id, 0 without Steam.
func steam_id() -> int:
	if not _can(&"getSteamID"):
		return 0
	return int(_steam.call(&"getSteamID"))


## "steam" or "direct": how players connect (`steam/connect`).
func connect_through() -> String:
	var value: String = str(ProjectSettings.get_setting(CONNECT_SETTING, CONNECT_STEAM))
	return CONNECT_DIRECT if value == CONNECT_DIRECT else CONNECT_STEAM


## Whether a lobby can carry online play: players connect through Steam,
## online play is added, its transport hook exists and a Steam multiplayer
## peer can be made.
func bridge_available() -> bool:
	return (
		available
		and connect_through() == CONNECT_STEAM
		and get_node_or_null(NET_PATH) != null
		and _class_known(NET_TRANSPORT_CLASS)
		and (transport_peer_factory.is_valid() or ClassDB.class_exists(PEER_CLASS))
	)


## Online page "Host on Steam": a friends-only lobby; online play follows.
func host_on_steam() -> void:
	if create_lobby(_lobby_size()):
		_toast(tr("Creating a Steam lobby…"))
	else:
		_toast(tr("Steam is not available."))


## Online page "Join friend": the Steam overlay lists friends' games.
func join_friend() -> void:
	if _can(&"activateGameOverlay"):
		_steam.call(&"activateGameOverlay", "Friends")
		_toast(tr("Choose a friend's game in the Steam overlay."))
	else:
		_toast(tr("Steam is not available."))


## Pause menu "Invite friends", while in a lobby.
func invite_friends() -> void:
	if lobby_id != 0 and _can(&"activateGameOverlayInviteDialog"):
		_steam.call(&"activateGameOverlayInviteDialog", lobby_id)


func _singleton() -> Object:
	if Engine.has_singleton(SINGLETON):
		return Engine.get_singleton(SINGLETON)
	return null


func _app_id() -> int:
	return int(ProjectSettings.get_setting(APP_ID_SETTING, 0))


## Starts Steam; returns "" or what went wrong.
func _start(steam: Object) -> String:
	var embed: bool = bool(ProjectSettings.get_setting(EMBED_SETTING, false))
	for method: String in INIT_METHODS:
		if not steam.has_method(method):
			continue
		var args: Array = [_app_id(), embed]
		var arity: int = _arity(steam, method)
		if arity >= 0 and arity < args.size():
			args.resize(arity)
		_embedded = embed and args.size() >= 2
		var result: Variant = steam.callv(method, args)
		return _init_problem(method, result)
	return "the extension has no init method this wrapper knows"


## steamInitEx answers {status: 0 = OK, verbal}; steamInit answers a bool, or
## (older versions) {status: 1 = OK, verbal}.
func _init_problem(method: String, result: Variant) -> String:
	if result is bool:
		return "" if result else "Steam did not start"
	if result is Dictionary:
		var answer: Dictionary = result
		var ok_status: int = 0 if method == "steamInitEx" else 1
		if int(answer.get("status", -1)) == ok_status:
			return ""
		var verbal: String = str(answer.get("verbal", ""))
		return verbal if not verbal.is_empty() else "status %s" % answer.get("status", "?")
	return "unexpected answer %s" % [result]


static func _arity(object: Object, method: String) -> int:
	for info: Dictionary in object.get_method_list():
		if str(info.get("name", "")) == method:
			if int(info.get("flags", 0)) & METHOD_FLAG_VARARG:
				return -1
			return (info.get("args", []) as Array).size()
	return -1


static func _ok(value: Variant) -> bool:
	return value is bool and value


func _can(method: StringName) -> bool:
	return available and is_instance_valid(_steam) and _steam.has_method(method)


func _release() -> void:
	if is_instance_valid(_steam):
		for signal_name: String in SIGNALS:
			var handler: Callable = Callable(self, SIGNALS[signal_name])
			if _steam.has_signal(signal_name) and _steam.is_connected(signal_name, handler):
				_steam.disconnect(signal_name, handler)
	_steam = null
	available = false
	_embedded = false


func _on_level_completed(_result: Dictionary) -> void:
	var id: String = str(ProjectSettings.get_setting(COMPLETE_SETTING, ""))
	if available and not id.is_empty():
		unlock_achievement(id)


# Signal handlers take any arguments, so a GodotSteam version that adds one
# does not break the connection. GodotSteam 4.23: lobby_created(connect,
# lobby_id), lobby_joined(lobby, permissions, locked, response),
# join_requested(lobby_id, steam_id); see SIGNAL_ARGS.
func _on_lobby_created(...args: Array) -> void:
	var result: int = int(args[0]) if args.size() > 0 else -1
	var id: int = int(args[1]) if args.size() > 1 else 0
	if result != RESULT_OK or id == 0:
		lobby_failed.emit(tr("Steam could not create the lobby (code %d).") % result)
		return
	lobby_id = id
	lobby_created.emit(id)
	if auto_bridge and bridge_available():
		_bridge(true, "")


func _on_lobby_joined(...args: Array) -> void:
	var id: int = int(args[0]) if args.size() > 0 else 0
	var response: int = int(args[3]) if args.size() > 3 else ENTER_SUCCESS
	if response != ENTER_SUCCESS or id == 0:
		lobby_failed.emit(tr("Steam could not join the lobby (code %d).") % response)
		return
	lobby_id = id
	lobby_joined.emit(id)
	var owner_id: int = 0
	if _can(&"getLobbyOwner"):
		owner_id = int(_steam.call(&"getLobbyOwner", id))
	if owner_id != 0 and owner_id != steam_id() and auto_bridge and bridge_available():
		_bridge(false, str(owner_id))


func _on_join_requested(...args: Array) -> void:
	var id: int = int(args[0]) if args.size() > 0 else 0
	if id > 0:
		if lobby_id != 0 and lobby_id != id:
			leave_lobby()
		join_lobby(id)


func _join_from_launch_options() -> void:
	var arguments: PackedStringArray = OS.get_cmdline_args()
	var at: int = arguments.find(CONNECT_LOBBY_ARG)
	if at >= 0 and at + 1 < arguments.size() and arguments[at + 1].is_valid_int():
		join_lobby(arguments[at + 1].to_int())


## Switches `Net` to the Steam transport and hosts or joins the lobby owner.
func _bridge(as_host: bool, owner_address: String) -> void:
	var net: Node = get_node_or_null(NET_PATH)
	var script: Script = load(TRANSPORT_PATH) as Script
	if net == null or script == null or not net.has_method(&"set_transport"):
		return
	var transport: Object = script.new()
	transport.set(&"peer_factory", transport_peer_factory)
	if not bridged_to_net:
		_previous_transport = _current_transport(net)
	net.call(&"set_transport", transport)
	var port: int = int(ProjectSettings.get_setting(PORT_SETTING, DEFAULT_PORT))
	var error: int = FAILED
	if as_host and net.has_method(&"host"):
		error = int(net.call(&"host", port, maxi(_lobby_size() - 1, 1)))
	elif not as_host and net.has_method(&"join"):
		error = int(net.call(&"join", owner_address, port))
	if error != OK:
		_restore_transport(net)
		lobby_failed.emit(tr("Online play over Steam could not start (error %d).") % error)
		return
	bridged_to_net = true
	bridged.emit(as_host)


func _unbridge() -> void:
	if not bridged_to_net:
		return
	# Cleared first: leaving ends the online session, which calls back here.
	bridged_to_net = false
	var net: Node = get_node_or_null(NET_PATH)
	if net != null:
		if net.has_method(&"leave"):
			net.call(&"leave")
		_restore_transport(net)


## Online play ended on its own (host left, connection lost, Leave game):
## leave the Steam lobby too and give online play its own transport back.
func _on_session_ended(_reason: String) -> void:
	if bridged_to_net:
		bridged_to_net = false
		var net: Node = get_node_or_null(NET_PATH)
		if net != null:
			_restore_transport(net)
		leave_lobby()


func _hook_net() -> void:
	var net: Node = get_node_or_null(NET_PATH)
	if net == null or not net.has_signal(&"session_ended"):
		return
	if not net.is_connected(&"session_ended", _on_session_ended):
		net.connect(&"session_ended", _on_session_ended)


## Uses the Steam name online unless the player chose a name (setting
## `online/name`) or the game was started with `--name=…`.
func _offer_persona_name() -> void:
	var net: Node = get_node_or_null(NET_PATH)
	var steam_name: String = persona_name().strip_edges()
	if net == null or steam_name.is_empty() or not (&"player_name" in net):
		return
	if net.has_method(&"has_arg") and bool(net.call(&"has_arg", "name")):
		return
	var settings: Node = get_node_or_null(SETTINGS_PATH)
	if settings != null and settings.has_method(&"get_value"):
		if not str(settings.call(&"get_value", "online", "name", "")).strip_edges().is_empty():
			return
	net.set(&"player_name", steam_name)


## Puts back the transport online play used before (null = its default, ENet).
func _restore_transport(net: Node) -> void:
	if net.has_method(&"set_transport"):
		net.call(&"set_transport", _previous_transport)
	_previous_transport = null


static func _current_transport(net: Node) -> Variant:
	if net.has_method(&"transport"):
		return net.call(&"transport")
	return null


## Online play's player limit (clients + host), or 4 without online play.
func _lobby_size() -> int:
	var clients: int = int(ProjectSettings.get_setting(MAX_CLIENTS_SETTING, DEFAULT_LOBBY_SIZE - 1))
	return maxi(clients + 1, 2)


static func _class_known(class_id: StringName) -> bool:
	if ClassDB.class_exists(class_id):
		return true
	for info: Dictionary in ProjectSettings.get_global_class_list():
		if StringName(info.get("class", "")) == class_id:
			return true
	return false


## "Host on Steam" and "Join friend" on online play's page while the bridge
## is available; "Invite friends" in the pause menu while Steam runs.
func _refresh_menu_entries() -> void:
	var lobby: Node = get_node_or_null(LOBBY_PATH)
	if lobby != null and lobby.has_method(&"add_action"):
		if bridge_available():
			lobby.call(
				&"add_action", &"steam_host", "Host on Steam", host_on_steam, 10, bridge_available
			)
			lobby.call(
				&"add_action", &"steam_join", "Join friend", join_friend, 11, bridge_available
			)
		else:
			lobby.call(&"remove_action", &"steam_host")
			lobby.call(&"remove_action", &"steam_join")
	var menus: Node = get_node_or_null(MENUS_PATH)
	if menus == null or not menus.has_method(&"add_entry"):
		return
	if available:
		menus.call(
			&"add_entry",
			&"pause",
			&"steam_invite",
			"Invite friends",
			invite_friends,
			35,
			func() -> bool: return lobby_id != 0
		)
	else:
		menus.call(&"remove_entry", &"pause", &"steam_invite")


func _toast(text: String) -> void:
	var menus: Node = get_node_or_null(MENUS_PATH)
	if menus != null and menus.has_method(&"toast"):
		menus.call(&"toast", text)
