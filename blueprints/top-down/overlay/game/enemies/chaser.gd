extends CharacterBody2D
## An enemy that chases the nearest living player and hurts on contact. It
## fades in for `spawn_grace` seconds (harmless and still), flashes and is
## knocked back when hit, and is defeated at 0 health. The "brute" variant is
## bigger, slower, tougher and hits harder. Only the authority (the server
## online, this game offline) moves enemies; others see SYNC_PROPERTIES.

signal defeated(enemy: Node)
signal was_hit(health: int)

const SYNC_PROPERTIES: Array[String] = ["position", "velocity", "Pivot:rotation", "health"]
const BRUTE := {
	"speed": 105.0,
	"max_health": 5,
	"contact_damage": 2,
	"points": 30,
	"size": 1.5,
	"colour": Color("#9c2f45"),
}
const FLASH_COLOUR := Color("#fff4d6")

@export_enum("chaser", "brute") var variant: String = "chaser"
@export var speed: float = 150.0
@export var acceleration: float = 900.0
@export var max_health: int = 2
@export var contact_damage: int = 1
@export var points: int = 10
@export var knockback: float = 420.0
## Seconds after spawning before it moves or hurts.
@export var spawn_grace: float = 0.6

## -1 until ready, then max_health (a spawn or a load may set it first).
var health: int = -1
## Set by the level when the fight is over: the enemy stops and stays harmless.
var frozen: bool = false

var _flash: float = 0.0
var _colour: Color = Color.WHITE
## The full grace period, for the fade-in (spawn_grace counts down to 0).
var _grace_total: float = 0.0

@onready var pivot: Node2D = $Pivot
@onready var body: Polygon2D = $Pivot/Body
@onready var shape: CollisionShape2D = $Shape


func _ready() -> void:
	if variant == "brute":
		speed = float(BRUTE["speed"])
		max_health = int(BRUTE["max_health"])
		contact_damage = int(BRUTE["contact_damage"])
		points = int(BRUTE["points"])
		var size: float = float(BRUTE["size"])
		pivot.scale = Vector2.ONE * size
		var circle: CircleShape2D = (shape.shape as CircleShape2D).duplicate() as CircleShape2D
		circle.radius *= size
		shape.shape = circle
		body.color = BRUTE["colour"]
	if health < 0:
		health = max_health
	_grace_total = spawn_grace
	_colour = body.color
	_update_look()


func _physics_process(delta: float) -> void:
	spawn_grace = maxf(spawn_grace - delta, 0.0)
	if _flash > 0.0:
		_flash = maxf(_flash - delta, 0.0)
	_update_look()
	if not Game.session().is_authority():
		return
	var desired: Vector2 = Vector2.ZERO
	if not frozen and health > 0 and spawn_grace <= 0.0:
		var target: Node2D = nearest_player()
		if target != null:
			desired = (target.global_position - global_position).normalized() * speed
	velocity = velocity.move_toward(desired, acceleration * delta)
	if desired.length_squared() > 1.0:
		pivot.rotation = lerp_angle(pivot.rotation, desired.angle(), minf(delta * 10.0, 1.0))
	move_and_slide()


## True when touching it hurts: after the spawn fade-in, alive, not frozen.
func is_harmful() -> bool:
	return spawn_grace <= 0.0 and health > 0 and not frozen


## The closest player that is still alive, or null.
func nearest_player() -> Node2D:
	var best: Node2D = null
	var best_distance: float = INF
	for node: Node in Game.players():
		var player: Node2D = node as Node2D
		if player == null or (player.has_method(&"is_alive") and not bool(player.call(&"is_alive"))):
			continue
		var distance: float = global_position.distance_squared_to(player.global_position)
		if distance < best_distance:
			best_distance = distance
			best = player
	return best


## Run through `Game.request_action` so an online session runs it on the server.
func take_hit(damage: int, from: Vector2) -> void:
	if health <= 0 or damage <= 0:
		return
	health = maxi(health - damage, 0)
	var away: Vector2 = global_position - from
	if away.length_squared() > 0.01:
		velocity = away.normalized() * knockback
	_flash = 0.12
	was_hit.emit(health)
	GameEvents.world_changed.emit(self)
	if health == 0:
		defeated.emit(self)
		queue_free()


func save_state() -> Dictionary:
	return {"variant": variant, "position": position, "health": health}


func load_state(state: Dictionary) -> void:
	var at: Variant = state.get("position")
	if at is Vector2:
		position = at
	health = clampi(int(state.get("health", health)), 0, max_health)


func _update_look() -> void:
	if body == null:
		return
	body.color = FLASH_COLOUR if _flash > 0.0 else _colour
	# Faint (35 %) when it appears, fully solid once it can hurt.
	var waiting: float = spawn_grace / _grace_total if _grace_total > 0.0 else 0.0
	var fade: float = 1.0 - clampf(waiting, 0.0, 1.0) * 0.65
	modulate = Color(1.0, 1.0, 1.0, fade)
