@tool
extends RefCounted
## Godot's empty-editor sky and sun defaults; independent of game environments.


func apply(root: Node3D, _camera: Camera3D) -> void:
	var top := Color(0.385, 0.454, 0.55)
	var ground := Color(0.2, 0.169, 0.133)
	var horizon := top.lerp(ground, 0.5)
	var luminance := horizon.get_luminance() * 3.333
	horizon = horizon.lerp(Color(luminance, luminance, luminance), 0.5)
	var material := ProceduralSkyMaterial.new()
	material.sky_top_color = top
	material.sky_horizon_color = horizon
	material.ground_bottom_color = ground
	material.ground_horizon_color = horizon
	var sky := Sky.new()
	sky.sky_material = material
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	environment.sky = sky
	var world := WorldEnvironment.new()
	world.environment = environment
	root.add_child(world)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-60, 150, 0)
	sun.shadow_enabled = true
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	root.add_child(sun)
