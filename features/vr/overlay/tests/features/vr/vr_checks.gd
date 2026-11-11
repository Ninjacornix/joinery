# gdlint: disable=max-public-methods
extends RefCounted
## The VR feature's checks, loaded by vr_test.gd once the autoloads exist, so
## this file can name them. Works on every 3D starting point.

const Comfort := preload("res://features/vr/comfort.gd")
const Hand := preload("res://features/vr/xr_hand.gd")
const FakePickable := preload("res://tests/features/vr/fake_pickable.gd")
const RIG := "res://features/vr/xr_rig.tscn"
const PHASE_ARG := "--vr-phase="
## Values the write phase stores and the read phase expects back.
const STORED := {
	"smooth_move": false,
	"move_speed": 1.5,
	"snap_turn": 30,
	"teleport": false,
	"vignette": 0.3,
	"seated": true,
	"left_handed": true,
}

var h: PlayHarness
var tree: SceneTree


func run(harness: PlayHarness, scene_tree: SceneTree) -> void:
	h = harness
	tree = scene_tree
	var phase: String = _arg(PHASE_ARG)
	if phase == "write":
		for key: String in STORED:
			VR.set_setting(key, STORED[key])
		h.expect(FileAccess.file_exists(Settings.path), "comfort settings are written at once")
		return
	if phase == "read":
		for key: String in STORED:
			h.expect(VR.setting(key) == STORED[key], "%s is kept between sessions" % key)
		var page: Control = preload("res://features/vr/comfort_page.gd").build()
		var snap: OptionButton = page.find_child("vr_snap_turn", true, false) as OptionButton
		h.expect(snap != null and snap.get_selected_id() == 30, "the page shows the kept snap turn")
		page.free()
		return
	_check_comfort_maths()
	_check_teleport_maths()
	await h.boot()
	h.expect(Game.current_level() != null, "the level starts")
	_check_mode()
	await _check_rig_scene()
	await _check_hand_grab()
	await _check_settings_page()
	if VR.is_active():
		await _check_rig_on_player()
		await _check_snap_turn()
		await _check_smooth_move()
		await _check_teleport()
		await _check_pointer()
		await _check_hand_on_level_pickable()
		await _check_menu_panel()
		await _check_turn_is_saved()
	else:
		await _check_desktop_fallback()


func _arg(prefix: String) -> String:
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(prefix):
			return argument.substr(prefix.length())
	return ""


# --- maths -------------------------------------------------------------------


