@tool
class_name Terrain
extends Node3D
## A heightmap terrain: chunked meshes with distance LODs, HeightMapShape3D
## collision, a splat material of up to four layers, and seeded scatter of the
## project's models. Our own MIT GDScript; see GUIDE.md.
##
## The terrain is centred on this node: x from -size.x/2 to size.x/2, z likewise,
## heights from min_height to max_height metres above it (the heightmap's 0-1).
## Everything it builds is an internal child, so the scene file keeps only these
## settings. The playtester reads playtest_state(): chunks, LODs in use, scatter
## counts, and how often a player stood on it or fell through it.

signal built

const MapScript := preload("res://features/terrain/terrain_map.gd")
const SHADER := preload("res://features/terrain/terrain.gdshader")
const STATE_GROUP := &"playtest_state"
const MAX_LAYERS := 4
## A player within this many metres above the surface is standing on it.
const ON_SURFACE := 0.6
## A player this far below the surface has fallen through it.
const THROUGH := 0.5

## The heightmap: a 16-bit grey PNG (keep its import as "Keep File"), an 8-bit
## PNG, or an EXR. Empty builds a flat terrain.
@export_file("*.png", "*.exr") var heightmap: String = "":
	set(value):
		heightmap = value
		_queue_build()
## The paint mask: r, g, b paint layers 1-3; a is free for scatter.
@export_file("*.png") var mask: String = "":
	set(value):
		mask = value
		_queue_build()
## Width and depth in metres.
@export var size: Vector2 = Vector2(64.0, 64.0):
	set(value):
		size = Vector2(maxf(value.x, 1.0), maxf(value.y, 1.0))
		_queue_build()
@export var min_height: float = 0.0:
	set(value):
		min_height = value
		_queue_build()
@export var max_height: float = 20.0:
	set(value):
		max_height = value
		_queue_build()
## Grid cells along each side of a chunk.
@export_range(4, 128, 4) var chunk_cells: int = 32:
	set(value):
		chunk_cells = value
		_queue_build()
## Detail levels: each halves the grid of the one before.
@export_range(1, 4) var lod_levels: int = 3:
	set(value):
		lod_levels = value
		_queue_build()
## Full detail up to this many metres from the camera; each further level
## doubles the distance.
@export var lod_distance: float = 48.0:
	set(value):
		lod_distance = maxf(value, 1.0)
		_queue_build()
## Chunks further than this are not drawn; 0 draws them at any distance.
@export var view_distance: float = 0.0:
	set(value):
		view_distance = maxf(value, 0.0)
		_queue_build()
@export var layers: Array[TerrainLayer] = []:
	set(value):
		layers = value
		_queue_build()
@export var scatter: Array[TerrainScatter] = []:
	set(value):
		scatter = value
		_queue_build()
@export var collision: bool = true:
	set(value):
		collision = value
		_queue_build()
@export_flags_3d_physics var collision_layer: int = 1:
	set(value):
		collision_layer = value
		_queue_build()
@export var cast_shadows: bool = true:
	set(value):
		cast_shadows = value
		_queue_build()

## Grid points along x and z, and the height of each (metres, row by row).
var map_width: int = 0
var map_depth: int = 0
var heights: PackedFloat32Array = PackedFloat32Array()
var mask_map: MapScript
## One per chunk: {"cells": Rect2i, "centre": Vector3, "meshes": [..], "triangles": [..]}.
var chunks: Array[Dictionary] = []
var material: ShaderMaterial
var body: StaticBody3D

var _root: Node3D
var _pending: bool = false
var _override: MapScript
var _override_mask: MapScript
var _scatter_counts: PackedInt32Array = PackedInt32Array()
var _ticks: int = 0
var _on_frames: int = 0
var _through_frames: int = 0
var _lowest_gap: float = INF


func _ready() -> void:
	add_to_group(STATE_GROUP)
	if chunks.is_empty():
		build()


