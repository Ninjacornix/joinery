@tool
extends VBoxContainer
## One shared viewer for all creation tools. Desktop opening is independent
## of MCP readiness; the Python index owns provenance and launch resolution.

signal catalog_changed(tools: Array, assets: Array)
signal creator_changed(tool: String)
signal asset_selected(asset: Dictionary)

const COMMAND_SCRIPT := preload("res://addons/joinery/command_job.gd")
const AUDIO_SCRIPT := preload("res://addons/joinery/audio_preview.gd")
const CONTEXT_SCRIPT := preload("res://addons/joinery/preview_context.gd")

var _tools: ItemList
var _heading: Label
var _creator_heading: Label
var _search: LineEdit
var _assets: ItemList
var _title: Label
var _status: Label
var _preview: VBoxContainer
var _open_with: OptionButton
var _open: Button
var _audio: AudioStreamPlayer
var _audio_panel: VBoxContainer
var _actions: HBoxContainer
var _command_job: RefCounted
var _job := ""
var _data: Dictionary = {}
var _rows: Array[Dictionary] = []
var _current: Dictionary = {}
var _selected_path := ""
var _stamp := ""
var _refresh_elapsed := 0.0
var _invalidated := false
var _queued_open := PackedStringArray()
var _pending_scene_preview := ""
var _preview_import_changed := false


func _ready() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(split)
	var browser := VBoxContainer.new()
	browser.custom_minimum_size.x = maxf(240, get_theme_default_font_size() * 14)
	split.add_child(browser)
	_heading = Label.new()
	_heading.text = "Assets"
	_heading.set_theme_type_variation("HeaderLarge")
	browser.add_child(_heading)
	_creator_heading = Label.new()
	_creator_heading.text = "Created in"
	browser.add_child(_creator_heading)
	_tools = ItemList.new()
	_tools.custom_minimum_size.y = get_theme_default_font_size() * 6
	_tools.item_selected.connect(func(_index: int) -> void: _filter())
	browser.add_child(_tools)
	_search = LineEdit.new()
	_search.placeholder_text = "Find an asset…"
	_search.text_changed.connect(func(_text: String) -> void: _filter())
	browser.add_child(_search)
	_assets = ItemList.new()
	_assets.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_assets.item_selected.connect(_select)
	browser.add_child(_assets)
	var body := VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(body)
	var header := HBoxContainer.new()
	body.add_child(header)
	_title = Label.new()
	_title.text = "Choose an asset"
	_title.set_theme_type_variation("HeaderLarge")
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(_title)
	_actions = HBoxContainer.new()
	_actions.hide()
	header.add_child(_actions)
	_open_with = OptionButton.new()
	_open_with.item_selected.connect(_update_open)
	_actions.add_child(_open_with)
	_open = Button.new()
	_open.text = "Open"
	_open.disabled = true
	_open.pressed.connect(_open_source)
	_actions.add_child(_open)
	_preview = VBoxContainer.new()
	_preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(_preview)
	_status = Label.new()
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.hide()
	body.add_child(_status)
	_audio = AudioStreamPlayer.new()
	add_child(_audio)


func _exit_tree() -> void:
	if _command_job != null:
		_command_job.cancel()
		_command_job = null


func _process(delta: float) -> void:
	if not _pending_scene_preview.is_empty() and ResourceLoader.exists(_pending_scene_preview):
		var path := _pending_scene_preview
		_pending_scene_preview = ""
		_show_preview(path, _preview_import_changed)
		_preview_import_changed = false
	_refresh_elapsed += delta
	if _command_job == null:
		if not _queued_open.is_empty():
			var arguments := _queued_open
			_queued_open = PackedStringArray()
			_start_job("open", arguments)
			return
		if is_visible_in_tree() and (_invalidated or _refresh_elapsed >= 2.0):
			refresh()
		return
	var result: Dictionary = _command_job.poll()
	if result.is_empty():
		return
	_command_job = null
	_refresh_elapsed = 0.0
	if int(result["code"]) != 0:
		_show_error(
			"Could not load assets." if _job == "refresh" else "Could not open application."
		)
		_status.tooltip_text = (
			str(result["error"]) if result.get("error") else str(result["output"])
		)
		return
	var payload: Variant = JSON.parse_string(str(result["output"]))
	if not payload is Dictionary:
		_show_error("Could not read workspace data.")
		return
	_status.hide()
	if _job == "refresh":
		_stamp = str(payload.get("stamp", ""))
		if (
			not bool(payload.get("unchanged", false))
			and (payload != _data or _preview_import_changed)
		):
			_apply_data(payload)
			_preview_import_changed = false
	else:
		invalidate()