func _check_comfort_maths() -> void:
	h.expect_near(Comfort.snap_angle(30), deg_to_rad(30.0), 0.0001, "snap turn 30°")
	h.expect_near(Comfort.snap_angle(45), deg_to_rad(45.0), 0.0001, "snap turn 45°")
	h.expect_near(Comfort.snap_angle(90), deg_to_rad(45.0), 0.0001, "other angles mean 45°")
	h.expect(Comfort.snap_step(0.9, true) == -1, "stick right snaps right")
	h.expect(Comfort.snap_step(-0.9, true) == 1, "stick left snaps left")
	h.expect(Comfort.snap_step(0.9, false) == 0, "a held stick does not snap again")
	h.expect(Comfort.snap_step(0.4, true) == 0, "a half-pushed stick does not snap")
	h.expect(not Comfort.rearm(0.9, true), "a snap disarms the stick")
	h.expect(not Comfort.rearm(0.45, false), "between the thresholds it stays disarmed")
	h.expect(Comfort.rearm(0.1, false), "a centred stick re-arms")
	var pivot: Vector3 = Vector3(1.0, 1.6, -0.5)
	var rig: Transform3D = Transform3D(Basis.IDENTITY, Vector3(0.0, 0.0, 0.0))
	var head_local: Vector3 = rig.affine_inverse() * pivot
	var turned: Transform3D = Comfort.snap_turn(rig, pivot, Comfort.snap_angle(45))
	var head_after: Vector3 = turned * head_local
	h.expect(
		Vector2(head_after.x, head_after.z).distance_to(Vector2(pivot.x, pivot.z)) < 0.0001,
		"snap turn keeps the head where it is (%s)" % head_after
	)
	h.expect_near(turned.basis.get_euler().y, deg_to_rad(45.0), 0.0001, "and turns by the angle")
	h.expect_near(turned.origin.y, 0.0, 0.0001, "and never lifts the rig")
	h.expect(Comfort.deadzone(Vector2(0.1, 0.0)) == Vector2.ZERO, "a resting stick is ignored")
	h.expect(
		Comfort.deadzone(Vector2(1.0, 0.0)).is_equal_approx(Vector2(1.0, 0.0)),
		"full stick stays full"
	)
	h.expect(
		Comfort.deadzone(Vector2(0.6, 0.0)).is_equal_approx(Vector2(0.5, 0.0)),
		"the rest is rescaled"
	)
	var forward: Vector3 = Comfort.smooth_velocity(Vector2(0.0, 1.0), Basis.IDENTITY, 2.0)
	h.expect(
		forward.is_equal_approx(Vector3(0.0, 0.0, -2.0)), "stick up walks forward at the speed"
	)
	var left: Vector3 = Comfort.smooth_velocity(Vector2(0.0, 1.0), Basis(Vector3.UP, PI * 0.5), 2.0)
	h.expect(left.is_equal_approx(Vector3(-2.0, 0.0, 0.0)), "forward follows the head (%s)" % left)
	var down: Vector3 = Comfort.smooth_velocity(
		Vector2(0.0, 1.0), Basis(Vector3.RIGHT, -PI / 3.0), 2.0
	)
	h.expect(down.is_equal_approx(Vector3(0.0, 0.0, -2.0)), "looking down never slows walking")
	var side: Vector3 = Comfort.smooth_velocity(Vector2(1.0, 0.0), Basis.IDENTITY, 2.0)
	h.expect(side.is_equal_approx(Vector3(2.0, 0.0, 0.0)), "stick right steps right")
	h.expect_near(Comfort.move_speed(10.0), Comfort.SPEED_MAX, 0.0001, "speed is capped")
	h.expect_near(Comfort.move_speed(0.0), Comfort.SPEED_MIN, 0.0001, "and has a floor")
	h.expect_near(Comfort.height_offset(true), Comfort.SEATED_LIFT, 0.0001, "seated lifts the view")
	h.expect_near(Comfort.height_offset(false), 0.0, 0.0001, "standing does not")
	h.expect_near(
		Comfort.vignette_amount(0.6, 1.0), 0.6, 0.0001, "the vignette follows the setting"
	)
	h.expect_near(Comfort.vignette_amount(0.0, 1.0), 0.0, 0.0001, "vignette off stays off")
	h.expect_near(Comfort.vignette_amount(0.6, 0.0), 0.0, 0.0001, "standing still is clear")
	h.expect(Comfort.move_hand(false) == &"left", "the left stick walks")
	h.expect(Comfort.move_hand(true) == &"right", "left-handed: the right stick walks")


func _check_teleport_maths() -> void:
	var points: PackedVector3Array = Comfort.arc_points(
		Vector3(0.0, 1.0, 0.0), Vector3(0.0, 0.0, -1.0)
	)
	h.expect(points.size() == Comfort.ARC_POINTS, "the arc has its points")
	h.expect(points[0].is_equal_approx(Vector3(0.0, 1.0, 0.0)), "the arc starts at the hand")
	h.expect(points[-1].y < points[1].y, "the arc falls")
	h.expect(points[-1].z < points[1].z, "and goes forward")
	h.expect(Comfort.is_valid_landing(Vector3.UP, 3.0), "level floor is a landing")
	h.expect(
		Comfort.is_valid_landing(Vector3(0.0, 0.94, 0.34).normalized(), 3.0), "a gentle ramp is"
	)
	h.expect(
		not Comfort.is_valid_landing(Vector3(0.0, 0.7, 0.7).normalized(), 3.0), "a 45° slope is not"
	)
	h.expect(not Comfort.is_valid_landing(Vector3.RIGHT, 3.0), "a wall is not")
	h.expect(
		not Comfort.is_valid_landing(Vector3.UP, Comfort.TELEPORT_RANGE + 1.0), "too far is not"
	)


# --- in the game --------------------------------------------------------------


func _check_mode() -> void:
	var simulated: bool = "--vr-simulate" in OS.get_cmdline_user_args()
	h.expect(VR.mode == (VR.SIMULATED if simulated else VR.DESKTOP), "mode is %s" % VR.mode)
	h.expect(not tree.root.use_xr, "nothing renders to a headset here")
	h.expect(not String(VR.reason).is_empty(), "the reason is known: %s" % VR.reason)
	var ids: Array[StringName] = []
	for page: Dictionary in Menus.settings_pages():
		ids.append(StringName(page["id"]))
	h.expect(&"vr" in ids, "Settings has a VR comfort page")


