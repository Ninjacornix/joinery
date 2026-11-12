extends RefCounted
## The checks behind camera_test.gd, loaded once the game runs. On a 2D blueprint
## they drive a CameraRig2D, on a 3D one a CameraRig3D, following a marker made in
## code: dead zone, smoothing, look-ahead, framing a group, and shake against the
## screen shake and reduced motion settings.

var tree: SceneTree
var h: PlayHarness
var is_3d: bool = false
## Distances are given in metres; 2D multiplies them by this many pixels.
var unit: float = 1.0
var rig: Node
var marker: Node


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	await h.boot()
	var player: Node = Game.local_player()
	if not (player is Node2D or player is Node3D):
		player = Node3D.new() if Game.current_level() is Node3D else Node2D.new()
		Game.current_level().add_child(player)
	is_3d = player is Node3D
	unit = 1.0 if is_3d else 64.0
	_make_rig(player)
	await h.physics_frames(3)
	_check_takes_over()
	await _check_dead_zone()
	await _check_follow()
	await _check_look_ahead()
	await _check_framing()
	await _check_shake()
	rig.queue_free()
	marker.queue_free()


func _make_rig(player: Node) -> void:
	if is_3d:
		marker = Node3D.new()
		Game.current_level().add_child(marker)
		(marker as Node3D).global_position = (player as Node3D).global_position
		var rig3: CameraRig3D = CameraRig3D.new()
		rig3.target_node = marker
		rig = rig3
	else:
		marker = Node2D.new()
		Game.current_level().add_child(marker)
		(marker as Node2D).global_position = (player as Node2D).global_position
		var rig2: CameraRig2D = CameraRig2D.new()
		rig2.target_node = marker
		rig = rig2
	marker.name = "CameraMarker"
	rig.name = "CameraRig"
	Game.current_level().add_child(rig)


func _check_takes_over() -> void:
	var current: Node = tree.root.get_camera_3d() if is_3d else tree.root.get_camera_2d()
	h.expect(current == rig, "the rig becomes the current camera")
	h.expect(Cameras.current_rig() == rig, "Cameras.current_rig() finds it")
	h.expect(_focus().distance_to(_at(marker)) < 0.01 * unit, "it starts on its target")


func _check_dead_zone() -> void:
	var before: Vector3 = _focus()
	_move(Vector3(0.4, 0.0, 0.4) if is_3d else Vector3(0.4, 0.4, 0.0))
	await h.physics_frames(30)
	h.expect(_focus().distance_to(before) < 0.001 * unit, "a move inside the dead zone keeps still")


func _check_follow() -> void:
	var start: Vector3 = _focus()
	_move(Vector3(6.0, 0.0, 0.0))
	await h.physics_frames(1)
	var first: float = _focus().x - start.x
	h.expect(first > 0.0 and first < 2.0 * unit, "smoothing eases in (%.2f)" % first)
	await h.physics_frames(120)
	var edge: float = _at(marker).x - _dead_zone().x
	h.expect(absf(_focus().x - edge) < 0.05 * unit, "it settles at the dead zone's edge")


func _check_look_ahead() -> void:
	var step: float = 0.1 if is_3d else 5.0 / 64.0
	for _i: int in 60:
		_move(Vector3(step, 0.0, 0.0))
		await h.physics_frames(1)
	var lead: float = _look_offset().x
	var most: float = float(rig.get(&"look_ahead"))
	h.expect(lead > most * 0.5, "it looks ahead of a moving target (%.2f of %.2f)" % [lead, most])
	await h.physics_frames(120)
	h.expect(absf(_look_offset().x) < most * 0.1, "and back once it stops")


func _check_framing() -> void:
	var other: Node = Node3D.new() if is_3d else Node2D.new()
	other.name = "FramedFriend"
	Game.current_level().add_child(other)
	var far: Vector3 = _at(marker) + Vector3(30.0, 0.0, 0.0) * unit
	if is_3d:
		(other as Node3D).global_position = far
	else:
		(other as Node2D).global_position = Vector2(far.x, far.y)
	rig.set(&"group_targets", [marker.get_path(), other.get_path()] as Array[NodePath])
	await h.physics_frames(150)
	if is_3d:
		var camera: CameraRig3D = rig
		h.expect(camera.pull_back > 1.2, "it pulls back to frame both (%.2f)" % camera.pull_back)
		h.expect(camera.is_position_in_frustum(_at(marker)), "the first target is in view")
		h.expect(camera.is_position_in_frustum(far), "the second target is in view")
	else:
		var camera2: CameraRig2D = rig
		h.expect(camera2.zoom.x < 0.95, "it zooms out to frame both (%.2f)" % camera2.zoom.x)
		var view: Vector2 = camera2.get_viewport_rect().size / camera2.zoom
		var shown: Rect2 = Rect2(camera2.get_screen_center_position() - view * 0.5, view)
		h.expect(shown.has_point((marker as Node2D).global_position), "the first target shows")
		h.expect(shown.has_point(Vector2(far.x, far.y)), "the second target shows")
	rig.set(&"group_targets", [] as Array[NodePath])
	other.queue_free()
	await h.physics_frames(120)
	var back: float = float(rig.get(&"pull_back")) if is_3d else (rig as CameraRig2D).zoom.x
	h.expect(absf(back - 1.0) < 0.05, "and comes back in for one target")


func _check_shake() -> void:
	Settings.set_value("access", "reduced_motion", false)
	Settings.set_value("access", "screen_shake", 1.0)
	Cameras.shake(1.0)
	await h.physics_frames(3)
	h.expect(_shake() > 0.01 * unit, "Cameras.shake() shakes the rig")
	await h.physics_frames(150)
	h.expect(_shake() == 0.0, "the shake dies away")
	Settings.set_value("access", "screen_shake", 0.0)
	Cameras.shake(1.0)
	await h.physics_frames(3)
	h.expect(_shake() == 0.0, "screen shake off keeps the camera still")
	Settings.set_value("access", "screen_shake", 1.0)
	Settings.set_value("access", "reduced_motion", true)
	Cameras.shake(1.0)
	await h.physics_frames(3)
	h.expect(_shake() == 0.0, "reduced motion keeps the camera still")
	Settings.set_value("access", "reduced_motion", false)
	rig.set(&"trauma", 0.0)


func _move(by: Vector3) -> void:
	if is_3d:
		(marker as Node3D).global_position += by
	else:
		(marker as Node2D).global_position += Vector2(by.x, by.y) * unit


func _focus() -> Vector3:
	var focus: Variant = rig.get(&"focus")
	return focus if focus is Vector3 else Vector3((focus as Vector2).x, (focus as Vector2).y, 0.0)


func _look_offset() -> Vector3:
	var lead: Variant = rig.get(&"look_offset")
	return lead if lead is Vector3 else Vector3((lead as Vector2).x, (lead as Vector2).y, 0.0)


func _dead_zone() -> Vector3:
	var zone: Variant = rig.get(&"dead_zone")
	return zone if zone is Vector3 else Vector3((zone as Vector2).x, (zone as Vector2).y, 0.0)


func _shake() -> float:
	if is_3d:
		var camera: CameraRig3D = rig
		return Vector2(camera.h_offset, camera.v_offset).length()
	return (rig as CameraRig2D).offset.length()


static func _at(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	var at: Vector2 = (node as Node2D).global_position
	return Vector3(at.x, at.y, 0.0)