func _show_error(message: String) -> void:
	_status.text = message
	_status.show()


func invalidate() -> void:
	# Keep the last confirmed revision while requesting fresh data. Filesystem
	# notifications also cover editor/cache writes that do not change assets.
	_invalidated = true


func resources_reimported(paths: PackedStringArray) -> void:
	# A real import can change a loaded scene even when its raw file and catalog
	# metadata are unchanged (for example import settings or external textures).
	if paths.has("res://" + _selected_path):
		_preview_import_changed = true
	invalidate()


func _start_job(job: String, arguments: PackedStringArray) -> void:
	if _command_job != null:
		if job == "open":
			_queued_open = arguments
		return
	_job = job
	var engine_dir := OS.get_environment("JOINERY_HOME")
	if engine_dir.is_empty():
		engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", engine_dir, "joinery"]
	)
	args.append_array(arguments)
	args.append_array(["--project", ProjectSettings.globalize_path("res://")])
	_command_job = COMMAND_SCRIPT.new()
	var error: Error = _command_job.start("uv", args)
	if error != OK:
		_command_job = null
		_show_error("Could not start workspace task: %s" % error_string(error))


func refresh() -> void:
	if _command_job != null:
		return
	var since := "" if _invalidated else _stamp
	_refresh_elapsed = 0.0
	_invalidated = false
	# The CLI defaults to an empty stamp. Godot's native Windows argv builder
	# drops empty arguments, so omit this optional flag until a stamp exists.
	var arguments := PackedStringArray(["workspace"])
	if not since.is_empty():
		arguments.append_array(["--since", since])
	_start_job("refresh", arguments)


func _apply_data(data: Dictionary) -> void:
	var selected_tool := _tool_id()
	_data = data
	_tools.clear()
	_tools.add_item("All tools")
	_tools.set_item_metadata(0, "")
	_tools.select(0)
	for tool: Dictionary in data.get("tools", []):
		var count := 0
		for asset: Dictionary in data.get("assets", []):
			if _matches_tool(asset, str(tool["id"])):
				count += 1
		if count == 0 and not bool(tool.get("enabled", false)):
			continue
		_tools.add_item("%s (%d)" % [tool["name"], count])
		var index := _tools.item_count - 1
		_tools.set_item_metadata(index, tool["id"])
		if str(tool["id"]) == selected_tool:
			_tools.select(index)
	for category: String in ["__imports", "__unassigned"]:
		var count := 0
		for asset: Dictionary in data.get("assets", []):
			if _matches_tool(asset, category):
				count += 1
		if count > 0:
			var label := "Imported" if category == "__imports" else "Unassigned"
			_tools.add_item("%s (%d)" % [label, count])
			var index := _tools.item_count - 1
			_tools.set_item_metadata(index, category)
			if selected_tool == category:
				_tools.select(index)
	_filter()
	catalog_changed.emit(data.get("tools", []), data.get("assets", []))
	creator_changed.emit(_tool_id())


func _matches_tool(asset: Dictionary, tool: String) -> bool:
	if tool == "__imports":
		return str(asset.get("origin", "")) == "human-import"
	if tool == "__unassigned":
		return str(asset.get("tool", "")).is_empty() and asset.get("origin") != "human-import"
	return tool.is_empty() or str(asset.get("tool", "")) == tool


func _tool_id() -> String:
	var selected := _tools.get_selected_items()
	return str(_tools.get_item_metadata(selected[0])) if not selected.is_empty() else ""


func use_rail_navigation() -> void:
	_tools.hide()
	_creator_heading.hide()


func _display_name(path: String) -> String:
	return path.get_file().get_basename().replace("_", " ").replace("-", " ").capitalize()


