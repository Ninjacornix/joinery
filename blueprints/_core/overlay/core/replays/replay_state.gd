extends RefCounted
## The state a replay checks: one hash of the running scene, and a short readable
## summary to say what differs when two hashes do not match.
##
## The hash covers, in tree order under the current scene: every node's class and
## place, transforms of 2D and 3D nodes, body velocities, animation positions, timers,
## visibility and label text; plus the game core's saveable state, level and play time.
## Values are written at full precision, so the same build replaying the same input
## gives the same hash. Generated node names ("@Node3D@12") are written as their class,
## since their numbers depend on how many nodes were ever made.

## Readable parts listed per direct child of the scene, at most this many.
const MAX_PARTS := 48


## {hash, state} for the tree right now.
static func capture(tree: SceneTree) -> Dictionary:
	var scene: Node = tree.current_scene
	var parts: Dictionary = {}
	var all: PackedStringArray = PackedStringArray()
	var state: Dictionary = {
		"scene": scene.scene_file_path if scene != null else "",
		"paused": tree.paused,
	}
	if scene != null:
		var index: int = 0
		for child: Node in scene.get_children():
			var lines: PackedStringArray = PackedStringArray()
			_walk(child, _name(child), lines)
			var text: String = "\n".join(lines)
			all.append(text)
			if index < MAX_PARTS:
				parts[_name(child)] = text.sha256_text().left(12)
			index += 1
		state["nodes"] = _count(scene)
	var game: Node = tree.root.get_node_or_null(^"Game")
	if game != null:
		var saveable: String = var_to_str(game.call(&"capture_state"))
		all.append(saveable)
		parts["(saveable)"] = saveable.sha256_text().left(12)
		state["level_time"] = snappedf(float(game.call(&"level_time")), 0.001)
		var places: Array[String] = []
		for player: Node in game.call(&"players"):
			places.append(_place(player))
			all.append(var_to_str(player.get(&"global_position")))
		state["players"] = places
		all.append(var_to_str([game.call(&"level_time"), game.call(&"play_time")]))
	state["parts"] = parts
	return {"hash": "\n".join(all).sha256_text().left(16), "state": state}


## Keys of `recorded` and `replayed` states that differ, as "key: recorded -> replayed",
## the parts named one by one.
static func differences(recorded: Dictionary, replayed: Dictionary) -> Array[String]:
	var found: Array[String] = []
	for key: String in ["scene", "paused", "nodes", "level_time", "players"]:
		if recorded.has(key) or replayed.has(key):
			var before: Variant = recorded.get(key)
			var after: Variant = replayed.get(key)
			if str(before) != str(after):
				found.append("%s: %s -> %s" % [key, before, after])
	var parts_before: Dictionary = recorded.get("parts", {})
	var parts_after: Dictionary = replayed.get("parts", {})
	var names: Array = parts_before.keys()
	for name: Variant in parts_after.keys():
		if not names.has(name):
			names.append(name)
	for name: Variant in names:
		if not parts_after.has(name):
			found.append("%s: gone" % name)
		elif not parts_before.has(name):
			found.append("%s: new" % name)
		elif str(parts_before[name]) != str(parts_after[name]):
			found.append("%s: changed" % name)
	return found


static func _walk(node: Node, path: String, lines: PackedStringArray) -> void:
	var line: String = "%s %s" % [path, node.get_class()]
	if node is Node3D and (node as Node3D).is_inside_tree():
		line += " " + var_to_str((node as Node3D).global_transform)
	elif node is Node2D and (node as Node2D).is_inside_tree():
		line += " " + var_to_str((node as Node2D).global_transform)
	if node is CanvasItem:
		line += " v" if (node as CanvasItem).visible else " h"
	if node is Node3D:
		line += " v" if (node as Node3D).visible else " h"
	if node is CharacterBody3D:
		line += " " + var_to_str((node as CharacterBody3D).velocity)
	elif node is CharacterBody2D:
		line += " " + var_to_str((node as CharacterBody2D).velocity)
	elif node is RigidBody3D:
		var body: RigidBody3D = node
		line += " " + var_to_str([body.linear_velocity, body.angular_velocity])
	elif node is RigidBody2D:
		var flat: RigidBody2D = node
		line += " " + var_to_str([flat.linear_velocity, flat.angular_velocity])
	elif node is AnimationPlayer:
		var player: AnimationPlayer = node
		line += " %s %s" % [player.current_animation, var_to_str(_animation_position(player))]
	elif node is Timer:
		line += " " + var_to_str((node as Timer).time_left)
	elif node is Label:
		line += " " + (node as Label).text
	lines.append(line)
	for child: Node in node.get_children():
		_walk(child, path + "/" + _name(child), lines)


static func _animation_position(player: AnimationPlayer) -> float:
	return player.current_animation_position if player.is_playing() else 0.0


static func _name(node: Node) -> String:
	var text: String = String(node.name)
	return "@" + node.get_class() if text.begins_with("@") else text


static func _count(node: Node) -> int:
	var total: int = 1
	for child: Node in node.get_children():
		total += _count(child)
	return total


static func _place(player: Node) -> String:
	var at: Variant = player.get(&"global_position")
	if at is Vector3:
		var p: Vector3 = at
		return "(%.2f, %.2f, %.2f)" % [p.x, p.y, p.z]
	if at is Vector2:
		var q: Vector2 = at
		return "(%.2f, %.2f)" % [q.x, q.y]
	return "?"
