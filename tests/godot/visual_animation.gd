extends RefCounted
## A small animated model for the visual suite: the Blender tab's animation browser and the
## review's before/after clips. Copied beside visual_editor_test.gd by run_visual_suite.py.


## A blocky hero with idle, walk and run: a body that bobs and legs and arms that swing.
static func hero() -> PackedScene:
	var hero := Node3D.new()
	hero.name = "Hero"
	var hips := Node3D.new()
	hips.name = "Hips"
	hips.position.y = 0.9
	_own(hero, hero, hips)
	var skin := StandardMaterial3D.new()
	skin.albedo_color = Color("c98f5a")
	var cloth := StandardMaterial3D.new()
	cloth.albedo_color = Color("3d6fb0")
	_part(hero, hips, "Body", Vector3(0.5, 0.7, 0.3), Vector3(0, 0.45, 0), cloth)
	_part(hero, hips, "Head", Vector3(0.3, 0.3, 0.3), Vector3(0, 1.0, 0), skin)
	for side: Array in [["L", 0.13], ["R", -0.13]]:
		var leg := Node3D.new()
		leg.name = "Leg" + side[0]
		leg.position.x = side[1]
		_own(hero, hips, leg)
		_part(hero, leg, "Shin", Vector3(0.18, 0.85, 0.2), Vector3(0, -0.45, 0), cloth)
		var arm := Node3D.new()
		arm.name = "Arm" + side[0]
		arm.position = Vector3(side[1] * 2.6, 0.75, 0)
		_own(hero, hips, arm)
		_part(hero, arm, "Fore", Vector3(0.12, 0.6, 0.12), Vector3(0, -0.3, 0), skin)
	var player := AnimationPlayer.new()
	player.name = "AnimationPlayer"
	_own(hero, hero, player)
	var library := AnimationLibrary.new()
	for spec: Array in [
		["idle", 2.0, 0.01, 4.0], ["walk", 1.0, 0.03, 30.0], ["run", 0.8, 0.07, 50.0]
	]:
		library.add_animation(spec[0], _cycle(spec[1], spec[2], spec[3]))
	player.add_animation_library("", library)
	var scene := PackedScene.new()
	scene.pack(hero)
	hero.free()
	return scene


static func _own(owner: Node, parent: Node, node: Node) -> void:
	parent.add_child(node)
	node.owner = owner


static func _part(
	owner: Node, parent: Node, name: String, size: Vector3, at: Vector3, material: Material
) -> void:
	var mesh := MeshInstance3D.new()
	mesh.name = name
	var box := BoxMesh.new()
	box.size = size
	box.material = material
	mesh.mesh = box
	mesh.position = at
	_own(owner, parent, mesh)


## A looping cycle of [param seconds]: the hips bob by [param bounce] metres twice a cycle,
## legs and arms swing by [param swing] degrees in opposition.
static func _cycle(seconds: float, bounce: float, swing: float) -> Animation:
	var animation := Animation.new()
	animation.length = seconds
	animation.loop_mode = Animation.LOOP_LINEAR
	var steps := 16
	var hips := animation.add_track(Animation.TYPE_VALUE)
	animation.track_set_path(hips, "Hips:position")
	var limbs := {}
	for path: String in ["Hips/LegL:rotation", "Hips/LegR:rotation", "Hips/ArmL:rotation"]:
		limbs[path] = animation.add_track(Animation.TYPE_VALUE)
		animation.track_set_path(limbs[path], path)
	limbs["Hips/ArmR:rotation"] = animation.add_track(Animation.TYPE_VALUE)
	animation.track_set_path(limbs["Hips/ArmR:rotation"], "Hips/ArmR:rotation")
	for step: int in steps + 1:
		var time := seconds * step / steps
		var phase := TAU * step / steps
		animation.track_insert_key(hips, time, Vector3(0, 0.9 + bounce * cos(phase * 2.0), 0))
		var angle := deg_to_rad(swing) * sin(phase)
		var signs := {"Hips/LegL:rotation": 1, "Hips/LegR:rotation": -1}
		signs["Hips/ArmL:rotation"] = -1
		signs["Hips/ArmR:rotation"] = 1
		for path: String in limbs:
			animation.track_insert_key(limbs[path], time, Vector3(angle * signs[path], 0, 0))
	return animation


## Eight frames of the hero's run side by side, the way Blender's review strip is laid out.
static func strip(host: Node, scene: PackedScene, bounce: float, seconds: float) -> Image:
	var side := 192
	var frames := 8
	var viewport := SubViewport.new()
	viewport.size = Vector2i(side, side)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("2e3034")
	environment.ambient_light_color = Color(0.6, 0.6, 0.6)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	var world := WorldEnvironment.new()
	world.environment = environment
	viewport.add_child(world)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-45, 35, 0)
	viewport.add_child(light)
	var hero := scene.instantiate()
	viewport.add_child(hero)
	var player: AnimationPlayer = hero.get_node("AnimationPlayer")
	player.get_animation("run").length = seconds
	var hips: Node3D = hero.get_node("Hips")
	var camera := Camera3D.new()
	camera.position = Vector3(2.4, 1.4, 1.8)
	viewport.add_child(camera)
	host.add_child(viewport)
	camera.look_at(Vector3(0, 0.9, 0))
	var strip := Image.create(side * frames, side, false, Image.FORMAT_RGBA8)
	player.assigned_animation = "run"
	for index: int in frames:
		player.seek(seconds * index / frames, true)
		hips.position.y = 0.9 + bounce * cos(TAU * 2.0 * index / frames)
		await host.get_tree().process_frame
		await host.get_tree().process_frame
		var frame := viewport.get_texture().get_image()
		frame.convert(Image.FORMAT_RGBA8)
		strip.blit_rect(frame, Rect2i(0, 0, side, side), Vector2i(index * side, 0))
	viewport.queue_free()
	return strip