func _asset_kind(path: String) -> String:
	match path.get_extension().to_lower():
		"blend", "glb", "gltf", "tscn":
			if (
				path.get_extension().to_lower() == "blend"
				and not ResourceLoader.exists("res://" + path)
			):
				return "Source"
			return "Model"
		"png", "jpg", "jpeg", "webp", "bmp", "tga", "svg":
			return "Image"
		"wav", "ogg", "mp3", "flac", "aiff":
			return "Audio"
	return "Source"


func _filter() -> void:
	_assets.clear()
	_rows.clear()
	var tool := _tool_id()
	creator_changed.emit(tool)
	_heading.text = "All assets"
	for entry: Dictionary in _data.get("tools", []):
		if str(entry["id"]) == tool:
			_heading.text = str(entry["name"])
	var query := _search.text.strip_edges().to_lower()
	for asset: Dictionary in _data.get("assets", []):
		var path := str(asset["path"])
		var matches_search := query.is_empty() or query in _display_name(path).to_lower()
		if _matches_tool(asset, tool) and matches_search:
			_rows.append(asset)
	_rows.sort_custom(
		func(left: Dictionary, right: Dictionary) -> bool:
			return (
				_display_name(str(left["path"])).naturalnocasecmp_to(
					_display_name(str(right["path"]))
				)
				< 0
			)
	)
	var label_counts: Dictionary = {}
	for asset: Dictionary in _rows:
		var label := _display_name(str(asset["path"]))
		label_counts[label] = int(label_counts.get(label, 0)) + 1
	for asset: Dictionary in _rows:
		var path := str(asset["path"])
		var label := _display_name(path)
		if int(label_counts[label]) > 1:
			label += " · " + _asset_kind(path)
		_assets.add_item(label)
		_assets.set_item_tooltip(_assets.item_count - 1, path)
	_clear_preview()
	_current = {}
	_open.disabled = true
	_actions.hide()
	_open_with.clear()
	_title.text = "No assets for this tool" if _rows.is_empty() else "Choose an asset"
	for index: int in _rows.size():
		if str(_rows[index]["path"]) == _selected_path:
			_assets.select(index)
			_select(index, false)
			break


func select_resource(path: String) -> void:
	var selected := path.trim_prefix("res://").split("::")[0]
	# The native filesystem dock can repeat its selection while rescanning. An
	# already-rendered model keeps its camera; pending imports still retry below.
	if (
		selected == _selected_path
		and _current.get("path") == selected
		and _preview.get_child_count() > 0
		and _preview.get_child(0) is SubViewportContainer
	):
		return
	if selected != _selected_path:
		_preview_import_changed = false
	_selected_path = selected
	if _data.is_empty():
		return
	_tools.select(0)
	_filter()


func select_tool(tool: String) -> void:
	for index: int in _tools.item_count:
		if str(_tools.get_item_metadata(index)) == tool:
			_tools.select(index)
			_search.clear()
			_filter()
			if not _rows.is_empty() and _current.is_empty():
				_assets.select(0)
				_select(0)
			elif not _current.is_empty():
				asset_selected.emit(_current.duplicate(true))
			return


func _select(index: int, notify: bool = true) -> void:
	if str(_rows[index]["path"]) != _selected_path:
		_preview_import_changed = false
	_current = _rows[index]
	_selected_path = str(_current["path"])
	_title.text = _display_name(_selected_path)
	_actions.show()
	_open_with.clear()
	for option: Dictionary in _current.get("open_options", []):
		_open_with.add_item(str(option["name"]))
		var item := _open_with.item_count - 1
		_open_with.set_item_metadata(item, option)
		if str(option["tool"]) == _tool_id():
			_open_with.select(item)
		# Keep unavailable apps discoverable, with their configuration hint.
		var hint := str(option["reason"]) if not option["available"] else str(option["path"])
		_open_with.set_item_tooltip(item, hint)
	_update_open(_open_with.selected)
	_show_preview("res://" + _selected_path, _preview_import_changed)
	_preview_import_changed = false
	if notify:
		asset_selected.emit(_current.duplicate(true))


