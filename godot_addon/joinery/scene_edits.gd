@tool
extends RefCounted
## Narrow live-scene transactions. Preview never saves or replaces the scene.

## An edit was accepted into the open scene ([param scene_path], "" for a scene not saved yet).
## It is on disk, and joins the Timeline, once the scene is saved.
signal accepted(scene_path: String, summary: String)

const NODE_OPERATIONS := preload("node_operations.gd")
const SNAPSHOT := preload("review_snapshot.gd")

var root: Node
var transaction_id := ""
var summary := ""
var changes: Array[Dictionary] = []
var operations: Array = []
var owns_preview_root := false
var _recorded := false
var _generic: RefCounted = NODE_OPERATIONS.new()
var _generic_plans: Array[Dictionary] = []


func context(scene: Node) -> Dictionary:
	return {
		"scene_instance": str(scene.get_instance_id()) if scene != null else "empty",
		"scene_path": scene.scene_file_path if scene != null else "",
	}


func pending() -> bool:
	return not transaction_id.is_empty()


# gdlint: disable=max-returns
## [param staged] maps a res:// path only the change's stage has to the stage's own loadable
## copy, so a model made in the same change can be placed (ADR 0055).
func preview(
	scene: Node, payload: Dictionary, expected: Dictionary, targets: Array, staged: Dictionary = {}
) -> Dictionary:
	if pending():
		return _error("Accept or reject the current preview first.")
	if scene == null:
		return _error("Open a 3D scene before editing it.")
	if context(scene) != expected:
		return _error("The open scene changed. Send the request again.")
	var ops: Variant = payload.get("operations")
	if not ops is Array or ops.is_empty() or ops.size() > 32:
		return _error("The scene edit has no valid operations.")
	_generic_plans.clear()
	_generic.begin(scene, staged)
	var planned: Array[Dictionary] = []
	var used: Array[Node] = []
	for value: Variant in ops:
		if not value is Dictionary:
			return _error("Invalid scene operation.")
		var op: Dictionary = value
		match str(op.get("operation", "")):
			"create_box":
				var size: Variant = _vector(op.get("size"))
				var position: Variant = _vector(op.get("position"))
				if size == null:
					return _error("Invalid box size.")
				if not _positive(size):
					return _error("Box dimensions must be positive, measured in metres.")
				if op.get("position") != null and position == null:
					return _error("Invalid box position.")
				var box_name := str(op.get("name", "Box"))
				if box_name.is_empty() or box_name.validate_node_name() != box_name:
					return _error("Invalid box name.")
				planned.append(
					{
						"kind": "create_box",
						"size": size,
						"name": box_name,
						"position": position if position != null else Vector3(0, size.y / 2.0, 0)
					}
				)
			"set_transform":
				var target := str(op.get("target", ""))
				if not targets.has(target) or not target.contains("::"):
					return _error("Select the object before requesting a transform edit.")
				var parts := target.split("::", true, 1)
				if parts[0] != scene.scene_file_path or parts[1].begins_with("/"):
					return _error("The selected object belongs to a different scene.")
				var node := scene.get_node_or_null(NodePath(parts[1])) as Node3D
				if node == null or (node != scene and not scene.is_ancestor_of(node)):
					return _error("The selected object no longer exists in this scene.")
				if used.has(node):
					return _error("Combine transform changes for an object into one operation.")
				used.append(node)
				var update := {"kind": "transform", "node": node, "op": op}
				var has_change := false
				for key: String in ["position", "rotation_degrees", "scale"]:
					if op.get(key) != null:
						var vector: Variant = _vector(op[key])
						if vector == null or (key == "scale" and not _positive(vector)):
							return _error("Invalid transform value.")
						has_change = true
				if not has_change:
					return _error("No transform change was supplied.")
				planned.append(update)
			_:
				var result: Dictionary = _generic.plan(op, targets)
				if not result["success"]:
					return _error(str(result["message"]))
				# A replacement removes the old node first, then places the new one.
				for removal: Dictionary in result.get("before", []):
					_generic_plans.append(removal)
				_generic_plans.append(result["plan"])
	var batch: Dictionary = _generic.validate_batch(_generic_plans)
	if not batch["success"]:
		return _error(str(batch["message"]))
	# The viewport before the edit, for the review's before/after.
	SNAPSHOT.take_before()
	root = scene
	operations = ops.duplicate(true)
	summary = str(payload.get("summary", "Scene edit")).left(160)
	transaction_id = Crypto.new().generate_random_bytes(16).hex_encode()
	for item: Dictionary in planned:
		if item["kind"] == "create_box":
			var node := MeshInstance3D.new()
			var mesh := BoxMesh.new()
			mesh.size = item["size"]
			node.mesh = mesh
			node.name = item["name"]
			scene.add_child(node, true)
			node.owner = scene
			node.position = item["position"]
			changes.append({"kind": "create", "node": node, "after": node.transform})
		else:
			var node: Node3D = item["node"]
			var before := node.transform
			var op: Dictionary = item["op"]
			var relative := bool(op.get("relative", false))
			if op.get("position") != null:
				var vector: Vector3 = _vector(op["position"])
				node.position = node.position + vector if relative else vector
			if op.get("rotation_degrees") != null:
				var vector: Vector3 = _vector(op["rotation_degrees"])
				node.rotation_degrees = node.rotation_degrees + vector if relative else vector
			if op.get("scale") != null:
				var vector: Vector3 = _vector(op["scale"])
				node.scale = node.scale * vector if relative else vector
			changes.append(
				{"kind": "transform", "node": node, "before": before, "after": node.transform}
			)
	_generic.apply_group(_generic_plans)
	for plan: Dictionary in _generic_plans:
		changes.append({"kind": "generic", "node": plan["node"], "plan": plan})
	SNAPSHOT.nodes.clear()
	for change: Dictionary in changes:
		if change["node"] is Node:
			SNAPSHOT.nodes.append(change["node"])
	return {
		"success": true,
		"status": "preview",
		"transaction_id": transaction_id,
		"message": summary + ". Preview is visible; the user must Accept or Reject."
	}