func _check_rig_scene() -> void:
	var rig: Node3D = (load(RIG) as PackedScene).instantiate() as Node3D
	var level: Node3D = Game.current_level() as Node3D
	var camera_before: Camera3D = tree.root.get_camera_3d()
	level.add_child(rig)
	rig.global_position = Vector3(0.0, 50.0, 0.0)
	await h.physics_frames(2)
	h.expect(rig is XROrigin3D, "the rig is an XROrigin3D")
	var camera: XRCamera3D = rig.get_node_or_null(^"XRCamera3D") as XRCamera3D
	h.expect(camera != null, "the rig has an XRCamera3D")
	for side: String in ["Left", "Right"]:
		var hand: XRController3D = rig.get_node_or_null(NodePath(side + "Hand")) as XRController3D
		h.expect(hand != null, "the rig has a %s hand" % side.to_lower())
		if hand == null:
			continue
		h.expect(
			String(hand.tracker) == "%s_hand" % side.to_lower(),
			"%s hand tracks %s_hand" % [side, side.to_lower()]
		)
		h.expect(
			hand.get_node_or_null(^"Palm") is MeshInstance3D, "%s hand has a greybox palm" % side
		)
		h.expect(hand.get_node_or_null(^"Pointer") is RayCast3D, "%s hand has a pointer ray" % side)
	if camera != null:
		h.expect_near(camera.position.y, 1.6, 0.001, "without tracking the eyes sit at 1.6 m")
		var vignette: MeshInstance3D = camera.get_node_or_null(^"Vignette") as MeshInstance3D
		h.expect(
			vignette != null and vignette.material_override is ShaderMaterial,
			"the vignette is a shader"
		)
	rig.queue_free()
	await h.physics_frames(1)
	if camera_before != null and is_instance_valid(camera_before):
		camera_before.current = true


func _check_hand_grab() -> void:
	var level: Node3D = Game.current_level() as Node3D
	var hand: XRController3D = Hand.new()
	hand.name = "TestHand"
	level.add_child(hand)
	var at: Vector3 = Vector3(0.0, 40.0, 0.0)
	hand.global_position = at
	var near: Node3D = _fake(level, at + Vector3(0.1, 0.0, 0.0))
	var nearer_but_locked: Node3D = _fake(level, at + Vector3(0.05, 0.0, 0.0))
	nearer_but_locked.set(&"allowed", false)
	var farther: Node3D = _fake(level, at + Vector3(0.15, 0.0, 0.0))
	var out_of_reach: Node3D = _fake(level, at + Vector3(0.5, 0.0, 0.0))
	var candidates: Array[Node] = [out_of_reach, farther, near, nearer_but_locked]
	h.expect(
		Hand.nearest_pickable(candidates, at, Hand.GRAB_RADIUS) == near,
		"the nearest free one is grabbed"
	)
	h.expect(
		Hand.nearest_pickable([out_of_reach], at, Hand.GRAB_RADIUS) == null, "nothing out of reach"
	)
	hand.call(&"simulate", Vector2.ZERO, true)
	await h.physics_frames(2)
	h.expect(hand.get(&"held") == near, "grip grabs it")
	h.expect(near.get(&"holder") == hand, "and it knows its holder")
	h.expect(farther.get(&"grabs") == 0, "only one is grabbed")
	for i: int in 6:
		hand.global_position += Vector3(0.02, 0.0, 0.0)
		await h.physics_frames(1)
	# Let go while the hand still moves, as a throw does.
	hand.call(&"simulate", Vector2.ZERO, false)
	hand.global_position += Vector3(0.02, 0.0, 0.0)
	await h.physics_frames(1)
	h.expect(hand.get(&"held") == null, "letting go of the grip releases it")
	h.expect(near.get(&"holder") == null, "it is free again")
	var thrown: Vector3 = near.get(&"last_velocity")
	h.expect_near(
		thrown.x,
		0.02 * float(Engine.physics_ticks_per_second),
		0.15,
		"it is thrown with the hand's speed"
	)
	hand.call(&"simulate", Vector2.ZERO, true)
	await h.physics_frames(2)
	h.expect(hand.get(&"held") != null, "grip again grabs again")
	hand.call(&"stop_simulating")
	await h.physics_frames(2)
	h.expect(hand.get(&"held") == null, "a hand that stops gripping lets go")
	for node: Node in [hand, near, nearer_but_locked, farther, out_of_reach]:
		node.queue_free()
	await h.physics_frames(1)


