extends CharacterBody3D
## First-person player: mouse and stick look with clamped pitch, walk, sprint,
## jump (with coyote time and a jump buffer), and a ray from the eyes that uses
## whatever interactable it points at. The body turns left and right (yaw);
## only the Head node tilts up and down (pitch). Input is read only on the peer
## that owns this player; other peers see it through SYNC_PROPERTIES.

signal target_changed(target: Node)
signal jumped
signal landed

const SYNC_PROPERTIES: Array[String] = ["position", "velocity", "rotation", "Head:rotation"]
const PITCH_LIMIT := deg_to_rad(85.0)

@export var save_id: String = "player"
@export_group("Movement")
@export var walk_speed: float = 4.5
@export var sprint_speed: float = 7.0
@export var acceleration: float = 40.0
@export var air_acceleration: float = 10.0
@export_group("Jump")
@export var jump_velocity: float = 5.0
## Seconds after walking off an edge in which a jump still works.
@export var coyote_time: float = 0.1
## Seconds a jump press is remembered before landing.
@export var jump_buffer_time: float = 0.1
@export_group("Interact")
## How far away (metres) a switch or door can be used from.
@export var interact_range: float = 2.5

## False while the win screen owns the controls.
var controls_enabled: bool = true
var gravity: float = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
var _target: Node = null
var _coyote: float = 0.0
var _buffer: float = 0.0
var _was_on_floor: bool = true

@onready var head: Node3D = $Head
@onready var camera: Camera3D = $Head/Camera3D
@onready var ray: RayCast3D = $Head/Camera3D/InteractRay
@onready var visor: MeshInstance3D = $Head/Visor


func _ready() -> void:
	add_to_group(&"player")
	add_to_group(&"saveable")
	ray.target_position = Vector3(0.0, 0.0, -interact_range)
	ray.add_exception(self)
	var local: bool = is_multiplayer_authority()
	camera.current = local
	# The visor shows which way other players face; it would block your own view.
	visor.visible = not local
	if local:
		_apply_fov()
		FirstPersonLook.look_changed.connect(_apply_fov)
		var hud: Node = preload("res://game/ui/hud.gd").new()
		hud.name = "Hud"
		add_child(hud)


func _unhandled_input(event: InputEvent) -> void:
	if not is_multiplayer_authority() or not can_control():
		return
	var motion: InputEventMouseMotion = event as InputEventMouseMotion
	if motion != null and FirstPersonLook.mouse_look_enabled():
		look(FirstPersonLook.mouse_delta(motion.relative))


func _physics_process(delta: float) -> void:
	if not is_multiplayer_authority():
		return
	var on_floor: bool = is_on_floor()
	if not on_floor:
		velocity.y -= gravity * delta
	_coyote = coyote_time if on_floor else maxf(_coyote - delta, 0.0)
	_buffer = maxf(_buffer - delta, 0.0)

	var input_dir: Vector2 = Vector2.ZERO
	var sprinting: bool = false
	if can_control():
		var stick: Vector2 = Input.get_vector(&"look_left", &"look_right", &"look_up", &"look_down")
		if stick != Vector2.ZERO:
			look(FirstPersonLook.stick_delta(stick, delta))
		if Input.is_action_just_pressed(&"interact"):
			interact()
		if Input.is_action_just_pressed(&"jump"):
			_buffer = jump_buffer_time
		input_dir = Input.get_vector(&"move_left", &"move_right", &"move_forward", &"move_back")
		sprinting = Input.is_action_pressed(&"sprint")

	if _buffer > 0.0 and _coyote > 0.0:
		velocity.y = jump_velocity
		_buffer = 0.0
		_coyote = 0.0
		jumped.emit()

	# Forward is where the body faces; the head's pitch does not slow walking.
	var direction: Vector3 = transform.basis * Vector3(input_dir.x, 0.0, input_dir.y)
	direction.y = 0.0
	direction = direction.normalized() * minf(input_dir.length(), 1.0)
	var speed: float = sprint_speed if sprinting else walk_speed
	var accel: float = acceleration if on_floor else air_acceleration
	var horizontal := Vector2(velocity.x, velocity.z)
	horizontal = horizontal.move_toward(Vector2(direction.x, direction.z) * speed, accel * delta)
	velocity.x = horizontal.x
	velocity.z = horizontal.y
	move_and_slide()
	if is_on_floor() and not _was_on_floor:
		landed.emit()
	_was_on_floor = is_on_floor()
	_update_target()


## True when input should drive this player: not after the win, and not while
## a menu is open over an online game (which keeps running under the menu).
func can_control() -> bool:
	return controls_enabled and not Menus.is_open()


## Turns the view: `delta.x` is yaw (body), `delta.y` is pitch (head), in radians.
func look(delta: Vector2) -> void:
	rotation.y = wrapf(rotation.y + delta.x, -PI, PI)
	head.rotation.x = clampf(head.rotation.x + delta.y, -PITCH_LIMIT, PITCH_LIMIT)


## The interactable the eyes point at within range, or null.
func target() -> Node:
	return _target


## Uses the interactable in view, if any. Returns true when something was used.
func interact() -> bool:
	_update_target()
	if _target == null:
		return false
	Game.request_action(_target, &"use", [])
	return true


func save_state() -> Dictionary:
	return {
		"position": position,
		"yaw": rotation.y,
		"pitch": head.rotation.x,
	}


func load_state(state: Dictionary) -> void:
	position = state.get("position", position)
	rotation.y = float(state.get("yaw", rotation.y))
	head.rotation.x = float(state.get("pitch", head.rotation.x))
	velocity = Vector3.ZERO


func _update_target() -> void:
	ray.force_raycast_update()
	var found: Node = null
	if ray.is_colliding():
		found = _interactable(ray.get_collider())
	if found != _target:
		_target = found
		target_changed.emit(_target)


func _interactable(node: Object) -> Node:
	var current: Node = node as Node
	while current != null and current != self:
		if current.is_in_group(&"interactable") and current.has_method(&"use"):
			if not current.has_method(&"can_use") or bool(current.call(&"can_use")):
				return current
			return null
		current = current.get_parent()
	return null


func _apply_fov() -> void:
	camera.fov = FirstPersonLook.fov()
