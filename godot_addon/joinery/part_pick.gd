@tool
extends RefCounted
## Picks a part of an imported model under a ray, with no physics bodies: ray → nearest
## MeshInstance3D → face → surface → {object, material, maps}.
##
## Names are the owning tool's, never Godot's: Godot renames nodes (Head.001 arrives as
## Head_001), so each object and material is mapped back through extras.joinery_name, which
## Joinery writes at export, falling back to the node or resource name for older exports.
## Surfaces map to materials by name, never by slot index (a slot with no faces has no
## surface). Maps come from the material's extras.joinery_maps, which also lists maps glTF
## cannot carry, such as height.

## Mesh instance id → TriangleMesh, so repeated picks on one model do not rebuild it.
static var _triangles: Dictionary = {}


## The TriangleMesh of [param mesh], built once.
static func triangle_mesh(mesh: Mesh) -> TriangleMesh:
	var key := mesh.get_instance_id()
	var cached: TriangleMesh = _triangles.get(key)
	if cached == null:
		cached = mesh.generate_triangle_mesh()
		_triangles[key] = cached
	return cached


static func clear_cache() -> void:
	_triangles.clear()


## The surface holding triangle [param face] of [param mesh], or -1.
static func surface_of(mesh: Mesh, face: int) -> int:
	var count := 0
	for surface: int in mesh.get_surface_count():
		var arrays := mesh.surface_get_arrays(surface)
		var index: Variant = arrays[Mesh.ARRAY_INDEX]
		var triangles := 0
		if index != null and (index as PackedInt32Array).size() > 0:
			triangles = (index as PackedInt32Array).size() / 3
		else:
			triangles = (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3
		if face < count + triangles:
			return surface
		count += triangles
	return -1


## The Blender name Joinery recorded on [param holder], or [param fallback].
static func source_name(holder: Object, fallback: String) -> String:
	var extras: Variant = holder.get_meta("extras", null) if holder != null else null
	if extras is Dictionary and str(extras.get("joinery_name", "")) != "":
		return str(extras["joinery_name"])
	return fallback


## role → image name for a material, from extras.joinery_maps.
static func material_maps(material: Material) -> Dictionary:
	var extras: Variant = material.get_meta("extras", null) if material != null else null
	if not extras is Dictionary:
		return {}
	var maps: Variant = extras.get("joinery_maps", {})
	if maps is String:
		maps = JSON.parse_string(maps)
	return maps if maps is Dictionary else {}


## [param node]'s transform relative to the world, also for a model not in the scene tree.
static func world_transform(node: Node3D) -> Transform3D:
	if node.is_inside_tree():
		return node.global_transform
	var result := node.transform
	var parent := node.get_parent()
	while parent != null:
		if parent is Node3D:
			result = (parent as Node3D).transform * result
		parent = parent.get_parent()
	return result


## The nearest part hit by the ray [param from] + t·[param direction] (world space) among the
## meshes under [param root], or {} for a miss. The result: object, material, maps (role →
## image), surface, face, position, distance and the hit MeshInstance3D as "node".
static func pick(root: Node, from: Vector3, direction: Vector3) -> Dictionary:
	var best := {}
	var nearest := INF
	var meshes: Array[Node] = root.find_children("*", "MeshInstance3D", true, false)
	if root is MeshInstance3D:
		meshes.append(root)
	for node: Node in meshes:
		var instance := node as MeshInstance3D
		if instance.mesh == null or not instance.is_visible_in_tree() and instance.is_inside_tree():
			continue
		var to_world := world_transform(instance)
		var to_local := to_world.affine_inverse()
		var triangles := triangle_mesh(instance.mesh)
		if triangles == null:
			continue
		var hit: Dictionary = triangles.intersect_ray(
			to_local * from, (to_local.basis * direction).normalized()
		)
		if hit.is_empty():
			continue
		var position: Vector3 = to_world * (hit["position"] as Vector3)
		var distance := from.distance_to(position)
		if distance >= nearest:
			continue
		var surface := surface_of(instance.mesh, int(hit["face_index"]))
		if surface < 0:
			continue
		nearest = distance
		var material := instance.get_active_material(surface)
		var fallback := ""
		if instance.mesh is ArrayMesh:
			fallback = (instance.mesh as ArrayMesh).surface_get_name(surface)
		if material != null and not material.resource_name.is_empty():
			fallback = material.resource_name
		best = {
			"object": source_name(instance, str(instance.name)),
			"material": source_name(material, fallback),
			"maps": material_maps(material),
			"surface": surface,
			"face": int(hit["face_index"]),
			"position": position,
			"distance": distance,
			"node": instance,
		}
	return best


## A part id: "res://art/models/hammer.glb#material=Steel&image=steel_rough".
static func part_id(resource: String, fields: Dictionary) -> String:
	var query := PackedStringArray()
	for key: String in ["object", "material", "image", "bone", "shape_key"]:
		var value := str(fields.get(key, ""))
		if not value.is_empty():
			query.append(key + "=" + value.uri_encode())
	return resource + ("#" + "&".join(query) if not query.is_empty() else "")


## The part target for a viewport [param hit] on [param resource]: the material on the picked
## object, or with [param role] one of its maps ("roughness").
static func part_for(hit: Dictionary, resource: String, role := "") -> Dictionary:
	var maps: Dictionary = hit.get("maps", {})
	var part := {
		"part_kind": "material",
		"resource": resource,
		"object": str(hit.get("object", "")),
		"material": str(hit.get("material", "")),
		"image": "",
		"role": "",
		"maps": ",".join(PackedStringArray(maps.values())),
	}
	if not role.is_empty() and maps.has(role):
		part["part_kind"] = "texture"
		part["image"] = str(maps[role])
		part["role"] = role
		var roles := PackedStringArray([role])
		for key: String in maps:
			if key != role and str(maps[key]) == part["image"]:
				roles.append(key)
		part["roles"] = ",".join(roles)
	part["id"] = part_id(
		resource,
		{"material": part["material"], "image": part["image"]},
	)
	return part


## The part an Alt+click in the editor's 3D view picks on the selected model, or {}. Stock
## Godot: the model's instance root (a GLB scene) must be the one selected node.
static func editor_pick(camera: Camera3D, event: InputEvent) -> Dictionary:
	var click := event as InputEventMouseButton
	if (
		click == null
		or not click.pressed
		or not click.alt_pressed
		or click.button_index != MOUSE_BUTTON_LEFT
	):
		return {}
	var nodes := EditorInterface.get_selection().get_selected_nodes()
	if nodes.size() != 1 or not nodes[0] is Node3D:
		return {}
	var model := nodes[0] as Node3D
	if not model.scene_file_path.get_extension().to_lower() in ["glb", "gltf"]:
		return {}
	var hit := pick(
		model, camera.project_ray_origin(click.position), camera.project_ray_normal(click.position)
	)
	if hit.is_empty():
		return {}
	var part := part_for(hit, model.scene_file_path)
	part["owner"] = "blender"
	return part
