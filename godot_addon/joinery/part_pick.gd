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
##
## A character's body is one mesh, so a pick on it also names the body region under the
## pointer: the hit face's vertices follow bones (the mesh's skin weights), and the mesh's
## extras.joinery_regions (written at export from the same rule as `joinery parts`) say
## which region each bone is in. Objects MPFB2 made as clothes or hair (extras.joinery_role)
## are picked whole. [method region_overlay] draws a region over the mesh, skinned like it.

## MPFB2 roles of objects worn on the body, not part of it (blender_regions.WORN_ROLES).
const WORN := ["clothes", "hair", "eyes", "eyebrows", "eyelashes", "teeth", "tongue", "proxymeshes"]

## Mesh instance id → TriangleMesh, so repeated picks on one model do not rebuild it.
static var _triangles: Dictionary = {}
## "<mesh id>:<surface>" → each vertex's heaviest bone index (-1 for none).
static var _dominant: Dictionary = {}


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
	_dominant.clear()


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


## The glTF extras Godot keeps on [param holder] as meta, or null.
static func _extras(holder: Object) -> Variant:
	return holder.get_meta("extras") if holder != null and holder.has_meta("extras") else null


## The Blender name Joinery recorded on [param holder], or [param fallback].
static func source_name(holder: Object, fallback: String) -> String:
	var extras: Variant = _extras(holder)
	if extras is Dictionary and str(extras.get("joinery_name", "")) != "":
		return str(extras["joinery_name"])
	return fallback


## role → image name for a material, from extras.joinery_maps.
static func material_maps(material: Material) -> Dictionary:
	var extras: Variant = _extras(material)
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
		if instance.name == &"JoineryRegion":
			continue
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
		_add_region(best, instance, surface, int(hit["face_index"]))
	return best


## The hit's bone, body region and the object's MPFB2 role, when the mesh has them.
static func _add_region(hit: Dictionary, instance: MeshInstance3D, surface: int, face: int) -> void:
	var role := extra(instance, "joinery_role")
	if not role.is_empty():
		hit["role"] = role
	var regions := node_regions(instance)
	if regions.is_empty() or role in WORN:
		return
	var bone := face_bone(instance, surface, face)
	if bone.is_empty():
		return
	hit["bone"] = bone
	for region: String in regions:
		if (regions[region] as PackedStringArray).has(bone):
			hit["region"] = region
			hit["bones"] = ",".join(regions[region])
			return


## One string extra of [param holder] ("" when absent).
static func extra(holder: Object, key: String) -> String:
	var extras: Variant = _extras(holder)
	return str(extras.get(key, "")) if extras is Dictionary else ""


## region → bone names (Godot's spelling) of a mesh, from extras.joinery_regions.
static func node_regions(instance: Node) -> Dictionary:
	var extras: Variant = _extras(instance)
	if not extras is Dictionary:
		return {}
	var regions: Variant = extras.get("joinery_regions", {})
	if regions is String:
		regions = JSON.parse_string(regions)
	if not regions is Dictionary:
		return {}
	var found := {}
	for region: Variant in regions:
		var bones := PackedStringArray()
		for bone: Variant in regions[region]:
			bones.append(bone_key(str(bone)))
		found[str(region)] = bones
	return found


## A bone name as Godot keeps it (its glTF import replaces ":" and "/").
static func bone_key(name: String) -> String:
	return name.replace(":", "_").replace("/", "_")


## The name of skin bind [param bind] of [param instance] ("" when unknown).
static func bind_bone_name(instance: MeshInstance3D, bind: int) -> String:
	var skin := instance.skin
	if skin == null or bind < 0 or bind >= skin.get_bind_count():
		return ""
	var name := str(skin.get_bind_name(bind))
	if name.is_empty():
		var skeleton := instance.get_node_or_null(instance.skeleton) as Skeleton3D
		var bone := skin.get_bind_bone(bind)
		if skeleton != null and bone >= 0 and bone < skeleton.get_bone_count():
			name = skeleton.get_bone_name(bone)
	return bone_key(name)


