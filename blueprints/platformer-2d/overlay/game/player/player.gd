extends CharacterBody2D
## Side-view player: a quick run, a jump whose height follows how long the
## button is held, coyote time (jump just after running off a ledge), a jump
## buffer (press just before landing), and drop-through one-way platforms.
## Input is read only on the peer that owns this player; other peers see it
## through SYNC_PROPERTIES. The node's origin is at the player's feet.

signal jumped
signal landed
signal respawned

const SYNC_PROPERTIES: Array[String] = ["position", "velocity", "Visual:scale", "Visual:rotation"]
## Physics layer of one-way platforms (see map_builder.gd).
const ONE_WAY_LAYER := 4

@export var save_id: String = "player"
@export_group("Run")
@export var run_speed: float = 130.0
@export var ground_acceleration: float = 1100.0
@export var ground_deceleration: float = 1400.0
@export var air_acceleration: float = 800.0
@export var air_deceleration: float = 450.0
@export_group("Jump")
@export var jump_velocity: float = 300.0
@export var rise_gravity: float = 720.0
@export var fall_gravity: float = 1100.0
@export var max_fall_speed: float = 360.0
## Upward speed is multiplied by this when jump is released early.
@export_range(0.0, 1.0) var jump_cut: float = 0.4
## Seconds after leaving a ledge in which a jump still works.
@export var coyote_time: float = 0.1
## Seconds a jump press is remembered before landing.
@export var jump_buffer_time: float = 0.12
## Both windows are multiplied by this with the "Forgiving jumps" setting.
@export var forgiving_scale: float = 2.0
@export var drop_through_time: float = 0.25
@export_group("Feel")
@export var look_ahead: float = 24.0
@export var lean: float = 0.12
@export var squash_recovery: float = 14.0
@export var shake_strength: float = 3.0
@export var shake_time: float = 0.2

## False while a win screen or cut-in owns the controls.
var controls_enabled: bool = true
## 1 facing right, -1 facing left.
var facing: int = 1

var _coyote: float = 0.0
var _buffer: float = 0.0
var _drop: float = 0.0
var _shake: float = 0.0
var _was_on_floor: bool = true

@onready var visual: Node2D = $Visual
@onready var camera: Camera2D = $Camera
@onready var dust: CPUParticles2D = $Dust


func _ready() -> void:
	camera.enabled = is_multiplayer_authority()
	if camera.enabled:
		camera.make_current()


func _physics_process(delta: float) -> void:
	if not is_multiplayer_authority():
		return
	var on_floor: bool = is_on_floor()
	_coyote = coyote_window() if on_floor else maxf(_coyote - delta, 0.0)
	_buffer = maxf(_buffer - delta, 0.0)
	_drop = maxf(_drop - delta, 0.0)
	set_collision_mask_value(ONE_WAY_LAYER, _drop <= 0.0)

	var axis: float = 0.0
	var pressed: bool = false
	# An online game keeps running under the pause menu; the arrow keys that
	# move through the menu must not also run and jump.
	if controls_enabled and not Menus.is_open():
		axis = Input.get_axis(&"move_left", &"move_right")
		if Input.is_action_just_pressed(&"jump"):
			if _wants_drop() and standing_on_one_way():
				_drop = drop_through_time
				_coyote = 0.0
				on_floor = false
				set_collision_mask_value(ONE_WAY_LAYER, false)
			else:
				pressed = true
				_buffer = buffer_window()
		if Input.is_action_just_released(&"jump") and velocity.y < 0.0:
			velocity.y *= jump_cut

	var gravity: float = rise_gravity if velocity.y < 0.0 else fall_gravity
	velocity.y = minf(velocity.y + gravity * delta, max_fall_speed)
	if (pressed or _buffer > 0.0) and (on_floor or _coyote > 0.0):
		_jump()

	var target: float = axis * run_speed
	var rate: float
	if absf(axis) > 0.01:
		rate = ground_acceleration if on_floor else air_acceleration
		facing = 1 if axis > 0.0 else -1
	else:
		rate = ground_deceleration if on_floor else air_deceleration
	velocity.x = move_toward(velocity.x, target, rate * delta)
	move_and_slide()

	if is_on_floor() and not _was_on_floor:
		_squash(Vector2(1.25, 0.75))
		_puff()
		landed.emit()
	_was_on_floor = is_on_floor()
	_animate(delta, axis)


