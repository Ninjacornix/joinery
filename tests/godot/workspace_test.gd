extends SceneTree
## Run against testbed after its normal editor import:
## godot --path testbed --script ../tests/godot/workspace_test.gd
## Set JOINERY_WORKSPACE_INDEX to a `joinery workspace` JSON report.
## Optional JOINERY_WORKSPACE_CAPTURE saves the real viewer for visual QA.
##
## With JOINERY_PARTS_INDEX instead (tests/godot/run_parts_tests.py, a project holding the
## hammer fixture), it checks the parts list of a model: JOINERY_PARTS_THEME (dark or light)
## and JOINERY_PARTS_CAPTURE save a screenshot of it.

const CHIP := preload("res://addons/joinery/target_chip.gd")
const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")

var _workspace: VBoxContainer
var _failed := false


func _initialize() -> void:
	if not OS.get_environment("JOINERY_PARTS_INDEX").is_empty():
		_run_parts.call_deferred()
	else:
		_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	root.size = Vector2i(1280, 800)
	_workspace = WORKSPACE.new()
	root.add_child(_workspace)
	_workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var path := OS.get_environment("JOINERY_WORKSPACE_INDEX")
	var payload: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	_workspace._apply_data(payload)
	var top_level := 0
	for asset: Dictionary in payload["assets"]:
		top_level += 1 if str(asset.get("part_of", "")).is_empty() else 0
	_check(top_level < payload["assets"].size(), "The index must fold derived parts")
	_check(_workspace._rows.size() == top_level, "Every source asset must be listed once")
	_check_groups()
	_workspace.select_tool("blender")
	for asset: Dictionary in _workspace._rows:
		_check(asset["tool"] == "blender", "Creator filter must use ownership")
	_check(
		not _workspace._matches_tool(
			{"tool": "blender", "open_options": [{"tool": "krita"}]}, "krita"
		),
		"Compatible viewers must not count as creators"
	)
	for index: int in _workspace._assets.item_count:
		_check(not ".blend" in _workspace._assets.get_item_text(index), "Hide file extensions")
	_workspace._search.text = "stone tiles"
	_workspace._filter()
	_check(_workspace._rows.size() == 1, "Baked maps must fold into their source asset")
	_workspace._search.text = "roughness"
	_workspace._filter()
	_check(_workspace._rows.size() == 1, "Search must also match part names")
	_workspace._search.text = "no such asset"
	_workspace._filter()
	_check(_workspace._empty.visible, "A search with no match must say so")
	_workspace._search.clear()
	_workspace.select_resource("res://art/props/door.blend")
	_check(_workspace._current.get("path") == "art/props/door.blend", "Selection must resolve door")
	_check(_workspace._preview.get_child(0) is SubViewportContainer, "Model preview must load")
	var viewport: SubViewport = _workspace._preview.get_child(0).get_child(0)
	var camera := viewport.get_camera_3d()
	await process_frame
	var environments := viewport.find_children("*", "WorldEnvironment", true, false)
	_check(environments.size() == 1, "Preview must have one default environment")
	var environment: Environment = environments[0].environment
	_check(environment.background_mode == Environment.BG_SKY, "Use Godot's empty-scene sky")
	_check(environment.sky.sky_material is ProceduralSkyMaterial, "Do not use the game's panorama")
	var zoom := InputEventMouseButton.new()
	zoom.button_index = MOUSE_BUTTON_WHEEL_UP
	zoom.pressed = true
	var distance := camera.position.z
	var viewer := _workspace._preview.get_child(0) as SubViewportContainer
	zoom.position = viewer.get_global_rect().get_center()
	zoom.global_position = zoom.position
	root.push_input(zoom)
	_check(camera.position.z < distance, "Mouse wheel must reach the model preview")
	zoom.button_index = MOUSE_BUTTON_WHEEL_DOWN
	distance = camera.position.z
	root.push_input(zoom)
	_check(camera.position.z > distance, "Mouse wheel must zoom out")
	var pinch := InputEventMagnifyGesture.new()
	pinch.position = zoom.position
	pinch.factor = 1.2
	distance = camera.position.z
	root.push_input(pinch)
	_check(camera.position.z < distance, "Trackpad pinch must zoom in")
	var pan := InputEventPanGesture.new()
	pan.position = zoom.position
	pan.delta = Vector2(0, 2)
	distance = camera.position.z
	root.push_input(pan)
	_check(camera.position.z > distance, "Trackpad scrolling must zoom out")
	_check(_workspace._preview.get_child_count() == 1, "Model view must have no extra controls")
	_check(viewer.tooltip_text.is_empty(), "Model view must have no instruction tooltip")
	await process_frame
	_workspace.select_resource("res://art/sky/src/dusk_sky.blend")
	_check(
		_workspace._preview.get_child(0) is TextureRect,
		"Render-only sky source must preview its existing rendered image"
	)
	_check(
		_workspace._current["path"] == "art/sky/src/dusk_sky.blend",
		"A rendered preview must preserve the selected source identity"
	)
	_workspace.select_resource("res://art/materials/stone_tiles_albedo.png")
	_check(_workspace._preview.get_child(0) is TextureRect, "Image preview must load")
	_check(_workspace._title.text == "Stone tiles", "A part shows under its source asset")
	_check(_workspace._caption.text == "Material · made in Blender", _workspace._caption.text)
	_check(_workspace._parts_panel.visible, "A source with parts must list them")
	var files: Array[String] = _workspace._parts_panel.file_names()
	_check(files == ["Source", "Albedo", "Roughness"], "Parts: %s" % [files])
	var owner_row: PackedInt32Array = _workspace._assets.get_selected_items()
	_check(
		owner_row.size() == 1 and _workspace._rows[owner_row[0]]["path"].ends_with("tiles.blend"),
		"Selecting a part must highlight its source row"
	)
	var option_tools: Array[String] = []
	for index: int in _workspace._open_with.item_count:
		option_tools.append(str(_workspace._open_with.get_item_metadata(index)["tool"]))
	_check(option_tools.has("krita"), "Krita must be offered without enabling its MCP")
	_workspace.select_resource("res://audio/sfx/door_creak.wav")
	_check(_workspace._audio.stream != null, "Audio preview must load")
	_check(_workspace._audio.stream.get_length() > 0, "Audio preview must have duration")
	_check(
		is_equal_approx(_workspace._audio_panel.length, _workspace._audio.stream.get_length()),
		"Audio transport must match the stream duration"
	)
	_workspace._audio_panel.play_button.pressed.emit()
	_workspace._audio_panel.play_button.pressed.emit()
	_check(_workspace._audio.stream_paused, "Audio preview must pause")
	var focus := load("res://addons/joinery/focus_mode.gd").new() as RefCounted
	var tabs := TabContainer.new()
	var navigation := VBoxContainer.new()
	navigation.name = "JoineryNavigation"
	tabs.add_child(navigation)
	_check(focus._should_keep(tabs), "Workspace navigation must survive Focus mode")
	tabs.free()
	_workspace._audio.play()
	_workspace.select_resource("res://art/props/door.blend")
	_check(not _workspace._audio.playing, "Changing assets must stop audio")
	_check(not _workspace._audio.stream_paused, "Changing assets must clear paused playback")
	# Exercise real CLI loading in the background rather than only injected data.
	_workspace.refresh()
	var deadline := Time.get_ticks_msec() + 15000
	while _workspace._command_job != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(_workspace._command_job == null, "Workspace CLI refresh must finish")
	_check(_workspace._load_error.is_empty(), _workspace._load_error)
	_check(not _workspace._error_box.visible, "A successful refresh shows no error")
	_workspace.select_resource("res://art/props/door.blend")
	var preview_id: int = _workspace._preview.get_child(0).get_instance_id()
	_workspace.refresh()
	while _workspace._command_job != null:
		await process_frame
	_check(
		_workspace._preview.get_child(0).get_instance_id() == preview_id,
		"Unchanged automatic refresh must preserve the preview and camera"
	)
	for button: Node in _workspace.find_children("*", "Button", true, false):
		_check(button.text != "Refresh", "Refresh must be automatic")
	await create_timer(0.2).timeout
	var capture := OS.get_environment("JOINERY_WORKSPACE_CAPTURE")
	if not capture.is_empty():
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png(capture)
	_check_states(payload)
	print("workspace viewer: ", "FAILED" if _failed else "ok")
	_workspace.queue_free()
	await process_frame
	quit(1 if _failed else 0)


