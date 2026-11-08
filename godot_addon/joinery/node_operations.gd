@tool
extends RefCounted
## Reusable native node CRUD and property transactions, independent of prompt wording.

var scene: Node
var allocated: Array[Node] = []
var reserved: Dictionary = {}


func begin(root: Node) -> void:
	discard_unattached()
	scene = root
	reserved.clear()


# gdlint: disable=max-returns
func plan(op: Dictionary, targets: Array) -> Dictionary:
	var action := str(op.get("operation", ""))
	var target := _target(str(op.get("target", "")), targets)
	var parent := _target(str(op.get("parent", "")), targets)
	if parent == null:
		if not str(op.get("parent", "")).is_empty():
			return _error("The parent no longer exists in this scene.")
		parent = scene
	if action not in ["create_node", "instantiate_scene"] and target == null:
		return _error("Select the target object before editing it.")
	if target != null and target != scene and target.owner != scene:
		return _error("Edit this imported object's owning source rather than a transient child.")
	if action in ["delete_node", "duplicate_node", "reparent_node"] and target == scene:
		return _error("This operation cannot remove or move the scene root.")
	if action == "reparent_node" and (parent == target or target.is_ancestor_of(parent)):
		return _error("A node cannot be moved inside itself or its descendants.")
	var entry := {"action": action, "node": target, "op": op, "scene": scene}
	if target != null:
		entry["parent_before"] = target.get_parent()
		entry["index_before"] = target.get_index()
		entry["owners_before"] = _owners(target)
		entry["name_before"] = str(target.name)
		if target is Node3D or target is Node2D:
			entry["transform_before"] = target.transform
	if action in ["create_node", "duplicate_node", "instantiate_scene"]:
		var node: Node
		if action == "create_node":
			var type := str(op.get("node_type", "Node3D"))
			if not ClassDB.class_exists(type) or not ClassDB.is_parent_class(type, "Node"):
				return _error("Use a built-in Godot node class.")
			if not ClassDB.can_instantiate(type):
				return _error("This abstract node class cannot be instantiated.")
			node = ClassDB.instantiate(type) as Node
		elif action == "instantiate_scene":
			var path := str(op.get("resource_path", ""))
			if not path.begins_with("res://") or not ResourceLoader.exists(path):
				return _error("The imported scene resource must exist in this project first.")
			var packed: PackedScene = load(path)
			if packed == null:
				return _error("This resource cannot be instantiated as a scene.")
			node = packed.instantiate()
		else:
			if str(op.get("parent", "")).is_empty():
				parent = target.get_parent()
			node = target.duplicate()
		allocated.append(node)
		entry["node"] = node
		entry["parent_after"] = parent
		var base := str(op.get("name", ""))
		if base.is_empty():
			base = str(target.name) + "Copy" if target != null else node.get_class()
		var name := base
		var suffix := 2
		while (
			parent.has_node(NodePath(name))
			or reserved.has(str(parent.get_instance_id()) + "/" + name)
		):
			name = base + str(suffix)
			suffix += 1
		reserved[str(parent.get_instance_id()) + "/" + name] = true
		node.name = name
		entry["name_after"] = name
	elif action == "reparent_node":
		if parent.has_node(NodePath(str(target.name))):
			return _error("The destination already has a child with this name.")
		entry["parent_after"] = parent
	elif action == "rename_node":
		var name := str(op.get("name", ""))
		if name.is_empty() or name.validate_node_name() != name:
			return _error("Invalid node name.")
		if target.get_parent() != null:
			var sibling := target.get_parent().get_node_or_null(NodePath(name))
			if sibling != null and sibling != target:
				return _error("A sibling already has that name.")
		entry["name_after"] = name
	elif action == "attach_script":
		var path := str(op.get("script_path", ""))
		if not path.begins_with("res://") or not path.ends_with(".gd"):
			return _error("Attach an existing validated project GDScript.")
		var script: Script = load(path)
		if script == null or script.reload() != OK:
			return _error("Script validation failed.")
		if not target.is_class(script.get_instance_base_type()):
			return _error("The script's base type does not match the node.")
		entry["script_before"] = target.get_script()
		entry["script_after"] = script
	elif action in ["connect_signal", "disconnect_signal"]:
		var receiver := _target(str(op.get("receiver", "")), targets)
		var signal_name := str(op.get("signal_name", ""))
		var method_name := str(op.get("method_name", ""))
		if (
			receiver == null
			or not receiver.has_method(method_name)
			or not target.has_signal(signal_name)
		):
			return _error("The signal or receiver method does not exist in this scene.")
		var callable := Callable(receiver, method_name)
		entry["callable"] = callable
		entry["receiver"] = receiver
		entry["signal"] = signal_name
		entry["connected_before"] = target.is_connected(signal_name, callable)
		entry["flags_before"] = CONNECT_PERSIST
		for connection: Dictionary in target.get_signal_connection_list(signal_name):
			if connection["callable"] == callable:
				entry["flags_before"] = connection["flags"]
	elif action not in ["delete_node", "set_node_properties"]:
		return _error("Unknown native node operation.")
	var node: Node = entry["node"]
	var before := {}
	var after := {}
	var properties: Dictionary = op.get("properties", {})
	for key: String in properties:
		var decoded := _property(node, key, properties[key])
		if not decoded["success"]:
			return decoded
		before[key] = node.get(key)
		after[key] = decoded["value"]
	entry["properties_before"] = before
	entry["properties_after"] = after
	return {"success": true, "plan": entry}