## Seconds after leaving a ledge in which a jump still works.
func coyote_window() -> float:
	return coyote_time * (forgiving_scale if _forgiving() else 1.0)


## Seconds a jump press is remembered before landing.
func buffer_window() -> float:
	return jump_buffer_time * (forgiving_scale if _forgiving() else 1.0)


## Whether the floor under the player is a one-way platform.
func standing_on_one_way() -> bool:
	if not is_on_floor():
		return false
	for i: int in get_slide_collision_count():
		var hit: KinematicCollision2D = get_slide_collision(i)
		var body: Node = hit.get_collider() as Node
		if body != null and body.is_in_group(&"one_way") and hit.get_normal().y < -0.5:
			return true
	return false


## Keeps the camera inside the level (world rectangle in pixels).
func set_camera_limits(area: Rect2) -> void:
	camera.limit_left = floori(area.position.x)
	camera.limit_top = floori(area.position.y)
	camera.limit_right = ceili(area.end.x)
	camera.limit_bottom = ceili(area.end.y)
	camera.reset_smoothing()


## Puts the player at `at` (feet), standing still, facing the same way.
func respawn(at: Vector2) -> void:
	global_position = at
	velocity = Vector2.ZERO
	_coyote = 0.0
	_buffer = 0.0
	_drop = 0.0
	_was_on_floor = false
	set_collision_mask_value(ONE_WAY_LAYER, true)
	visual.scale = Vector2(float(facing), 1.0)
	visual.rotation = 0.0
	reset_physics_interpolation()
	camera.reset_smoothing()
	respawned.emit()


## A short camera shake, scaled by the screen shake setting.
func shake() -> void:
	_shake = shake_time


func save_state() -> Dictionary:
	return {"position": global_position, "facing": facing}


func load_state(state: Dictionary) -> void:
	facing = 1 if int(state.get("facing", facing)) >= 0 else -1
	respawn(state.get("position", global_position))


func _jump() -> void:
	velocity.y = -jump_velocity
	_buffer = 0.0
	_coyote = 0.0
	# A buffered press that was already let go gives the short hop.
	if not Input.is_action_pressed(&"jump"):
		velocity.y *= jump_cut
	_squash(Vector2(0.75, 1.3))
	_puff()
	jumped.emit()


func _wants_drop() -> bool:
	return InputMap.has_action(&"move_down") and Input.is_action_pressed(&"move_down")


func _forgiving() -> bool:
	return bool(Settings.get_value("platformer", "forgiving_jumps", false))


func _reduced_motion() -> bool:
	return bool(Settings.get_value("access", "reduced_motion", false))


func _squash(amount: Vector2) -> void:
	if _reduced_motion():
		return
	visual.scale = Vector2(amount.x * float(facing), amount.y)


func _puff() -> void:
	if not _reduced_motion():
		dust.restart()


func _animate(delta: float, axis: float) -> void:
	var rest: Vector2 = Vector2(float(facing), 1.0)
	var settle: float = minf(squash_recovery * delta, 1.0)
	if _reduced_motion():
		visual.scale = rest
		visual.rotation = 0.0
	else:
		visual.scale = Vector2(
			lerpf(absf(visual.scale.x), 1.0, settle) * float(facing),
			lerpf(visual.scale.y, 1.0, settle)
		)
		var tilt: float = axis * lean if is_on_floor() else 0.0
		visual.rotation = lerpf(visual.rotation, tilt, settle)
	camera.offset.x = lerpf(camera.offset.x, float(facing) * look_ahead, minf(3.0 * delta, 1.0))
	camera.offset.y = 0.0
	if _shake > 0.0:
		_shake = maxf(_shake - delta, 0.0)
		var strength: float = shake_strength * Settings.screen_shake()
		camera.offset += Vector2(randf_range(-1.0, 1.0), randf_range(-1.0, 1.0)) * strength