## Parts report rows for the hero's idle, walk and run.
static func report_rows() -> Array:
	var rows: Array = []
	for spec: Array in [["idle", "1,49", "2.0"], ["walk", "1,25", "1.0"], ["run", "1,17", "0.667"]]:
		var row := {"part_kind": "animation", "resource": "", "object": "HeroRig"}
		row.merge({"action": spec[0], "frames": spec[1], "seconds": spec[2]})
		row.merge({"fps": "24", "loop": "true"})
		rows.append(row)
	return rows


## Review media for a bouncier run: strips of the hero before and after, written to [param cache].
static func clip_media(host: Node, cache: String) -> Array:
	var scene := hero()
	var before := cache.path_join("run-before.png")
	var after := cache.path_join("run-after.png")
	(await strip(host, scene, 0.06, 0.8)).save_png(before)
	(await strip(host, scene, 0.09, 0.8)).save_png(after)
	var meta := {"frames": 8, "fps": 10.0, "seconds": 0.8, "loop": true}
	var item := {"path": "art/sources/hero.blend", "media": "clip", "label": "run"}
	item["part"] = "res://art/models/hero.glb#object=HeroRig&action=run"
	item.merge({"before": before, "after": after})
	item["before_clip"] = meta.merged({"bounce": 0.06})
	item["after_clip"] = meta.merged({"bounce": 0.09})
	return [item]


## The hammer as live 3D: renders first, then the GLB files load and both sides turn together.
static func live_model(
	host: Node, dock: Control, panel: Control, cache: String, renders: Array
) -> void:
	var still: String = renders[0]
	var far: String = renders[1]
	var models := [cache.path_join("hammer-before.glb"), cache.path_join("hammer-after.glb")]
	hammer_glb(models[0], 1.8)
	hammer_glb(models[1], 2.3)
	(
		dock
		. call(
			"show_file_review",
			"Lengthen the hammer's handle",
			"",
			[
				{
					"path": "art/sources/hammer.blend",
					"media": "image",
					"before": still,
					"before_back": far,
					"after": still,
					"after_back": far,
					"before_model": models[0],
					"after_model": models[1],
				}
			],
			[
				{"path": "art/sources/hammer.blend", "change": "modified"},
				{"path": "art/models/hammer.glb", "change": "modified"},
			],
		)
	)
	var live: Control = panel.get("_media").call("model_view")
	var deadline := Time.get_ticks_msec() + 5000
	while not live.call("is_live") and Time.get_ticks_msec() < deadline:
		await host.get_tree().process_frame
	host.call("_check", live.call("is_live"), "The hammer loads as live 3D")
	live.call("orbit_by", Vector2(-60.0, 0.0))
	for frame: int in 6:
		await host.get_tree().process_frame
	await host.call("_shot", "review-model-live", [panel, dock])
	dock.call("hide_edit_review")
	await host.get_tree().process_frame


## A hammer with a handle of [param handle] metres, written as a GLB.
static func hammer_glb(path: String, handle: float) -> void:
	var root := Node3D.new()
	root.name = "Hammer"
	var steel := StandardMaterial3D.new()
	steel.albedo_color = Color("9aa3ad")
	steel.metallic = 0.6
	steel.roughness = 0.4
	var wood := StandardMaterial3D.new()
	wood.albedo_color = Color("8a5a32")
	var head := MeshInstance3D.new()
	head.name = "Head"
	var head_mesh := BoxMesh.new()
	head_mesh.size = Vector3(1.2, 0.4, 0.4)
	head_mesh.material = steel
	head.mesh = head_mesh
	head.position = Vector3(0, handle / 2.0, 0)
	root.add_child(head)
	head.owner = root
	var grip := MeshInstance3D.new()
	grip.name = "Handle"
	var grip_mesh := CylinderMesh.new()
	grip_mesh.top_radius = 0.09
	grip_mesh.bottom_radius = 0.11
	grip_mesh.height = handle
	grip_mesh.material = wood
	grip.mesh = grip_mesh
	root.add_child(grip)
	grip.owner = root
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	document.append_from_scene(root, state)
	document.write_to_filesystem(state, path)
	root.free()


## Mark the open scene's node [param name] as changed by a live edit, so the review offers
## Open in editor; "" forgets it. Returns the node, or null.
static func mark_changed(name: String) -> Node:
	var snapshot: GDScript = load("res://addons/joinery/review_snapshot.gd")
	var scene_root := EditorInterface.get_edited_scene_root()
	var node := scene_root.get_node_or_null(name) if scene_root and not name.is_empty() else null
	snapshot.set("nodes", [node] as Array[Node] if node != null else [] as Array[Node])
	return node
