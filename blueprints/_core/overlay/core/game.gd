extends Node
## Starts, restarts and leaves levels, spawns players through the session, and
## saves or restores the state of every node in group `saveable`.
##
## Hooks come from project settings written when the starting point was added:
## `game/hooks/level_scene` and `game/hooks/player_scene` ("" = no player).

const LEVEL_SETTING := "game/hooks/level_scene"
const PLAYER_SETTING := "game/hooks/player_scene"
const MAIN_SETTING := "application/run/main_scene"
const DEFAULT_LEVEL := "res://game/level.tscn"
const DEFAULT_MAIN := "res://core/main.tscn"
const AUTOSTART_ARG := "--autostart"

## Set by save-load before `start_game`; applied once by `level_ready`.
var pending_state: Dictionary = {}
## Options passed to the last `start_game`.
var options: Dictionary = {}
## True when the main scene should skip the menu and start the level
## (user arg `--autostart`). Read once through `take_autostart`.
var autostart: bool = false

var _session: GameSession = GameSession.new()
var _level: Node = null
var _level_time: float = 0.0
var _play_time: float = 0.0
var _actor: int = 1
var _routing_restart: bool = false


func _ready() -> void:
	autostart = AUTOSTART_ARG in OS.get_cmdline_user_args()


func _process(delta: float) -> void:
	if is_instance_valid(_level) and _level.is_inside_tree():
		_level_time += delta
		_play_time += delta


## Changes to the level scene. `options` stays readable as `Game.options`.
func start_game(start_options: Dictionary = {}) -> void:
	options = start_options
	_play_time = 0.0
	Menus.close_all()
	get_tree().paused = false
	var level_path: String = str(ProjectSettings.get_setting(LEVEL_SETTING, DEFAULT_LEVEL))
	if not ResourceLoader.exists(level_path):
		push_error("The level scene %s does not exist." % level_path)
		return
	get_tree().change_scene_to_file(level_path)


## Restarts the current level. A session that has its own `restart_level`
## (online play) is asked first, so a restart reaches every player; it
## returns true when it took care of it.
func restart_level() -> void:
	if not _routing_restart and _session.has_method(&"restart_level"):
		# The session may call back here to do the actual reload.
		_routing_restart = true
		var handled: bool = bool(_session.call(&"restart_level"))
		_routing_restart = false
		if handled:
			return
	Menus.close_all()
	get_tree().paused = false
	if is_instance_valid(_level):
		get_tree().reload_current_scene()
	else:
		start_game(options)


func quit_to_menu() -> void:
	Menus.close_all()
	get_tree().paused = false
	_session.end()
	_session = GameSession.new()
	_level = null
	get_tree().change_scene_to_file(str(ProjectSettings.get_setting(MAIN_SETTING, DEFAULT_MAIN)))


## Returns the autostart flag and clears it, so returning to the menu shows it.
func take_autostart() -> bool:
	var value: bool = autostart
	autostart = false
	return value


## Every level calls this from `_ready`.
func level_ready(level: Node) -> void:
	_level = level
	_level_time = 0.0
	var spawn_root: Node = level.get_node_or_null(^"Players")
	if spawn_root == null:
		spawn_root = level
	var player_path: String = str(ProjectSettings.get_setting(PLAYER_SETTING, ""))
	if not player_path.is_empty():
		var player_scene: PackedScene = load(player_path) as PackedScene
		if player_scene == null:
			push_error("The player scene %s could not be loaded." % player_path)
		else:
			_session.spawn_players(level, spawn_root, player_scene, spawn_points(level))
	if not pending_state.is_empty():
		var state: Dictionary = pending_state
		pending_state = {}
		apply_state(state)
	GameEvents.level_started.emit(level)


## Nodes in group `player_spawn` inside `level`, in tree order.
func spawn_points(level: Node) -> Array[Node]:
	var points: Array[Node] = []
	for node: Node in get_tree().get_nodes_in_group(&"player_spawn"):
		if level.is_ancestor_of(node):
			points.append(node)
	return points


func set_session(session: GameSession) -> void:
	if session == null:
		session = GameSession.new()
	if session != _session:
		_session.end()
	_session = session


func session() -> GameSession:
	return _session


func spawn(scene: PackedScene, parent: Node, data: Dictionary = {}) -> Node:
	return _session.spawn(scene, parent, data)


## World interactions go through here so an online session can route them to
## the server. The target then emits `GameEvents.world_changed(self)`.
func request_action(target: Node, action: StringName, args: Array = []) -> void:
	var previous: int = _actor
	_actor = multiplayer.get_unique_id()
	_session.request_action(target, action, args)
	_actor = previous


## Runs an action on behalf of `peer_id`. Sessions call this on the authority.
func perform_action(peer_id: int, target: Node, action: StringName, args: Array = []) -> void:
	if not is_instance_valid(target) or not target.has_method(action):
		push_warning("Action %s is not available on %s." % [action, target])
		return
	var previous: int = _actor
	_actor = peer_id
	target.callv(action, args)
	_actor = previous


## The peer that issued the action being run (1 offline).
func actor_id() -> int:
	return _actor


func players() -> Array[Node]:
	var found: Array[Node] = []
	if not is_instance_valid(_level):
		return found
	var spawn_root: Node = _level.get_node_or_null(^"Players")
	if spawn_root == null:
		return found
	for child: Node in spawn_root.get_children():
		if child is MultiplayerSpawner or child is MultiplayerSynchronizer:
			continue
		if child.is_queued_for_deletion():
			continue
		found.append(child)
	return found


## The player this game instance controls, or null.
func local_player() -> Node:
	var me: int = multiplayer.get_unique_id()
	for player: Node in players():
		if player.get_multiplayer_authority() == me:
			return player
	return null


func peer_ids() -> PackedInt32Array:
	var ids: PackedInt32Array = PackedInt32Array()
	var peer: MultiplayerPeer = multiplayer.multiplayer_peer
	if (
		peer == null
		or peer is OfflineMultiplayerPeer
		or peer.get_connection_status() != MultiplayerPeer.CONNECTION_CONNECTED
	):
		ids.append(1)
		return ids
	ids.append(multiplayer.get_unique_id())
	ids.append_array(multiplayer.get_peers())
	ids.sort()
	return ids


func current_level() -> Node:
	if is_instance_valid(_level) and _level.is_inside_tree():
		return _level
	return null


## Seconds since the current level started, paused time excluded.
func level_time() -> float:
	return _level_time


## Seconds played since `start_game`, paused time excluded.
func play_time() -> float:
	return _play_time


## `{save_id: save_state()}` for every node in group `saveable`.
func capture_state() -> Dictionary:
	var state: Dictionary = {}
	for node: Node in get_tree().get_nodes_in_group(&"saveable"):
		if not node.has_method(&"save_state"):
			continue
		var id: String = _save_id(node)
		if id.is_empty() or state.has(id):
			push_warning("Saveable node %s needs a unique save_id." % node.get_path())
			continue
		state[id] = node.call(&"save_state")
	return state


## Calls `load_state` on each saveable node whose save_id is in `nodes`.
func apply_state(nodes: Dictionary) -> void:
	for node: Node in get_tree().get_nodes_in_group(&"saveable"):
		var id: String = _save_id(node)
		if not id.is_empty() and nodes.has(id) and node.has_method(&"load_state"):
			node.call(&"load_state", nodes[id])


func _save_id(node: Node) -> String:
	if not ("save_id" in node):
		return ""
	return str(node.get(&"save_id"))