func _queue_build() -> void:
	if not is_inside_tree() or _pending:
		return
	_pending = true
	_rebuild_later.call_deferred()


func _rebuild_later() -> void:
	_pending = false
	build()


## Builds from `heightmap` and `mask` (or what build_from() last gave).
func build() -> void:
	var source: MapScript = _override
	if source == null and not heightmap.is_empty():
		var file_map: MapScript = MapScript.load_file(heightmap, 1)
		if file_map != null:
			source = file_map.duplicate_map()
			for i: int in source.values.size():
				source.values[i] = lerpf(min_height, max_height, source.values[i])
	if source == null:
		source = MapScript.create(chunk_cells + 1, chunk_cells + 1, 1, min_height)
	var painted: MapScript = _override_mask
	if painted == null and not mask.is_empty():
		painted = MapScript.load_file(mask, 4)
	_build(source, painted)


## Builds from maps made in code: heights in metres above this node (any grid
## size), and an optional 4-channel mask. Later rebuilds keep using them.
func build_from(height_metres: MapScript, paint: MapScript = null) -> void:
	_override = height_metres
	_override_mask = paint
	build()


func _build(source: MapScript, painted: MapScript) -> void:
	_clear()
	map_width = maxi(source.width, 2)
	map_depth = maxi(source.height, 2)
	heights = PackedFloat32Array()
	heights.resize(map_width * map_depth)
	for i: int in map_width * map_depth:
		heights[i] = source.values[i * source.channels]
	mask_map = painted.resized(map_width, map_depth) if painted != null else null
	material = _make_material()
	_root = Node3D.new()
	_root.name = "Chunks"
	add_child(_root, false, Node.INTERNAL_MODE_FRONT)
	if collision:
		body = StaticBody3D.new()
		body.name = "Collision"
		body.collision_layer = collision_layer
		body.collision_mask = 0
		add_child(body, false, Node.INTERNAL_MODE_FRONT)
	var parts: Array = []
	for rule: TerrainScatter in scatter:
		parts.append(_scatter_parts(rule))
	_scatter_counts = PackedInt32Array()
	_scatter_counts.resize(scatter.size())
	var cells: int = maxi(chunk_cells, 4)
	var index: int = 0
	for z0: int in range(0, map_depth - 1, cells):
		for x0: int in range(0, map_width - 1, cells):
			var rect := Rect2i(
				x0, z0, mini(cells, map_width - 1 - x0), mini(cells, map_depth - 1 - z0)
			)
			_build_chunk(rect, index, parts)
			index += 1
	built.emit()


func _clear() -> void:
	for child: Node in get_children(true):
		if child == _root or child == body:
			remove_child(child)
			child.queue_free()
	_root = null
	body = null
	chunks.clear()


func _build_chunk(rect: Rect2i, index: int, parts: Array) -> void:
	var a: Vector2 = point(rect.position.x, rect.position.y)
	var b: Vector2 = point(rect.end.x, rect.end.y)
	var centre := Vector3((a.x + b.x) * 0.5, 0.0, (a.y + b.y) * 0.5)
	var chunk := {"cells": rect, "centre": centre, "meshes": [], "triangles": []}
	for level: int in lod_levels:
		var built_mesh: Array = _chunk_mesh(rect, 1 << level, centre)
		var instance := MeshInstance3D.new()
		instance.name = "Chunk%d_LOD%d" % [index, level]
		instance.mesh = built_mesh[0]
		instance.material_override = material
		instance.position = centre
		instance.cast_shadow = (
			GeometryInstance3D.SHADOW_CASTING_SETTING_ON
			if cast_shadows
			else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		)
		var shown: Vector2 = lod_range(level)
		instance.visibility_range_begin = shown.x
		instance.visibility_range_end = shown.y
		_root.add_child(instance)
		chunk["meshes"].append(instance)
		chunk["triangles"].append(built_mesh[1])
	if body != null:
		body.add_child(_chunk_shape(rect, centre, index))
	for rule_index: int in scatter.size():
		_scatter_chunk(rect, centre, index, rule_index, parts[rule_index])
	chunks.append(chunk)


