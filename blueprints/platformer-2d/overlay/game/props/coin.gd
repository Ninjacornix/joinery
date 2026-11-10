extends Area2D
## A coin. Touching it collects it; the level counts collected coins. State
## changes go through `Game.request_action`, so an online session can run
## them on the server.

signal collected_changed(coin: Node)

@export var save_id: String = "coin"
@export var bob_height: float = 1.5
@export var bob_speed: float = 3.0

var collected: bool = false
var _time: float = 0.0

@onready var visual: Node2D = $Visual


func _ready() -> void:
	body_entered.connect(_on_body_entered)
	_time = position.x * 0.05


func _process(delta: float) -> void:
	if collected or bool(Settings.get_value("access", "reduced_motion", false)):
		visual.position.y = 0.0
		return
	_time += delta
	visual.position.y = roundf(sin(_time * bob_speed) * bob_height)


func collect() -> void:
	if collected:
		return
	_set_collected(true)
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"collected": collected}


func load_state(state: Dictionary) -> void:
	_set_collected(bool(state.get("collected", false)))


func _set_collected(value: bool) -> void:
	if value == collected:
		return
	collected = value
	visible = not value
	set_deferred(&"monitoring", not value)
	collected_changed.emit(self)


func _on_body_entered(body: Node2D) -> void:
	if collected or not body.is_in_group(&"player") or not body.is_multiplayer_authority():
		return
	Game.request_action(self, &"collect", [])
