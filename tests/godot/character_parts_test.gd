extends SceneTree
## A character's parts in the editor (tests/godot/run_parts_tests.py, a project holding the
## MPFB2 character fixture, tests/fixtures/parts/character.glb): picking a body region on the
## model and in the parts list, the region tinted over the body, shape keys marked In game
## or Baked, and a click on the live model in review picking the region there too.
## JOINERY_PARTS_THEME (dark or light) and JOINERY_CHARACTER_CAPTURE (a path prefix) save
## screenshots of the asset page and of the review's model.

const PICK := preload("res://addons/joinery/part_pick.gd")
const CHIP := preload("res://addons/joinery/target_chip.gd")
const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")
const MODEL_VIEW := preload("res://addons/joinery/review_model_view.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const MODEL := "res://art/models/character.glb"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		printerr("FAILED: ", message)
		_failed = true


func _run() -> void:
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("character-test-ui.cfg")
		var file := ConfigFile.new()
		file.set_value("appearance", "theme", theme)
		file.save(config)
		Engine.set_meta("joinery_ui_config", config)
	var scene := load(MODEL) as PackedScene
	_check(scene != null, "The character fixture must import")
	if scene == null:
		quit(1)
		return
	var model := scene.instantiate() as Node3D
	root.add_child(model)
	_check_regions(model)
	_check_targets(model)
	model.queue_free()
	await process_frame
	await _check_asset_page()
	await _check_review()
	print("character parts: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _body(model: Node) -> MeshInstance3D:
	for node: Node in model.find_children("*", "MeshInstance3D", true, false):
		if PICK.source_name(node, str(node.name)) == "Hero":
			return node as MeshInstance3D
	return null


## A ray at the middle of a front-facing triangle of [param region]'s overlay, along its
## normal: the pick there, or {} when no sampled triangle is in plain view.
func _shoot(model: Node, body: MeshInstance3D, region: String) -> Dictionary:
	var bones: PackedStringArray = PICK.node_regions(body).get(region, PackedStringArray())
	var overlay := PICK.region_overlay(body, bones)
	if overlay == null:
		return {}
	var arrays := overlay.surface_get_arrays(0)
	var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var index: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
	var to_world := PICK.world_transform(body)
	var step := maxi(1, index.size() / 3 / 40)
	for triangle: int in range(0, index.size() / 3, step):
		var a := to_world * vertices[index[triangle * 3]]
		var b := to_world * vertices[index[triangle * 3 + 1]]
		var c := to_world * vertices[index[triangle * 3 + 2]]
		var normal := (b - a).cross(c - a).normalized()
		if normal.z < 0.5:
			continue
		var centre := (a + b + c) / 3.0
		var hit := PICK.pick(model, centre + normal * 2.0, -normal)
		if str(hit.get("region", "")) == region:
			return hit
	return {}


## The GLB's regions (extras.joinery_regions) and roles, and a pick on each region.
func _check_regions(model: Node3D) -> void:
	var body := _body(model)
	_check(body != null, "The body mesh keeps its Blender name")
	if body == null:
		return
	_check(PICK.extra(body, "joinery_role") == "basemesh", "MPFB2 made Hero as its basemesh")
	var regions := PICK.node_regions(body)
	_check(regions.size() == 9, "Nine body regions: %s" % [regions.keys()])
	_check(
		(regions.get("shoulders", PackedStringArray()) as PackedStringArray).has("DEF-shoulder.L"),
		"The left shoulder bone is in the shoulders"
	)
	var total := 0
	for surface: int in body.mesh.get_surface_count():
		total += body.mesh.surface_get_array_index_len(surface) / 3
	var shoulders := PICK.region_overlay(body, regions["shoulders"])
	_check(shoulders != null, "The shoulders have triangles")
	if shoulders != null:
		var tinted := shoulders.surface_get_array_index_len(0) / 3
		_check(tinted > 50 and tinted < total / 5, "A region is a part of the body: %d" % tinted)
	for region: String in ["head", "torso", "shoulders", "arms", "legs", "feet"]:
		var hit := _shoot(model, body, region)
		_check(not hit.is_empty(), "A click on the %s picks the %s" % [region, region])
		if not hit.is_empty():
			_check(hit["object"] == "Hero" and not str(hit["bone"]).is_empty(), str(hit))
	_check(PICK.bone_key("mixamorig:Hips") == "mixamorig_Hips", "Godot's bone spelling")


## A region pick becomes a message target routed to MPFB2's body operations.
func _check_targets(model: Node3D) -> void:
	var hit := _shoot(model, _body(model), "shoulders")
	if hit.is_empty():
		return
	var part := PICK.part_for(hit, MODEL)
	_check(part["part_kind"] == "region", "A pick on the body is its region")
	_check(part["id"] == MODEL + "#object=Hero&region=shoulders", part["id"])
	var target := CHIP.for_part(part)
	_check(target["label"] == "Shoulders · Hero", target["label"])
	_check(target["ops"] == "set_body_shape,edit_bone", "Region ops: " + str(target["ops"]))
	_check(str(target["tooltip"]).begins_with("Character › Hero › Shoulders"), target["tooltip"])
	var material := PICK.part_for(hit, MODEL, "base_color")
	_check(material["part_kind"] in ["material", "texture"], "A map pick still names the map")
	var worn := hit.duplicate()
	worn.erase("region")
	worn["role"] = "clothes"
	worn["object"] = "Shirt"
	_check(PICK.part_for(worn, MODEL)["part_kind"] == "object", "Clothes are picked whole")


## The asset page: the parts list groups, a region picked from the list and on the model.
func _check_asset_page() -> void:
	root.size = Vector2i(1280, 800)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var workspace := WORKSPACE.new()
	root.add_child(workspace)
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var box := StyleBoxFlat.new()
	box.bg_color = UI_THEME.color(workspace, "surface")
	backdrop.add_theme_stylebox_override("panel", box)
	var index: Dictionary = JSON.parse_string(
		FileAccess.get_file_as_string(OS.get_environment("JOINERY_PARTS_INDEX"))
	)
	workspace._apply_data(index)
	workspace.select_resource(MODEL)
	var panel: VBoxContainer = workspace._parts_panel
	var deadline := Time.get_ticks_msec() + 60000
	while panel._job != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(panel._error.is_empty(), "joinery parts failed: " + panel._error)
	var counts: Dictionary = panel.group_counts()
	_check(int(counts.get("region", 0)) == 9, "9 regions: %s" % [counts])
	_check(int(counts.get("body_shape", 0)) == 11, "11 body settings: %s" % [counts])
	_check(int(counts.get("shape_key", 0)) == 10, "10 shape keys: %s" % [counts])
	var headings: Array[String] = []
	for heading: TreeItem in panel.tree.get_root().get_children():
		if (heading.get_metadata(0) as Dictionary).has("group"):
			headings.append(heading.get_text(0))
	_check(
		headings.slice(0, 2) == ["9 body regions", "11 body settings"],
		"A character lists its body first: %s" % [headings]
	)
	var rows := {}
	for item: TreeItem in panel._all_rows(panel.tree.get_root()):
		var meta: Dictionary = item.get_metadata(0)
		var kind := str(meta.get("part_kind", ""))
		var field: String = {"body_shape": "parameter"}.get(kind, kind)
		if kind in ["region", "body_shape", "shape_key"]:
			rows[kind + ":" + str(meta.get(field, ""))] = item
	var picked: Array[Dictionary] = []
	workspace.part_selected.connect(func(target: Dictionary) -> void: picked.append(target))
	var viewer: SubViewportContainer = workspace._preview.get_child(0)
	var shoulders: TreeItem = rows.get("region:shoulders")
	var smile: TreeItem = rows.get("shape_key:smile")
	var muscle: TreeItem = rows.get("body_shape:muscle")
	_check(shoulders != null and smile != null and muscle != null, "Rows: %s" % [rows.keys()])
	if shoulders == null or smile == null or muscle == null:
		return
	_check(shoulders.get_text(0) == "Shoulders" and shoulders.get_text(1) == "Hero", "Row")
	_check(muscle.get_text(0) == "Muscle" and muscle.get_text(1) == "0.5", muscle.get_text(1))
	_check(smile.get_text(1) == "In game", "smile reaches the game: " + smile.get_text(1))
	var macro: TreeItem = null
	for key: String in rows:
		if key.begins_with("shape_key:$md-"):
			macro = rows[key]
	_check(macro != null and macro.get_text(1) == "Baked", "A body-shape macro is baked")
	panel.tree.set_selected(smile, 0)
	var smile_tint: int = viewer._region_triangles
	_check(smile_tint > 0, "A shape key tints the regions it moves")
	panel.tree.set_selected(shoulders, 0)
	_check(viewer._region_triangles > 0, "The shoulders are tinted on the model")
	_check(viewer._region_triangles != smile_tint, "Another region, another tint")
	_check(not picked.is_empty() and picked[-1]["part_kind"] == "region", "A region is picked")
	if not picked.is_empty():
		_check(picked[-1]["ops"] == "set_body_shape,edit_bone", "Ops: " + str(picked[-1]["ops"]))
		_check(str(picked[-1]["targets"]).contains("shoulder-muscle"), "Region targets listed")
	await process_frame
	await process_frame
	var hit: Dictionary = viewer.pick_at(viewer.size / 2.0)
	_check(
		not str(hit.get("region", "")).is_empty(), "A click on the body hits a region: %s" % [hit]
	)
	if not hit.is_empty():
		panel._on_model_clicked(hit)
		_check(picked[-1]["part_kind"] == "region", "A model click picks the region")
		_check(picked[-1]["region"] == hit["region"], "The region under the pointer")
	panel.tree.set_selected(shoulders, 0)
	await create_timer(0.4).timeout
	var capture := OS.get_environment("JOINERY_CHARACTER_CAPTURE")
	if not capture.is_empty():
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png(capture + "-asset.png")
	workspace.queue_free()
	backdrop.queue_free()
	await process_frame


## Review: a click on the live model picks the region there and tints it on both sides.
func _check_review() -> void:
	root.size = Vector2i(900, 520)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var view: HBoxContainer = MODEL_VIEW.new()
	view.threaded = false
	root.add_child(view)
	view.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var box := StyleBoxFlat.new()
	box.bg_color = UI_THEME.color(view, "surface_sunken")
	backdrop.add_theme_stylebox_override("panel", box)
	var path := ProjectSettings.globalize_path(MODEL)
	view.load_models(path, path)
	for frame: int in 4:
		await process_frame
	_check(view.is_live() and view.views().size() == 2, "Both sides load live")
	if not view.is_live():
		return
	var clicked: Array[Dictionary] = []
	view.part_clicked.connect(func(hit: Dictionary, _side: int) -> void: clicked.append(hit))
	var after: SubViewportContainer = view.views()[1]
	var press := InputEventMouseButton.new()
	press.button_index = MOUSE_BUTTON_LEFT
	press.pressed = true
	press.position = after.size / 2.0
	after.gui_input.emit(press)
	var release := press.duplicate() as InputEventMouseButton
	release.pressed = false
	after.gui_input.emit(release)
	_check(clicked.size() == 1, "A click on the After model picks a part")
	if clicked.is_empty():
		return
	var hit := clicked[0]
	_check(int(hit["side"]) == 1, "The pick names its side")
	_check(not str(hit.get("region", "")).is_empty(), "Review picks a region: %s" % [hit])
	var bones := str(hit.get("bones", "")).split(",", false)
	var tinted: int = view.highlight_region("Hero", bones)
	_check(tinted > 0, "The region is tinted on both sides")
	var drag := press.duplicate() as InputEventMouseButton
	after.gui_input.emit(drag)
	var moved := release.duplicate() as InputEventMouseButton
	moved.position += Vector2(40, 0)
	after.gui_input.emit(moved)
	_check(clicked.size() == 1, "A drag orbits; it picks nothing")
	await create_timer(0.3).timeout
	var capture := OS.get_environment("JOINERY_CHARACTER_CAPTURE")
	if not capture.is_empty():
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png(capture + "-review.png")
	view.queue_free()
	backdrop.queue_free()
	await process_frame
