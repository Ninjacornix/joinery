extends SceneTree
## Picks parts of the committed hammer fixture (tests/fixtures/parts/hammer.glb, made in
## Blender by tests/fixtures/parts/build.py) the way the 3D view and the workspace preview do.
## Run by tests/godot/run_parts_tests.py in a throwaway project that imported the model with
## Joinery's import settings (textures kept embedded).

const PICK := preload("res://addons/joinery/part_pick.gd")
const CHIP := preload("res://addons/joinery/target_chip.gd")
const MODEL := "res://art/models/hammer.glb"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		printerr("FAILED: ", message)
		_failed = true


func _run() -> void:
	var scene := load(MODEL) as PackedScene
	_check(scene != null, "The hammer fixture must import")
	if scene == null:
		quit(1)
		return
	var model := scene.instantiate() as Node3D
	# Outside the scene tree first (the transform is walked by hand), then inside it.
	_check_picks(model, "detached")
	root.add_child(model)
	model.position = Vector3(1, 0, 0)
	_check_picks(model, "in the tree, moved", Vector3(1, 0, 0))
	_check_names(model)
	_check_textures(model)
	_check_targets(model)
	model.queue_free()
	print("part pick: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## Handle side → Wood; the handle's end cap (its second slot) → Steel; the head → Steel.
func _check_picks(model: Node3D, where: String, offset := Vector3.ZERO) -> void:
	var side := PICK.pick(model, offset + Vector3(0, 0.3, 2), Vector3(0, 0, -1))
	_check(side.get("material") == "Wood", "%s: handle side is Wood, got %s" % [where, side])
	_check(side.get("object") == "Handle", "%s: handle side is on Handle" % where)
	var cap := PICK.pick(model, offset + Vector3(0, -2, 0), Vector3(0, 1, 0))
	_check(cap.get("material") == "Steel", "%s: end cap is Steel, got %s" % [where, cap])
	_check(cap.get("object") == "Handle", "%s: end cap is on Handle" % where)
	_check(int(cap.get("surface", -1)) == 1, "%s: the cap is the handle's second surface" % where)
	var head := PICK.pick(model, offset + Vector3(0.08, 0.64, 2), Vector3(0, 0, -1))
	_check(head.get("material") == "Steel", "%s: head is Steel, got %s" % [where, head])
	_check(head.get("object") == "Head.001", "%s: head keeps its Blender name" % where)
	var position: Vector3 = head.get("position", Vector3.INF)
	_check(absf(position.z - 0.04) < 0.005, "%s: the hit is on the head's front face" % where)
	var miss := PICK.pick(model, offset + Vector3(5, 5, 5), Vector3(0, 1, 0))
	_check(miss.is_empty(), "%s: a ray past the model picks nothing" % where)


## extras.joinery_name maps Godot's sanitised names back to Blender's.
func _check_names(model: Node3D) -> void:
	var head := model.find_child("Head_001", true, false) as MeshInstance3D
	_check(head != null, "Godot names the head node Head_001")
	if head == null:
		return
	_check(PICK.source_name(head, str(head.name)) == "Head.001", "joinery_name overrides Head_001")
	_check(PICK.source_name(null, "Plain") == "Plain", "No extras falls back to the given name")
	var steel := head.get_active_material(0)
	_check(PICK.source_name(steel, "") == "Steel", "The material carries its Blender name")
	var maps := PICK.material_maps(steel)
	_check(maps.get("roughness") == "steel_rough", "Steel maps: %s" % [maps])
	_check(maps.get("height") == "steel_height", "Height is listed though glTF cannot carry it")
	var handle := model.find_child("Handle", true, false) as MeshInstance3D
	var wood := PICK.material_maps(handle.get_active_material(0))
	_check(wood.get("roughness") == "wood_orm" and wood.get("metallic") == "wood_orm", "ORM")
	var first := PICK.triangle_mesh(head.mesh)
	_check(PICK.triangle_mesh(head.mesh) == first, "Triangle meshes are cached")


## Joinery's import settings keep textures embedded, named as in Blender, and write no
## extracted PNG files beside the model.
func _check_textures(model: Node3D) -> void:
	var head := model.find_child("Head_001", true, false) as MeshInstance3D
	var steel := head.get_active_material(0) as BaseMaterial3D
	_check(steel != null and steel.roughness_texture != null, "Steel has a roughness texture")
	if steel != null and steel.roughness_texture != null:
		_check(
			steel.roughness_texture.resource_name == "steel_rough",
			(
				"Embedded textures keep the Blender image name: %s"
				% steel.roughness_texture.resource_name
			)
		)
	var extracted := DirAccess.get_files_at("res://art/models")
	for file: String in extracted:
		_check(not file.ends_with(".png"), "No texture is extracted beside the model: " + file)


## A pick becomes a message target with a leaf-first chip label.
func _check_targets(model: Node3D) -> void:
	var head := PICK.pick(model, Vector3(1.08, 0.64, 2), Vector3(0, 0, -1))
	var material := PICK.part_for(head, MODEL)
	_check(material["part_kind"] == "material", "A plain pick is the material")
	_check(material["id"] == MODEL + "#material=Steel", material["id"])
	_check(CHIP.for_part(material)["label"] == "Steel · Head.001", CHIP.for_part(material)["label"])
	var rough := PICK.part_for(head, MODEL, "roughness")
	_check(rough["image"] == "steel_rough", "A map pick names its image")
	_check(rough["id"] == MODEL + "#material=Steel&image=steel_rough", rough["id"])
	var target := CHIP.for_part(rough)
	_check(target["label"] == "Roughness · Head.001", target["label"])
	_check(target["kind"] == "Image", "A map chip uses the image icon")
	_check(
		str(target["ops"]).split(",").has("make_map_editable"),
		"A picked map routes to its operations: %s" % target["ops"]
	)
	_check(
		(
			CHIP.for_part({"part_kind": "bone", "bone": "Mid", "ops": "edit_bone"})["ops"]
			== "edit_bone"
		),
		"A listed part keeps the operations `joinery parts` gave it"
	)
	_check(CHIP.for_part(material)["ops"] == "set_material_value,split_material", "Material ops")
	_check(
		str(target["tooltip"]).begins_with("Hammer › Head.001 › Steel › Roughness"),
		target["tooltip"]
	)
	var handle := PICK.pick(model, Vector3(1, 0.3, 2), Vector3(0, 0, -1))
	var orm := CHIP.for_part(PICK.part_for(handle, MODEL, "metallic"))
	_check(orm["label"] == "Metallic and roughness · Handle", orm["label"])