func validate_batch(plans: Array[Dictionary]) -> Dictionary:
	for entry: Dictionary in plans:
		if entry["action"] != "delete_node":
			continue
		var deleted: Node = entry["node"]
		for other: Dictionary in plans:
			if other == entry:
				continue
			var node: Node = other["node"]
			var parent: Node = other.get("parent_after")
			var receiver: Node = other.get("receiver")
			if node == deleted or deleted.is_ancestor_of(node):
				return _error("Do not edit or delete a node and its ancestor in the same batch.")
			if parent == deleted or (parent != null and deleted.is_ancestor_of(parent)):
				return _error("The destination parent is being deleted.")
			if receiver == deleted or (receiver != null and deleted.is_ancestor_of(receiver)):
				return _error("The signal receiver is being deleted.")
	return {"success": true}


func apply_group(plans: Array[Dictionary]) -> void:
	for entry: Dictionary in plans:
		var node: Node = entry["node"]
		var owner_scene: Node = entry["scene"]
		match str(entry["action"]):
			"attach_script":
				node.set_script(entry["script_after"])
			"connect_signal", "disconnect_signal":
				_set_connection(entry, entry["action"] == "connect_signal", CONNECT_PERSIST)
			"create_node", "duplicate_node", "instantiate_scene":
				var parent: Node = entry["parent_after"]
				parent.add_child(node, true)
				node.owner = owner_scene
				for child: Node in node.find_children("*", "", true, false):
					if child.owner == null:
						child.owner = owner_scene
			"delete_node":
				node.get_parent().remove_child(node)
			"rename_node":
				node.name = entry["name_after"]
			"reparent_node":
				node.reparent(entry["parent_after"], true)
				_restore_owners(entry["owners_before"])
				if entry.has("transform_after"):
					node.transform = entry["transform_after"]
				elif node is Node3D or node is Node2D:
					entry["transform_after"] = node.transform
		for key: String in entry["properties_after"]:
			node.set(key, entry["properties_after"][key])


func restore_group(plans: Array[Dictionary]) -> void:
	var deleted: Array[Dictionary] = []
	for index: int in range(plans.size() - 1, -1, -1):
		var entry := plans[index]
		var node: Variant = entry["node"]
		if not is_instance_valid(node):
			continue
		if entry["action"] == "delete_node":
			deleted.append(entry)
			continue
		for key: String in entry["properties_before"]:
			node.set(key, entry["properties_before"][key])
		match str(entry["action"]):
			"attach_script":
				node.set_script(entry["script_before"])
			"connect_signal", "disconnect_signal":
				_set_connection(entry, entry["connected_before"], int(entry["flags_before"]))
			"create_node", "duplicate_node", "instantiate_scene":
				if node.get_parent() != null:
					node.get_parent().remove_child(node)
			"rename_node":
				node.name = entry["name_before"]
			"reparent_node":
				node.reparent(entry["parent_before"], true)
				if entry.has("transform_before"):
					node.transform = entry["transform_before"]
				_restore_owners(entry["owners_before"])
				node.get_parent().move_child(node, int(entry["index_before"]))
	deleted.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool:
			return int(a["index_before"]) < int(b["index_before"])
	)
	for entry: Dictionary in deleted:
		var node: Node = entry["node"]
		var parent: Node = entry["parent_before"]
		if not is_instance_valid(parent):
			continue
		parent.add_child(node)
		parent.move_child(node, mini(int(entry["index_before"]), parent.get_child_count() - 1))
		_restore_owners(entry["owners_before"])


func register(undo: EditorUndoRedoManager, plans: Array[Dictionary]) -> void:
	undo.add_do_reference(self)
	undo.add_do_method(self, "apply_group", plans)
	undo.add_undo_method(self, "restore_group", plans)
	for entry: Dictionary in plans:
		if entry["action"] in ["create_node", "duplicate_node", "instantiate_scene"]:
			undo.add_do_reference(entry["node"])
		elif entry["action"] == "delete_node":
			undo.add_undo_reference(entry["node"])


func valid(plans: Array[Dictionary]) -> bool:
	for entry: Dictionary in plans:
		var node: Variant = entry["node"]
		if not is_instance_valid(node):
			return false
		if entry["action"] == "delete_node":
			if node.get_parent() != null:
				return false
		elif not node.is_inside_tree():
			return false
		elif node.get_parent() != entry.get("parent_after", entry.get("parent_before")):
			return false
		if str(node.name) != str(entry.get("name_after", entry.get("name_before", node.name))):
			return false
		for key: String in entry["properties_after"]:
			if node.get(key) != entry["properties_after"][key]:
				return false
		if entry["action"] == "attach_script" and node.get_script() != entry["script_after"]:
			return false
	return true


