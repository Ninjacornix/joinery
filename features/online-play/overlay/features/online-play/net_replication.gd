extends Node
## Keeps one level in step on every peer of an online game.
##
## - The server decides which level runs. When its level starts it tells
##   every client to load it (`_enter_level`); a client that has loaded it
##   says so (`_level_loaded`) and only then receives players, spawned nodes
##   and synchronised properties, so nothing arrives for a node it lacks.
## - Players are spawned by the server through a MultiplayerSpawner under the
##   level's `Players` node, named after their peer id and owned by that peer.
##   A MultiplayerSynchronizer built from the player's `SYNC_PROPERTIES`
##   carries their movement to everyone else.
## - Saveable world objects change only on the server (`Game.request_action`
##   from a client becomes an RPC); the server sends the changed node's
##   `save_state()` to every player, and a whole snapshot to a player who
##   joins late.
## - Nodes `Game.spawn` adds under the parents listed in
##   `online/spawn_parents` are network-spawned; nodes in group `net_synced`
##   get a synchroniser from their `SYNC_PROPERTIES`.

const PLAYER_SPAWNER := "NetPlayerSpawner"
const SPAWNER := "NetSpawner"
const SYNC := "NetSync"
const GATE := "NetGate"
const SERVER := 1

## Increases each time the server starts a level.
var serial: int = 0
## Peers whose copy of the current level is loaded: {peer_id: true}. The
## server owns it and sends it to every client.
var level_peers: Dictionary = {}
var level: Node = null

var _player_scene: PackedScene = null
var _spawn_root: Node = null
var _spawn_points: Array[Node] = []
var _player_spawner: MultiplayerSpawner = null
var _spawners: Dictionary = {}
var _next_spawn_index: int = 0
var _expected_serial: int = -1
var _setup_id: int = 0
var _filter: Callable


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_filter = _visible_to
	GameEvents.level_started.connect(_on_level_started)
	GameEvents.world_changed.connect(_on_world_changed)


## True while an online session runs.
func active() -> bool:
	return bool(get_parent().call(&"is_online")) and Game.session() is NetSession


## Forgets the level; called when the session ends.
func reset() -> void:
	serial = 0
	level_peers.clear()
	level = null
	_player_scene = null
	_spawn_root = null
	_spawn_points.clear()
	_player_spawner = null
	_spawners.clear()
	_next_spawn_index = 0
	_expected_serial = -1
	_setup_id = 0


## Called by `NetSession.spawn_players` from `Game.level_ready`, on every peer,
## before a pending load is applied, so the server's own player exists then.
func prepare_players(
	new_level: Node, spawn_root: Node, player_scene: PackedScene, spawns: Array[Node]
) -> void:
	_spawn_root = spawn_root
	_player_scene = player_scene
	_spawn_points = spawns
	_setup(new_level)


## Peers that have the current level loaded, sorted.
func ready_peers() -> PackedInt32Array:
	var ids: PackedInt32Array = PackedInt32Array()
	for id: Variant in level_peers:
		ids.append(int(id))
	ids.sort()
	return ids


## The server spawns `scene` under `parent` for everyone; see
## `online/spawn_parents`. A client cannot spawn and gets null.
func spawn(scene: PackedScene, parent: Node, data: Dictionary) -> Node:
	if not multiplayer.is_server():
		print_verbose("Online: only the server spawns %s." % scene.resource_path)
		return null
	var spawner: MultiplayerSpawner = _spawner_for(parent)
	if spawner == null:
		push_warning(
			(
				"Online: %s is not listed in online/spawn_parents, so %s appears only on the server."
				% [level.get_path_to(parent) if level != null else parent.get_path(), scene.resource_path]
			)
		)
		return GameSession.new().spawn(scene, parent, data)
	return spawner.spawn({"scene": scene.resource_path, "data": _plain(data)})


## Runs `action` on `target` on the server, from any peer.
func request(target: Node, action: StringName, args: Array) -> void:
	if multiplayer.is_server():
		if is_instance_valid(target) and target.has_method(action):
			target.callv(action, args)
		else:
			push_warning("Action %s is not available on %s." % [action, target])
		return
	if level == null or not is_instance_valid(target) or not _in_level(target):
		push_warning("Online: %s is not part of the level, so %s cannot run." % [target, action])
		return
	_request_action.rpc_id(SERVER, str(level.get_path_to(target)), action, _plain(args))


## Server: a peer finished authenticating; pull it into the running level.
func peer_joined(id: int) -> void:
	if multiplayer.is_server() and level != null and serial > 0:
		_enter_level.rpc_id(id, serial)


