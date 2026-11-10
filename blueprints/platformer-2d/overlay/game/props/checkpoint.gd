extends Area2D
## A checkpoint post. Touching it makes it the place the player comes back to
## after spikes or a fall. The node sits where the player's feet go.

signal reached(checkpoint: Node)

const IDLE_COLOUR := Color("#8c95a6")
const ACTIVE_COLOUR := Color("#5fd38d")

@export var checkpoint_id: String = "checkpoint"

var active: bool = false

@onready var banner: Polygon2D = $Banner


func _ready() -> void:
	body_entered.connect(_on_body_entered)
	_show()


func set_active(on: bool) -> void:
	active = on
	_show()


## Run through `Game.request_action` when a player touches the post.
func activate() -> void:
	reached.emit(self)
	GameEvents.world_changed.emit(self)


func respawn_position() -> Vector2:
	return global_position


## State another game instance needs to mirror this post (online play). The
## level saves which checkpoint is active, so the post is not in `saveable`.
func save_state() -> Dictionary:
	return {"active": active}


func load_state(state: Dictionary) -> void:
	if bool(state.get("active", false)) and not active:
		reached.emit(self)


func _show() -> void:
	if banner != null:
		banner.color = ACTIVE_COLOUR if active else IDLE_COLOUR


func _on_body_entered(body: Node2D) -> void:
	if active or not body.is_in_group(&"player") or not body.is_multiplayer_authority():
		return
	Game.request_action(self, &"activate", [])