func _update_open(index: int) -> void:
	_open_with.visible = _open_with.item_count > 1
	if _open_with.item_count == 0:
		_open.disabled = true
		_open.text = "Open"
		_open.tooltip_text = "No compatible application is configured."
		return
	var option: Dictionary = _open_with.get_item_metadata(index)
	_open.disabled = not bool(option["available"])
	_open.tooltip_text = str(option["path"]) if option["available"] else str(option["reason"])
	_open.text = "Open" if _open_with.visible else "Open in " + str(option["name"])


func _open_source() -> void:
	if _current.is_empty() or _open_with.item_count == 0:
		return
	var option: Dictionary = _open_with.get_item_metadata(_open_with.selected)
	_start_job(
		"open",
		PackedStringArray(
			[
				"open",
				str(_current["path"]),
				"--tool",
				str(option["tool"]),
			]
		)
	)


func _clear_preview() -> void:
	_pending_scene_preview = ""
	_audio.stop()
	_audio.stream_paused = false
	_audio.stream = null
	_audio_panel = null
	_audio.pitch_scale = 1.0
	_audio.volume_db = 0
	for child: Node in _preview.get_children():
		_preview.remove_child(child)
		child.queue_free()


func _show_preview(path: String, reimported: bool = false) -> void:
	_clear_preview()
	var cache_mode := (
		ResourceLoader.CACHE_MODE_REPLACE if reimported else ResourceLoader.CACHE_MODE_REUSE
	)
	if not FileAccess.file_exists(path):
		var missing := Label.new()
		missing.text = "This file is missing."
		_preview.add_child(missing)
		return
	path = _preview_path(path)
	var extension := path.get_extension().to_lower()
	if extension in ["png", "jpg", "jpeg", "webp", "bmp", "tga", "svg"]:
		var image := Image.load_from_file(ProjectSettings.globalize_path(path))
		if image != null and not image.is_empty():
			_show_image(image)
			return
	if extension in ["kra", "ora"]:
		var archive := ZIPReader.new()
		if archive.open(ProjectSettings.globalize_path(path)) == OK:
			var image := Image.new()
			var error := image.load_png_from_buffer(archive.read_file("mergedimage.png"))
			archive.close()
			if error == OK:
				_show_image(image)
				return
	if extension in ["wav", "ogg", "mp3"] and ResourceLoader.exists(path):
		_audio.stream = ResourceLoader.load(path, "", cache_mode) as AudioStream
		if _audio.stream != null:
			_audio.stream = _audio.stream.duplicate() as AudioStream
			if _audio.stream is AudioStreamWAV:
				_audio.stream.loop_mode = AudioStreamWAV.LOOP_DISABLED
			elif _audio.stream is AudioStreamOggVorbis or _audio.stream is AudioStreamMP3:
				_audio.stream.loop = false
			_show_audio()
			return
	if extension in ["blend", "glb", "gltf", "tscn"] and ResourceLoader.exists(path):
		var scene := ResourceLoader.load(path, "", cache_mode) as PackedScene
		if scene != null:
			_show_scene(scene)
			return
	if extension in ["scd", "sc", "gd", "svg", "ptex"] and FileAccess.file_exists(path):
		var text := TextEdit.new()
		text.editable = false
		text.text = FileAccess.get_file_as_string(path)
		text.size_flags_vertical = Control.SIZE_EXPAND_FILL
		_preview.add_child(text)
		return
	var note := Label.new()
	if extension in ["glb", "gltf"] and not ResourceLoader.exists(path):
		_pending_scene_preview = path
		note.text = "Importing model…"
	else:
		note.text = "Preview unavailable for this format. Open it in its desktop app."
	note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_preview.add_child(note)


func _preview_path(path: String) -> String:
	var extension := path.get_extension().to_lower()
	var native := extension in ["blend", "glb", "gltf", "tscn"]
	if native and ResourceLoader.exists(path):
		return path
	if (
		not native
		and (
			extension
			in [
				"png",
				"jpg",
				"jpeg",
				"webp",
				"bmp",
				"tga",
				"svg",
				"kra",
				"ora",
				"wav",
				"ogg",
				"mp3",
				"scd",
				"sc",
				"gd",
				"ptex",
			]
		)
	):
		return path
	for relative: String in _current.get("previews", []):
		var preview := "res://" + relative
		if FileAccess.file_exists(preview):
			return preview
	return path


