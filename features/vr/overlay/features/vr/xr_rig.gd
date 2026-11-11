extends XROrigin3D
## The XR rig: origin, camera and two hands, carried on the local player.
##
## While it carries a player it drives the player's body itself (the player's
## own physics and mouse look stop): the body follows the head when the
## player walks in the room, the move stick walks (smooth move), the other
## stick snaps the view 30° or 45° left or right, and pushing it forward
## aims a teleport arc that lands only on level floor. The vignette darkens
## the edges of the view while moving or turning. The pointer hand's ray uses
## whatever `interactable` it points at with the trigger, and clicks the menu
## panel. Settings come from the `VR` autoload and apply at once.

signal turned(radians: float)
signal teleported(to: Vector3)

const Comfort := preload("res://features/vr/comfort.gd")
## Eye height used when no headset moves the camera (simulated runs).
const SIMULATED_EYE_HEIGHT := 1.6
const POINTER_RANGE := 5.0
const VALID_COLOUR := Color(0.3, 0.7, 1.0, 0.9)
const INVALID_COLOUR := Color(0.95, 0.3, 0.25, 0.9)
const IDLE_BEAM := Color(0.9, 0.92, 0.95, 0.5)
const ACTIVE_BEAM := Color(1.0, 0.85, 0.3, 0.9)

## The player body this rig moves, or null (the rig then moves itself).
var body: Node3D = null
## The interactable the pointer is on, or null.
var target: Node = null
## The current teleport aim: see `Comfort.find_landing` ({} when not aiming).
var landing: Dictionary = {}

var _turn_armed: bool = true
var _aiming: bool = false
var _trigger_was: bool = false
var _menu_was: bool = false
var _motion: float = 0.0
var _turn_flash: float = 0.0
var _gravity: float = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
var _arc: ImmediateMesh = ImmediateMesh.new()

@onready var camera: XRCamera3D = $XRCamera3D
@onready var left_hand: XRController3D = $LeftHand
@onready var right_hand: XRController3D = $RightHand
@onready var vignette: MeshInstance3D = $XRCamera3D/Vignette
@onready var arc_mesh: MeshInstance3D = $TeleportArc
@onready var marker: MeshInstance3D = $TeleportMarker


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	arc_mesh.mesh = _arc
	arc_mesh.top_level = true
	marker.top_level = true
	arc_mesh.visible = false
	marker.visible = false
	if not _tracking():
		camera.position = Vector3(0.0, SIMULATED_EYE_HEIGHT, 0.0)
	camera.current = true
	_apply_settings()
	Settings.changed.connect(_on_settings_changed)


## Starts carrying `player` (or nothing): its own physics and look input stop
## and the rig moves its body from now on.
func carry(player: Node3D) -> void:
	body = player
	for hand: XRController3D in [left_hand, right_hand]:
		var ray: RayCast3D = hand.get_node(^"Pointer") as RayCast3D
		ray.clear_exceptions()
		if body is CollisionObject3D:
			ray.add_exception(body as CollisionObject3D)
	if body != null:
		body.set_physics_process(false)
		body.set_process_unhandled_input(false)
	_apply_settings()
	camera.current = true


## Which way the rig faces, in radians about +Y.
func turn() -> float:
	return global_rotation.y


func set_turn(radians: float) -> void:
	turn_by(angle_difference(turn(), radians))


## Turns the view on the spot by `radians` (positive is to the left).
func turn_by(radians: float) -> void:
	global_transform = Comfort.snap_turn(global_transform, camera.global_position, radians)
	_turn_flash = 1.0
	turned.emit(radians)


func move_hand() -> XRController3D:
	return right_hand if Comfort.move_hand(_left_handed()) == &"right" else left_hand


## The hand that turns, teleports and points.
func pointer_hand() -> XRController3D:
	return left_hand if move_hand() == right_hand else right_hand


## Moves the player (or the rig) so the head stands over `point`.
func teleport_to(point: Vector3) -> void:
	var feet: Vector3 = _feet()
	var offset: Vector3 = point - feet
	if body != null:
		body.global_position += offset
		if body is CharacterBody3D:
			(body as CharacterBody3D).velocity = Vector3.ZERO
	else:
		global_position += offset
	teleported.emit(point)