## Rows are grouped by kind under section titles.
func _check_groups() -> void:
	var titles: Array[String] = []
	for label: Node in _workspace._assets.find_children("*", "Label", true, false):
		if label.theme_type_variation == "JoinerySection":
			titles.append(label.text)
	_check(titles == ["Models", "Sounds", "Images", "Materials"], "Sections: %s" % [titles])
	_check(not _workspace._assets.get_item_text(0).is_empty(), "Rows have names")


## Open action, missing apps, readiness dots, and empty, loading and error states.
func _check_states(payload: Dictionary) -> void:
	_workspace.select_resource("res://art/props/door.blend")
	var options: Array = _workspace._current["open_options"]
	if bool(options[0]["available"]):
		_check(_workspace._open.text == "Open in " + options[0]["name"], _workspace._open.text)
	else:
		_check(_workspace._unavailable.visible, "A missing app is explained inline")
		_check(not _workspace._open.visible, "A missing app has no disabled button")
		var requested: Array[int] = [0]
		_workspace.setup_requested.connect(func() -> void: requested[0] += 1)
		_workspace._setup_link.pressed.emit()
		_check(requested[0] == 1, "Set up apps must ask for app setup")
	var data := payload.duplicate(true)
	var tools: Array = data["tools"]
	tools[0]["status"] = "needs_docker"
	tools[0]["status_reason"] = "Docker is not running"
	_workspace._apply_data(data)
	var dots := 0
	for index: int in _workspace._tools.item_count:
		var row: Button = _workspace._tools._rows[index]
		dots += 1 if row.dot.visible else 0
		if _workspace._tools.get_item_metadata(index) == tools[0]["id"]:
			_check(row.tooltip_text.contains("Needs Docker"), row.tooltip_text)
	_check(dots == 1, "Only tools that report readiness show a dot")
	var counted: int = _workspace._tools.item_count
	_workspace._apply_data(payload)
	_check(_workspace._tools.item_count == counted, "Tool rows update in place")
	_workspace._data = {}
	_workspace._load_error = ""
	_workspace._update_list_state()
	_check(_workspace._loading.visible, "No data yet shows the loading state")
	_workspace._load_error = "uv: command not found"
	_workspace._update_list_state()
	_check(_workspace._error_box.visible and not _workspace._loading.visible, "Error state")
	_check(_workspace._error_label.text.begins_with("Couldn't load assets."), "Error wording")
	_workspace._load_error = ""
	_workspace._apply_data({"tools": payload["tools"], "assets": [], "issues": []})
	_check(_workspace._empty.text.begins_with("No assets yet."), _workspace._empty.text)