func _fake(parent: Node, at: Vector3) -> Node3D:
	var item: Node3D = FakePickable.new()
	parent.add_child(item)
	item.global_position = at
	return item


func _check_settings_page() -> void:
	Menus.open_settings()
	var list: Control = Menus.current_page()
	var entry: Button = list.find_child("vr", true, false) as Button
	h.expect(entry != null, "Settings lists VR comfort")
	if entry == null:
		Menus.close_all()
		return
	entry.pressed.emit()
	await h.process_frames(1)
	var page: Control = Menus.current_page()
	for control: String in [
		"vr_smooth_move",
		"vr_move_speed",
		"vr_snap_turn",
		"vr_teleport",
		"vr_vignette",
		"vr_seated",
		"vr_left_handed",
	]:
		h.expect(page.find_child(control, true, false) != null, "the page has %s" % control)
	h.expect_min_text(page, "VR comfort page")
	var snap: OptionButton = page.find_child("vr_snap_turn", true, false) as OptionButton
	if snap != null:
		h.expect(snap.item_count == 2, "snap turn offers 30° and 45°")
		h.expect(snap.get_selected_id() == 45, "45° by default")
		snap.select(0)
		snap.item_selected.emit(0)
		h.expect(int(VR.setting("snap_turn")) == 30, "choosing 30° saves it")
		snap.select(1)
		snap.item_selected.emit(1)
	var seated: CheckButton = page.find_child("vr_seated", true, false) as CheckButton
	if seated != null:
		seated.button_pressed = true
		h.expect(bool(VR.setting("seated")), "the seated toggle saves")
		seated.button_pressed = false
	var speed: HSlider = page.find_child("vr_move_speed", true, false) as HSlider
	if speed != null:
		speed.value = 3.0
		h.expect_near(float(VR.setting("move_speed")), 3.0, 0.001, "the speed slider saves")
		speed.value = 2.0
	Menus.close_all()
	await h.process_frames(1)


func _check_desktop_fallback() -> void:
	h.expect(VR.rig == null, "no rig on the desktop")
	h.expect(VR.panel() == null, "no headset menu panel on the desktop")
	var player: Node3D = Game.local_player() as Node3D
	if player != null:
		h.expect(player.get_node_or_null(^"XRRig") == null, "the player keeps its own camera")
		var camera: Camera3D = tree.root.get_camera_3d()
		h.expect(camera != null and player.is_ancestor_of(camera), "the player's camera is in use")
	var toasts: Node = Menus.get_node(^"Root/Toasts")
	var shown: bool = false
	for holder: Node in toasts.get_children():
		for label: Node in holder.get_children():
			if label is Label and (label as Label).text == VR.NOTICE:
				shown = true
	h.expect(shown, "a notice says the game is on the desktop")
	if player != null:
		var start: Vector3 = player.global_position
		await h.hold(&"move_forward", 30)
		await h.physics_frames(10)
		h.expect(player.global_position.distance_to(start) > 0.3, "the desktop controls still play")


# --- the simulated rig -------------------------------------------------------


func _rig() -> Node3D:
	return VR.rig


func _hand(name: String) -> XRController3D:
	return _rig().get_node(NodePath(name)) as XRController3D


func _check_rig_on_player() -> void:
	var rig: Node3D = _rig()
	var player: Node3D = Game.local_player() as Node3D
	h.expect(rig != null, "the rig is carried")
	if rig == null:
		return
	if player != null:
		h.expect(rig.get_parent() == player, "on the local player")
		h.expect(rig.get(&"body") == player, "and moves that body")
		h.expect(not player.is_physics_processing(), "the player's own controls stop")
	var camera: Camera3D = rig.get_node(^"XRCamera3D") as Camera3D
	h.expect(camera.current, "the XR camera is current")
	VR.set_setting("seated", true)
	h.expect_near(rig.position.y, Comfort.SEATED_LIFT, 0.001, "seated play lifts the rig")
	VR.set_setting("seated", false)
	h.expect_near(rig.position.y, 0.0, 0.001, "standing play puts it back")
	VR.set_setting("left_handed", true)
	h.expect(rig.call(&"move_hand") == _hand("RightHand"), "left-handed: the right hand walks")
	h.expect(rig.call(&"pointer_hand") == _hand("LeftHand"), "and the left hand points")
	VR.set_setting("left_handed", false)
	h.expect(rig.call(&"move_hand") == _hand("LeftHand"), "the left hand walks by default")
	await h.physics_frames(2)


