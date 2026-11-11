extends SceneTree
## A model in review as live 3D (review_media.gd with review_model_view.gd): the renders show
## first, then the staged GLB files load at runtime and replace them, side by side with one
## shared orbit. Drag orbits both, the wheel zooms both, a double-click resets. Nothing is
## written to the project. A file that does not load keeps the renders; the views are freed
## when the item changes or the panel hides. An animated GLB plays its action, posed by the
## shared transport. The GLB files are exported here from small scenes.

const MEDIA := preload("res://addons/joinery/review_media.gd")
const MODEL_VIEW := preload("res://addons/joinery/review_model_view.gd")

var _failed := false
var _folder := ""


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 3) -> void:
	for index: int in count:
		await process_frame


## A box of [param width] metres, exported as a GLB; with [param animated] it carries a "run"
## action that lifts the box over 0.8 s.
func _glb(name: String, width: float, animated := false) -> String:
	var root := Node3D.new()
	root.name = "Door"
	var box := MeshInstance3D.new()
	box.name = "Panel"
	var mesh := BoxMesh.new()
	mesh.size = Vector3(width, 2.0, 0.1)
	box.mesh = mesh
	root.add_child(box)
	box.owner = root
	if animated:
		var player := AnimationPlayer.new()
		player.name = "AnimationPlayer"
		root.add_child(player)
		player.owner = root
		var animation := Animation.new()
		animation.length = 0.8
		var track := animation.add_track(Animation.TYPE_POSITION_3D)
		animation.track_set_path(track, "Panel")
		animation.position_track_insert_key(track, 0.0, Vector3.ZERO)
		animation.position_track_insert_key(track, 0.8, Vector3(0.0, 1.0, 0.0))
		var library := AnimationLibrary.new()
		library.add_animation("run", animation)
		player.add_animation_library("", library)
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	document.append_from_scene(root, state)
	var path := _folder.path_join(name)
	document.write_to_filesystem(state, path)
	root.free()
	return path


func _picture(name: String, tone: Color) -> String:
	var image := Image.create(64, 64, false, Image.FORMAT_RGBA8)
	image.fill(tone)
	var path := _folder.path_join(name)
	image.save_png(path)
	return path


func _project_files() -> PackedStringArray:
	var found := PackedStringArray()
	for file: String in DirAccess.get_files_at("res://"):
		found.append(file)
	for folder: String in DirAccess.get_directories_at("res://"):
		if not folder.begins_with("."):
			found.append(folder + "/")
	return found