## The parts list of the hammer: groups and counts from the real `joinery parts` command
## (served from its cache), picking a map, the map beside the model, and a click on the model.
func _run_parts() -> void:
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("parts-test-ui.cfg")
		var file := ConfigFile.new()
		file.set_value("appearance", "theme", theme)
		file.save(config)
		Engine.set_meta("joinery_ui_config", config)
	root.size = Vector2i(1280, 800)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_workspace = WORKSPACE.new()
	root.add_child(_workspace)
	_workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var background := load("res://addons/joinery/ui_theme.gd").color(_workspace, "surface") as Color
	var box := StyleBoxFlat.new()
	box.bg_color = background
	backdrop.add_theme_stylebox_override("panel", box)
	var index: Dictionary = JSON.parse_string(
		FileAccess.get_file_as_string(OS.get_environment("JOINERY_PARTS_INDEX"))
	)
	_workspace._apply_data(index)
	_workspace.select_resource("res://art/models/hammer.glb")
	var panel: VBoxContainer = _workspace._parts_panel
	_check(panel.visible, "A Blender model lists its parts")
	var deadline := Time.get_ticks_msec() + 60000
	while panel._job != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(panel._error.is_empty(), "joinery parts failed: " + panel._error)
	var counts: Dictionary = panel.group_counts()
	_check(int(counts.get("object", 0)) == 2, "2 objects: %s" % [counts])
	_check(int(counts.get("material", 0)) == 2, "2 materials: %s" % [counts])
	_check(int(counts.get("texture", 0)) == 5, "5 textures: %s" % [counts])
	_check(int(counts.get("shape_key", 0)) == 1, "Shape key Dent: %s" % [counts])
	# Every exported map sits inside its material: the files are the source and the model.
	_check(panel.file_names() == ["Source", "Model"], "Files: %s" % [panel.file_names()])
	var headings: Array[String] = []
	for heading: TreeItem in panel.tree.get_root().get_children():
		if (heading.get_metadata(0) as Dictionary).has("group"):
			headings.append(heading.get_text(0))
			_check(heading.collapsed, "Groups start closed: " + heading.get_text(0))
	_check(headings == ["2 materials", "2 objects", "1 shape key"], "Groups: %s" % [headings])
	var picked: Array[Dictionary] = []
	_workspace.part_selected.connect(func(target: Dictionary) -> void: picked.append(target))
	var rough: TreeItem = null
	var steel: TreeItem = null
	var handle: TreeItem = null
	for item: TreeItem in panel._all_rows(panel.tree.get_root()):
		var meta: Dictionary = item.get_metadata(0)
		if meta.get("image") == "steel_rough":
			rough = item
		elif meta.get("part_kind") == "material" and meta.get("material") == "Steel":
			steel = item
		elif meta.get("part_kind") == "object" and meta.get("object") == "Handle":
			handle = item
	_check(rough != null and rough.get_parent() == steel, "Steel's roughness map is inside Steel")
	var viewer: SubViewportContainer = _workspace._preview.get_child(0)
	if steel != null and handle != null:
		_check(steel.get_text(1) == "3 maps", "Steel holds 3 maps: " + steel.get_text(1))
		panel.tree.set_selected(handle, 0)
		_check(viewer._highlighted == "\nHandle", "An object is tinted on the model")
		panel.tree.set_selected(steel, 0)
		_check(viewer._highlighted == "Steel\n", "A material is tinted on the model")
		picked.clear()
	if rough != null:
		_check(rough.get_text(0) == "Roughness", rough.get_text(0))
		panel.select_part(str(rough.get_metadata(0)["id"]))
		_check(picked.is_empty(), "Selecting from code is no pick")
		_check(not rough.get_parent().collapsed, "Selecting a part opens its group")
		panel.tree.deselect_all()
		panel.tree.set_selected(rough, 0)
	_check(picked.size() == 1, "Selecting a map emits one part")
	if not picked.is_empty():
		var target := picked[0]
		_check(target["label"] == "Roughness · Steel", target["label"])
		_check(target["part_kind"] == "texture" and target["image"] == "steel_rough", "Map part")
		var chip := CHIP.build(
			_workspace, target, func(_p: String) -> void: pass, func() -> void: pass
		)
		var texts: Array[String] = []
		for label: Node in chip.find_children("*", "Label", true, false):
			texts.append((label as Label).text)
		_check(texts.has("Roughness · Steel"), "Chip text: %s" % [texts])
		_check(chip.tooltip_text.contains("Used by Handle, Head.001"), chip.tooltip_text)
		chip.free()
	_check(panel.map_view.visible, "A picked map shows beside the model")
	_check(panel._map_picture.texture != null, "The map preview has the exported pixels")
	await process_frame
	await process_frame
	var hit: Dictionary = viewer.pick_at(viewer.size / 2.0)
	_check(not hit.is_empty(), "A click in the middle of the model hits a part")
	if not hit.is_empty():
		panel._on_model_clicked(hit)
		var last: Dictionary = picked[-1]
		_check(last["object"] == hit["object"], "A model click keeps the object it hit")
		_check(str(last["label"]).ends_with(" · " + str(hit["object"])), last["label"])
	panel.tree.set_selected(rough, 0)
	await create_timer(0.4).timeout
	var capture := OS.get_environment("JOINERY_PARTS_CAPTURE")
	if not capture.is_empty():
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png(capture)
	print("workspace parts: ", "FAILED" if _failed else "ok")
	_workspace.queue_free()
	await process_frame
	quit(1 if _failed else 0)
