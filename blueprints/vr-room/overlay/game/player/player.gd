extends CharacterBody3D
## The VR room's player on the desktop: mouse or stick look with clamped
## pitch, walking, and one interact action that picks up what the eyes point
## at (it then floats at the hold point), drops what is held, or presses the
## button. In a headset the VR feature's rig carries this body instead and
## its hands do the grabbing. Input is read only on the peer that owns this
## player; other peers see it through SYNC_PROPERTIES.

signal target_changed(target: Node)
signal held_changed(item: Node3D)

const SYNC_PROPERTIES: Array[String] = ["position", "velocity", "rotation", "Head:rotation"]
const PITCH_LIMIT := deg_to_rad(85.0)
## Radians of turn per pixel of mouse motion at sensitivity 1.
const MOUSE_RADIANS_PER_PIXEL := 0.0025
## Radians per second at full stick or key deflection.
const STICK_RADIANS_PER_SECOND := 2.5

@export var save_id: String = "player"
## Metres a second: a calm pace, the same as the VR default.
@export var walk_speed: float = 2.5
@export var acceleration: float = 30.0
## How far away (metres) something can be picked up or pressed.
@export var reach: float = 2.5

## False while the win page owns the controls.
var controls_enabled: bool = true
## What the player holds, or null.
var held: Node3D = null
var gravity: float = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
var _target: Node = null

@onready var head: Node3D = $Head
@onready var camera: Camera3D = $Head/Camera3D
@onready var ray: RayCast3D = $Head/Camera3D/AimRay
@onready var hold_point: Node3D = $Head/Camera3D/HoldPoint
@onready var visor: MeshInstance3D = $Head/Visor


func _ready() -> void:
	add_to_group(&"player")
	add_to_group(&"saveable")
	ray.target_position = Vector3(0.0, 0.0, -reach)
	ray.add_exception(self)
	var local: bool = is_multiplayer_authority()
	camera.current = local
	# The visor shows which way other players face; it would block your own view.
	visor.visible = not local
	if local:
		var hud: Node = preload("res://game/ui/hud.gd").new()
		hud.name = "Hud"
		add_child(hud)


func _exit_tree() -> void:
	if is_multiplayer_authority() and DisplayServer.get_name() != "headless":
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _unhandled_input(event: InputEvent) -> void:
	if not is_multiplayer_authority() or not can_control():
		return
	var motion: InputEventMouseMotion = event as InputEventMouseMotion
	if motion != null and _mouse_look_enabled():
		look(mouse_delta(motion.relative))


func _process(_delta: float) -> void:
	if is_multiplayer_authority() and DisplayServer.get_name() != "headless":
		var want: bool = can_control() and not get_tree().paused
		var mode: Input.MouseMode = Input.MOUSE_MODE_CAPTURED if want else Input.MOUSE_MODE_VISIBLE
		if Input.mouse_mode != mode and DisplayServer.window_is_focused():
			Input.mouse_mode = mode


func _physics_process(delta: float) -> void:
	if not is_multiplayer_authority():
		return
	if not is_on_floor():
		velocity.y -= gravity * delta
	var input_dir: Vector2 = Vector2.ZERO
	if can_control():
		var stick: Vector2 = Input.get_vector(&"look_left", &"look_right", &"look_up", &"look_down")
		if stick != Vector2.ZERO:
			look(stick_delta(stick, delta))
		if Input.is_action_just_pressed(&"interact"):
			interact()
		input_dir = Input.get_vector(&"move_left", &"move_right", &"move_forward", &"move_back")
	var direction: Vector3 = transform.basis * Vector3(input_dir.x, 0.0, input_dir.y)
	direction.y = 0.0
	direction = direction.normalized() * minf(input_dir.length(), 1.0)
	var horizontal: Vector2 = Vector2(velocity.x, velocity.z)
	horizontal = horizontal.move_toward(
		Vector2(direction.x, direction.z) * walk_speed, acceleration * delta
	)
	velocity.x = horizontal.x
	velocity.z = horizontal.y
	move_and_slide()
	_update_target()