## Each vertex's heaviest bone (as a skin bind index) on one surface, or -1; built once.
static func dominant_binds(mesh: Mesh, surface: int) -> PackedInt32Array:
	var key := "%d:%d" % [mesh.get_instance_id(), surface]
	if _dominant.has(key):
		return _dominant[key]
	var arrays := mesh.surface_get_arrays(surface)
	var bones: Variant = arrays[Mesh.ARRAY_BONES]
	var weights: Variant = arrays[Mesh.ARRAY_WEIGHTS]
	var found := PackedInt32Array()
	var count := (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
	found.resize(count)
	found.fill(-1)
	if bones is PackedInt32Array and weights is PackedFloat32Array and count > 0:
		var per := (bones as PackedInt32Array).size() / count
		for vertex: int in count:
			var best := -1
			var heaviest := 0.0
			for slot: int in per:
				var weight: float = weights[vertex * per + slot]
				if weight > heaviest:
					heaviest = weight
					best = bones[vertex * per + slot]
			found[vertex] = best
	_dominant[key] = found
	return found


## The bone most of triangle [param face]'s vertices follow most, or "".
static func face_bone(instance: MeshInstance3D, surface: int, face: int) -> String:
	var mesh := instance.mesh
	var first := 0
	for earlier: int in surface:
		first += _triangle_count(mesh, earlier)
	var local := face - first
	var arrays := mesh.surface_get_arrays(surface)
	var index: Variant = arrays[Mesh.ARRAY_INDEX]
	var corners := PackedInt32Array()
	for corner: int in 3:
		if index is PackedInt32Array and (index as PackedInt32Array).size() > 0:
			corners.append(index[local * 3 + corner])
		else:
			corners.append(local * 3 + corner)
	var binds := dominant_binds(mesh, surface)
	var votes := {}
	for vertex: int in corners:
		if vertex < binds.size() and binds[vertex] >= 0:
			votes[binds[vertex]] = int(votes.get(binds[vertex], 0)) + 1
	var best := -1
	for bind: int in votes:
		if best < 0 or int(votes[bind]) > int(votes[best]):
			best = bind
	return bind_bone_name(instance, best)


static func _triangle_count(mesh: Mesh, surface: int) -> int:
	var arrays := mesh.surface_get_arrays(surface)
	var index: Variant = arrays[Mesh.ARRAY_INDEX]
	if index != null and (index as PackedInt32Array).size() > 0:
		return (index as PackedInt32Array).size() / 3
	return (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3


## The triangles of [param instance] whose vertices follow [param bones] most (two of
## three corners), as a mesh with the same vertices, bones and weights, or null when none
## do. Drawn as a child of the instance with its skin and skeleton, it moves with it.
static func region_overlay(instance: MeshInstance3D, bones: PackedStringArray) -> ArrayMesh:
	var mesh := instance.mesh
	if mesh == null:
		return null
	var wanted := {}
	var binds_count := instance.skin.get_bind_count() if instance.skin != null else 0
	for bind: int in binds_count:
		if bones.has(bind_bone_name(instance, bind)):
			wanted[bind] = true
	if wanted.is_empty():
		return null
	var overlay := ArrayMesh.new()
	for surface: int in mesh.get_surface_count():
		var arrays := mesh.surface_get_arrays(surface)
		var binds := dominant_binds(mesh, surface)
		var index: Variant = arrays[Mesh.ARRAY_INDEX]
		var corners: PackedInt32Array
		if index is PackedInt32Array and (index as PackedInt32Array).size() > 0:
			corners = index
		else:
			corners = PackedInt32Array(
				range((arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size())
			)
		var kept := PackedInt32Array()
		for triangle: int in corners.size() / 3:
			var inside := 0
			for corner: int in 3:
				var vertex := corners[triangle * 3 + corner]
				if vertex < binds.size() and wanted.has(binds[vertex]):
					inside += 1
			if inside >= 2:
				for corner: int in 3:
					kept.append(corners[triangle * 3 + corner])
		if kept.is_empty():
			continue
		var shape := []
		shape.resize(Mesh.ARRAY_MAX)
		for slot: int in [
			Mesh.ARRAY_VERTEX, Mesh.ARRAY_NORMAL, Mesh.ARRAY_BONES, Mesh.ARRAY_WEIGHTS
		]:
			shape[slot] = arrays[slot]
		shape[Mesh.ARRAY_INDEX] = kept
		var flags := 0
		if (
			(arrays[Mesh.ARRAY_BONES] as PackedInt32Array).size()
			== ((arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() * 8)
		):
			flags = Mesh.ARRAY_FLAG_USE_8_BONE_WEIGHTS
		overlay.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, shape, [], {}, flags)
	return overlay if overlay.get_surface_count() > 0 else null


## Draw [param bones]' region over every skinned mesh under [param root] (only the one
## named [param object] when given) with [param tint]; clears the last overlay first.
## Returns how many triangles are tinted.
static func show_region(
	root: Node, object: String, bones: PackedStringArray, tint: Material
) -> int:
	clear_region(root)
	var triangles := 0
	if root == null or bones.is_empty():
		return 0
	var meshes: Array[Node] = root.find_children("*", "MeshInstance3D", true, false)
	for node: Node in meshes:
		var instance := node as MeshInstance3D
		if instance.name == &"JoineryRegion" or instance.skin == null:
			continue
		if not object.is_empty() and source_name(instance, str(instance.name)) != object:
			continue
		var overlay := region_overlay(instance, bones)
		if overlay == null:
			continue
		var shown := MeshInstance3D.new()
		shown.name = &"JoineryRegion"
		shown.mesh = overlay
		shown.skin = instance.skin
		shown.skeleton = NodePath("../" + str(instance.skeleton))
		shown.material_override = tint
		shown.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		instance.add_child(shown)
		for surface: int in overlay.get_surface_count():
			triangles += overlay.surface_get_array_index_len(surface) / 3
	return triangles


## Remove the region overlays under [param root].
static func clear_region(root: Node) -> void:
	if root == null:
		return
	for node: Node in root.find_children("JoineryRegion", "MeshInstance3D", true, false):
		node.get_parent().remove_child(node)
		node.queue_free()


## A part id: "res://art/models/hammer.glb#material=Steel&image=steel_rough".
static func part_id(resource: String, fields: Dictionary) -> String:
	var query := PackedStringArray()
	for key: String in ["object", "material", "image", "bone", "shape_key", "region", "parameter"]:
		var value := str(fields.get(key, ""))
		if not value.is_empty():
			query.append(key + "=" + value.uri_encode())
	return resource + ("#" + "&".join(query) if not query.is_empty() else "")


## The part target for a viewport [param hit] on [param resource]: the body region under the
## pointer on a character, the whole object for clothes or hair, else the material on the
## picked object, or with [param role] one of its maps ("roughness").
static func part_for(hit: Dictionary, resource: String, role := "") -> Dictionary:
	if role.is_empty() and not str(hit.get("region", "")).is_empty():
		return {
			"part_kind": "region",
			"resource": resource,
			"object": str(hit.get("object", "")),
			"region": str(hit["region"]),
			"bones": str(hit.get("bones", "")),
			"object_role": str(hit.get("role", "")),
			"id": part_id(resource, {"object": hit.get("object", ""), "region": hit["region"]}),
		}
	if role.is_empty() and str(hit.get("role", "")) in WORN:
		return {
			"part_kind": "object",
			"resource": resource,
			"object": str(hit.get("object", "")),
			"object_role": str(hit["role"]),
			"id": part_id(resource, {"object": hit.get("object", "")}),
		}
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
