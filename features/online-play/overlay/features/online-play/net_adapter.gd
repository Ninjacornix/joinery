class_name NetAdapter
extends Node
## Base of a starting point's online adapter. A feature adapter ships
## `res://features/online-play/adapter.gd` (extends this class) for one
## starting point; `Net` adds it as `/root/Net/Adapter` on every peer, so an
## RPC declared in the subclass reaches the same node on every player.
##
## Hooks, all optional, called only during an online session:
## - `_setup()`: the level is ready on this peer (`level`, `server` are set).
## - `peer_ready(peer)`: server; `peer` has loaded the level and has the world.
## - `snapshot_extra()`: server; `{level-relative path: state}` added to the
##   snapshot a joining player receives, for nodes outside group `saveable`.
##
## Like `GameSession`, this class reaches the core autoloads through
## `GameSession.autoload`, because a test script can compile it before they
## exist. Subclasses are loaded later and may use the autoload names.

const SERVER := 1

## The level of the running online session on this peer, or null.
var level: Node = null
## True on the server (host or dedicated).
var server: bool = false


func _init() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS


## Called by `Net` when an online level is set up on this peer.
func level_setup(new_level: Node, is_server: bool) -> void:
	level = new_level
	server = is_server
	_setup()


## Override: the online level is ready on this peer.
func _setup() -> void:
	pass


## Override (server): `peer` has the level loaded and the world snapshot.
func peer_ready(_peer: int) -> void:
	pass


## Override (server): extra state for a joining player, by level-relative path.
func snapshot_extra() -> Dictionary:
	return {}


## True while this peer runs an online level.
func has_level() -> bool:
	if level == null or not is_instance_valid(level) or not level.is_inside_tree():
		return false
	var game: Node = GameSession.autoload(&"Game")
	return game.call(&"current_level") == level and bool(get_parent().call(&"is_online"))


## True on the server while it runs an online level.
func serving() -> bool:
	return has_level() and server and multiplayer.is_server()


## Players other than the server that have the current level loaded, sorted.
func clients() -> PackedInt32Array:
	var ids: PackedInt32Array = PackedInt32Array()
	var replication: Node = get_parent().get(&"replication") as Node
	if replication == null:
		return ids
	for id: int in replication.call(&"ready_peers"):
		if id != SERVER:
			ids.append(id)
	return ids


## Server: runs the RPC `method` with `args` on every client that has the level.
func send_to_clients(method: StringName, args: Array = []) -> void:
	if not multiplayer.is_server():
		return
	for peer: int in clients():
		send_to(peer, method, args)


## Server: runs the RPC `method` with `args` on one client.
func send_to(peer: int, method: StringName, args: Array = []) -> void:
	if not multiplayer.is_server() or peer == SERVER:
		return
	var call_args: Array = [peer, method]
	call_args.append_array(args)
	callv(&"rpc_id", call_args)


## True when `node` is part of the current online level.
func in_level(node: Node) -> bool:
	return has_level() and is_instance_valid(node) and (node == level or level.is_ancestor_of(node))
