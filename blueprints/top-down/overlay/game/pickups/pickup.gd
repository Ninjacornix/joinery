extends Area2D
## Something to walk over: a gem (adds `amount` to the score) or a heart
## (restores `amount` health; left alone while the player is at full health).
## The level decides what a pickup gives through the `picked` signal.

## Fired once when a player takes it (not when a save is loaded).
signal picked(pickup: Node, peer_id: int)

@export var save_id: String = "gem"
@export_enum("gem", "heart") var kind: String = "gem"
## Score for a gem, health for a heart.
@export var amount: int = 10
@export var bob_height: float = 4.0

var collected: bool = false
var _time: float = 0.0

@onready var art: Node2D = $Art


func _ready() -> void:
	_time = position.x * 0.013


func _process(delta: float) -> void:
	if collected or bool(Settings.call(&"reduced_motion")):
		art.position.y = 0.0
		return
	_time += delta
	art.position.y = sin(_time * 3.0) * bob_height


func _physics_process(_delta: float) -> void:
	if collected:
		return
	for body: Node2D in get_overlapping_bodies():
		if not body.is_in_group(&"player") or not body.is_multiplayer_authority():
			continue
		if not _wanted_by(body):
			continue
		Game.request_action(self, &"collect", [])
		return


## Run through `Game.request_action`; the actor is the player who took it.
func collect() -> void:
	if collected:
		return
	_set_collected(true)
	picked.emit(self, Game.actor_id())
	GameEvents.world_changed.emit(self)


func save_state() -> Dictionary:
	return {"collected": collected}


func load_state(state: Dictionary) -> void:
	_set_collected(bool(state.get("collected", false)))


func _wanted_by(body: Node) -> bool:
	if kind != "heart":
		return true
	return int(body.get(&"health")) < int(body.get(&"max_health")) and int(body.get(&"health")) > 0


func _set_collected(value: bool) -> void:
	collected = value
	visible = not value
	set_deferred(&"monitoring", not value)
