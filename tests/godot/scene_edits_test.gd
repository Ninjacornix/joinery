extends SceneTree

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var script := load("res://addons/joinery/scene_edits.gd")
	var edits: RefCounted = script.new()
	var scene := Node3D.new()
	scene.name = "TestScene"
	root.add_child(scene)
	var existing := Node3D.new()
	existing.name = "Existing"
	scene.add_child(existing)
	existing.owner = scene
	existing.position = Vector3(2, 3, 4)
	var before := existing.transform
	var context: Dictionary = edits.context(scene)
	var box := {
		"summary": "Create a one metre box",
		"operations": [{"operation": "create_box", "name": "Box", "size": [1, 1, 1]}]
	}
	var result: Dictionary = edits.preview(scene, box, context, [])
	_check(result["success"], "Preview must create the box")
	var mesh := scene.get_node("Box") as MeshInstance3D
	_check(mesh.mesh is BoxMesh and mesh.mesh.size == Vector3.ONE, "Box is exactly one metre")
	_check(mesh.owner == scene, "Native scene serialization must include the box")
	_check(mesh.position == Vector3(0, 0.5, 0), "Box rests on the ground")
	_check(not edits.preview(scene, box, context, [])["success"], "Only one preview at a time")
	edits.reject()
	_check(
		scene.get_child_count() == 1 and existing.transform == before, "Reject restores original"
	)
	var id := scene.scene_file_path + "::Existing"
	var transform := {
		"summary": "Move and resize selected node",
		"operations":
		[
			{
				"operation": "set_transform",
				"target": id,
				"relative": true,
				"position": [-1, 0, 0],
				"scale": [2, 1, 1]
			}
		]
	}
	_check(not edits.preview(scene, transform, context, [])["success"], "Require a selected target")
	result = edits.preview(scene, transform, context, [id])
	_check(result["success"], "Transform should preview")
	_check(existing.position == Vector3(1, 3, 4), "Move left by exactly one metre")
	_check(existing.scale == Vector3(2, 1, 1), "Scale the selected node")
	edits.reject()
	_check(existing.transform == before, "Reject restores the exact original transform")
	var rotate := {
		"summary": "Rotate selected node",
		"operations": [{"operation": "set_transform", "target": id, "rotation_degrees": [0, 90, 0]}]
	}
	_check(edits.preview(scene, rotate, context, [id])["success"], "Rotation should preview")
	_check(existing.rotation_degrees.is_equal_approx(Vector3(0, 90, 0)), "Rotation uses degrees")
	edits.reject()
	_check(existing.transform == before, "Reject restores the original rotated basis")
	var invalid := box.duplicate(true)
	invalid["operations"].append({"operation": "delete_everything"})
	_check(not edits.preview(scene, invalid, context, [])["success"], "Validate whole batch first")
	_check(scene.get_child_count() == 1, "Invalid batches must not partially apply")
	invalid = box.duplicate(true)
	invalid["operations"][0]["size"] = [1, -1, 1]
	_check(not edits.preview(scene, invalid, context, [])["success"], "Reject invalid dimensions")
	_check(
		not edits.preview(scene, box, {"scene_instance": "stale"}, [])["success"],
		"Reject stale scene context"
	)
	var generic := {
		"summary": "Create a general native mesh node",
		"operations":
		[
			{
				"operation": "create_node",
				"node_type": "MeshInstance3D",
				"name": "GenericBox",
				"properties":
				{
					"position": [1, 2, 3],
					"mesh": {"resource_type": "BoxMesh", "properties": {"size": [1, 1, 1]}}
				}
			}
		]
	}
	result = edits.preview(scene, generic, context, [])
	print("Generic creation result: ", result)
	_check(result["success"], "Generic creation uses native property/resource schemas")
	if not result["success"]:
		quit(1)
		return
	var generic_box := scene.get_node("GenericBox") as MeshInstance3D
	_check(generic_box.position == Vector3(1, 2, 3), "Generic vector property conversion")
	_check(generic_box.mesh.size == Vector3.ONE, "Generic native resource construction")
	edits.reject()
	_check(not scene.has_node("GenericBox"), "Generic creation rejection restores original")
	# Values Godot rounds or reports its own way must not read as a manual change.
	var camera := {
		"summary": "Create the doorway camera",
		"operations":
		[
			{
				"operation": "create_node",
				"node_type": "Camera3D",
				"name": "DoorCamera",
				"properties":
				{"position": [0.3, 1.6, 4.1], "rotation": [-0.2217, 0.5812, 0.0017], "fov": 61.3}
			}
		]
	}
	result = edits.preview(scene, camera, context, [])
	_check(result["success"], "A camera previews")
	_check(edits._generic.valid(edits._generic_plans), "A fresh camera preview is unchanged")
	(scene.get_node("DoorCamera") as Camera3D).fov = 40.0
	_check(
		not edits._generic.valid(edits._generic_plans),
		"An edit after the preview still counts as a change"
	)
	edits.reject()
	var order: Array[String] = []
	for name: String in ["A", "B", "C", "D"]:
		var child := Node3D.new()
		child.name = name
		scene.add_child(child)
		child.owner = scene
	var nested := Node3D.new()
	nested.name = "Nested"
	scene.get_node("B").add_child(nested)
	nested.owner = scene
	for child: Node in scene.get_children():
		order.append(str(child.name))
	var bid := scene.scene_file_path + "::B"
	var did := scene.scene_file_path + "::D"
	var remove := {
		"summary": "Remove two selected siblings",
		"operations":
		[{"operation": "delete_node", "target": did}, {"operation": "delete_node", "target": bid}]
	}
	result = edits.preview(scene, remove, context, [bid, did])
	_check(result["success"], "Generic deletion previews")
	_check(not scene.has_node("B") and not scene.has_node("D"), "Deleted nodes leave the live tree")
	edits.reject()
	var restored: Array[String] = []
	for child: Node in scene.get_children():
		restored.append(str(child.name))
	_check(restored == order, "Reject restores exact sibling ordering")
	_check(nested.owner == scene, "Reject restores descendant ownership")
	_check(scene.get_node("B/Nested") == nested, "Reject restores the original subtree identity")
	_rest_on(edits, scene, context)
	scene.queue_free()
	await process_frame
	print("scene edits: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## A model put on a table rests on its top, centred, beside it under the same parent.
func _rest_on(edits: RefCounted, scene: Node3D, context: Dictionary) -> void:
	var room := Node3D.new()
	room.name = "Room"
	scene.add_child(room)
	room.owner = scene
	room.position = Vector3(1, 0.25, 0)
	var table := Node3D.new()
	table.name = "Table"
	room.add_child(table)
	table.owner = scene
	table.position = Vector3(2, 0, -1)
	_box(scene, table, Vector3(1.2, 0.8, 0.6), Vector3(0, 0.4, 0))
	var bare := Node3D.new()
	bare.name = "Marker"
	room.add_child(bare)
	bare.owner = scene
	# The mug's mesh is centred on its origin: its bottom is below the origin.
	var mug := Node3D.new()
	mug.name = "Mug"
	_box(mug, mug, Vector3(0.1, 0.12, 0.1), Vector3.ZERO)
	var packed := PackedScene.new()
	_check(packed.pack(mug) == OK, "Pack the mug")
	mug.free()
	var path := "res://rest_on_mug.tscn"
	_check(ResourceSaver.save(packed, path) == OK, "Save the mug scene")
	var id := scene.scene_file_path + "::Room/Table"
	var edit := {
		"summary": "Put the mug on the table",
		"operations": [{"operation": "instantiate_scene", "resource_path": path, "on": id}]
	}
	var result: Dictionary = edits.preview(scene, edit, context, [id])
	_check(result["success"], "Put the mug on the table: " + str(result))
	var placed := room.get_node_or_null("Mug") as Node3D
	_check(placed != null, "The mug stands beside the table, under its parent")
	if placed != null:
		var box: AABB = placed.global_transform * edits._generic._bounds(placed)
		_check(absf(box.position.y - 1.05) < 0.001, "Mug bottom on the table top: %s" % box)
		_check(absf(box.get_center().x - 3.0) < 0.001, "Mug centred on x: %s" % box)
		_check(absf(box.get_center().z + 1.0) < 0.001, "Mug centred on z: %s" % box)
	edits.reject()
	_check(not room.has_node("Mug"), "Reject takes the mug away")
	edit["operations"][0]["at"] = [0.4, -0.2]
	edit["operations"][0]["as_child"] = true
	result = edits.preview(scene, edit, context, [id])
	_check(result["success"], "Put the mug on the table's corner: " + str(result))
	placed = table.get_node_or_null("Mug") as Node3D
	_check(placed != null, "as_child puts the mug under the table")
	if placed != null:
		var box: AABB = placed.global_transform * edits._generic._bounds(placed)
		_check(absf(box.position.y - 1.05) < 0.001, "Still on the top: %s" % box)
		_check(absf(box.get_center().x - 3.4) < 0.001, "at moves it along x: %s" % box)
		_check(absf(box.get_center().z + 1.2) < 0.001, "at moves it along z: %s" % box)
	edits.reject()
	var marker := scene.scene_file_path + "::Room/Marker"
	edit["operations"] = [{"operation": "instantiate_scene", "resource_path": path, "on": marker}]
	result = edits.preview(scene, edit, context, [marker])
	_check(not result["success"], "Nothing visible to rest on is refused")
	_check(str(result.get("message", "")).contains("nothing visible"), str(result))
	room.free()
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))


func _box(owner: Node, parent: Node, size: Vector3, at: Vector3) -> void:
	var mesh := MeshInstance3D.new()
	var shape := BoxMesh.new()
	shape.size = size
	mesh.mesh = shape
	mesh.position = at
	parent.add_child(mesh)
	mesh.owner = owner