## The vignette's current opacity (0 = clear).
func vignette_amount() -> float:
	var material: ShaderMaterial = vignette.material_override as ShaderMaterial
	return float(material.get_shader_parameter(&"amount")) if material != null else 0.0


func _physics_process(delta: float) -> void:
	_menu_button()
	_update_pointer()
	if get_tree().paused or not _can_move():
		_end_aim(false)
		_set_vignette(0.0)
		return
	_follow_head()
	var velocity: Vector3 = Vector3.ZERO
	if bool(VR.setting("smooth_move")):
		velocity = Comfort.smooth_velocity(
			move_hand().stick(), camera.global_basis, _speed(), VR.deadzone()
		)
	_move(velocity, delta)
	var stick: Vector2 = pointer_hand().stick()
	var step: int = Comfort.snap_step(stick.x, _turn_armed)
	_turn_armed = Comfort.rearm(stick.x, _turn_armed)
	if step != 0:
		turn_by(float(step) * Comfort.snap_angle(int(VR.setting("snap_turn"))))
	if bool(VR.setting("teleport")) and stick.y >= Comfort.TELEPORT_PRESS:
		_aim()
	elif _aiming:
		_end_aim(true)
	_turn_flash = maxf(_turn_flash - delta * 4.0, 0.0)
	_motion = maxf(velocity.length() / Comfort.SPEED_MAX, _turn_flash)
	_set_vignette(Comfort.vignette_amount(float(VR.setting("vignette")), _motion))


func _tracking() -> bool:
	var xr: XRInterface = XRServer.primary_interface
	return xr != null and xr.is_initialized()


func _left_handed() -> bool:
	return bool(VR.setting("left_handed"))


func _speed() -> float:
	return Comfort.move_speed(float(VR.setting("move_speed")))


func _can_move() -> bool:
	if Menus.is_open():
		return false
	if body != null and "controls_enabled" in body:
		return bool(body.get(&"controls_enabled"))
	return true


func _feet() -> Vector3:
	var head: Vector3 = camera.global_position
	var base: Vector3 = body.global_position if body != null else global_position
	return Vector3(head.x, base.y, head.z)


## Keeps the body under the head when the player walks in their room: the
## body moves, and the rig moves back the same amount so the view stays.
func _follow_head() -> void:
	if body == null:
		return
	var offset: Vector3 = camera.global_position - body.global_position
	offset.y = 0.0
	if offset.length() < 0.02:
		return
	var before: Vector3 = body.global_position
	if body is CharacterBody3D:
		(body as CharacterBody3D).move_and_collide(offset)
	else:
		body.global_position += offset
	global_position -= body.global_position - before


func _move(velocity: Vector3, delta: float) -> void:
	if body is CharacterBody3D:
		var character: CharacterBody3D = body as CharacterBody3D
		var fall: float = character.velocity.y
		fall = 0.0 if character.is_on_floor() else fall - _gravity * delta
		character.velocity = Vector3(velocity.x, fall, velocity.z)
		character.move_and_slide()
	elif body != null:
		body.global_position += velocity * delta
	else:
		global_position += velocity * delta


func _aim() -> void:
	_aiming = true
	var hand: XRController3D = pointer_hand()
	var direction: Vector3 = -hand.global_basis.z
	var points: PackedVector3Array = Comfort.arc_points(hand.global_position, direction)
	var exclude: Array[RID] = []
	if body is CollisionObject3D:
		exclude.append((body as CollisionObject3D).get_rid())
	landing = Comfort.find_landing(get_world_3d().direct_space_state, points, exclude)
	_draw_arc(landing.get("points", points), bool(landing.get("valid", false)))
	marker.visible = bool(landing.get("hit", false))
	if marker.visible:
		marker.global_position = landing["position"] + Vector3.UP * 0.01
		_tint(marker, VALID_COLOUR if bool(landing["valid"]) else INVALID_COLOUR)