## Distances (from, to) at which a detail level is drawn; to = 0 means no limit.
func lod_range(level: int) -> Vector2:
	var begin: float = 0.0 if level == 0 else lod_distance * pow(2.0, level - 1)
	var end: float = lod_distance * pow(2.0, level)
	if level == lod_levels - 1:
		end = view_distance
	return Vector2(begin, end)


## The detail level drawn at `distance`, or -1 when nothing is.
func lod_for_distance(distance: float) -> int:
	for level: int in lod_levels:
		var shown: Vector2 = lod_range(level)
		if distance >= shown.x and (shown.y <= 0.0 or distance < shown.y):
			return level
	return -1


# --- Meshes and collision ---------------------------------------------------------------


## Terrain-local x, z of a grid point.
func point(x: int, z: int) -> Vector2:
	return Vector2(
		-size.x * 0.5 + size.x * x / float(map_width - 1),
		-size.y * 0.5 + size.y * z / float(map_depth - 1)
	)


func _h(x: int, z: int) -> float:
	return heights[clampi(z, 0, map_depth - 1) * map_width + clampi(x, 0, map_width - 1)]


func _grid_normal(x: int, z: int) -> Vector3:
	var dx: float = size.x / float(map_width - 1)
	var dz: float = size.y / float(map_depth - 1)
	var sx: float = (_h(x + 1, z) - _h(x - 1, z)) / (2.0 * dx)
	var sz: float = (_h(x, z + 1) - _h(x, z - 1)) / (2.0 * dz)
	return Vector3(-sx, 1.0, -sz).normalized()


static func _steps(start: int, count: int, stride: int) -> PackedInt32Array:
	var out := PackedInt32Array()
	for i: int in range(0, count, stride):
		out.append(start + i)
	out.append(start + count)
	return out


## [ArrayMesh, triangles] for one chunk at one grid stride, with a skirt that hides
## the cracks where it meets a chunk drawn at another level.
func _chunk_mesh(rect: Rect2i, stride: int, centre: Vector3) -> Array:
	var xs: PackedInt32Array = _steps(rect.position.x, rect.size.x, stride)
	var zs: PackedInt32Array = _steps(rect.position.y, rect.size.y, stride)
	var vertices := PackedVector3Array()
	var normals := PackedVector3Array()
	var uvs := PackedVector2Array()
	var uv2s := PackedVector2Array()
	var indices := PackedInt32Array()
	for z: int in zs:
		for x: int in xs:
			var at: Vector2 = point(x, z)
			var y: float = _h(x, z)
			vertices.append(Vector3(at.x - centre.x, y, at.y - centre.z))
			normals.append(_grid_normal(x, z))
			uvs.append(at)
			uv2s.append(Vector2(y, 0.0))
	var across: int = xs.size()
	for row: int in zs.size() - 1:
		for column: int in across - 1:
			var a: int = row * across + column
			var b: int = a + 1
			var c: int = a + across
			var d: int = c + 1
			indices.append_array([a, b, d, a, d, c])
	var triangles: int = int(indices.size() / 3.0)
	# The skirt: the chunk's outline copied straight down, faced both ways.
	var depth: float = (
		2.0 * stride * maxf(size.x / float(map_width - 1), size.y / float(map_depth - 1))
	)
	var outline := PackedInt32Array()
	for column: int in across:
		outline.append(column)
	for row: int in range(1, zs.size()):
		outline.append(row * across + across - 1)
	for column: int in range(across - 2, -1, -1):
		outline.append((zs.size() - 1) * across + column)
	for row: int in range(zs.size() - 2, 0, -1):
		outline.append(row * across)
	outline.append(0)
	for i: int in outline.size() - 1:
		var top_a: int = outline[i]
		var top_b: int = outline[i + 1]
		var low_a: int = vertices.size()
		for top: int in [top_a, top_b]:
			vertices.append(vertices[top] - Vector3(0.0, depth, 0.0))
			normals.append(normals[top])
			uvs.append(uvs[top])
			uv2s.append(uv2s[top] - Vector2(depth, 0.0))
		indices.append_array([top_a, top_b, low_a + 1, top_a, low_a + 1, low_a])
		indices.append_array([top_a, low_a + 1, top_b, top_a, low_a, low_a + 1])
	var arrays: Array = []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_TEX_UV2] = uv2s
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return [mesh, triangles]


