@tool
extends RefCounted
## What Journeyman sees of the open scene with each request: up to 1000 nodes with their
## transforms, scripts and stored properties, and the instance id of each, so an edit can
## check its target is still the node the user selected.


## Add the node manifest of [param scene] and the identities of [param targets] to
## [param context], and return it.
static func describe(scene: Node, targets: Array, context: Dictionary) -> Dictionary:
	var identities := {}
	if scene != null:
		for target: Dictionary in targets:
			var id := str(target.get("id", ""))
			var parts := id.split("::", true, 1)
			if parts.size() == 2 and parts[0] == scene.scene_file_path:
				var node := scene.get_node_or_null(NodePath(parts[1]))
				if node != null:
					identities[id] = str(node.get_instance_id())
	var manifest: Array[Dictionary] = []
	var context_bytes := 0
	if scene != null:
		var queue: Array[Node] = [scene]
		var cursor := 0
		while cursor < queue.size() and manifest.size() < 1000:
			var node := queue[cursor]
			cursor += 1
			var id := scene.scene_file_path + "::" + str(scene.get_path_to(node))
			identities[id] = str(node.get_instance_id())
			var row := {"id": id, "name": str(node.name), "type": node.get_class()}
			if node is Node3D:
				row["position"] = [node.position.x, node.position.y, node.position.z]
				row["rotation_degrees"] = [
					node.rotation_degrees.x, node.rotation_degrees.y, node.rotation_degrees.z
				]
				row["scale"] = [node.scale.x, node.scale.y, node.scale.z]
			var script: Script = node.get_script()
			if script != null:
				row["script"] = script.resource_path
			var properties := {}
			for property: Dictionary in node.get_property_list():
				if not int(property["usage"]) & PROPERTY_USAGE_STORAGE:
					continue
				var key := str(property["name"])
				var value: Variant = node.get(key)
				if properties.size() >= 32:
					break
				if value is String and value.length() > 4096:
					continue
				if value is bool or value is int or value is float or value is String:
					properties[key] = value
				elif value is Resource and not value is Script:
					properties[key] = _resource_context(value)
			row["properties"] = properties
			var source_node: Node = node
			while source_node != null and source_node != scene:
				if not source_node.scene_file_path.is_empty():
					row["source"] = source_node.scene_file_path
					break
				source_node = source_node.get_parent()
			context_bytes += JSON.stringify(row).length()
			if context_bytes > 500000:
				break
			manifest.append(row)
			for child: Node in node.get_children():
				queue.append(child)
	context["nodes"] = JSON.stringify(manifest)
	context["node_manifest_count"] = str(manifest.size())
	context["selection_instances"] = JSON.stringify(identities)
	return context


static func _resource_context(resource: Resource, depth: int = 0) -> Dictionary:
	var result := {"resource_type": resource.get_class(), "resource_path": resource.resource_path}
	var properties := {}
	if depth < 2:
		for property: Dictionary in resource.get_property_list():
			if properties.size() >= 32:
				break
			if not int(property["usage"]) & PROPERTY_USAGE_STORAGE:
				continue
			var key := str(property["name"])
			var value: Variant = resource.get(key)
			if value is String and value.length() > 4096:
				continue
			if value is bool or value is int or value is float or value is String:
				properties[key] = value
			elif value is Vector3:
				properties[key] = [value.x, value.y, value.z]
			elif value is Vector2:
				properties[key] = [value.x, value.y]
			elif value is Color:
				properties[key] = [value.r, value.g, value.b, value.a]
			elif value is Resource and not value is Script:
				properties[key] = _resource_context(value, depth + 1)
	result["properties"] = properties
	return result