func _set_connection(entry: Dictionary, connected: bool, flags: int) -> void:
	var node: Node = entry["node"]
	var signal_name: String = entry["signal"]
	var callable: Callable = entry["callable"]
	if connected and not node.is_connected(signal_name, callable):
		node.connect(signal_name, callable, flags)
	elif not connected and node.is_connected(signal_name, callable):
		node.disconnect(signal_name, callable)


func discard_unattached() -> void:
	for node: Node in allocated:
		if is_instance_valid(node) and node.get_parent() == null:
			node.free()
	allocated.clear()


func forget_allocated() -> void:
	allocated.clear()


func _target(id: String, targets: Array) -> Node:
	if not targets.has(id) or not id.contains("::"):
		return null
	var parts := id.split("::", true, 1)
	if parts[0] != scene.scene_file_path or parts[1].begins_with("/"):
		return null
	var node := scene.get_node_or_null(NodePath(parts[1]))
	return node if node == scene or (node != null and scene.is_ancestor_of(node)) else null


func _owners(node: Node) -> Array[Dictionary]:
	var records: Array[Dictionary] = [{"node": node, "owner": node.owner}]
	for child: Node in node.find_children("*", "", true, false):
		records.append({"node": child, "owner": child.owner})
	return records


func _restore_owners(records: Array) -> void:
	for record: Dictionary in records:
		var node: Node = record["node"]
		if is_instance_valid(node):
			node.owner = record["owner"]


# gdlint: disable=max-returns
func _property(object: Object, key: String, encoded: Variant) -> Dictionary:
	if key in ["script", "owner", "name"]:
		return _error("Use the dedicated source or rename operation for this property.")
	for property: Dictionary in object.get_property_list():
		if str(property["name"]) != key:
			continue
		if not int(property["usage"]) & (PROPERTY_USAGE_STORAGE | PROPERTY_USAGE_EDITOR):
			return _error("This property cannot be saved in a scene.")
		var type := int(property["type"])
		var value: Variant = encoded
		if type == TYPE_VECTOR3 and encoded is Array and encoded.size() == 3:
			value = Vector3(float(encoded[0]), float(encoded[1]), float(encoded[2]))
		elif type == TYPE_VECTOR2 and encoded is Array and encoded.size() == 2:
			value = Vector2(float(encoded[0]), float(encoded[1]))
		elif type == TYPE_COLOR and encoded is Array and encoded.size() in [3, 4]:
			value = Color(
				float(encoded[0]),
				float(encoded[1]),
				float(encoded[2]),
				float(encoded[3]) if encoded.size() == 4 else 1.0
			)
		elif type == TYPE_FLOAT and (encoded is int or encoded is float):
			value = float(encoded)
		elif type == TYPE_INT and (encoded is int or encoded is float):
			value = int(encoded)
		elif type == TYPE_NODE_PATH and encoded is String:
			value = NodePath(encoded)
		elif type == TYPE_OBJECT:
			var resource := _resource(encoded)
			if not resource["success"]:
				return resource
			value = resource["value"]
			var hint := str(property.get("hint_string", ""))
			if value != null and not hint.is_empty():
				var matches := false
				for allowed: String in hint.split(","):
					matches = matches or value.is_class(allowed)
				if not matches:
					return _error("Resource type does not match the property.")
		if value != null and typeof(value) != type:
			return _error("Invalid value type for " + key)
		return {"success": true, "value": value}
	return _error("No saved property named " + key)


func _resource(encoded: Variant) -> Dictionary:
	if encoded == null:
		return {"success": true, "value": null}
	if not encoded is Dictionary:
		return _error("Provide a resource_type and properties object.")
	var resource: Resource
	if not str(encoded.get("resource_path", "")).is_empty():
		var path := str(encoded["resource_path"])
		if not path.begins_with("res://") or not ResourceLoader.exists(path):
			return _error("Use an existing project resource path.")
		resource = load(path)
		if resource == null or resource is Script:
			return _error("Scripts must go through source validation.")
		if not encoded.get("properties", {}).is_empty():
			resource = resource.duplicate(true)
	else:
		var type := str(encoded.get("resource_type", ""))
		if not ClassDB.class_exists(type) or not ClassDB.is_parent_class(type, "Resource"):
			return _error("Use a built-in resource type.")
		if ClassDB.is_parent_class(type, "Script"):
			return _error("Scripts must go through source validation.")
		if not ClassDB.can_instantiate(type):
			return _error("This abstract resource cannot be instantiated.")
		resource = ClassDB.instantiate(type) as Resource
	for key: String in encoded.get("properties", {}):
		var decoded := _property(resource, key, encoded["properties"][key])
		if not decoded["success"]:
			return decoded
		resource.set(key, decoded["value"])
	return {"success": true, "value": resource}


func _error(message: String) -> Dictionary:
	return {"success": false, "message": message}