func _head(rig: Node3D) -> Vector3:
	return (rig.get_node(^"XRCamera3D") as Node3D).global_position


func _check_snap_turn() -> void:
	var rig: Node3D = _rig()
	var turner: XRController3D = rig.call(&"pointer_hand")
	VR.set_setting("snap_turn", 45)
	var yaw: float = float(rig.call(&"turn"))
	var head: Vector3 = _head(rig)
	turner.call(&"simulate", Vector2(1.0, 0.0))
	await h.physics_frames(10)
	h.expect_near(
		angle_difference(yaw, float(rig.call(&"turn"))),
		-deg_to_rad(45.0),
		0.001,
		"stick right snaps 45° right, once"
	)
	var moved: Vector3 = _head(rig) - head
	h.expect(Vector2(moved.x, moved.z).length() < 0.01, "turning keeps the head in place")
	turner.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(2)
	turner.call(&"simulate", Vector2(-1.0, 0.0))
	await h.physics_frames(2)
	h.expect_near(
		angle_difference(yaw, float(rig.call(&"turn"))), 0.0, 0.001, "stick left snaps back"
	)
	turner.call(&"simulate", Vector2.ZERO)
	VR.set_setting("snap_turn", 30)
	await h.physics_frames(2)
	turner.call(&"simulate", Vector2(-1.0, 0.0))
	await h.physics_frames(2)
	h.expect_near(
		angle_difference(yaw, float(rig.call(&"turn"))),
		deg_to_rad(30.0),
		0.001,
		"the 30° setting snaps 30°"
	)
	turner.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(2)
	rig.call(&"set_turn", yaw)
	VR.set_setting("snap_turn", 45)
	turner.call(&"stop_simulating")


func _check_smooth_move() -> void:
	var rig: Node3D = _rig()
	var walker: XRController3D = rig.call(&"move_hand")
	var player: Node3D = Game.local_player() as Node3D
	var mover: Node3D = player if player != null else rig
	VR.set_setting("smooth_move", true)
	VR.set_setting("move_speed", 2.0)
	VR.set_setting("vignette", 0.6)
	await h.physics_frames(5)
	var start: Vector3 = mover.global_position
	var camera: Node3D = rig.get_node(^"XRCamera3D") as Node3D
	var forward: Vector3 = -camera.global_basis.z
	forward = Vector3(forward.x, 0.0, forward.z).normalized()
	walker.call(&"simulate", Vector2(0.0, 1.0))
	await h.physics_frames(30)
	h.expect(float(rig.call(&"vignette_amount")) > 0.3, "the vignette closes in while moving")
	await h.physics_frames(30)
	walker.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(3)
	h.expect_near(float(rig.call(&"vignette_amount")), 0.0, 0.001, "and opens when standing still")
	var moved: Vector3 = mover.global_position - start
	var along: float = Vector2(moved.x, moved.z).dot(Vector2(forward.x, forward.z))
	h.expect(
		along > 1.2 and along < 2.3, "a second of stick walks about 2 m forward (%.2f)" % along
	)
	VR.set_setting("smooth_move", false)
	start = mover.global_position
	walker.call(&"simulate", Vector2(0.0, 1.0))
	await h.physics_frames(20)
	walker.call(&"simulate", Vector2.ZERO)
	moved = mover.global_position - start
	h.expect(Vector2(moved.x, moved.z).length() < 0.05, "smooth move off: the stick does not walk")
	VR.set_setting("smooth_move", true)
	walker.call(&"stop_simulating")
	# Back over open floor for the next checks.
	mover.global_position = start - forward * 1.0
	await h.physics_frames(5)


