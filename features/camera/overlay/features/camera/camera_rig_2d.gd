class_name CameraRig2D
extends Camera2D
## A 2D camera that follows a target with a dead zone, smoothing and look-ahead,
## frames a group of targets by zooming out, and shakes (Cameras.shake) as much as
## the player's screen shake setting allows.
##
##   Level
##     Camera (CameraRig2D)  target = ../Players/Player
##
## Code: `rig.target_node = player`, `rig.snap()` after a respawn or a level load,
## `rig.add_trauma(0.5)` or `Cameras.shake(0.5)` for a hit. Put two or more nodes in
## `group_targets` (or name a `group_name`) to keep them all in view.

const SHAKE_FREQUENCY := 18.0

## What to follow (ignored while two or more group targets are set).
@export var target: NodePath
@export var take_over: bool = true
@export_group("Follow")
## Half the size of the box, in pixels, the target moves in before the camera does.
@export var dead_zone: Vector2 = Vector2(64.0, 48.0)
## How quickly the camera catches up (per second; 0 snaps).
@export var smoothing: float = 6.0
@export_group("Look ahead")
## How far ahead of a moving target the camera looks, in pixels.
@export var look_ahead: float = 80.0
## The target's speed (px/s) that gives the full look-ahead.
@export var look_ahead_speed: float = 300.0
@export var look_ahead_smoothing: float = 3.0
@export_group("Framing")
## Nodes kept in view together (two or more: the camera frames them all).
@export var group_targets: Array[NodePath] = []
## Or every node in this group.
@export var group_name: StringName = &""
@export var framing_margin: float = 120.0
@export var min_zoom: float = 0.4
@export var max_zoom: float = 1.0
@export_group("Shake")
@export var max_shake_offset: Vector2 = Vector2(28.0, 20.0)
@export var max_shake_roll: float = 0.04
## Trauma lost per second.
@export var trauma_decay: float = 1.5

var target_node: Node2D = null
## The point followed (dead zone and smoothing applied), without look-ahead.
var focus: Vector2 = Vector2.ZERO
var look_offset: Vector2 = Vector2.ZERO
var trauma: float = 0.0
var _last_target: Vector2 = Vector2.INF
var _time: float = 0.0
var _rng: RandomNumberGenerator


func _ready() -> void:
	add_to_group(Cameras.GROUP)
	process_callback = Camera2D.CAMERA2D_PROCESS_PHYSICS
	position_smoothing_enabled = false
	_rng = Replays.rng("camera:%s" % name)
	if not target.is_empty():
		target_node = get_node_or_null(target) as Node2D
	snap()
	if take_over:
		make_current.call_deferred()


## Jumps to the target at once (after a respawn, a teleport or a level load).
func snap() -> void:
	var at: Vector2 = _goal()
	focus = at
	look_offset = Vector2.ZERO
	_last_target = at
	global_position = at
	reset_smoothing()


func add_trauma(amount: float) -> void:
	trauma = clampf(trauma + amount, 0.0, 1.0)


## The shake shown now, after the player's setting (zero when it is off).
func shake_amount() -> float:
	return trauma * trauma * Cameras.shake_scale()


## The targets framed together, or [] when the camera follows one target.
func framed() -> Array[Node2D]:
	var found: Array[Node2D] = []
	for path: NodePath in group_targets:
		var node: Node2D = get_node_or_null(path) as Node2D
		if node != null:
			found.append(node)
	if group_name != &"":
		for node: Node in get_tree().get_nodes_in_group(group_name):
			if node is Node2D and not found.has(node):
				found.append(node)
	return found


func _physics_process(delta: float) -> void:
	_time += delta
	var goal: Vector2 = _goal()
	var wanted: Vector2 = focus
	var away: Vector2 = goal - focus
	if absf(away.x) > dead_zone.x:
		wanted.x = goal.x - signf(away.x) * dead_zone.x
	if absf(away.y) > dead_zone.y:
		wanted.y = goal.y - signf(away.y) * dead_zone.y
	focus = focus.lerp(wanted, _blend(smoothing, delta))
	var velocity: Vector2 = Vector2.ZERO
	if _last_target != Vector2.INF and delta > 0.0:
		velocity = (goal - _last_target) / delta
	_last_target = goal
	var lead: Vector2 = Vector2.ZERO
	if framed().size() < 2 and look_ahead_speed > 0.0:
		lead = (velocity / look_ahead_speed).limit_length(1.0) * look_ahead
	look_offset = look_offset.lerp(lead, _blend(look_ahead_smoothing, delta))
	global_position = focus + look_offset
	_frame(delta)
	_shake(delta)


## Where the camera wants to be: the target, or the middle of the framed group.
func _goal() -> Vector2:
	var group: Array[Node2D] = framed()
	if group.size() >= 2:
		return _bounds(group).get_center()
	if target_node == null or not is_instance_valid(target_node):
		target_node = _fallback_target()
	return target_node.global_position if target_node != null else global_position


func _frame(delta: float) -> void:
	var group: Array[Node2D] = framed()
	var wanted: float = max_zoom
	if group.size() >= 2:
		var bounds: Rect2 = _bounds(group).grow(framing_margin)
		var view: Vector2 = get_viewport_rect().size
		var fit: float = minf(view.x / maxf(bounds.size.x, 1.0), view.y / maxf(bounds.size.y, 1.0))
		wanted = clampf(fit, min_zoom, max_zoom)
	var now: float = lerpf(zoom.x, wanted, _blend(smoothing, delta))
	zoom = Vector2(now, now)


func _shake(delta: float) -> void:
	trauma = maxf(trauma - trauma_decay * delta, 0.0)
	var amount: float = shake_amount()
	if amount <= 0.0:
		offset = Vector2.ZERO
		rotation = 0.0
		return
	var t: float = _time * SHAKE_FREQUENCY
	offset = Vector2(
		max_shake_offset.x * amount * sin(t * 1.3 + _rng.randf() * 0.5),
		max_shake_offset.y * amount * cos(t * 1.7 + _rng.randf() * 0.5)
	)
	rotation = max_shake_roll * amount * sin(t * 0.9)
	ignore_rotation = false


func _fallback_target() -> Node2D:
	var player: Node = Game.local_player() if Game.current_level() != null else null
	return player as Node2D


static func _bounds(nodes: Array[Node2D]) -> Rect2:
	var box: Rect2 = Rect2(nodes[0].global_position, Vector2.ZERO)
	for node: Node2D in nodes:
		box = box.expand(node.global_position)
	return box


static func _blend(rate: float, delta: float) -> float:
	return 1.0 if rate <= 0.0 else 1.0 - exp(-rate * delta)