func _run() -> void:
	root.size = Vector2i(900, 360)
	_folder = OS.get_user_data_dir().path_join("review-live-test")
	DirAccess.make_dir_recursive_absolute(_folder)
	var before_glb := _glb("door-before.glb", 1.0)
	var after_glb := _glb("door-after.glb", 1.2)
	_check(FileAccess.file_exists(after_glb), "The fixture GLB was written")
	var files := _project_files()
	var media: MEDIA = MEDIA.new()
	root.add_child(media)
	media.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var item := {
		"path": "art/sources/door.blend",
		"media": "image",
		"before": _picture("door-front-a.png", Color(0.4, 0.4, 0.4)),
		"before_back": _picture("door-back-a.png", Color(0.3, 0.3, 0.3)),
		"after": _picture("door-front-b.png", Color(0.6, 0.4, 0.3)),
		"after_back": _picture("door-back-b.png", Color(0.5, 0.3, 0.2)),
		"before_model": before_glb,
		"after_model": after_glb,
	}
	media.show_media([item])
	var live: MODEL_VIEW = media.model_view()
	_check(media.texture() != null, "The render shows first")
	_check(not live.is_live(), "…before the live models are ready")
	var deadline := Time.get_ticks_msec() + 3000
	while not live.is_live() and Time.get_ticks_msec() < deadline:
		await process_frame
	await _frames()
	_check(live.is_live() and live.visible, "The live models replace the renders")
	_check(not media._front.visible, "The renders step aside")
	_check(live.views().size() == 2, "Before and after side by side")
	_check(media.side == "compare", "Both alike once both are on screen: " + media.side)
	_check(media.sides().both_shown, "Each tag sits under its own view")
	for view: SubViewportContainer in live.views():
		var viewport := view.get_child(0) as SubViewport
		_check(viewport.own_world_3d, "Each side in its own world")
		_check(
			(
				viewport.size.y <= media.frame_height() + 1
				and viewport.size.x <= viewport.size.y * 1.3
			),
			"Small viewports: %s in a %.0f frame" % [viewport.size, media.frame_height()]
		)
		_check(view.focus_mode == Control.FOCUS_CLICK, "A view takes focus only when clicked")
		var meshes := viewport.find_children("Panel*", "MeshInstance3D", true, false)
		_check(not meshes.is_empty(), "The door's mesh is in the view")
		var camera := viewport.get_camera_3d()
		for node: Node in meshes:
			var mesh := node as MeshInstance3D
			var middle := mesh.global_transform * mesh.get_aabb().get_center()
			var at := camera.unproject_position(middle)
			_check(
				(
					not camera.is_position_behind(middle)
					and Rect2(Vector2.ZERO, viewport.size).has_point(at)
				),
				"The camera frames the model: %s in %s" % [at, viewport.size]
			)
	_check(_project_files() == files, "Loading writes nothing to the project")
	_check_orbit(live)
	media.set_side("before")
	_check(live.views()[1].modulate.a < 0.5, "Before dims the change")
	# Hiding the panel frees the views; showing it again loads them anew.
	media.hide()
	await _frames()
	_check(live.views().is_empty(), "Hidden, the views are freed")
	media.show()
	deadline = Time.get_ticks_msec() + 3000
	while not live.is_live() and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(live.is_live(), "Shown again, the live models come back")
	await _check_broken(media, item)
	await _check_animation(media)
	media.clear_media()
	_check(live.views().is_empty(), "Clearing frees the views")
	media.queue_free()
	await _frames()
	print("review live: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## One orbit for both: a drag turns both cameras alike, the wheel zooms both, a double-click
## puts them back.
func _check_orbit(live: MODEL_VIEW) -> void:
	var cameras := live.cameras()
	var start := cameras[0].global_position
	var drag := InputEventMouseMotion.new()
	drag.relative = Vector2(40.0, 10.0)
	drag.button_mask = MOUSE_BUTTON_MASK_LEFT
	live.views()[1].gui_input.emit(drag)
	_check(not cameras[0].global_position.is_equal_approx(start), "Dragging one side orbits")
	_check(
		cameras[0].global_transform.is_equal_approx(cameras[1].global_transform),
		"…and both cameras turn together"
	)
	var distance := cameras[0].global_position.length()
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_UP
	wheel.pressed = true
	wheel.factor = 1.0
	live.views()[0].gui_input.emit(wheel)
	_check(live.zoom < 1.0, "The wheel zooms in")
	_check(cameras[1].global_transform.is_equal_approx(cameras[0].global_transform), "…both sides")
	_check(cameras[0].global_position.length() != distance, "The camera moved in")
	var twice := InputEventMouseButton.new()
	twice.button_index = MOUSE_BUTTON_LEFT
	twice.pressed = true
	twice.double_click = true
	live.views()[0].gui_input.emit(twice)
	_check(
		cameras[0].global_position.is_equal_approx(start) and is_equal_approx(live.zoom, 1.0),
		"A double-click resets the view"
	)


## A file that does not load keeps the renders, as does one past the time budget.
func _check_broken(media: MEDIA, item: Dictionary) -> void:
	var broken := _folder.path_join("broken.glb")
	var file := FileAccess.open(broken, FileAccess.WRITE)
	file.store_string("not a model")
	file.close()
	var bad := item.duplicate()
	bad["path"] = "art/sources/crate.blend"
	bad["before_model"] = broken
	bad["after_model"] = broken
	media.show_media([bad])
	var live: MODEL_VIEW = media.model_view()
	var deadline := Time.get_ticks_msec() + 2000
	while live.is_loading() and Time.get_ticks_msec() < deadline:
		await process_frame
	await _frames()
	_check(not live.is_live(), "A broken file has no live view")
	_check(media.texture() != null and media._front.visible, "…the renders stay")
	# Too slow: past the budget, the renders stay even when the files load.
	var slow := MODEL_VIEW.new()
	root.add_child(slow)
	var gave_up := [false]
	slow.failed.connect(func() -> void: gave_up[0] = true)
	slow.load_models(str(item["before_model"]), str(item["after_model"]))
	slow._waited = MODEL_VIEW.BUDGET + 0.1
	deadline = Time.get_ticks_msec() + 2000
	while slow.is_loading() and Time.get_ticks_msec() < deadline:
		await process_frame
		slow._waited = MODEL_VIEW.BUDGET + 0.1
	await _frames()
	_check(gave_up[0] and not slow.is_live(), "A load past the budget keeps the renders")
	slow.queue_free()


## An animated GLB plays its action live, posed by the shared clock; Right steps a frame.
func _check_animation(media: MEDIA) -> void:
	var run := _glb("hero-run.glb", 0.5, true)
	var strip := Image.create(48 * 8, 48, false, Image.FORMAT_RGBA8)
	strip.fill(Color("1f1f1f"))
	var strip_path := _folder.path_join("run-strip.png")
	strip.save_png(strip_path)
	var meta := {"frames": 8, "fps": 10.0, "seconds": 0.8, "loop": true}
	var clip := {
		"path": "art/sources/hero.blend",
		"media": "clip",
		"label": "run",
		"before": strip_path,
		"after": strip_path,
		"before_clip": meta,
		"after_clip": meta,
		"before_model": run,
		"after_model": run,
		"before_action": "run",
		"after_action": "run",
	}
	media.show_media([clip])
	var live: MODEL_VIEW = media.model_view()
	var deadline := Time.get_ticks_msec() + 3000
	while not live.is_live() and Time.get_ticks_msec() < deadline:
		await process_frame
	await _frames()
	_check(live.is_live(), "An animated model plays live")
	_check(not media.clip().visible, "The strips step aside")
	_check(media.transport().visible, "One play/pause and scrub bar")
	_check(
		is_equal_approx(live.animation_length(), 0.8), "The action: %s" % live.animation_length()
	)
	media.transport().scrubbed.emit(0.5)
	var players := live.views()[0].find_children("*", "AnimationPlayer", true, false)
	_check(not players.is_empty(), "The GLB brought its AnimationPlayer")
	if not players.is_empty():
		var player := players[0] as AnimationPlayer
		_check(
			absf(player.current_animation_position - 0.4) < 0.01,
			"Scrubbing poses the model: %s" % player.current_animation_position
		)
		var right := InputEventKey.new()
		right.keycode = KEY_RIGHT
		right.pressed = true
		live.views()[0].gui_input.emit(right)
		_check(
			absf(player.current_animation_position - 0.5) < 0.01,
			"Right steps a frame: %s" % player.current_animation_position
		)