func _show_image(image: Image) -> void:
	var view := TextureRect.new()
	view.texture = ImageTexture.create_from_image(image)
	view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	view.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(view)


func _show_audio() -> void:
	var above := Control.new()
	above.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(above)
	_audio_panel = AUDIO_SCRIPT.new()
	_audio_panel.player = _audio
	_preview.add_child(_audio_panel)
	var below := Control.new()
	below.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(below)


func _show_scene(scene: PackedScene) -> void:
	var container := SubViewportContainer.new()
	container.stretch = true
	container.mouse_target = false
	container.mouse_force_pass_scroll_events = false
	container.custom_minimum_size = Vector2(200, 200)
	container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(container)
	var viewport := SubViewport.new()
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	container.add_child(viewport)
	var root := Node3D.new()
	viewport.add_child(root)
	var instance := scene.instantiate(PackedScene.GEN_EDIT_STATE_DISABLED)
	root.add_child(instance)
	var bounds := AABB()
	var meshes: Array[Node] = instance.find_children("*", "MeshInstance3D", true, false)
	if instance is MeshInstance3D:
		meshes.append(instance)
	var found := false
	for node: Node in meshes:
		var mesh := node as MeshInstance3D
		var box: AABB = mesh.global_transform * mesh.get_aabb()
		bounds = bounds.merge(box) if found else box
		found = true
	var center := bounds.get_center()
	var radius := maxf(bounds.size.length() * 0.5, 0.5)
	var pivot := Node3D.new()
	root.add_child(pivot)
	pivot.position = center
	pivot.rotation = Vector3(-0.4, PI / 4.0, 0)
	var camera := Camera3D.new()
	pivot.add_child(camera)
	camera.fov = 42
	camera.position.z = radius * 2.0
	camera.near = maxf(radius * 0.001, 0.001)
	camera.far = maxf(radius * 20.0, 100.0)
	camera.current = true
	container.gui_input.connect(_orbit_preview.bind(pivot, camera, radius))
	var camera_id := camera.get_instance_id()
	var pivot_id := pivot.get_instance_id()
	var viewport_id := viewport.get_instance_id()
	viewport.size_changed.connect(_frame_model.bind(camera_id, pivot_id, viewport_id, bounds))
	_frame_model(camera_id, pivot_id, viewport_id, bounds)
	var context: RefCounted = CONTEXT_SCRIPT.new()
	context.apply(root, camera)


func _frame_model(camera_id: int, pivot_id: int, viewport_id: int, bounds: AABB) -> void:
	var camera := instance_from_id(camera_id) as Camera3D
	var pivot := instance_from_id(pivot_id) as Node3D
	var viewport := instance_from_id(viewport_id) as SubViewport
	if not is_instance_valid(camera) or not is_instance_valid(viewport):
		return
	if not is_instance_valid(pivot) or not pivot.is_inside_tree():
		return
	var local_bounds: AABB = pivot.global_transform.affine_inverse() * bounds
	var half := local_bounds.size * 0.5
	var aspect := float(maxi(viewport.size.x, 1)) / maxi(viewport.size.y, 1)
	var tangent := tan(deg_to_rad(camera.fov * 0.5))
	var fit := maxf(half.y / tangent, half.x / (tangent * aspect))
	camera.position.z = maxf((fit + half.z) * 1.15, 0.1)


func _orbit_preview(event: InputEvent, pivot: Node3D, camera: Camera3D, radius: float) -> void:
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_LEFT:
		pivot.rotation.y -= event.relative.x * 0.01
		pivot.rotation.x = clampf(pivot.rotation.x - event.relative.y * 0.01, -1.5, 1.5)
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_zoom_model(camera, radius, pow(0.9, event.factor))
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_zoom_model(camera, radius, pow(1.1, event.factor))
	if event is InputEventMagnifyGesture and event.factor > 0:
		_zoom_model(camera, radius, 1.0 / event.factor)
	if event is InputEventPanGesture:
		_zoom_model(camera, radius, exp(event.delta.y * 0.05))


func _zoom_model(camera: Camera3D, radius: float, factor: float) -> void:
	camera.position.z = clampf(camera.position.z * factor, radius * 0.05, radius * 30.0)
