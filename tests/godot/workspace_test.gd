extends SceneTree
## Run against testbed after its normal editor import:
## godot --path testbed --script ../tests/godot/workspace_test.gd
## Set JOINERY_WORKSPACE_INDEX to a `joinery workspace` JSON report.
## Optional JOINERY_WORKSPACE_CAPTURE saves the real viewer for visual QA.

var _workspace: VBoxContainer
var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	root.size = Vector2i(1280, 800)
	_workspace = load("res://addons/joinery/tool_workspace.gd").new() as VBoxContainer
	root.add_child(_workspace)
	_workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var path := OS.get_environment("JOINERY_WORKSPACE_INDEX")
	var payload: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	_workspace._apply_data(payload)
	_check(_workspace._rows.size() == payload["assets"].size(), "All assets must be listed")
	_workspace.select_tool("blender")
	for asset: Dictionary in _workspace._rows:
		_check(asset["tool"] == "blender", "Creator filter must use ownership")
	_check(
		not _workspace._matches_tool({"tool": "blender", "open_options": [{"tool": "krita"}]}, "krita"),
		"Compatible viewers must not count as creators"
	)
	for index: int in _workspace._assets.item_count:
		_check(not ".blend" in _workspace._assets.get_item_text(index), "Hide file extensions")
	_workspace._search.text = "stone tiles"
	_workspace._filter()
	_check(_workspace._rows.size() == 3, "Search must match readable asset names")
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
	var option_tools: Array[String] = []
	for index: int in _workspace._open_with.item_count:
		option_tools.append(str(_workspace._open_with.get_item_metadata(index)["tool"]))
	_check(option_tools.has("krita"), "Krita must be offered without enabling its MCP")
	_workspace.select_resource("res://audio/sfx/door_creak.wav")
	_check(_workspace._audio.stream != null, "Audio preview must load")
	_check(_workspace._audio.stream.get_length() > 0, "Audio preview must have duration")
	_check(
		is_equal_approx(
			_workspace._audio_panel.seek_slider.max_value, _workspace._audio.stream.get_length()
		),
		"Audio seek control must match the stream duration"
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
	_check(not _workspace._status.text.begins_with("Could not"), _workspace._status.text)
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
	print("workspace viewer: ", "FAILED" if _failed else "ok")
	_workspace.queue_free()
	await process_frame
	quit(1 if _failed else 0)
