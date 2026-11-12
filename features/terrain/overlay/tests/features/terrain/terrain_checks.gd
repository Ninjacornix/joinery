extends RefCounted
## The checks behind terrain_test.gd, loaded once a 3D game runs: the operations
## are seeded and do what they say, the PNG codec keeps 16 bits, and a Terrain
## built beside the level collides, carries the walking player, draws coarser
## chunks further away and scatters only where the mask allows.

const OpsScript := preload("res://features/terrain/terrain_ops.gd")
const MapScript := preload("res://features/terrain/terrain_map.gd")
const Png := preload("res://features/terrain/terrain_png.gd")
## Beside the blueprints' levels, clear of their geometry.
const PLACE := Vector3(90.0, -1.0, 0.0)
const DOC := {
	"size": [48.0, 48.0],
	"resolution": 65,
	"min_height": -2.0,
	"max_height": 12.0,
	"seed": 7,
	"ops":
	[
		{"op": "noise", "amplitude": 3.0, "scale": 16.0, "octaves": 4},
		{"op": "flatten", "at": [0.0, 0.0], "radius": 7.0, "height": 1.0, "falloff": 0.4},
		{"op": "path", "points": [[-20.0, 12.0], [20.0, 12.0]], "width": 3.0, "paint": "r"},
		{"op": "paint", "channel": "a", "at": [-12.0, -12.0], "radius": 8.0, "falloff": 0.2},
	],
}

var tree: SceneTree
var h: PlayHarness
var terrain: Terrain
var built: Dictionary


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	await h.boot()
	_check_seeded()
	_check_png()
	_check_ops()
	_make_terrain()
	await h.physics_frames(3)
	_check_collision()
	_check_lods()
	_check_scatter()
	_check_material_and_file()
	await _check_player()
	_check_state()
	terrain.queue_free()


func _check_seeded() -> void:
	built = OpsScript.build(DOC)
	var again: Dictionary = OpsScript.build(DOC)
	h.expect(built["problems"].is_empty(), "the document runs cleanly %s" % [built["problems"]])
	h.expect(
		built["height"].values == again["height"].values, "the same seed builds the same ground"
	)
	h.expect(built["mask"].values == again["mask"].values, "and the same mask")
	var other: Dictionary = DOC.duplicate(true)
	other["seed"] = 8
	h.expect(
		OpsScript.build(other)["height"].values != built["height"].values,
		"another seed builds other ground"
	)
	var span: Vector2 = built["height"].value_range()
	h.expect(
		span.x >= -2.0 and span.y <= 12.0 and span.y - span.x > 1.0,
		"heights stay in range (%s)" % span
	)


func _check_png() -> void:
	var ops: OpsScript = OpsScript.new()
	ops.run(DOC)
	var height: MapScript = ops.normalised()
	var bytes: PackedByteArray = Png.encode(height.width, height.height, 1, height.values)
	h.expect(
		bytes == Png.encode(height.width, height.height, 1, height.values),
		"the same heights write the same bytes"
	)
	var back: Dictionary = Png.decode(bytes)
	h.expect(back.get("width") == 65 and back.get("channels") == 1, "a 16-bit PNG reads back")
	var worst: float = 0.0
	for i: int in height.values.size():
		worst = maxf(worst, absf(back["values"][i] - height.values[i]))
	h.expect(worst <= 1.0 / 65535.0, "16 bits are kept (worst %.7f)" % worst)
	# Godot's own PNG writer filters its rows; the reader undoes every filter.
	var image := Image.create(37, 21, false, Image.FORMAT_RGBA8)
	for y: int in 21:
		for x: int in 37:
			image.set_pixel(x, y, Color8((x * 7) % 256, (y * 11) % 256, (x * y) % 256, 255 - x))
	var godot_png: Dictionary = Png.decode(image.save_png_to_buffer())
	var same: bool = godot_png.get("channels") == 4
	for y: int in 21:
		for x: int in 37:
			var at: int = (y * 37 + x) * 4
			var colour: Color = image.get_pixel(x, y)
			same = (
				same
				and absf(godot_png["values"][at] - colour.r) < 0.002
				and absf(godot_png["values"][at + 3] - colour.a) < 0.002
			)
	h.expect(same, "filtered 8-bit PNGs read back exactly")