## True when input should drive this player: not after the win, and not while
## a menu is open.
func can_control() -> bool:
	return controls_enabled and not Menus.is_open()


## Turns the view: `delta.x` is yaw (body), `delta.y` is pitch (head), in radians.
func look(delta: Vector2) -> void:
	rotation.y = wrapf(rotation.y + delta.x, -PI, PI)
	head.rotation.x = clampf(head.rotation.x + delta.y, -PITCH_LIMIT, PITCH_LIMIT)


## Yaw and pitch change for a mouse motion in pixels.
func mouse_delta(relative: Vector2) -> Vector2:
	var scale: float = MOUSE_RADIANS_PER_PIXEL * _sensitivity()
	var pitch_sign: float = 1.0 if _invert_y() else -1.0
	return Vector2(-relative.x * scale, relative.y * scale * pitch_sign)


## Yaw and pitch change for look actions held over `delta` seconds.
func stick_delta(stick: Vector2, delta: float) -> Vector2:
	var scale: float = STICK_RADIANS_PER_SECOND * _sensitivity() * delta
	var pitch_sign: float = 1.0 if _invert_y() else -1.0
	return Vector2(-stick.x * scale, stick.y * scale * pitch_sign)


## What the eyes point at within reach: a pickable or a button, or null.
func target() -> Node:
	return _target


## Drops what is held, or picks up or presses what is in view. Returns true
## when something happened.
func interact() -> bool:
	if held != null:
		drop()
		return true
	_update_target()
	if _target == null:
		return false
	if _target.is_in_group(&"pickable"):
		return pick_up(_target as Node3D)
	Game.request_action(_target, &"use", [])
	return true


func pick_up(item: Node3D) -> bool:
	if held != null or item == null or not bool(item.call(&"grab", hold_point, true)):
		return false
	held = item
	held_changed.emit(held)
	_update_target()
	return true


## Lets go of what is held; it keeps the player's walking speed.
func drop() -> void:
	if held == null:
		return
	var item: Node3D = held
	held = null
	if is_instance_valid(item):
		item.call(&"release", Vector3(velocity.x, 0.0, velocity.z))
	held_changed.emit(null)


func save_state() -> Dictionary:
	return {"position": position, "yaw": rotation.y, "pitch": head.rotation.x}


func load_state(state: Dictionary) -> void:
	drop()
	position = state.get("position", position)
	rotation.y = float(state.get("yaw", rotation.y))
	head.rotation.x = float(state.get("pitch", head.rotation.x))
	velocity = Vector3.ZERO


func _sensitivity() -> float:
	return clampf(float(Settings.get_value("look", "sensitivity", 1.0)), 0.2, 3.0)


func _invert_y() -> bool:
	return bool(Settings.get_value("input", "invert_look_y", false))


## Mouse motion turns the view only while the mouse is captured, so moving a
## free cursor never spins it. A headless run has no cursor (play tests).
func _mouse_look_enabled() -> bool:
	return Input.mouse_mode == Input.MOUSE_MODE_CAPTURED or DisplayServer.get_name() == "headless"


func _update_target() -> void:
	ray.force_raycast_update()
	var found: Node = null
	if ray.is_colliding():
		found = _usable(ray.get_collider())
	if found != _target:
		_target = found
		target_changed.emit(_target)


func _usable(node: Object) -> Node:
	var current: Node = node as Node
	while current != null and current != self:
		if current == held:
			return null
		if current.is_in_group(&"pickable") and current.has_method(&"grab"):
			if (
				held == null
				and (not current.has_method(&"can_grab") or bool(current.call(&"can_grab")))
			):
				return current
			return null
		if current.is_in_group(&"interactable") and current.has_method(&"use"):
			if not current.has_method(&"can_use") or bool(current.call(&"can_use")):
				return current
			return null
		current = current.get_parent()
	return null
