class_name NetSession
extends GameSession
## An online game. The server spawns one player per peer and owns the world;
## each player's movement is owned by that player's peer. World interactions
## from a client travel to the server, run there, and the changed node's state
## goes back to everyone. The work is done by `Net`'s replication node; this
## class is what `Game` talks to.
##
## Like `GameSession` it reaches autoloads through `GameSession.autoload`,
## because a test script can compile it before they exist.

var _net: Node = null


func _init(net: Node = null) -> void:
	_net = net


func is_online() -> bool:
	return true


func can_pause() -> bool:
	return false


func is_authority() -> bool:
	return _get_net().multiplayer.is_server()


func spawn_players(
	level: Node, spawn_root: Node, player_scene: PackedScene, spawns: Array[Node]
) -> void:
	_replication().call(&"prepare_players", level, spawn_root, player_scene, spawns)


func spawn(scene: PackedScene, parent: Node, data: Dictionary) -> Node:
	return _replication().call(&"spawn", scene, parent, data) as Node


func request_action(target: Node, action: StringName, args: Array) -> void:
	_replication().call(&"request", target, action, args)


## Asks for a fresh level for everyone. Returns true when it was handled.
func restart_level() -> bool:
	_get_net().call(&"restart_level")
	return true


func end() -> void:
	var net: Node = _get_net()
	if net != null and bool(net.call(&"is_online")):
		net.call(&"leave")


func _get_net() -> Node:
	if _net == null or not is_instance_valid(_net):
		_net = GameSession.autoload(&"Net")
	return _net


func _replication() -> Node:
	return _get_net().get(&"replication") as Node