func _chunk_shape(rect: Rect2i, centre: Vector3, index: int) -> CollisionShape3D:
	var dx: float = size.x / float(map_width - 1)
	var dz: float = size.y / float(map_depth - 1)
	var uniform: bool = absf(dx - dz) < 0.0001
	var shape := HeightMapShape3D.new()
	shape.map_width = rect.size.x + 1
	shape.map_depth = rect.size.y + 1
	var data := PackedFloat32Array()
	for z: int in range(rect.position.y, rect.end.y + 1):
		for x: int in range(rect.position.x, rect.end.x + 1):
			# A uniform scale keeps the physics happy: heights are stored in cells.
			data.append(_h(x, z) / dx if uniform else _h(x, z))
	shape.map_data = data
	var holder := CollisionShape3D.new()
	holder.name = "Chunk%d" % index
	holder.shape = shape
	holder.position = centre
	holder.scale = Vector3(dx, dx, dx) if uniform else Vector3(dx, 1.0, dz)
	return holder


# --- Material -------------------------------------------------------------------------


func _make_material() -> ShaderMaterial:
	var made := ShaderMaterial.new()
	made.shader = SHADER
	made.set_shader_parameter(&"terrain_size", size)
	if mask_map != null:
		made.set_shader_parameter(
			&"splat_mask", ImageTexture.create_from_image(mask_map.to_image())
		)
	var scales := Vector4(0.25, 0.25, 0.25, 0.25)
	var rough := Vector4(0.9, 0.9, 0.9, 0.9)
	var rule_on := Vector3.ZERO
	for i: int in mini(layers.size(), MAX_LAYERS):
		var layer: TerrainLayer = layers[i]
		if layer == null:
			continue
		made.set_shader_parameter(StringName("color_%d" % i), layer.color)
		if layer.albedo != null:
			made.set_shader_parameter(StringName("albedo_%d" % i), layer.albedo)
		scales[i] = layer.uv_scale
		rough[i] = layer.roughness
		if i > 0:
			rule_on[i - 1] = 1.0 if layer.use_rule else 0.0
			made.set_shader_parameter(
				StringName("rule_%d" % i),
				Vector4(
					layer.height_range.x,
					layer.height_range.y,
					layer.slope_range.x,
					layer.slope_range.y
				)
			)
	made.set_shader_parameter(&"uv_scale", scales)
	made.set_shader_parameter(&"roughness", rough)
	made.set_shader_parameter(&"rule_on", rule_on)
	return made


# --- Scatter --------------------------------------------------------------------------


## [[Mesh, Transform3D], …]: every mesh a scatter rule draws, placed as in its scene.
func _scatter_parts(rule: TerrainScatter) -> Array:
	var found: Array = []
	if rule == null:
		return found
	if rule.mesh != null:
		found.append([rule.mesh, Transform3D.IDENTITY])
	elif rule.scene != null:
		var model: Node = rule.scene.instantiate()
		_collect_meshes(model, Transform3D.IDENTITY, found)
		model.free()
	return found


func _collect_meshes(node: Node, parent: Transform3D, found: Array) -> void:
	var here: Transform3D = parent
	if node is Node3D:
		here = parent * (node as Node3D).transform
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		found.append([(node as MeshInstance3D).mesh, here])
	for child: Node in node.get_children():
		_collect_meshes(child, here, found)


