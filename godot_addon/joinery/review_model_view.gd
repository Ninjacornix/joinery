@tool
extends HBoxContainer
## A model in review as live 3D: the original and the change side by side, each in its own small
## SubViewport, loaded at runtime from the staged GLB/glTF files the review item names
## ([code]before_model[/code], [code]after_model[/code]) with GLTFDocument. Nothing is imported
## into the project or written to it. Both cameras share one orbit, so turning or zooming one
## side turns the other: drag to orbit, the wheel or a pinch to zoom, double-click to reset.
## A neutral studio light and ground; the camera frames both models' bounds together, so a
## change in size shows.
##
## The renders are shown first (review_media.gd); the files load on a worker thread and the live
## views replace the renders only when they are ready within [constant BUDGET] seconds. A file
## that fails to load, or loads late, leaves the renders in place. [method release] frees the
## viewports (the item changed, or the panel was hidden).
##
## An animation plays the same way: [method set_time] poses both models at one time, driven by
## the review's shared transport.
##
## A click (a press and release without dragging) picks the part under the pointer, as on
## the asset page (part_pick.gd): on a character, the body region there. [method
## highlight_region] tints a region on both sides.

## The live views are on screen.
signal loaded
## The files did not load in time, or not at all: keep the renders.
signal failed
## A key was pressed while a view had focus.
signal key_pressed(event: InputEventKey)
## A click hit a part of one side's model (0 before, 1 after): part_pick.gd's hit.
signal part_clicked(hit: Dictionary, side: int)

const UI_THEME := preload("ui_theme.gd")
const PICK := preload("part_pick.gd")
const PREVIEW := preload("model_preview.gd")
## A press that moves less than this many pixels before release is a click, not an orbit.
const CLICK_SLOP := 4.0
## Seconds the live views may take to load before the renders stay.
const BUDGET := 1.0
## Orbit radians per dragged pixel.
const ORBIT_SPEED := 0.01
## Zoom range, as multiples of the distance that frames the models.
const ZOOM_RANGE := Vector2(0.25, 4.0)
## The starting view: a three-quarter angle from slightly above.
const HOME := Vector2(PI / 5.0, -0.35)
## Opacity of the side that is not in focus.
const DIMMED := 0.35
const FOV := 35.0

## Orbit angles shared by both cameras (yaw, pitch) and the zoom factor.
var orbit := HOME
var zoom := 1.0
## Load on a worker thread (tests may load inline).
var threaded := true
## The area whose width this may take (the review's stage). Sizing from it, not from this
## control's own width, lets the stage shrink: widths set from our own size would hold it.
var room: Control:
	set(value):
		room = value
		if value != null and not value.resized.is_connected(_refit):
			value.resized.connect(_refit)
var _views: Array[SubViewportContainer] = []
var _cameras: Array[Camera3D] = []
var _players: Array[AnimationPlayer] = []
var _actions: Array[String] = []
var _task := -1
var _paths: Array[String] = []
var _results: Array = []
var _waited := 0.0
var _stale := false
var _bounds := AABB()
var _side := "compare"
## Each view's model root, and which file side (0 before, 1 after) it shows.
var _scenes: Array[Node] = []
var _sides: Array[int] = []
var _press_at := Vector2.INF


func _init() -> void:
	theme_type_variation = "JoineryRow"
	alignment = BoxContainer.ALIGNMENT_CENTER
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	accessibility_name = "The model before and after, in 3D"
	set_process(false)
	resized.connect(_fit)
	hide()


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE:
		_finish_task()
		_free_results()


## Load [param before] and [param after] (absolute GLB/glTF paths, "" for a side that has
## none). [param actions] names the animation each side plays ("" for none).
func load_models(before: String, after: String, actions: Array[String] = ["", ""]) -> void:
	release()
	_paths = [before, after]
	_actions = actions.duplicate()
	if before.is_empty() and after.is_empty():
		failed.emit()
		return
	_stale = false
	_waited = 0.0
	_results = [null, null]
	if threaded:
		_task = WorkerThreadPool.add_task(_load_all, false, "Load the review's models")
		set_process(true)
	else:
		_load_all()
		_on_loaded()


