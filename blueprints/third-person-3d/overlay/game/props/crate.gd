extends RigidBody3D
## A crate the player can pick up, carry and put down. While carried it
## follows the carrier's hands and does not collide, so it cannot press a
## plate until it is put down.

signal carried_changed(carrier: Node3D)

const SYNC_PROPERTIES: Array[String] = [
	"position",
	"rotation",
	"linear_velocity",
	"angular_velocity",
]

@export var save_id: String = "crate"

var carrier: Node3D = null

var _home: Transform3D
var _layer: int = 0
var _mask: int = 0


func _ready() -> void:
	_home = global_transform
	_layer = collision_layer
	_mask = collision_mask


func _physics_process(_delta: float) -> void:
	if carrier != null and is_instance_valid(carrier):
		global_transform = carrier.call(&"carry_transform")


func interact_prompt() -> String:
	return tr("Put down") if carrier != null else tr("Pick up")


func interact() -> void:
	if carrier != null:
		_drop()
	else:
		var actor: Node3D = _actor()
		if actor == null or actor.get(&"carrying") != null:
			return
		_pick_up(actor)
	GameEvents.world_changed.emit(self)


func is_carried() -> bool:
	return carrier != null


## Puts the crate back where the level placed it (it fell off the world).
func return_home() -> void:
	if carrier != null:
		_drop()
	_place(_home)


func save_state() -> Dictionary:
	return {
		"transform": global_transform,
		"carrier": str(carrier.get_path()) if carrier != null else "",
	}


func load_state(state: Dictionary) -> void:
	if carrier != null:
		_release()
	_place(state.get("transform", global_transform))
	var path: String = str(state.get("carrier", ""))
	if path != "":
		var holder: Node3D = get_node_or_null(path) as Node3D
		if holder != null and holder.has_method(&"set_carrying"):
			_pick_up(holder)


func _pick_up(actor: Node3D) -> void:
	carrier = actor
	freeze = true
	collision_layer = 0
	collision_mask = 0
	actor.call(&"set_carrying", self)
	carried_changed.emit(actor)


func _drop() -> void:
	var holder: Node3D = carrier
	_release()
	_place(holder.call(&"drop_transform"))


func _release() -> void:
	var holder: Node3D = carrier
	carrier = null
	collision_layer = _layer
	collision_mask = _mask
	freeze = false
	if is_instance_valid(holder):
		holder.call(&"set_carrying", null)
	carried_changed.emit(null)


func _place(at: Transform3D) -> void:
	global_transform = at
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO
	sleeping = false
	reset_physics_interpolation()


func _actor() -> Node3D:
	var id: int = Game.actor_id()
	for player: Node in Game.players():
		if player.get_multiplayer_authority() == id:
			return player as Node3D
	return Game.local_player() as Node3D