func _scatter_chunk(
	rect: Rect2i, centre: Vector3, index: int, rule_index: int, parts: Array
) -> void:
	var rule: TerrainScatter = scatter[rule_index]
	if rule == null or parts.is_empty() or rule.density <= 0.0:
		return
	var placed: Array[Transform3D] = placements(rect, index, rule_index)
	_scatter_counts[rule_index] += placed.size()
	if placed.is_empty():
		return
	for part_index: int in parts.size():
		var multimesh := MultiMesh.new()
		multimesh.transform_format = MultiMesh.TRANSFORM_3D
		multimesh.mesh = parts[part_index][0]
		multimesh.instance_count = placed.size()
		for i: int in placed.size():
			multimesh.set_instance_transform(i, placed[i] * parts[part_index][1])
		var instance := MultiMeshInstance3D.new()
		instance.name = "Scatter%d_%d_%d" % [rule_index, index, part_index]
		instance.multimesh = multimesh
		instance.position = centre
		instance.visibility_range_end = rule.visible_distance
		_root.add_child(instance)


## The seeded instance transforms of one scatter rule in one chunk, relative to the
## chunk's centre. The same settings always give the same transforms.
func placements(rect: Rect2i, index: int, rule_index: int) -> Array[Transform3D]:
	var rule: TerrainScatter = scatter[rule_index]
	var placed: Array[Transform3D] = []
	var a: Vector2 = point(rect.position.x, rect.position.y)
	var b: Vector2 = point(rect.end.x, rect.end.y)
	var centre := Vector3((a.x + b.x) * 0.5, 0.0, (a.y + b.y) * 0.5)
	var area: float = (b.x - a.x) * (b.y - a.y)
	var rng := RandomNumberGenerator.new()
	rng.seed = (rule.placement_seed * 73856093) ^ (index * 19349663) ^ (rule_index * 83492791)
	var count: int = roundi(rule.density * area / 100.0)
	for _i: int in count:
		var x: float = rng.randf_range(a.x, b.x)
		var z: float = rng.randf_range(a.y, b.y)
		var turn: float = rng.randf_range(0.0, TAU)
		var grow: float = rng.randf_range(rule.scale_range.x, rule.scale_range.y)
		var y: float = local_height(x, z)
		var up: Vector3 = local_normal(x, z)
		if y < rule.height_range.x or y > rule.height_range.y:
			continue
		if rad_to_deg(acos(clampf(up.y, -1.0, 1.0))) > rule.max_slope:
			continue
		if rule.mask_channel >= 0 and mask_value(x, z, rule.mask_channel) < rule.mask_threshold:
			continue
		var basis := Basis(Vector3.UP, turn if rule.random_turn else 0.0)
		if rule.align_to_ground:
			basis = Basis(Quaternion(Vector3.UP, up)) * basis
		var origin := Vector3(x, y - rule.sink, z) - centre
		placed.append(Transform3D(basis * Basis.from_scale(Vector3.ONE * grow), origin))
	return placed


## How many instances each scatter rule placed (all chunks), or one rule's count.
func scatter_count(rule_index: int = -1) -> int:
	if rule_index >= 0:
		return _scatter_counts[rule_index] if rule_index < _scatter_counts.size() else 0
	var total: int = 0
	for count: int in _scatter_counts:
		total += count
	return total


# --- Queries --------------------------------------------------------------------------


## Height in metres above this node at terrain-local x, z (bilinear).
func local_height(x: float, z: float) -> float:
	if heights.is_empty():
		return 0.0
	var fx: float = clampf((x + size.x * 0.5) / size.x, 0.0, 1.0) * (map_width - 1)
	var fz: float = clampf((z + size.y * 0.5) / size.y, 0.0, 1.0) * (map_depth - 1)
	var x0: int = floori(fx)
	var z0: int = floori(fz)
	var tx: float = fx - x0
	var tz: float = fz - z0
	var near: float = lerpf(_h(x0, z0), _h(x0 + 1, z0), tx)
	var far: float = lerpf(_h(x0, z0 + 1), _h(x0 + 1, z0 + 1), tx)
	return lerpf(near, far, tz)