## Server: a peer left; drop its player (the spawner despawns it everywhere).
func peer_left(id: int) -> void:
	if not multiplayer.is_server():
		level_peers.erase(id)
		return
	level_peers.erase(id)
	_send_level_peers()
	if _spawn_root != null and is_instance_valid(_spawn_root):
		var player: Node = _spawn_root.get_node_or_null(NodePath(str(id)))
		if player != null:
			player.queue_free()


## A client asks the server for a fresh level for everyone.
func request_restart() -> void:
	if multiplayer.is_server():
		Game.restart_level()
	else:
		_request_restart.rpc_id(SERVER)


# --- Level start -----------------------------------------------------------------


func _on_level_started(new_level: Node) -> void:
	if new_level.get_instance_id() != _setup_id:
		_player_scene = null
		_spawn_root = new_level.get_node_or_null(^"Players")
		_spawn_points.clear()
	_setup(new_level)


## Builds spawners and synchronisers for `new_level` (once per level), then
## the server announces the level and a client reports that it has it.
func _setup(new_level: Node) -> void:
	if not active() or new_level.get_instance_id() == _setup_id:
		return
	_setup_id = new_level.get_instance_id()
	level = new_level
	_spawners.clear()
	_player_spawner = null
	_build_spawners()
	_sync_level_nodes()
	get_parent().call(&"level_setup", level)
	if multiplayer.is_server():
		serial += 1
		level_peers.clear()
		_next_spawn_index = 0
		if not bool(get_parent().get(&"dedicated")):
			level_peers[SERVER] = true
			_spawn_player(SERVER)
		_send_level_peers()
		_enter_level.rpc(serial)
	elif _expected_serial > 0:
		var loaded: int = _expected_serial
		_expected_serial = -1
		_level_loaded.rpc_id(SERVER, loaded)
	else:
		# The level reloaded here without the server asking (a Retry button
		# calling Game.restart_level). Ask the server to bring us back in.
		_request_enter.rpc_id(SERVER)


func _build_spawners() -> void:
	if _spawn_root == null or not is_instance_valid(_spawn_root):
		_spawn_root = level.get_node_or_null(^"Players")
	if _spawn_root != null and _player_scene != null:
		_player_spawner = _make_spawner(_spawn_root, PLAYER_SPAWNER, _spawn_player_node)
	for raw: String in _setting_list("online/spawn_parents"):
		var parent: Node = level.get_node_or_null(NodePath(raw))
		if parent == null:
			push_warning("Online: spawn parent %s is not in the level." % raw)
			continue
		_spawners[str(level.get_path_to(parent))] = _make_spawner(parent, SPAWNER, _spawn_scene_node)


func _make_spawner(parent: Node, spawner_name: String, function: Callable) -> MultiplayerSpawner:
	var existing: Node = parent.get_node_or_null(NodePath(spawner_name))
	if existing is MultiplayerSpawner:
		return existing as MultiplayerSpawner
	var spawner: MultiplayerSpawner = MultiplayerSpawner.new()
	spawner.name = spawner_name
	spawner.spawn_function = function
	spawner.spawn_path = NodePath("..")
	parent.add_child(spawner)
	return spawner


func _sync_level_nodes() -> void:
	for node: Node in get_tree().get_nodes_in_group(&"net_synced"):
		if not level.is_ancestor_of(node) or node.has_node(NodePath(SYNC)):
			continue
		var config: SceneReplicationConfig = _config_for(node)
		if config == null:
			continue
		var sync: MultiplayerSynchronizer = _synchronizer(SYNC, config)
		sync.set_multiplayer_authority(node.get_multiplayer_authority())
		node.add_child(sync)


# --- Players and spawned nodes ---------------------------------------------------


func _spawn_player(peer: int) -> void:
	if _player_spawner == null or not multiplayer.is_server():
		return
	if _spawn_root.has_node(NodePath(str(peer))):
		return
	var index: int = _next_spawn_index
	_next_spawn_index += 1
	_player_spawner.spawn({"peer": peer, "index": index})


func _spawn_player_node(data: Variant) -> Node:
	var info: Dictionary = data if data is Dictionary else {}
	var peer: int = int(info.get("peer", 0))
	if _player_scene == null or peer <= 0:
		return null
	var player: Node = _player_scene.instantiate()
	player.name = str(peer)
	if "save_id" in player:
		player.set(&"save_id", "player" if peer == SERVER else "player_%d" % peer)
	_place(player, int(info.get("index", 0)))
	_attach_synchronizers(player, peer)
	player.ready.connect(_announce_player.bind(player, peer), CONNECT_ONE_SHOT)
	return player


func _announce_player(player: Node, peer: int) -> void:
	GameEvents.player_spawned.emit(player, peer)


