extends Area3D
## A coin. Touching it collects it; the level counts collected coins.

signal collected_changed(coin: Node)

@export var save_id: String = "coin"
@export var spin_speed: float = 2.5
@export var bob_height: float = 0.15

var collected: bool = false
var _time: float = 0.0

@onready var mesh: Node3D = $Mesh


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	if collected or bool(Settings.get_value("access", "reduced_motion", false)):
		return
	_time += delta
	mesh.rotation.y = wrapf(_time * spin_speed, -PI, PI)
	mesh.position.y = sin(_time * 2.0) * bob_height


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


func _on_body_entered(body: Node3D) -> void:
	if collected or not body.is_in_group(&"player") or not body.is_multiplayer_authority():
		return
	Game.request_action(self, &"collect", [])