## True while the files are still loading.
func is_loading() -> bool:
	return _task >= 0 and not _stale


## True when the live views are on screen.
func is_live() -> bool:
	return not _views.is_empty()


## Free the viewports and forget the models. A load still running finishes on its own and is
## thrown away.
func release() -> void:
	_stale = true
	for view: SubViewportContainer in _views:
		remove_child(view)
		view.queue_free()
	_views.clear()
	_cameras.clear()
	_players.clear()
	_scenes.clear()
	_sides.clear()
	hide()
	if _task < 0:
		_free_results()


## Dim the side not in focus ("before", "after"; "compare" shows both alike).
func set_side(value: String) -> void:
	_side = value
	if _views.size() < 2:
		return
	_views[0].modulate.a = DIMMED if value == "after" else 1.0
	_views[1].modulate.a = DIMMED if value == "before" else 1.0


## Pose each animated model at [param seconds] (looped over its own length).
func set_time(seconds: float) -> void:
	for player: AnimationPlayer in _players:
		var length := _length_of(player)
		if length > 0.0:
			player.seek(fposmod(seconds, length), true)


## The longest animation shown, in seconds (0 without one).
func animation_length() -> float:
	var longest := 0.0
	for player: AnimationPlayer in _players:
		longest = maxf(longest, _length_of(player))
	return longest


## Seconds of the action [param player] is set to (0 without one).
static func _length_of(player: AnimationPlayer) -> float:
	if player == null or player.assigned_animation.is_empty():
		return 0.0
	var animation := player.get_animation(player.assigned_animation)
	return animation.length if animation != null else 0.0


## Each side's view rectangle in this control's coordinates (before, after); an empty
## rectangle for a side not shown.
func side_rects() -> Array[Rect2]:
	var rects: Array[Rect2] = [Rect2(), Rect2()]
	var index := 0
	for key: int in 2:
		if _paths.size() > key and not _paths[key].is_empty() and index < _views.size():
			var view := _views[index]
			rects[key] = Rect2(view.position, view.size)
			index += 1
	return rects


## The viewports on screen, for tests.
func views() -> Array[SubViewportContainer]:
	return _views


## The camera of each view, for tests.
func cameras() -> Array[Camera3D]:
	return _cameras


## Back to the starting angle and distance.
func reset_view() -> void:
	orbit = HOME
	zoom = 1.0
	_aim()


## Orbit both cameras by a drag of [param relative] pixels.
func orbit_by(relative: Vector2) -> void:
	orbit.x -= relative.x * ORBIT_SPEED
	orbit.y = clampf(orbit.y - relative.y * ORBIT_SPEED, -1.4, 1.4)
	_aim()


## Zoom both cameras by [param factor] (below 1 moves in).
func zoom_by(factor: float) -> void:
	zoom = clampf(zoom * factor, ZOOM_RANGE.x, ZOOM_RANGE.y)
	_aim()


func _process(delta: float) -> void:
	if _task < 0:
		set_process(false)
		return
	_waited += delta
	if not WorkerThreadPool.is_task_completed(_task):
		return
	_finish_task()
	set_process(false)
	_on_loaded()


func _finish_task() -> void:
	if _task >= 0:
		WorkerThreadPool.wait_for_task_completion(_task)
		_task = -1


## Runs on the worker thread: read and parse each file. The scenes are made from the parsed
## state on the main thread ([method _on_loaded]), where their meshes are created.
func _load_all() -> void:
	for index: int in 2:
		var path := _paths[index] if index < _paths.size() else ""
		_results[index] = parse(path) if not path.is_empty() else null


