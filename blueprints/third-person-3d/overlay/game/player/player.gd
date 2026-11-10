extends CharacterBody3D
## Third-person player: runs relative to the orbit camera, jumps with coyote
## time and a jump buffer, sprints, and interacts with nearby props (pick up
## and put down a crate, pull a lever). Input is read only on the peer that
## owns this player; other peers see it through SYNC_PROPERTIES.
##
## The body turns through Pivot; Pivot/Visual holds the meshes and squashes
## and stretches on jumps and landings (its origin is at the feet, so they
## stay on the ground).

signal jumped
signal landed
signal carrying_changed(item: Node3D)
signal interact_target_changed(target: Node)

const OrbitCamera := preload("res://game/player/orbit_camera.gd")
const SYNC_PROPERTIES: Array[String] = [
	"position", "velocity", "Pivot:rotation", "Pivot/Visual:scale"
]
## Visual scale on take-off (tall and thin) and on landing (short and wide).
const JUMP_STRETCH := Vector3(0.85, 1.2, 0.85)
const LAND_SQUASH := Vector3(1.2, 0.8, 1.2)

@export var save_id: String = "player"
@export_group("Movement")
@export var walk_speed: float = 6.0
@export var sprint_speed: float = 9.5
@export var carry_speed_scale: float = 0.8
@export var acceleration: float = 45.0
@export var air_acceleration: float = 16.0
@export var turn_speed: float = 12.0
@export_group("Jump")
@export var jump_velocity: float = 7.5
@export var jump_cut: float = 0.5
@export var coyote_time: float = 0.12
@export var jump_buffer_time: float = 0.12
@export_group("Feel")
## How quickly the squash and stretch settles back (higher is snappier).
@export var squash_recovery: float = 10.0
## Landings slower than this (m/s downwards) do not squash.
@export var land_squash_speed: float = 4.0

## False while a win screen or cut-in owns the controls.
var controls_enabled: bool = true
var carrying: Node3D = null
var interact_target: Node = null

var _gravity: float = 9.8
var _coyote: float = 0.0
var _buffer: float = 0.0
var _was_on_floor: bool = true

@onready var pivot: Node3D = $Pivot
@onready var visual: Node3D = $Pivot/Visual
@onready var camera_rig: OrbitCamera = $CameraRig
@onready var carry_point: Marker3D = $Pivot/CarryPoint
@onready var interact_area: Area3D = $Pivot/InteractArea


func _ready() -> void:
	_gravity = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
	camera_rig.setup(self)
	camera_rig.set_active(is_multiplayer_authority())
	_fold_root_rotation()


func _physics_process(delta: float) -> void:
	if not is_multiplayer_authority():
		return
	if rotation != Vector3.ZERO:
		_fold_root_rotation()
	var on_floor: bool = is_on_floor()
	if not on_floor:
		velocity.y -= _gravity * delta
	_coyote = coyote_time if on_floor else maxf(_coyote - delta, 0.0)
	_buffer = maxf(_buffer - delta, 0.0)

	var input: Vector2 = Vector2.ZERO
	var sprinting: bool = false
	if controls_enabled and not Menus.is_open():
		input = Input.get_vector(&"move_left", &"move_right", &"move_forward", &"move_back")
		sprinting = Input.is_action_pressed(&"sprint") and carrying == null
		if Input.is_action_just_pressed(&"jump"):
			_buffer = jump_buffer_time
		if Input.is_action_just_released(&"jump") and velocity.y > 0.0:
			velocity.y *= jump_cut
		if Input.is_action_just_pressed(&"interact"):
			interact()

	if _buffer > 0.0 and _coyote > 0.0:
		velocity.y = jump_velocity
		_buffer = 0.0
		_coyote = 0.0
		_squash(JUMP_STRETCH)
		jumped.emit()

	var direction: Vector3 = move_direction(input)
	var speed: float = sprint_speed if sprinting else walk_speed
	if carrying != null:
		speed *= carry_speed_scale
	var rate: float = (acceleration if on_floor else air_acceleration) * delta
	velocity.x = move_toward(velocity.x, direction.x * speed, rate)
	velocity.z = move_toward(velocity.z, direction.z * speed, rate)
	var falling_speed: float = -velocity.y
	move_and_slide()

	if direction.length_squared() > 0.0001:
		var facing: float = atan2(-direction.x, -direction.z)
		pivot.rotation.y = lerp_angle(pivot.rotation.y, facing, minf(turn_speed * delta, 1.0))
	if is_on_floor() and not _was_on_floor:
		if falling_speed > land_squash_speed:
			_squash(LAND_SQUASH)
		landed.emit()
	_was_on_floor = is_on_floor()
	visual.scale = visual.scale.lerp(Vector3.ONE, minf(squash_recovery * delta, 1.0))
	_update_interact_target()


