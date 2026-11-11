extends SceneTree
## Loads a built blockout scene in the game and checks it: it instantiates, its pieces have
## collision, there is a floor under the spawn, and its markers are in their groups.
## Run: godot --headless --path <game> --script scene_check.gd -- --scene=res://…
## Prints `blockout_check: ok` and a `BLOCKOUT {…}` line with what it counted.

var _scene_path: String = ""


func _initialize() -> void:
	for arg: String in OS.get_cmdline_user_args():
		if arg.begins_with("--scene="):
			_scene_path = arg.get_slice("=", 1)
	_run.call_deferred()


func _run() -> void:
	var packed: PackedScene = load(_scene_path) as PackedScene
	if packed == null:
		print("blockout_check: FAILED to load ", _scene_path)
		quit(1)
		return
	var level: Node = packed.instantiate()
	root.add_child(level)
	for _i: int in 4:
		await physics_frame
	var solids: int = 0
	for node: Node in level.find_children("*", "", true, false):
		if node is CSGShape3D and (node as CSGShape3D).use_collision:
			solids += 1
		elif node is StaticBody2D and node.get_child_count() > 0:
			solids += 1
	var spawn: Node = level.get_node_or_null(^"Markers/Spawn")
	var floor_under: bool = false
	if spawn is Node3D:
		var at: Vector3 = (spawn as Node3D).global_position
		var space := (level as Node3D).get_world_3d().direct_space_state
		var ray := PhysicsRayQueryParameters3D.create(at + Vector3.UP, at + Vector3.DOWN * 3.0)
		floor_under = not space.intersect_ray(ray).is_empty()
	elif spawn is Node2D:
		var at2: Vector2 = (spawn as Node2D).global_position
		var space2 := (level as Node2D).get_world_2d().direct_space_state
		var ray2 := PhysicsRayQueryParameters2D.create(at2, at2 + Vector2.DOWN * 2000.0)
		floor_under = not space2.intersect_ray(ray2).is_empty() or level.get("mode") != "2d-side"
	var groups: Dictionary = {}
	for group: String in ["player_spawn", "goal", "coin", "pickup", "checkpoint", "hazard"]:
		groups[group] = get_nodes_in_group(group).size()
	var found := {"solids": solids, "floor_under_spawn": floor_under, "groups": groups}
	print("BLOCKOUT " + JSON.stringify(found))
	var ok: bool = solids > 0 and floor_under and int(groups["player_spawn"]) == 1
	print("blockout_check: ", "ok" if ok else "FAILED")
	quit(0 if ok else 1)