## A GLB/glTF file parsed, or null. Textures stay in memory: nothing is extracted to or
## imported into the project.
static func parse(path: String) -> GLTFState:
	if not _looks_like_gltf(path):
		return null
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	# In the editor, extracting would write the textures into the project and import them.
	state.handle_binary_image_mode = GLTFState.HANDLE_BINARY_IMAGE_MODE_EMBED_AS_UNCOMPRESSED
	if document.append_from_file(path, state) != OK:
		return null
	return state


## A GLB/glTF file as a new scene (not in the tree), or null.
static func load_scene(path: String) -> Node:
	var state := parse(path)
	return scene_of(state) if state != null else null


## The scene of a parsed file, ready to draw. In the editor GLTFDocument leaves its meshes as
## ImporterMeshInstance3D (for the import pipeline), which draw nothing: they become
## MeshInstance3D here, keeping their skin and skeleton.
static func scene_of(state: GLTFState) -> Node:
	var scene := GLTFDocument.new().generate_scene(state)
	if scene == null:
		return null
	for node: Node in scene.find_children("*", "ImporterMeshInstance3D", true, false):
		var source := node as ImporterMeshInstance3D
		var shown := MeshInstance3D.new()
		shown.name = source.name
		shown.transform = source.transform
		shown.visible = source.visible
		if source.mesh != null:
			shown.mesh = source.mesh.get_mesh()
		shown.skin = source.skin
		shown.skeleton = source.skeleton_path
		# extras (joinery_name, joinery_regions) name the parts a click picks.
		for key: StringName in source.get_meta_list():
			shown.set_meta(key, source.get_meta(key))
		source.replace_by(shown)
		shown.owner = scene
		source.free()
	return scene


## A GLB starts with "glTF"; a glTF file is JSON. Anything else is not parsed at all.
static func _looks_like_gltf(path: String) -> bool:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null or file.get_length() < 12:
		return false
	var head := file.get_buffer(4)
	if path.get_extension().to_lower() == "gltf":
		file.seek(0)
		return file.get_buffer(64).get_string_from_utf8().strip_edges().begins_with("{")
	return head.get_string_from_ascii() == "glTF"


func _on_loaded() -> void:
	var late := threaded and _waited > BUDGET
	var none := _results.all(func(state: Variant) -> bool: return state == null)
	if _stale or late or none:
		var gave_up := not _stale
		_results = []
		if gave_up:
			failed.emit()
		return
	for index: int in _results.size():
		var state := _results[index] as GLTFState
		_results[index] = scene_of(state) if state != null else null
	_build()
	loaded.emit()


func _free_results() -> void:
	# Parsed states are freed with the array; only scenes made from them need freeing.
	for scene: Variant in _results:
		if scene is Node and is_instance_valid(scene) and not (scene as Node).is_inside_tree():
			(scene as Node).free()
	_results = []


func _build() -> void:
	_stale = false
	var found := false
	for index: int in 2:
		var scene := _results[index] as Node
		if scene == null:
			continue
		var view := _make_view(scene, _actions[index] if index < _actions.size() else "")
		_scenes.append(scene)
		_sides.append(index)
		var box := _scene_bounds(scene)
		if box.size != Vector3.ZERO:
			_bounds = _bounds.merge(box) if found else box
			found = true
		_views.append(view)
	_results = []
	if not found:
		_bounds = AABB(Vector3(-0.5, 0.0, -0.5), Vector3.ONE)
	for view: SubViewportContainer in _views:
		_ground(view)
	show()
	_fit()
	reset_view()
	set_side(_side)


