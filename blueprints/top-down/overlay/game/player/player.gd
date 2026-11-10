extends CharacterBody2D
## Top-down player: moves in eight directions at one speed, swings a melee
## arc in front, dashes with a cooldown (and cannot be hit while dashing), and
## takes contact damage from enemies with a short invulnerable window after
## each hit. Input is read only on the peer that owns this player; other
## peers see it through SYNC_PROPERTIES.

signal health_changed(health: int, max_health: int)
signal hurt(amount: int)
signal died
signal attacked
signal dashed

const SYNC_PROPERTIES: Array[String] = ["position", "velocity", "Pivot:rotation", "health"]
## Physics layer 3, "enemies".
const ENEMY_MASK := 4

@export var save_id: String = "player"
@export_group("Movement")
@export var move_speed: float = 260.0
@export var acceleration: float = 2400.0
@export_group("Attack")
@export var attack_damage: int = 1
@export var attack_cooldown: float = 0.3
@export var swipe_time: float = 0.12
@export_group("Dash")
@export var dash_speed: float = 900.0
@export var dash_time: float = 0.14
@export var dash_cooldown: float = 0.7
@export_group("Health")
@export var max_health: int = 5
@export var hurt_invulnerability: float = 1.0
@export var hurt_knockback: float = 320.0
@export var shake_pixels: float = 10.0

var health: int = 5
## False while a win or lose screen owns the controls.
var controls_enabled: bool = true
## The way the player faces; attacks and still dashes go this way.
var facing: Vector2 = Vector2.RIGHT

var _attack_left: float = 0.0
var _swipe_left: float = 0.0
var _dash_left: float = 0.0
var _dash_cooldown_left: float = 0.0
var _dash_direction: Vector2 = Vector2.RIGHT
var _invulnerable_left: float = 0.0
var _shake: float = 0.0
var _rng: RandomNumberGenerator = RandomNumberGenerator.new()

@onready var pivot: Node2D = $Pivot
@onready var body: Polygon2D = $Body
@onready var swipe: Polygon2D = $Pivot/Swipe
@onready var attack_shape: CollisionShape2D = $Pivot/AttackArea/Arc
@onready var hurtbox: Area2D = $Hurtbox
@onready var camera: Camera2D = $Camera


func _ready() -> void:
	health = max_health
	swipe.visible = false
	camera.enabled = is_multiplayer_authority()
	if camera.enabled:
		camera.make_current()
	face(facing)


func _process(delta: float) -> void:
	if _shake > 0.0:
		_shake = maxf(_shake - delta * 4.0, 0.0)
		var jitter: Vector2 = Vector2(_rng.randf_range(-1.0, 1.0), _rng.randf_range(-1.0, 1.0))
		camera.offset = jitter * shake_pixels * _shake
	else:
		camera.offset = Vector2.ZERO
	_update_look()


func _physics_process(delta: float) -> void:
	_tick(delta)
	if not is_multiplayer_authority():
		return
	var input: Vector2 = Vector2.ZERO
	# An online game keeps running under the pause menu; the keys that move
	# through the menu must not also move, swing and dash.
	if controls_enabled and is_alive() and not Menus.is_open():
		input = Input.get_vector(&"move_left", &"move_right", &"move_up", &"move_down")
		if Input.is_action_just_pressed(&"dash"):
			dash(input)
		if Input.is_action_just_pressed(&"attack"):
			attack()
	if is_dashing():
		velocity = _dash_direction * dash_speed
	else:
		velocity = velocity.move_toward(input * move_speed, acceleration * delta)
		if input.length_squared() > 0.01:
			face(input)
	move_and_slide()
	if is_alive():
		_check_contact()


func _unhandled_input(event: InputEvent) -> void:
	# A mouse attack swings toward the cursor.
	if not is_multiplayer_authority() or not controls_enabled or Menus.is_open():
		return
	var click: InputEventMouseButton = event as InputEventMouseButton
	if click != null and click.pressed and event.is_action_pressed(&"attack"):
		face(get_global_mouse_position() - global_position)


## Turns the player (and its attack arc) toward `direction`.
func face(direction: Vector2) -> void:
	if direction.length_squared() < 0.0001:
		return
	facing = direction.normalized()
	if pivot != null:
		pivot.rotation = facing.angle()


## Swings at every enemy inside the arc in front. False while cooling down.
func attack() -> bool:
	if _attack_left > 0.0 or not is_alive():
		return false
	_attack_left = attack_cooldown
	_swipe_left = swipe_time
	swipe.visible = true
	attacked.emit()
	for enemy: Node in enemies_in_reach():
		Game.request_action(enemy, &"take_hit", [attack_damage, global_position])
	return true


