extends RigidBody3D
## Something a hand (or the desktop player) can pick up, carry, drop and
## throw. While held it is frozen (kinematic) and follows its holder; let go,
## it falls with the velocity it was thrown with.
##
## Any node in group `pickable` with `grab` and `release` works with the VR
## feature's hands, which grab the nearest one within reach.

signal grabbed(holder: Node3D)
signal released(velocity: Vector3)

@export var save_id: String = "pickup"
## Shown in the hint: "Pick up the <name>". An English msgid.
@export var display_name: String = "object"
## False keeps it where it is (for example inside the closed case).
@export var can_be_grabbed: bool = true

## The node it follows while held, or null.
var holder: Node3D = null
var _offset: Transform3D = Transform3D.IDENTITY
var _ignored: CollisionObject3D = null


func _ready() -> void:
	add_to_group(&"saveable")
	add_to_group(&"pickable")
	freeze_mode = RigidBody3D.FREEZE_MODE_KINEMATIC
	can_sleep = true


func can_grab() -> bool:
	return can_be_grabbed and holder == null


func is_held() -> bool:
	return holder != null


func prompt() -> String:
	return tr("Pick up the %s") % tr(display_name)


## Starts following `by`. With `snap` it sits right at `by`; otherwise it
## keeps where it was relative to `by` (a hand closing around it). Returns
## false when it cannot be taken.
func grab(by: Node3D, snap: bool = false) -> bool:
	if by == null or not can_grab():
		return false
	holder = by
	_offset = (
		Transform3D.IDENTITY if snap else by.global_transform.affine_inverse() * global_transform
	)
	freeze = true
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO
	_ignore(_carrier(by))
	grabbed.emit(by)
	return true


## Lets go and moves on with `velocity` (metres a second).
func release(velocity: Vector3 = Vector3.ZERO) -> void:
	if holder == null:
		return
	holder = null
	freeze = false
	linear_velocity = velocity
	angular_velocity = Vector3.ZERO
	sleeping = false
	_ignore(null)
	released.emit(velocity)
	GameEvents.world_changed.emit(self)


func _physics_process(_delta: float) -> void:
	if holder == null:
		return
	if not is_instance_valid(holder) or not holder.is_inside_tree():
		release()
		return
	global_transform = holder.global_transform * _offset


func save_state() -> Dictionary:
	return {"transform": global_transform, "can_be_grabbed": can_be_grabbed}


func load_state(state: Dictionary) -> void:
	release()
	global_transform = state.get("transform", global_transform)
	can_be_grabbed = bool(state.get("can_be_grabbed", can_be_grabbed))
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO


## The physics body carrying `by` (the player), so the held object does not
## push its own carrier.
func _carrier(by: Node) -> CollisionObject3D:
	var current: Node = by
	while current != null:
		if current is CharacterBody3D:
			return current as CollisionObject3D
		current = current.get_parent()
	return null


func _ignore(body: CollisionObject3D) -> void:
	if is_instance_valid(_ignored):
		remove_collision_exception_with(_ignored)
	_ignored = body
	if body != null:
		add_collision_exception_with(body)