func prepare_accept() -> Dictionary:
	if not pending() or not is_instance_valid(root):
		return _error("This preview is no longer available.")
	if not _generic.valid(_generic_plans):
		return _error(
			"Part of this preview was changed in the editor after it was shown. Discard it and ask again."
		)
	for change: Dictionary in changes:
		if change["kind"] == "generic":
			continue
		var node: Node3D = change["node"]
		if (
			not is_instance_valid(node)
			or not node.is_inside_tree()
			or node.transform != change["after"]
		):
			return _error(
				"Part of this preview was changed in the editor after it was shown. Discard it and ask again."
			)
	var log_error := OK if _recorded else _write_record()
	if log_error != OK:
		return _error("Could not record this edit. The preview has not been accepted.")
	_recorded = true
	return {"success": true}


func accept(undo: EditorUndoRedoManager) -> Dictionary:
	var ready := prepare_accept()
	if not ready["success"]:
		return ready
	undo.create_action(summary, UndoRedo.MERGE_DISABLE, root)
	if not _generic_plans.is_empty():
		_generic.register(undo, _generic_plans.duplicate())
	for change: Dictionary in changes:
		if change["kind"] == "generic":
			continue
		var node: Node3D = change["node"]
		if change["kind"] == "create":
			undo.add_do_method(root, "add_child", node, true)
			undo.add_do_property(node, "owner", root)
			undo.add_do_property(node, "transform", change["after"])
			undo.add_undo_method(root, "remove_child", node)
			undo.add_do_reference(node)
		else:
			undo.add_do_property(node, "transform", change["after"])
			undo.add_undo_property(node, "transform", change["before"])
	undo.commit_action(false)
	# One wording for an accepted change everywhere; the summary is already the review title.
	var result := {"success": true, "status": "accepted", "message": "Change accepted"}
	var accepted_in := root.scene_file_path if is_instance_valid(root) else ""
	var accepted_summary := summary
	_clear()
	accepted.emit(accepted_in, accepted_summary)
	return result


func reject() -> void:
	_generic.restore_group(_generic_plans)
	_generic.discard_unattached()
	for index: int in range(changes.size() - 1, -1, -1):
		var change := changes[index]
		if change["kind"] == "generic":
			continue
		var node: Node3D = change["node"]
		if not is_instance_valid(node):
			continue
		if change["kind"] == "create":
			if node.get_parent() != null:
				node.get_parent().remove_child(node)
			node.free()
		else:
			node.transform = change["before"]
	if owns_preview_root and is_instance_valid(root):
		if root.get_parent() != null:
			root.get_parent().remove_child(root)
		root.free()
	_clear()


func _write_record() -> Error:
	var directory := "res://.joinery/transactions"
	var error := DirAccess.make_dir_recursive_absolute(directory)
	if error != OK:
		return error
	var file := FileAccess.open(directory.path_join(transaction_id + ".json"), FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	var states: Array[Dictionary] = []
	for change: Dictionary in changes:
		if change["kind"] == "generic":
			states.append({"kind": "native-node-operation", "operation": change["plan"]["op"]})
			continue
		states.append(
			{
				"kind": change["kind"],
				"node": str(root.get_path_to(change["node"])),
				"before": var_to_str(change.get("before", "absent")),
				"after": var_to_str(change["after"])
			}
		)
	file.store_string(
		JSON.stringify(
			{
				"version": 1,
				"origin": "tool-created",
				"tool": "godot",
				"scope": "scene-scaffold",
				"scene": root.scene_file_path,
				"summary": summary,
				"transaction_id": transaction_id,
				"operations": operations,
				"states": states
			},
			"\t"
		)
	)
	file.flush()
	return file.get_error()


func _clear() -> void:
	_generic.forget_allocated()
	_generic_plans.clear()
	_recorded = false
	transaction_id = ""
	changes.clear()
	SNAPSHOT.nodes.clear()
	operations.clear()
	root = null
	owns_preview_root = false


func _positive(value: Vector3) -> bool:
	return (
		value.x >= 0.001
		and value.y >= 0.001
		and value.z >= 0.001
		and value.x <= 10000
		and value.y <= 10000
		and value.z <= 10000
	)


func _vector(value: Variant) -> Variant:
	if not value is Array or value.size() != 3:
		return null
	for component: Variant in value:
		if not (component is float or component is int):
			return null
		if not is_finite(float(component)) or absf(float(component)) > 1000000.0:
			return null
	return Vector3(float(value[0]), float(value[1]), float(value[2]))


func _error(message: String) -> Dictionary:
	_generic.discard_unattached()
	if not pending():
		_generic_plans.clear()
	return {"success": false, "message": message}