## Enemies the attack arc touches right now.
func enemies_in_reach() -> Array[Node]:
	var query: PhysicsShapeQueryParameters2D = PhysicsShapeQueryParameters2D.new()
	query.shape = attack_shape.shape
	query.transform = attack_shape.global_transform
	query.collision_mask = ENEMY_MASK
	query.collide_with_areas = false
	query.collide_with_bodies = true
	var found: Array[Node] = []
	for hit: Dictionary in get_world_2d().direct_space_state.intersect_shape(query, 32):
		var node: Node = hit.get("collider") as Node
		if node != null and node.is_in_group(&"enemy") and not found.has(node):
			found.append(node)
	return found


## Bursts along `direction` (or the facing) for `dash_time`. False while the
## dash is cooling down.
func dash(direction: Vector2 = Vector2.ZERO) -> bool:
	if _dash_cooldown_left > 0.0 or not is_alive():
		return false
	_dash_direction = direction.normalized() if direction.length_squared() > 0.01 else facing
	_dash_left = dash_time
	_dash_cooldown_left = dash_time + dash_cooldown
	face(_dash_direction)
	dashed.emit()
	return true


func is_dashing() -> bool:
	return _dash_left > 0.0


## 1.0 when a dash is available, rising from 0.0 while it cools down.
func dash_ready() -> float:
	return 1.0 - clampf(_dash_cooldown_left / maxf(dash_time + dash_cooldown, 0.001), 0.0, 1.0)


func is_alive() -> bool:
	return health > 0


func is_invulnerable() -> bool:
	return _invulnerable_left > 0.0 or is_dashing()


## Loses `amount` health unless invulnerable. Returns whether it hurt.
func take_damage(amount: int, from: Vector2 = Vector2.INF) -> bool:
	if amount <= 0 or is_invulnerable() or not is_alive():
		return false
	health = maxi(health - amount, 0)
	_invulnerable_left = hurt_invulnerability
	if from != Vector2.INF and from != global_position:
		velocity = (global_position - from).normalized() * hurt_knockback
	shake(1.0)
	hurt.emit(amount)
	health_changed.emit(health, max_health)
	if health == 0:
		_die()
	return true


## Restores up to `amount` health. False when already full or down.
func heal(amount: int) -> bool:
	if amount <= 0 or not is_alive() or health >= max_health:
		return false
	health = mini(health + amount, max_health)
	health_changed.emit(health, max_health)
	return true


## Shakes this player's camera, scaled by the screen shake setting.
func shake(strength: float) -> void:
	var scale_setting: float = float(Settings.call(&"screen_shake"))
	_shake = maxf(_shake, strength * scale_setting)


func set_camera_limits(area: Rect2) -> void:
	camera.limit_left = floori(area.position.x)
	camera.limit_top = floori(area.position.y)
	camera.limit_right = ceili(area.end.x)
	camera.limit_bottom = ceili(area.end.y)


func save_state() -> Dictionary:
	return {"position": position, "health": health, "facing": facing}


func load_state(state: Dictionary) -> void:
	var at: Variant = state.get("position")
	if at is Vector2:
		position = at
	var look: Variant = state.get("facing")
	if look is Vector2:
		face(look)
	velocity = Vector2.ZERO
	health = clampi(int(state.get("health", health)), 0, max_health)
	if health > 0:
		controls_enabled = true
	health_changed.emit(health, max_health)


func _tick(delta: float) -> void:
	_attack_left = maxf(_attack_left - delta, 0.0)
	_dash_cooldown_left = maxf(_dash_cooldown_left - delta, 0.0)
	_invulnerable_left = maxf(_invulnerable_left - delta, 0.0)
	if _swipe_left > 0.0:
		_swipe_left -= delta
		if _swipe_left <= 0.0:
			swipe.visible = false
	if _dash_left > 0.0:
		_dash_left -= delta
		if _dash_left <= 0.0:
			_dash_left = 0.0
			velocity = velocity.limit_length(move_speed)


func _check_contact() -> void:
	if is_invulnerable():
		return
	for other: Node2D in hurtbox.get_overlapping_bodies():
		if not other.is_in_group(&"enemy") or not other.has_method(&"is_harmful"):
			continue
		if bool(other.call(&"is_harmful")):
			take_damage(int(other.get(&"contact_damage")), other.global_position)
			return


func _die() -> void:
	controls_enabled = false
	velocity = Vector2.ZERO
	_dash_left = 0.0
	died.emit()


func _update_look() -> void:
	if not is_alive():
		body.modulate = Color(0.45, 0.45, 0.5, 1.0)
		return
	var alpha: float = 1.0
	if _invulnerable_left > 0.0:
		var calm: bool = bool(Settings.call(&"reduced_motion"))
		alpha = 0.55 if calm or int(_invulnerable_left * 16.0) % 2 == 0 else 1.0
	body.modulate = Color(1.0, 1.0, 1.0, alpha)