func _make_view(scene: Node, action: String) -> SubViewportContainer:
	var view := SubViewportContainer.new()
	view.stretch = true
	view.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	view.mouse_filter = Control.MOUSE_FILTER_STOP
	view.mouse_force_pass_scroll_events = false
	view.mouse_default_cursor_shape = Control.CURSOR_DRAG
	# A click on the model gives the review its keys; hovering or the wheel never takes focus.
	view.focus_mode = Control.FOCUS_CLICK
	view.tooltip_text = (
		"Click a part to make it the message's target. Drag to turn both models, scroll to"
		+ " zoom, double-click to reset"
	)
	view.gui_input.connect(_on_input)
	view.gui_input.connect(_on_click.bind(view))
	add_child(view)
	var viewport := SubViewport.new()
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_WHEN_VISIBLE
	viewport.msaa_3d = Viewport.MSAA_2X
	viewport.gui_disable_input = true
	view.add_child(viewport)
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.62, 0.62, 0.64)
	environment.ambient_light_energy = 0.9
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var world := WorldEnvironment.new()
	world.environment = environment
	viewport.add_child(world)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-50.0, 35.0, 0.0)
	key.light_energy = 1.1
	key.shadow_enabled = true
	viewport.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-20.0, -140.0, 0.0)
	fill.light_energy = 0.35
	viewport.add_child(fill)
	viewport.add_child(scene)
	var camera := Camera3D.new()
	camera.fov = FOV
	camera.current = true
	viewport.add_child(camera)
	_cameras.append(camera)
	var player := _animation_player(scene, action)
	_players.append(player)
	return view


## The scene's AnimationPlayer set to [param action], paused (the transport poses it), or null.
static func _animation_player(scene: Node, action: String) -> AnimationPlayer:
	if action.is_empty():
		return null
	var players := scene.find_children("*", "AnimationPlayer", true, false)
	for node: Node in players:
		var player := node as AnimationPlayer
		for name: StringName in player.get_animation_list():
			if str(name) == action or str(name).get_slice("/", 1) == action:
				player.play(name)
				player.pause()
				return player
	return null


## A ground under the models in each view, and the frame's own background colour.
func _ground(view: SubViewportContainer) -> void:
	var viewport := view.get_child(0) as SubViewport
	var back := UI_THEME.color(self, "surface_sunken") if is_inside_tree() else Color("2e3034")
	var ink := UI_THEME.color(self, "text") if is_inside_tree() else Color.WHITE
	for node: Node in viewport.get_children():
		if node is WorldEnvironment:
			(node as WorldEnvironment).environment.background_color = back
	var radius := maxf(_bounds.size.length() * 0.5, 0.1)
	var plane := PlaneMesh.new()
	plane.size = Vector2.ONE * radius * 6.0
	var material := StandardMaterial3D.new()
	material.albedo_color = back.lerp(ink, 0.08)
	material.roughness = 1.0
	plane.material = material
	var floor := MeshInstance3D.new()
	floor.mesh = plane
	floor.position = Vector3(_bounds.get_center().x, _bounds.position.y, _bounds.get_center().z)
	viewport.add_child(floor)


## Merged bounds of the meshes under [param scene], in its own space.
static func _scene_bounds(scene: Node) -> AABB:
	var meshes := scene.find_children("*", "VisualInstance3D", true, false)
	if scene is VisualInstance3D:
		meshes.append(scene)
	var bounds := AABB()
	var found := false
	for node: Node in meshes:
		if not node is GeometryInstance3D:
			continue
		var instance := node as VisualInstance3D
		var box := _to_scene(instance, scene) * instance.get_aabb()
		bounds = bounds.merge(box) if found else box
		found = true
	return bounds


## The transform from [param node] to [param scene] (both may be outside the tree).
static func _to_scene(node: Node, scene: Node) -> Transform3D:
	var result := Transform3D.IDENTITY
	var walk := node
	while walk != null and walk != scene.get_parent():
		if walk is Node3D:
			result = (walk as Node3D).transform * result
		if walk == scene:
			break
		walk = walk.get_parent()
	return result


