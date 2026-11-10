class_name GameSession
extends RefCounted
## How a game is played: who the players are, who may change the world, and
## whether pausing is allowed. This one is local and single-player. Online
## play swaps in its own session through `Game.set_session`.
##
## A `class_name` script can be compiled before the autoloads exist (a test's
## `--script` file that names this class does that), so it reaches them
## through `GameSession.autoload()` instead of their global names.


func is_online() -> bool:
	return false


func can_pause() -> bool:
	return true


func is_authority() -> bool:
	return true


## Spawns the local player (peer 1) at the first spawn marker.
func spawn_players(
	_level: Node, spawn_root: Node, player_scene: PackedScene, spawns: Array[Node]
) -> void:
	var player: Node = player_scene.instantiate()
	player.name = "1"
	player.set_multiplayer_authority(1)
	spawn_root.add_child(player, true)
	if not spawns.is_empty():
		GameSession.place_at(player, spawns[0])
	GameSession.autoload(&"GameEvents").emit_signal(&"player_spawned", player, 1)


## Instances `scene` under `parent`. Keys of `data` that are properties of
## the new node are set before it enters the tree.
func spawn(scene: PackedScene, parent: Node, data: Dictionary) -> Node:
	var node: Node = scene.instantiate()
	for key: Variant in data:
		if str(key) in node:
			node.set(str(key), data[key])
	parent.add_child(node, true)
	return node


## Runs a world interaction. Locally that is a direct call.
func request_action(target: Node, action: StringName, args: Array) -> void:
	if not is_instance_valid(target) or not target.has_method(action):
		push_warning("Action %s is not available on %s." % [action, target])
		return
	target.callv(action, args)


## Called when the game returns to the menu or another session takes over.
func end() -> void:
	pass


## A core autoload (`Settings`, `GameEvents`, `Menus`, `Game`) by name.
static func autoload(autoload_name: StringName) -> Node:
	var tree: SceneTree = Engine.get_main_loop() as SceneTree
	return tree.root.get_node(NodePath(String(autoload_name)))


## Moves a node that is already in the tree onto a marker (2D or 3D).
static func place_at(node: Node, marker: Node) -> void:
	if node is Node2D and marker is Node2D:
		(node as Node2D).global_position = (marker as Node2D).global_position
	elif node is Node3D and marker is Node3D:
		(node as Node3D).global_transform = (marker as Node3D).global_transform