func _spawn_scene_node(data: Variant) -> Node:
	var info: Dictionary = data if data is Dictionary else {}
	var path: String = str(info.get("scene", ""))
	if not path in _spawnable_scenes():
		push_warning("Online: %s is not in online/spawnable_scenes." % path)
		return null
	var scene: PackedScene = load(path) as PackedScene
	if scene == null:
		return null
	var node: Node = scene.instantiate()
	var values: Variant = info.get("data", {})
	if values is Dictionary:
		for key: Variant in values:
			if str(key) != "script" and str(key) in node:
				node.set(str(key), (values as Dictionary)[key])
	node.set_meta(&"net_spawned", true)
	_attach_synchronizers(node, SERVER)
	return node


## A synchroniser for `SYNC_PROPERTIES` owned by `owner_peer`, plus a gate
## owned by the server when nothing else the server owns decides who gets the
## node, so a node reaches a peer only once that peer has the level.
func _attach_synchronizers(node: Node, owner_peer: int) -> void:
	var config: SceneReplicationConfig = _config_for(node)
	var sync: MultiplayerSynchronizer = null
	if config != null:
		sync = _synchronizer(SYNC, config)
		node.add_child(sync)
	var gate: MultiplayerSynchronizer = null
	if sync == null or owner_peer != SERVER:
		gate = _synchronizer(GATE, SceneReplicationConfig.new())
		node.add_child(gate)
	node.set_multiplayer_authority(owner_peer, true)
	if gate != null:
		gate.set_multiplayer_authority(SERVER)


func _synchronizer(sync_name: String, config: SceneReplicationConfig) -> MultiplayerSynchronizer:
	var sync: MultiplayerSynchronizer = MultiplayerSynchronizer.new()
	sync.name = sync_name
	sync.root_path = NodePath("..")
	sync.replication_config = config
	sync.add_visibility_filter(_filter)
	return sync


func _config_for(node: Node) -> SceneReplicationConfig:
	var props: Array = _sync_properties(node)
	if props.is_empty():
		return null
	var config: SceneReplicationConfig = SceneReplicationConfig.new()
	for prop: Variant in props:
		var text: String = str(prop)
		var path: NodePath = NodePath(text if ":" in text else ":" + text)
		config.add_property(path)
		config.property_set_spawn(path, true)
		config.property_set_replication_mode(path, SceneReplicationConfig.REPLICATION_MODE_ALWAYS)
	return config


static func _sync_properties(node: Node) -> Array:
	var script: Script = node.get_script() as Script
	while script != null:
		var constants: Dictionary = script.get_script_constant_map()
		if constants.has("SYNC_PROPERTIES"):
			var value: Variant = constants["SYNC_PROPERTIES"]
			return value if value is Array else []
		script = script.get_base_script()
	return []


func _place(node: Node, index: int) -> void:
	if _spawn_points.is_empty():
		return
	var marker: Node = _spawn_points[index % _spawn_points.size()]
	var round_number: int = floori(float(index) / _spawn_points.size())
	if node is Node2D and marker is Node2D:
		var at: Vector2 = (marker as Node2D).global_position + Vector2(32.0 * round_number, 0.0)
		if _spawn_root is Node2D:
			at = (_spawn_root as Node2D).to_local(at)
		(node as Node2D).position = at
	elif node is Node3D and marker is Node3D:
		var at3: Transform3D = (marker as Node3D).global_transform
		at3.origin += Vector3(1.5 * round_number, 0.0, 0.0)
		if _spawn_root is Node3D:
			at3 = (_spawn_root as Node3D).global_transform.affine_inverse() * at3
		(node as Node3D).transform = at3


func _spawner_for(parent: Node) -> MultiplayerSpawner:
	if level == null or not is_instance_valid(parent):
		return null
	var key: String = str(level.get_path_to(parent))
	var spawner: Variant = _spawners.get(key)
	if spawner is MultiplayerSpawner and is_instance_valid(spawner):
		return spawner as MultiplayerSpawner
	return null


# --- World state ------------------------------------------------------------------


func _on_world_changed(node: Node) -> void:
	if not active() or not multiplayer.is_server() or level == null:
		return
	if not is_instance_valid(node) or not _in_level(node) or _is_player(node):
		return
	if not node.has_method(&"save_state"):
		return
	var path: String = str(level.get_path_to(node))
	var state: Variant = _plain(node.call(&"save_state"))
	for peer: int in ready_peers():
		if peer != SERVER:
			_world_state.rpc_id(peer, path, state)


func _snapshot() -> Dictionary:
	var states: Dictionary = {}
	var excluded: PackedStringArray = _setting_list("online/snapshot_exclude")
	for node: Node in get_tree().get_nodes_in_group(&"saveable"):
		if not _in_level(node) or _is_player(node) or node.has_meta(&"net_spawned"):
			continue
		if not node.has_method(&"save_state"):
			continue
		var path: String = str(level.get_path_to(node))
		var save_id: String = str(node.get(&"save_id")) if "save_id" in node else ""
		if path in excluded or (not save_id.is_empty() and save_id in excluded):
			continue
		states[path] = _plain(node.call(&"save_state"))
	var extra: Variant = get_parent().call(&"snapshot_extra")
	if extra is Dictionary:
		for path: Variant in extra:
			states[str(path)] = _plain((extra as Dictionary)[path])
	return states