## Point both cameras at the models from the shared orbit.
func _aim() -> void:
	if _cameras.is_empty():
		return
	var center := _bounds.get_center()
	var radius := maxf(_bounds.size.length() * 0.5, 0.05)
	var aspect := 1.0
	if not _views.is_empty() and _views[0].size.y > 0.0:
		aspect = _views[0].size.x / _views[0].size.y
	var half := deg_to_rad(FOV * 0.5)
	# Far enough for the bounding sphere to fit the narrower of the two angles of view.
	var reach := minf(half, atan(tan(half) * aspect))
	var distance := radius / sin(reach) * 1.15 * zoom
	var direction := Basis.from_euler(Vector3(orbit.y, orbit.x, 0.0)) * Vector3.BACK
	for camera: Camera3D in _cameras:
		camera.near = maxf(distance * 0.01, 0.001)
		camera.far = maxf(distance * 10.0, 10.0)
		camera.position = center + direction * distance
		if camera.is_inside_tree():
			camera.look_at(center, Vector3.UP)
		else:
			camera.basis = Basis.looking_at(-direction, Vector3.UP)


## The width the views share: the stage's when known, else this control's.
func _room_width() -> float:
	return room.size.x if room != null and room.is_inside_tree() else size.x


func _refit() -> void:
	_fit()


## Each view is as tall as the frame and a little wider than square; the views share the
## width when the frame is narrow.
func _fit() -> void:
	if _views.is_empty():
		return
	var gap := float(get_theme_constant("separation"))
	var share := (_room_width() - gap * (_views.size() - 1)) / _views.size()
	var width := floorf(maxf(minf(size.y * 1.25, share), 0.0))
	for view: SubViewportContainer in _views:
		view.custom_minimum_size.x = width
	_aim.call_deferred()


## The part under [param point] in [param view] (its own coordinates), with "side", or {}.
func pick_at(view: SubViewportContainer, point: Vector2) -> Dictionary:
	var index := _views.find(view)
	if index < 0 or index >= _scenes.size():
		return {}
	var camera := _cameras[index]
	var viewport := view.get_child(0) as SubViewport
	var scale := Vector2(viewport.size) / view.size if view.size.x > 0 else Vector2.ONE
	var at := point * scale
	var hit := PICK.pick(
		_scenes[index], camera.project_ray_origin(at), camera.project_ray_normal(at)
	)
	if not hit.is_empty():
		hit["side"] = _sides[index]
	return hit


## Tint the triangles of [param object] (every skinned mesh when "") following [param bones]
## on both sides; empty bones clear it. Returns the tinted triangles over both sides.
func highlight_region(object: String, bones: PackedStringArray) -> int:
	var tinted := 0
	var tint := PREVIEW.region_tint(self)
	for scene: Node in _scenes:
		tinted += PICK.show_region(scene, object, bones, tint)
	return tinted


func _on_click(event: InputEvent, view: SubViewportContainer) -> void:
	var button := event as InputEventMouseButton
	if button == null or button.button_index != MOUSE_BUTTON_LEFT or button.double_click:
		return
	if button.pressed:
		_press_at = button.position
		return
	var moved := _press_at.distance_to(button.position)
	_press_at = Vector2.INF
	if moved > CLICK_SLOP:
		return
	var hit := pick_at(view, button.position)
	if not hit.is_empty():
		part_clicked.emit(hit, int(hit["side"]))


func _on_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key != null and key.pressed:
		key_pressed.emit(key)
		return
	var button := event as InputEventMouseButton
	if button != null and button.pressed:
		if button.double_click and button.button_index == MOUSE_BUTTON_LEFT:
			reset_view()
		elif button.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom_by(pow(0.9, maxf(button.factor, 1.0)))
		elif button.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom_by(pow(1.1, maxf(button.factor, 1.0)))
		else:
			return
		accept_event()
		return
	var motion := event as InputEventMouseMotion
	if motion != null and motion.button_mask & MOUSE_BUTTON_MASK_LEFT:
		orbit_by(motion.relative)
		accept_event()
		return
	var pinch := event as InputEventMagnifyGesture
	if pinch != null and pinch.factor > 0.0:
		zoom_by(1.0 / pinch.factor)
		accept_event()
		return
	var scroll := event as InputEventPanGesture
	if scroll != null:
		zoom_by(exp(scroll.delta.y * 0.05))
		accept_event()