func _check_ops() -> void:
	var height: MapScript = built["height"]
	var mask: MapScript = built["mask"]
	var flat: bool = true
	for at: Vector2 in [Vector2.ZERO, Vector2(2.25, -2.25), Vector2(-3.0, 0.75)]:
		flat = flat and absf(_at(height, at) - 1.0) < 0.001
	h.expect(flat, "flatten levels its area to the height asked")
	h.expect(_at(mask, Vector2(0.0, 12.0), 0) > 0.95, "the path is painted into the mask")
	h.expect(_at(mask, Vector2(0.0, -20.0), 0) < 0.01, "and nowhere else")
	h.expect(_at(mask, Vector2(-12.0, -12.0), 3) > 0.95, "paint fills its circle")
	var plain := {
		"size": [32.0, 32.0], "resolution": 33, "min_height": -10.0, "max_height": 10.0, "seed": 1
	}
	var raised: Dictionary = plain.duplicate(true)
	raised["ops"] = [
		{"op": "raise", "at": [0.0, 0.0], "radius": 6.0, "amount": 2.0},
		{"op": "lower", "at": [10.0, 10.0], "radius": 4.0, "amount": 1.5, "falloff": 0.0},
	]
	var bumps: MapScript = OpsScript.build(raised)["height"]
	h.expect(
		absf(_at(bumps, Vector2.ZERO, 0, 32.0) - 2.0) < 0.001,
		"raise lifts the centre by its amount"
	)
	h.expect(
		absf(_at(bumps, Vector2(-12.0, 0.0), 0, 32.0)) < 0.001, "and nothing outside its radius"
	)
	h.expect(absf(_at(bumps, Vector2(10.0, 10.0), 0, 32.0) + 1.5) < 0.001, "lower sinks its area")
	var spiky: Dictionary = plain.duplicate(true)
	spiky["ops"] = [{"op": "raise", "at": [0.0, 0.0], "radius": 1.5, "amount": 8.0, "falloff": 0.0}]
	var peak: float = OpsScript.build(spiky)["height"].value_range().y
	spiky["ops"].append(
		{"op": "smooth", "at": [0.0, 0.0], "radius": 6.0, "strength": 1.0, "iterations": 3}
	)
	h.expect(
		OpsScript.build(spiky)["height"].value_range().y < peak * 0.7, "smooth softens a spike"
	)
	spiky["ops"][1] = {"op": "erode", "iterations": 20, "talus": 0.5}
	h.expect(OpsScript.build(spiky)["height"].value_range().y < peak, "erode wears a cliff down")


func _make_terrain() -> void:
	terrain = Terrain.new()
	terrain.name = "TestTerrain"
	terrain.size = Vector2(48.0, 48.0)
	terrain.min_height = -2.0
	terrain.max_height = 12.0
	terrain.chunk_cells = 16
	terrain.lod_distance = 20.0
	var base := TerrainLayer.new()
	var rock := TerrainLayer.new()
	rock.use_rule = true
	rock.slope_range = Vector2(30.0, 90.0)
	terrain.layers = [base, rock] as Array[TerrainLayer]
	var pebbles := TerrainScatter.new()
	var box := BoxMesh.new()
	box.size = Vector3(0.3, 0.3, 0.3)
	pebbles.mesh = box
	pebbles.density = 60.0
	pebbles.placement_seed = 3
	pebbles.mask_channel = 3
	var flat_only := TerrainScatter.new()
	flat_only.mesh = box
	flat_only.density = 10.0
	flat_only.mask_channel = -1
	flat_only.max_slope = 5.0
	terrain.scatter = [pebbles, flat_only] as Array[TerrainScatter]
	Game.current_level().add_child(terrain)
	terrain.global_position = PLACE
	terrain.build_from(built["height"], built["mask"])