## World-space direction for a stick or key vector, relative to the camera.
func move_direction(input: Vector2) -> Vector3:
	var flat: Vector3 = Vector3(input.x, 0.0, input.y)
	if flat.length_squared() > 1.0:
		flat = flat.normalized()
	return Basis(Vector3.UP, camera_rig.yaw) * flat


## Uses whatever is in hand, or the nearest interactable in front.
func interact() -> void:
	var target: Node = carrying if carrying != null else interact_target
	if target == null:
		return
	Game.request_action(target, &"interact", [])


func carry_transform() -> Transform3D:
	return carry_point.global_transform


## Where a carried item is put down: on the ground a little in front.
func drop_transform() -> Transform3D:
	var forward: Vector3 = -pivot.global_basis.z
	var origin: Vector3 = global_position + forward * 1.2 + Vector3.UP * 0.55
	return Transform3D(Basis(Vector3.UP, pivot.rotation.y), origin)


func set_carrying(item: Node3D) -> void:
	carrying = item
	carrying_changed.emit(item)


func facing_yaw() -> float:
	return pivot.rotation.y


func respawn(at: Transform3D) -> void:
	global_position = at.origin
	velocity = Vector3.ZERO
	var forward: Vector3 = -at.basis.z
	var yaw: float = atan2(-forward.x, -forward.z)
	pivot.rotation.y = yaw
	visual.scale = Vector3.ONE
	camera_rig.yaw = yaw
	camera_rig.snap()
	reset_physics_interpolation()


func save_state() -> Dictionary:
	return {
		"position": global_position,
		"facing": pivot.rotation.y,
		"camera_yaw": camera_rig.yaw,
		"camera_pitch": camera_rig.pitch,
	}


func load_state(state: Dictionary) -> void:
	global_position = state.get("position", global_position)
	velocity = Vector3.ZERO
	pivot.rotation.y = float(state.get("facing", pivot.rotation.y))
	camera_rig.yaw = float(state.get("camera_yaw", camera_rig.yaw))
	camera_rig.pitch = float(state.get("camera_pitch", camera_rig.pitch))
	camera_rig.snap()


## Squash and stretch the meshes; skipped with the Reduced motion setting.
func _squash(amount: Vector3) -> void:
	if bool(Settings.get_value("access", "reduced_motion", false)):
		return
	visual.scale = amount


## Spawn markers may turn the root; the body turns through Pivot instead, so
## the turn moves there and the camera starts behind the player.
func _fold_root_rotation() -> void:
	if rotation == Vector3.ZERO:
		return
	pivot.rotation.y = wrapf(pivot.rotation.y + rotation.y, -PI, PI)
	rotation = Vector3.ZERO
	camera_rig.yaw = pivot.rotation.y
	camera_rig.snap()


func _update_interact_target() -> void:
	var best: Node = null
	if carrying == null:
		var best_distance: float = INF
		for body: Node3D in interact_area.get_overlapping_bodies():
			if not body.is_in_group(&"interactable"):
				continue
			var distance: float = body.global_position.distance_squared_to(global_position)
			if distance < best_distance:
				best_distance = distance
				best = body
	if best != interact_target:
		interact_target = best
		interact_target_changed.emit(best)
