class_name CameraRig3D
extends Camera3D
## A 3D camera that follows a target from an offset, with a dead zone, smoothing and
## look-ahead, pulls back to frame a group of targets, and shakes (Cameras.shake) as
## much as the player's screen shake setting allows.
##
##   Level
##     Camera (CameraRig3D)  target = ../Players/Player, follow_offset = (0, 6, 8)
##
## Code: `rig.target_node = player`, `rig.snap()` after a respawn or a level load,
## `rig.add_trauma(0.5)` or `Cameras.shake(0.5)` for a hit. Put two or more nodes in
## `group_targets` (or name a `group_name`) to keep them all in view.
## Shake moves the view (h_offset, v_offset), never the camera's transform.

const SHAKE_FREQUENCY := 18.0

## What to follow (ignored while two or more group targets are set).
@export var target: NodePath
@export var take_over: bool = true
## From the followed point to the camera, in world space (metres).
@export var follow_offset: Vector3 = Vector3(0.0, 6.0, 8.0)
## Looked-at point above the followed one (a character's chest, not its feet).
@export var look_height: float = 1.0
@export_group("Follow")
## Half the size of the box, in metres (x, y, z), the target moves in first.
@export var dead_zone: Vector3 = Vector3(1.0, 0.5, 1.0)
@export var smoothing: float = 5.0
@export_group("Look ahead")
@export var look_ahead: float = 2.0
@export var look_ahead_speed: float = 6.0
@export var look_ahead_smoothing: float = 2.5
@export_group("Framing")
@export var group_targets: Array[NodePath] = []
@export var group_name: StringName = &""
@export var framing_margin: float = 2.0
## How far the camera may pull back, as a multiple of follow_offset.
@export var max_pull_back: float = 3.0
@export_group("Shake")
@export var max_shake_offset: Vector2 = Vector2(0.35, 0.25)
@export var trauma_decay: float = 1.5

var target_node: Node3D = null
var focus: Vector3 = Vector3.ZERO
var look_offset: Vector3 = Vector3.ZERO
## follow_offset is multiplied by this to frame a group (1 for one target).
var pull_back: float = 1.0
var trauma: float = 0.0
var _last_target: Vector3 = Vector3.INF
var _time: float = 0.0
var _rng: RandomNumberGenerator


func _ready() -> void:
	add_to_group(Cameras.GROUP)
	_rng = Replays.rng("camera:%s" % name)
	if not target.is_empty():
		target_node = get_node_or_null(target) as Node3D
	snap()
	if take_over:
		make_current.call_deferred()


func snap() -> void:
	var at: Vector3 = _goal()
	focus = at
	look_offset = Vector3.ZERO
	_last_target = at
	_place()


func add_trauma(amount: float) -> void:
	trauma = clampf(trauma + amount, 0.0, 1.0)


func shake_amount() -> float:
	return trauma * trauma * Cameras.shake_scale()


func framed() -> Array[Node3D]:
	var found: Array[Node3D] = []
	for path: NodePath in group_targets:
		var node: Node3D = get_node_or_null(path) as Node3D
		if node != null:
			found.append(node)
	if group_name != &"":
		for node: Node in get_tree().get_nodes_in_group(group_name):
			if node is Node3D and not found.has(node):
				found.append(node)
	return found


func _physics_process(delta: float) -> void:
	_time += delta
	var goal: Vector3 = _goal()
	var wanted: Vector3 = focus
	var away: Vector3 = goal - focus
	for axis: int in 3:
		if absf(away[axis]) > dead_zone[axis]:
			wanted[axis] = goal[axis] - signf(away[axis]) * dead_zone[axis]
	focus = focus.lerp(wanted, _blend(smoothing, delta))
	var velocity: Vector3 = Vector3.ZERO
	if _last_target != Vector3.INF and delta > 0.0:
		velocity = (goal - _last_target) / delta
	_last_target = goal
	var lead: Vector3 = Vector3.ZERO
	if framed().size() < 2 and look_ahead_speed > 0.0:
		var flat: Vector3 = Vector3(velocity.x, 0.0, velocity.z)
		lead = (flat / look_ahead_speed).limit_length(1.0) * look_ahead
	look_offset = look_offset.lerp(lead, _blend(look_ahead_smoothing, delta))
	pull_back = lerpf(pull_back, _wanted_pull_back(), _blend(smoothing, delta))
	_place()
	_shake(delta)


func _place() -> void:
	var looked: Vector3 = focus + look_offset + Vector3.UP * look_height
	global_position = focus + look_offset + follow_offset * pull_back
	if not global_position.is_equal_approx(looked):
		look_at(
			looked, Vector3.UP if absf(follow_offset.normalized().y) < 0.99 else Vector3.FORWARD
		)


func _goal() -> Vector3:
	var group: Array[Node3D] = framed()
	if group.size() >= 2:
		return _bounds(group).get_center()
	if target_node == null or not is_instance_valid(target_node):
		target_node = Game.local_player() as Node3D if Game.current_level() != null else null
	return target_node.global_position if target_node != null else focus


## How far back the camera must sit so the whole group (plus margin) fits the view.
func _wanted_pull_back() -> float:
	var group: Array[Node3D] = framed()
	if group.size() < 2:
		return 1.0
	var box: AABB = _bounds(group)
	var radius: float = box.size.length() * 0.5 + framing_margin
	var half_fov: float = deg_to_rad(fov) * 0.5
	var distance: float = radius / maxf(tan(half_fov), 0.01)
	return clampf(distance / maxf(follow_offset.length(), 0.01), 1.0, max_pull_back)


func _shake(delta: float) -> void:
	trauma = maxf(trauma - trauma_decay * delta, 0.0)
	var amount: float = shake_amount()
	if amount <= 0.0:
		h_offset = 0.0
		v_offset = 0.0
		return
	var t: float = _time * SHAKE_FREQUENCY
	h_offset = max_shake_offset.x * amount * sin(t * 1.3 + _rng.randf() * 0.5)
	v_offset = max_shake_offset.y * amount * cos(t * 1.7 + _rng.randf() * 0.5)


static func _bounds(nodes: Array[Node3D]) -> AABB:
	var box: AABB = AABB(nodes[0].global_position, Vector3.ZERO)
	for node: Node3D in nodes:
		box = box.expand(node.global_position)
	return box


static func _blend(rate: float, delta: float) -> float:
	return 1.0 if rate <= 0.0 else 1.0 - exp(-rate * delta)