func _check_collision() -> void:
	var space: PhysicsDirectSpaceState3D = terrain.get_world_3d().direct_space_state
	var worst: float = 0.0
	var hits: int = 0
	for offset: Vector3 in [
		Vector3.ZERO, Vector3(15.0, 0.0, 15.0), Vector3(-17.3, 0.0, 6.1), Vector3(9.0, 0.0, -20.0)
	]:
		var top: Vector3 = PLACE + offset + Vector3(0.0, 40.0, 0.0)
		var query := PhysicsRayQueryParameters3D.create(
			top, top - Vector3(0.0, 80.0, 0.0), terrain.collision_layer
		)
		var hit: Dictionary = space.intersect_ray(query)
		if not hit.is_empty():
			hits += 1
			worst = maxf(worst, absf(hit["position"].y - terrain.height_at(top)))
	h.expect(hits == 4, "rays from above hit the terrain's collision (%d of 4)" % hits)
	h.expect(worst < 0.05, "the collision matches the drawn ground (worst %.3f m)" % worst)
	h.expect(
		terrain.body != null and terrain.body.get_child_count() == terrain.chunks.size(),
		"one collision shape per chunk"
	)


func _check_lods() -> void:
	h.expect(
		terrain.chunks.size() == 16,
		"a 64-cell grid makes 4 × 4 chunks (%d)" % terrain.chunks.size()
	)
	h.expect(
		terrain.lod_for_distance(5.0) == 0 and terrain.lod_for_distance(30.0) == 1,
		"near chunks are detailed"
	)
	h.expect(terrain.lod_for_distance(500.0) == 2, "far chunks are coarse")
	var triangles: PackedInt32Array = terrain.triangle_counts()
	h.expect(
		triangles[0] > triangles[1] and triangles[1] > triangles[2],
		"each level has fewer triangles %s" % triangles
	)
	var chunk: Dictionary = terrain.chunks[0]
	var lod2: MeshInstance3D = chunk["meshes"][2]
	h.expect(
		lod2.visibility_range_begin == 40.0 and lod2.visibility_range_end == 0.0,
		"the last level draws from 40 m on"
	)
	var far: PackedInt32Array = terrain.lods_seen_from(PLACE + Vector3(0.0, 0.0, 600.0))
	h.expect(far[2] == 16, "seen from far away every chunk is coarse %s" % far)
	var near: PackedInt32Array = terrain.lods_seen_from(PLACE + Vector3(-18.0, 2.0, -18.0))
	h.expect(
		near[0] >= 1 and near[0] < 16, "seen from a corner, only near chunks are detailed %s" % near
	)


func _check_scatter() -> void:
	h.expect(
		terrain.scatter_count(0) > 10, "scatter places instances (%d)" % terrain.scatter_count(0)
	)
	var inside: bool = true
	var level: bool = true
	for index: int in terrain.chunks.size():
		var chunk: Dictionary = terrain.chunks[index]
		var placed: Array[Transform3D] = terrain.placements(chunk["cells"], index, 0)
		h.expect(placed == terrain.placements(chunk["cells"], index, 0), "placement is seeded")
		for at: Transform3D in placed:
			var local: Vector3 = at.origin + chunk["centre"]
			inside = inside and terrain.mask_value(local.x, local.z, 3) >= 0.5
		for at: Transform3D in terrain.placements(chunk["cells"], index, 1):
			var spot: Vector3 = at.origin + chunk["centre"]
			level = level and terrain.local_normal(spot.x, spot.z).y >= cos(deg_to_rad(5.0))
	h.expect(inside, "masked scatter stays where the mask is painted")
	h.expect(level, "slope-limited scatter keeps to flat ground")
	var multimeshes: int = 0
	for holder: Node in terrain.get_children(true):
		for child: Node in holder.get_children():
			if child is MultiMeshInstance3D:
				multimeshes += (child as MultiMeshInstance3D).multimesh.instance_count
	h.expect(multimeshes == terrain.scatter_count(), "every instance is drawn (%d)" % multimeshes)