func local_normal(x: float, z: float) -> Vector3:
	var step: Vector2 = size / Vector2(map_width - 1, map_depth - 1)
	var sx: float = (local_height(x + step.x, z) - local_height(x - step.x, z)) / (2.0 * step.x)
	var sz: float = (local_height(x, z + step.y) - local_height(x, z - step.y)) / (2.0 * step.y)
	return Vector3(-sx, 1.0, -sz).normalized()


## The mask's channel (0-3) at terrain-local x, z; 0 without a mask.
func mask_value(x: float, z: float, channel: int) -> float:
	if mask_map == null:
		return 0.0
	return mask_map.sample((x + size.x * 0.5) / size.x, (z + size.y * 0.5) / size.y, channel)


## Whether a world position is over the terrain.
func contains(world: Vector3) -> bool:
	var at: Vector3 = to_local(world)
	return absf(at.x) <= size.x * 0.5 and absf(at.z) <= size.y * 0.5


## The world height of the ground under a world position.
func height_at(world: Vector3) -> float:
	var at: Vector3 = to_local(world)
	return to_global(Vector3(at.x, local_height(at.x, at.z), at.z)).y


## The ground's world normal under a world position.
func normal_at(world: Vector3) -> Vector3:
	var at: Vector3 = to_local(world)
	return (global_basis * local_normal(at.x, at.z)).normalized()


## Triangles drawn per detail level, all chunks together.
func triangle_counts() -> PackedInt32Array:
	var totals := PackedInt32Array()
	totals.resize(lod_levels)
	for chunk: Dictionary in chunks:
		for level: int in chunk["triangles"].size():
			totals[level] += int(chunk["triangles"][level])
	return totals


## How many chunks each detail level draws for a camera at `eye` (world).
func lods_seen_from(eye: Vector3) -> PackedInt32Array:
	var seen := PackedInt32Array()
	seen.resize(lod_levels + 1)
	for chunk: Dictionary in chunks:
		var level: int = lod_for_distance(eye.distance_to(to_global(chunk["centre"])))
		seen[level if level >= 0 else lod_levels] += 1
	return seen


# --- The playtester -------------------------------------------------------------------


func _physics_process(_delta: float) -> void:
	if Engine.is_editor_hint() or heights.is_empty():
		return
	_ticks += 1
	if _ticks % 6 != 0:
		return
	var game: Node = get_tree().root.get_node_or_null(^"Game")
	if game == null or not game.has_method(&"players"):
		return
	for player: Node in game.call(&"players"):
		var where: Node3D = player as Node3D
		if where == null or not contains(where.global_position):
			continue
		var gap: float = where.global_position.y - height_at(where.global_position)
		_lowest_gap = minf(_lowest_gap, gap)
		if gap < -THROUGH:
			_through_frames += 1
		elif gap <= ON_SURFACE:
			_on_frames += 1


## For the playtester: size, chunks, LODs drawn for the current camera, scatter
## counts, and how often a player stood on the terrain or was found below it.
func playtest_state() -> Dictionary:
	var camera: Camera3D = get_viewport().get_camera_3d() if is_inside_tree() else null
	var seen := PackedInt32Array()
	if camera != null:
		seen = lods_seen_from(camera.global_position)
	var lowest: Variant = null
	if _lowest_gap != INF:
		lowest = snappedf(_lowest_gap, 0.01)
	return {
		"kind": "terrain",
		"heightmap": heightmap,
		"size": [size.x, size.y],
		"grid": [map_width, map_depth],
		"chunks": chunks.size(),
		"lods_seen": Array(seen),
		"triangles": Array(triangle_counts()),
		"scatter": Array(_scatter_counts),
		"checks_on_surface": _on_frames,
		"checks_below_surface": _through_frames,
		"lowest_gap": lowest,
	}