func _apply_state(path: String, state: Variant) -> void:
	if level == null or not is_instance_valid(level) or not state is Dictionary:
		return
	var node: Node = level.get_node_or_null(NodePath(path))
	if node != null and node.has_method(&"load_state"):
		node.call(&"load_state", state)


# --- RPCs -------------------------------------------------------------------------


@rpc("authority", "call_remote", "reliable")
func _enter_level(level_serial: int) -> void:
	_expected_serial = level_serial
	if not Game.session() is NetSession:
		return
	Game.start_game(Game.options)


@rpc("any_peer", "call_remote", "reliable")
func _level_loaded(level_serial: int) -> void:
	var sender: int = multiplayer.get_remote_sender_id()
	if not multiplayer.is_server() or level == null or level_serial != serial:
		return
	level_peers[sender] = true
	_send_level_peers()
	_world_snapshot.rpc_id(sender, _snapshot())
	_spawn_player(sender)
	get_parent().call(&"level_peer_ready", sender)


@rpc("any_peer", "call_remote", "reliable")
func _request_enter() -> void:
	var sender: int = multiplayer.get_remote_sender_id()
	if not multiplayer.is_server():
		return
	if level_peers.has(sender):
		level_peers.erase(sender)
		_send_level_peers()
		var player: Node = (
			_spawn_root.get_node_or_null(NodePath(str(sender))) if _spawn_root != null else null
		)
		if player != null:
			player.queue_free()
	if level != null and serial > 0:
		_enter_level.rpc_id(sender, serial)


@rpc("any_peer", "call_remote", "reliable")
func _request_restart() -> void:
	if multiplayer.is_server() and level != null:
		Game.restart_level()


@rpc("any_peer", "call_remote", "reliable")
func _request_action(path: String, action: StringName, args: Array) -> void:
	var sender: int = multiplayer.get_remote_sender_id()
	if not multiplayer.is_server() or level == null or not level_peers.has(sender):
		return
	var target: Node = level.get_node_or_null(NodePath(path))
	if target == null or (_is_player(target) and target.get_multiplayer_authority() != sender):
		return
	Game.perform_action(sender, target, action, args)


@rpc("authority", "call_remote", "reliable")
func _set_level_peers(ids: PackedInt32Array) -> void:
	level_peers.clear()
	for id: int in ids:
		level_peers[id] = true


@rpc("authority", "call_remote", "reliable")
func _world_state(path: String, state: Variant) -> void:
	_apply_state(path, state)


@rpc("authority", "call_remote", "reliable")
func _world_snapshot(states: Dictionary) -> void:
	for path: Variant in states:
		_apply_state(str(path), states[path])


# --- Helpers ----------------------------------------------------------------------


func _send_level_peers() -> void:
	if multiplayer.is_server() and multiplayer.has_multiplayer_peer():
		_set_level_peers.rpc(ready_peers())


## Visibility filter of every synchroniser: a peer gets a node once it has
## the level. The server (1) always has it.
func _visible_to(peer: int) -> bool:
	return peer == SERVER or level_peers.has(peer)


func _in_level(node: Node) -> bool:
	return (
		level != null
		and is_instance_valid(level)
		and (node == level or level.is_ancestor_of(node))
	)


func _is_player(node: Node) -> bool:
	return _spawn_root != null and is_instance_valid(_spawn_root) and node.get_parent() == _spawn_root


## Scenes `Game.spawn` may network-spawn: `online/spawnable_scenes` plus the
## starting point's `game/hooks/spawnable_scenes` when the project has it.
static func _spawnable_scenes() -> PackedStringArray:
	var scenes: PackedStringArray = _setting_list("online/spawnable_scenes")
	for path: String in _setting_list("game/hooks/spawnable_scenes"):
		if not path in scenes:
			scenes.append(path)
	return scenes


static func _setting_list(key: String) -> PackedStringArray:
	var value: Variant = ProjectSettings.get_setting(key, PackedStringArray())
	if value is PackedStringArray:
		return value
	if value is Array:
		return PackedStringArray(value)
	return PackedStringArray()


## `value` without Objects (they cannot cross the network).
static func _plain(value: Variant) -> Variant:
	if value is Object:
		return null
	if value is Dictionary:
		var copy: Dictionary = {}
		for key: Variant in value:
			copy[key] = _plain((value as Dictionary)[key])
		return copy
	if value is Array:
		var items: Array = []
		for item: Variant in value:
			items.append(_plain(item))
		return items
	return value