func _check_material_and_file() -> void:
	var material: ShaderMaterial = terrain.material
	h.expect(
		material != null and material.get_shader_parameter(&"splat_mask") != null,
		"the splat material has the mask"
	)
	h.expect(
		material.get_shader_parameter(&"rule_on").x == 1.0, "a layer with a rule switches it on"
	)
	DirAccess.make_dir_recursive_absolute("user://terrain_test")
	var ops: OpsScript = OpsScript.new()
	ops.run(DOC)
	h.expect(ops.normalised().save_png("user://terrain_test/height.png") == OK, "a heightmap saves")
	h.expect(ops.mask.save_png("user://terrain_test/mask.png") == OK, "a mask saves")
	var from_file := Terrain.new()
	from_file.size = terrain.size
	from_file.min_height = terrain.min_height
	from_file.max_height = terrain.max_height
	from_file.heightmap = "user://terrain_test/height.png"
	from_file.mask = "user://terrain_test/mask.png"
	from_file.collision = false
	Game.current_level().add_child(from_file)
	var worst: float = 0.0
	for i: int in terrain.heights.size():
		worst = maxf(worst, absf(from_file.heights[i] - terrain.heights[i]))
	h.expect(worst < 0.001, "a terrain from the files has the same heights (worst %.5f m)" % worst)
	h.expect(absf(from_file.mask_value(-12.0, -12.0, 3) - 1.0) < 0.01, "and the same mask")
	from_file.queue_free()


func _check_player() -> void:
	var player: CharacterBody3D = Game.local_player() as CharacterBody3D
	h.expect(player != null, "the 3D game has a player body")
	if player == null:
		return
	player.global_position = PLACE + Vector3(0.0, 3.5, 0.0)
	player.velocity = Vector3.ZERO
	await h.physics_frames(90)
	var gap: float = player.global_position.y - terrain.height_at(player.global_position)
	h.expect(player.is_on_floor(), "the player lands on the terrain")
	h.expect(gap > -0.05 and gap < 0.3, "and stands on its surface (%.3f m)" % gap)
	var start: Vector3 = player.global_position
	await h.hold(&"move_forward", 70)
	await h.physics_frames(20)
	var walked: float = (
		Vector2(player.global_position.x - start.x, player.global_position.z - start.z).length()
	)
	gap = player.global_position.y - terrain.height_at(player.global_position)
	h.expect(walked > 1.5, "the player walks across it (%.2f m)" % walked)
	h.expect(terrain.contains(player.global_position), "and is still over it")
	h.expect(gap > -0.05 and gap < 0.4, "standing on the ground, not in it (%.3f m)" % gap)


func _check_state() -> void:
	h.expect(terrain.is_in_group(&"playtest_state"), "the terrain reports to the playtester")
	var state: Dictionary = terrain.playtest_state()
	h.expect(
		state.get("kind") == "terrain" and state.get("chunks") == 16,
		"its state names it %s" % state
	)
	h.expect(int(state.get("checks_on_surface", 0)) > 0, "it saw the player on it")
	h.expect(int(state.get("checks_below_surface", 0)) == 0, "and never below it")


## A map's value at terrain-local x, z.
static func _at(map: MapScript, at: Vector2, channel: int = 0, extent: float = 48.0) -> float:
	var uv: Vector2 = (at + Vector2(extent, extent) * 0.5) / extent
	return map.sample(uv.x, uv.y, channel)