func _check_teleport() -> void:
	var rig: Node3D = _rig()
	var player: Node3D = Game.local_player() as Node3D
	var mover: Node3D = player if player != null else rig
	var hand: XRController3D = rig.call(&"pointer_hand")
	var camera: Node3D = rig.get_node(^"XRCamera3D") as Node3D
	var forward: Vector3 = -camera.global_basis.z
	forward = Vector3(forward.x, 0.0, forward.z).normalized()
	# Point down and ahead: the arc lands on the floor about a metre ahead.
	hand.global_basis = Basis.looking_at((forward - Vector3.UP).normalized(), Vector3.UP)
	VR.set_setting("teleport", true)
	hand.call(&"simulate", Vector2(0.0, 1.0))
	await h.physics_frames(3)
	var landing: Dictionary = rig.get(&"landing")
	h.expect(bool(landing.get("hit", false)), "the arc finds the floor")
	h.expect(bool(landing.get("valid", false)), "level floor is a valid landing: %s" % [landing])
	h.expect((rig.get_node(^"TeleportArc") as Node3D).visible, "the arc is drawn while aiming")
	var target: Vector3 = landing.get("position", Vector3.ZERO)
	var teleported: Dictionary = h.watch(Signal(rig, &"teleported"))
	hand.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(3)
	h.expect(h.fire_count(teleported) == 1, "letting go of the stick teleports")
	h.unwatch(teleported)
	var feet: Vector3 = mover.global_position
	var head: Vector3 = _head(rig)
	h.expect(
		Vector2(head.x, head.z).distance_to(Vector2(target.x, target.z)) < 0.1,
		"the player stands where the arc landed"
	)
	h.expect(absf(feet.y - target.y) < 0.15, "on the floor")
	h.expect(not (rig.get_node(^"TeleportArc") as Node3D).visible, "the arc hides after the jump")
	VR.set_setting("teleport", false)
	hand.call(&"simulate", Vector2(0.0, 1.0))
	await h.physics_frames(3)
	h.expect(not bool(rig.get(&"landing").get("hit", false)), "teleport off: no arc")
	hand.call(&"stop_simulating")
	VR.set_setting("teleport", true)
	await h.physics_frames(2)
	# Bodies marked no_teleport (tables, walls, posts) are never landings.
	var space: PhysicsDirectSpaceState3D = rig.get_world_3d().direct_space_state
	for node: Node in tree.get_nodes_in_group(Comfort.NO_TELEPORT):
		if node is Node3D and Game.current_level().is_ancestor_of(node):
			var top: Vector3 = (node as Node3D).global_position
			var straight: PackedVector3Array = PackedVector3Array([top + Vector3.UP * 5.0, top])
			var found: Dictionary = Comfort.find_landing(space, straight)
			if bool(found["hit"]) and found["collider"] == node:
				h.expect(not bool(found["valid"]), "%s is not a landing" % node.name)


func _check_pointer() -> void:
	var rig: Node3D = _rig()
	var hand: XRController3D = rig.call(&"pointer_hand")
	var target: Node3D = _interactable()
	if target == null:
		return
	var aim: Vector3 = _shape_centre(target)
	hand.global_position = aim + Vector3(0.0, 0.3, 0.5)
	hand.global_basis = Basis.looking_at(aim - hand.global_position, Vector3.UP)
	hand.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(2)
	h.expect(rig.get(&"target") == target, "the pointer finds %s" % target.name)
	var before: Variant = target.call(&"save_state") if target.has_method(&"save_state") else null
	hand.call(&"simulate", Vector2.ZERO, false, true)
	await h.physics_frames(3)
	hand.call(&"simulate", Vector2.ZERO)
	await h.physics_frames(30)
	if before != null:
		h.expect(target.call(&"save_state") != before, "the trigger uses %s" % target.name)
	hand.call(&"stop_simulating")


func _interactable() -> Node3D:
	var level: Node = Game.current_level()
	var player: Node = Game.local_player()
	for node: Node in tree.get_nodes_in_group(&"interactable"):
		if not (node is CollisionObject3D) or not level.is_ancestor_of(node):
			continue
		if player != null and player.is_ancestor_of(node):
			continue
		if node.has_method(&"use") or node.has_method(&"interact"):
			if node.is_in_group(&"carryable"):
				continue
			return node as Node3D
	return null


func _shape_centre(body: Node3D) -> Vector3:
	for child: Node in body.get_children():
		if child is CollisionShape3D:
			return (child as Node3D).global_position
	return body.global_position


