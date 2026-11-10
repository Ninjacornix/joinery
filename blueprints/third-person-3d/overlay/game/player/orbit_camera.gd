extends Node3D
## Orbit camera on a spring arm. Sits at the player's shoulder height, yaws
## and pitches from the look actions (keys or right stick) and from mouse
## motion, and pulls in when the arm hits world geometry. Only the camera of
## the local player is active.

const SECTION := "camera"

@export var height: float = 1.5
@export var distance: float = 5.0
## Radians per second at full stick or key.
@export var look_speed: float = 2.6
## Radians per pixel of mouse motion at sensitivity 1.
@export var mouse_speed: float = 0.004
@export var min_pitch: float = -1.25
@export var max_pitch: float = 0.5
@export var follow_sharpness: float = 16.0
@export var default_fov: float = 70.0

var yaw: float = 0.0
var pitch: float = -0.3
var active: bool = false

var _target: Node3D = null
var _sensitivity: float = 1.0
var _invert_y: bool = false
var _reduced_motion: bool = false

@onready var pitch_node: Node3D = $Pitch
@onready var arm: SpringArm3D = $Pitch/SpringArm3D
@onready var camera: Camera3D = $Pitch/SpringArm3D/Camera3D


func setup(target: CollisionObject3D) -> void:
	_target = target
	arm.add_excluded_object(target.get_rid())
	arm.spring_length = distance
	yaw = target.global_rotation.y
	_read_settings()
	Settings.changed.connect(_on_setting_changed)
	GameEvents.paused_changed.connect(_on_paused_changed)
	GameEvents.level_completed.connect(_on_level_completed)
	snap()


func set_active(on: bool) -> void:
	active = on
	camera.current = on
	set_process_unhandled_input(on)
	if on:
		capture_mouse(true)


func capture_mouse(on: bool) -> void:
	if not active and on:
		return
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED if on else Input.MOUSE_MODE_VISIBLE


## True when mouse motion should turn the camera. A headless run has no cursor
## to capture, so motion always counts there (automated play tests).
func mouse_look_enabled() -> bool:
	return Input.mouse_mode == Input.MOUSE_MODE_CAPTURED or DisplayServer.get_name() == "headless"


func rotate_view(delta_yaw: float, delta_pitch: float) -> void:
	yaw = wrapf(yaw + delta_yaw, -PI, PI)
	var signed: float = -delta_pitch if _invert_y else delta_pitch
	pitch = clampf(pitch + signed, min_pitch, max_pitch)
	_apply_rotation()


## Camera forward on the ground plane.
func flat_forward() -> Vector3:
	return Basis(Vector3.UP, yaw) * Vector3.FORWARD


## Moves to the target at once, without follow lag.
func snap() -> void:
	if _target != null:
		global_position = _focus_point()
	_apply_rotation()
	reset_physics_interpolation()


func _physics_process(delta: float) -> void:
	if _target == null:
		return
	if active and Menus.is_open():
		# Online sessions keep running under menus; give the menu the mouse.
		if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
			capture_mouse(false)
	elif active:
		var look: Vector2 = Input.get_vector(&"look_left", &"look_right", &"look_up", &"look_down")
		if look != Vector2.ZERO:
			rotate_view(-look.x * look_speed * delta, -look.y * look_speed * delta)
	var focus: Vector3 = _focus_point()
	if _reduced_motion:
		global_position = focus
	else:
		global_position = global_position.lerp(focus, minf(follow_sharpness * delta, 1.0))
	_apply_rotation()


func _unhandled_input(event: InputEvent) -> void:
	if not active or Menus.is_open() or not bool(_target.get(&"controls_enabled")):
		return
	var motion: InputEventMouseMotion = event as InputEventMouseMotion
	if motion != null and mouse_look_enabled():
		var step: float = mouse_speed * _sensitivity
		rotate_view(-motion.relative.x * step, -motion.relative.y * step)
		return
	var button: InputEventMouseButton = event as InputEventMouseButton
	if button != null and button.pressed and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
		capture_mouse(true)


func _exit_tree() -> void:
	if active:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _focus_point() -> Vector3:
	return _target.global_position + Vector3.UP * height


func _apply_rotation() -> void:
	rotation = Vector3(0.0, yaw, 0.0)
	if pitch_node != null:
		pitch_node.rotation = Vector3(pitch, 0.0, 0.0)


func _read_settings() -> void:
	_sensitivity = float(Settings.get_value(SECTION, "mouse_sensitivity", 1.0))
	var invert: bool = bool(Settings.get_value(SECTION, "invert_y", false))
	_invert_y = invert or bool(Settings.get_value("input", "invert_look_y", false))
	camera.fov = float(Settings.get_value(SECTION, "fov", default_fov))
	_reduced_motion = bool(Settings.get_value("access", "reduced_motion", false))


func _on_setting_changed(section: String, _key: String) -> void:
	if section == SECTION or section == "input" or section == "access":
		_read_settings()


func _on_paused_changed(paused: bool) -> void:
	capture_mouse(not paused)


func _on_level_completed(_result: Dictionary) -> void:
	capture_mouse(false)