func _end_aim(jump: bool) -> void:
	if not _aiming:
		return
	_aiming = false
	arc_mesh.visible = false
	marker.visible = false
	if jump and bool(landing.get("valid", false)):
		teleport_to(landing["position"])
		_turn_flash = 1.0
	landing = {}


func _draw_arc(points: PackedVector3Array, valid: bool) -> void:
	_arc.clear_surfaces()
	if points.size() < 2:
		arc_mesh.visible = false
		return
	_arc.surface_begin(Mesh.PRIMITIVE_LINE_STRIP)
	_arc.surface_set_color(VALID_COLOUR if valid else INVALID_COLOUR)
	for point: Vector3 in points:
		_arc.surface_add_vertex(point)
	_arc.surface_end()
	arc_mesh.global_transform = Transform3D.IDENTITY
	arc_mesh.visible = true


func _update_pointer() -> void:
	var hand: XRController3D = pointer_hand()
	var other: XRController3D = move_hand()
	var ray: RayCast3D = hand.get_node(^"Pointer") as RayCast3D
	(other.get_node(^"Pointer") as RayCast3D).enabled = false
	(other.get_node(^"Pointer") as Node3D).visible = false
	ray.enabled = true
	ray.visible = true
	ray.target_position = Vector3(0.0, 0.0, -POINTER_RANGE)
	ray.force_raycast_update()
	var trigger: bool = bool(hand.call(&"trigger_down"))
	var pressed: bool = trigger and not _trigger_was
	_trigger_was = trigger
	var found: Node = null
	var length: float = POINTER_RANGE
	if ray.is_colliding():
		length = ray.global_position.distance_to(ray.get_collision_point())
		var hit: Object = ray.get_collider()
		var panel: Node = null
		if hit is Node and (hit as Node).has_meta(&"panel"):
			panel = (hit as Node).get_meta(&"panel") as Node
		if panel != null:
			panel.call(&"point", ray.get_collision_point(), trigger)
		elif not Menus.is_open():
			found = _interactable(hit)
	target = found
	var beam: Node3D = ray.get_node(^"Beam") as Node3D
	beam.scale = Vector3(1.0, 1.0, maxf(length, 0.01))
	_tint(beam.get_node(^"Line") as MeshInstance3D, ACTIVE_BEAM if found != null else IDLE_BEAM)
	if pressed and found != null and _can_move():
		Game.request_action(found, action_for(found), [])


func _menu_button() -> void:
	var down: bool = bool(left_hand.call(&"menu_down")) or bool(right_hand.call(&"menu_down"))
	if down and not _menu_was:
		if Menus.is_open():
			Menus.back()
		elif Game.current_level() != null:
			Menus.open_pause()
	_menu_was = down


func _interactable(node: Object) -> Node:
	var current: Node = node as Node
	while current != null:
		if current == body:
			return null
		if current.is_in_group(&"interactable") and action_for(current) != &"":
			if not current.has_method(&"can_use") or bool(current.call(&"can_use")):
				return current
			return null
		current = current.get_parent()
	return null


## The method the pointer calls on an interactable: `use`, or `interact`
## (third-person props), or &"" when it has neither.
static func action_for(node: Node) -> StringName:
	if node.has_method(&"use"):
		return &"use"
	if node.has_method(&"interact"):
		return &"interact"
	return &""


func _set_vignette(amount: float) -> void:
	var material: ShaderMaterial = vignette.material_override as ShaderMaterial
	if material != null:
		material.set_shader_parameter(&"amount", amount)
	vignette.visible = amount > 0.001


func _tint(mesh: MeshInstance3D, colour: Color) -> void:
	var material: StandardMaterial3D = mesh.material_override as StandardMaterial3D
	if material == null:
		material = StandardMaterial3D.new()
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mesh.material_override = material
	material.albedo_color = colour


func _apply_settings() -> void:
	position.y = Comfort.height_offset(bool(VR.setting("seated"))) if body != null else position.y


func _on_settings_changed(section: String, _key: String) -> void:
	if section == VR.SECTION:
		_apply_settings()
