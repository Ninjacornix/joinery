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