func _check_hand_on_level_pickable() -> void:
	var item: Node3D = null
	for node: Node in tree.get_nodes_in_group(&"pickable"):
		if node is RigidBody3D and bool(node.call(&"can_grab")):
			item = node as Node3D
			break
	if item == null:
		return
	var rig: Node3D = _rig()
	var hand: XRController3D = rig.call(&"move_hand")
	hand.global_position = item.global_position + Vector3(0.0, 0.05, 0.0)
	hand.call(&"simulate", Vector2.ZERO, true)
	await h.physics_frames(2)
	h.expect(hand.get(&"held") == item, "a hand grabs %s" % item.name)
	h.expect(bool(item.call(&"is_held")), "%s follows the hand" % item.name)
	hand.global_position += Vector3(0.0, 0.3, 0.0)
	await h.physics_frames(2)
	h.expect(item.global_position.y > hand.global_position.y - 0.2, "it moves with the hand")
	hand.call(&"simulate", Vector2.ZERO, false)
	await h.physics_frames(30)
	h.expect(not bool(item.call(&"is_held")), "letting go drops %s" % item.name)
	hand.call(&"stop_simulating")


func _check_menu_panel() -> void:
	var rig: Node3D = _rig()
	var panel: Node3D = VR.panel()
	h.expect(panel != null, "there is a menu panel for the headset")
	if panel == null:
		return
	var hand: XRController3D = rig.call(&"pointer_hand")
	# A headless window is 64 px square; give the menus a real window to lay
	# out in, as a headset's desktop window has.
	var window_size: Vector2i = tree.root.size
	tree.root.size = Vector2i(1280, 720)
	hand.call(&"simulate", Vector2.ZERO, false, false, true)
	await h.physics_frames(2)
	hand.call(&"simulate", Vector2.ZERO)
	await h.process_frames(2)
	h.expect(Menus.is_open(), "the menu button opens the pause menu")
	h.expect(panel.visible, "the panel shows it in front of the player")
	var head: Vector3 = _head(rig)
	h.expect_near(panel.global_position.distance_to(head), 1.5, 0.3, "about 1.5 m away")
	var resume: Button = Menus.current_page().find_child("resume", true, false) as Button
	h.expect(resume != null, "the pause menu has Resume")
	if resume != null:
		await h.process_frames(2)
		var pixel: Vector2 = resume.get_global_rect().get_center()
		var spot: Vector3 = panel.call(&"to_world", pixel)
		var normal: Vector3 = panel.global_basis.z
		hand.global_position = spot + normal * 0.6
		hand.global_basis = Basis.looking_at(spot - hand.global_position, Vector3.UP)
		hand.call(&"simulate", Vector2.ZERO)
		await h.physics_frames(2)
		hand.call(&"simulate", Vector2.ZERO, false, true)
		await h.physics_frames(2)
		hand.call(&"simulate", Vector2.ZERO)
		await h.physics_frames(3)
		h.expect(not Menus.is_open(), "pointing at Resume and pulling the trigger closes the menu")
	hand.call(&"stop_simulating")
	if Menus.is_open():
		Menus.close_all()
	await h.process_frames(2)
	h.expect(not panel.visible, "the panel hides with the menu")
	tree.root.size = window_size


func _check_turn_is_saved() -> void:
	var save_game: Node = tree.root.get_node_or_null(^"SaveGame")
	if save_game == null:
		return
	var rig: Node3D = _rig()
	rig.call(&"set_turn", 1.0)
	VR.set_setting("vignette", 0.2)
	h.expect(int(save_game.call(&"save_slot", 1)) == OK, "the game saves")
	rig.call(&"set_turn", -0.5)
	VR.set_setting("vignette", 0.9)
	var started: Dictionary = h.watch(GameEvents.level_started)
	h.expect(int(save_game.call(&"load_slot", 1)) == OK, "and loads")
	await h.wait_watched(started, 300)
	await h.physics_frames(3)
	h.expect(VR.rig != null and VR.rig != rig, "the loaded level carries a new rig")
	if VR.rig != null:
		h.expect_near(
			angle_difference(float(VR.rig.call(&"turn")), 1.0),
			0.0,
			0.01,
			"a save keeps which way the player was turned"
		)
	h.expect_near(
		float(VR.setting("vignette")), 0.9, 0.001, "loading a save never changes comfort settings"
	)
	VR.set_setting("vignette", 0.6)
