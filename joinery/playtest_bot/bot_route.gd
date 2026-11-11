extends RefCounted
## Walks the player along routes Joinery worked out from a blockout layout (ADR 0043), to
## show in the engine itself that the spawn reaches every goal: real collision, real
## steps and jumps, the game's own player and input map.
##
## For each target the player is put back on the spawn, then steered through the
## route's waypoints with the movement actions (camera-relative in 3D, up/down/left/right
## top-down, left/right and jump side-on), jumping where the route jumps. A target is
## reached when the player gets within `reach` of it; it is missed when the player stops
## getting closer for too long. The route file is JSON:
## {mode, spawn, reach, targets: [{id, kind, at, waypoints: [[x, y(, z), jump], …]}]}.

const MOVES_3D: Array[StringName] = [&"move_forward", &"move_back", &"move_left", &"move_right"]
const MOVES_2D: Array[StringName] = [&"move_up", &"move_down", &"move_left", &"move_right"]
## Seconds without getting closer before a jump, and before giving up on a target.
const NUDGE_SECONDS := 2.5
const GIVE_UP_SECONDS := 9.0
## Frames the jump action is held (a long hold makes the full jump).
const JUMP_FRAMES := 18
## Frames on the spawn before walking.
const SETTLE_FRAMES := 20

## One entry per target: {id, kind, reached, seconds, closest}.
var results: Array[Dictionary] = []
var done: bool = false

var _tree: SceneTree
var _fps: int = 60
var _mode: String = "3d"
var _spawn: Vector3 = Vector3.ZERO
var _reach: float = 1.0
var _targets: Array = []
var _index: int = -1
var _waypoint: int = 0
var _started: int = 0
var _settle: int = 0
var _best: float = INF
var _best_at: int = 0
var _jump_until: int = -1
var _jump_pending: bool = false
var _last_at: Vector3 = Vector3.ZERO


func _init(scene_tree: SceneTree, route: Dictionary, fps: int) -> void:
	_tree = scene_tree
	_fps = fps
	_mode = str(route.get("mode", "3d"))
	_spawn = _vector(route.get("spawn", []))
	_reach = float(route.get("reach", 1.0))
	var targets: Variant = route.get("targets", [])
	_targets = targets if targets is Array else []
	done = _targets.is_empty()


## One physics frame: steer [param player] toward the current target's next waypoint.
func step(frame: int, player: Node) -> void:
	if done:
		return
	if player == null or not is_instance_valid(player) or not player.is_inside_tree():
		_release_all()
		return
	if _index < 0:
		_next(frame, player)
		return
	if _settle > 0:
		_settle -= 1
		_started = frame
		_best_at = frame
		return
	var target: Dictionary = _targets[_index]
	var at := _where(player)
	if at.distance_to(_last_at) > _reach * 4.0 and _waypoint > 0:
		# Sent back to the spawn (a fall or a hazard): walk the route again.
		_waypoint = 0
	_last_at = at
	var goal := _vector(target.get("at", []))
	var distance := _flat(goal - at)
	if distance <= _reach and absf(_height(goal - at)) <= _reach * 2.5:
		_record(true, frame, distance)
		_next(frame, player)
		return
	if distance < _best - 0.05 * _reach:
		_best = distance
		_best_at = frame
	var waiting := frame - _best_at
	if waiting > int(GIVE_UP_SECONDS * _fps):
		_record(false, frame, _best)
		_next(frame, player)
		return
	_steer(frame, player, target, at, waiting > int(NUDGE_SECONDS * _fps))


func _steer(frame: int, player: Node, target: Dictionary, at: Vector3, stuck: bool) -> void:
	var points: Array = target.get("waypoints", [])
	var tolerance := _reach * 0.45
	while _waypoint < points.size() - 1:
		var point := _vector(points[_waypoint])
		var near := _flat(point - at) <= tolerance
		if _mode == "2d-side":
			near = absf(point.x - at.x) <= tolerance and absf(point.y - at.y) <= _reach * 3.0
		if not near:
			break
		_waypoint += 1
		if _jumps(points[_waypoint]):
			_jump_pending = true
	var aim := _vector(points[mini(_waypoint, points.size() - 1)]) if not points.is_empty() else at
	if _waypoint >= points.size() - 1:
		aim = _vector(target.get("at", []))
	_release_all()
	_move(aim - at)
	var grounded := not player.has_method(&"is_on_floor") or bool(player.call(&"is_on_floor"))
	if (_jump_pending or stuck) and grounded and frame > _jump_until + 6:
		_jump_pending = false
		_jump_until = frame + JUMP_FRAMES
	if frame <= _jump_until:
		Input.action_press(&"jump")
	elif InputMap.has_action(&"jump"):
		Input.action_release(&"jump")


func _move(delta: Vector3) -> void:
	match _mode:
		"2d-side":
			if absf(delta.x) > 1.0:
				_press(&"move_right" if delta.x > 0 else &"move_left", 1.0)
		"2d-topdown":
			var flat := Vector2(delta.x, delta.y).normalized()
			_axis(flat.x, &"move_right", &"move_left")
			_axis(flat.y, &"move_down", &"move_up")
		_:
			var camera := _tree.root.get_viewport().get_camera_3d()
			var basis := camera.global_basis if camera != null else Basis.IDENTITY
			var forward := Vector3(-basis.z.x, 0.0, -basis.z.z).normalized()
			var right := Vector3(basis.x.x, 0.0, basis.x.z).normalized()
			var flat3 := Vector3(delta.x, 0.0, delta.z).normalized()
			_axis(flat3.dot(right), &"move_right", &"move_left")
			_axis(flat3.dot(forward), &"move_forward", &"move_back")


func _axis(value: float, positive: StringName, negative: StringName) -> void:
	if value > 0.05:
		_press(positive, value)
	elif value < -0.05:
		_press(negative, -value)


func _press(action: StringName, strength: float) -> void:
	if InputMap.has_action(action):
		Input.action_press(action, clampf(strength, 0.0, 1.0))


func _release_all() -> void:
	for action: StringName in MOVES_3D + MOVES_2D:
		if InputMap.has_action(action):
			Input.action_release(action)


func _next(frame: int, player: Node) -> void:
	_release_all()
	if InputMap.has_action(&"jump"):
		Input.action_release(&"jump")
	_index += 1
	if _index >= _targets.size():
		done = true
		return
	_waypoint = 0
	_best = INF
	_best_at = frame
	_started = frame
	_jump_pending = false
	_jump_until = -1
	_put_on_spawn(player)
	_last_at = _where(player)
	_settle = SETTLE_FRAMES


func _put_on_spawn(player: Node) -> void:
	if player is Node3D:
		(player as Node3D).global_position = _spawn + Vector3.UP * 0.1
	elif player is Node2D:
		(player as Node2D).global_position = Vector2(_spawn.x, _spawn.y - 2.0)
	if "velocity" in player:
		player.set(&"velocity", Vector3.ZERO if player is Node3D else Vector2.ZERO)


func _record(reached: bool, frame: int, closest: float) -> void:
	var target: Dictionary = _targets[_index]
	(
		results
		. append(
			{
				"id": str(target.get("id", "")),
				"kind": str(target.get("kind", "")),
				"reached": reached,
				"seconds": snappedf((frame - _started) / float(_fps), 0.01),
				"closest": snappedf(closest, 0.01),
			}
		)
	)


func _flat(delta: Vector3) -> float:
	if _mode == "3d":
		return Vector2(delta.x, delta.z).length()
	if _mode == "2d-side":
		return absf(delta.x) + maxf(absf(delta.y) - _reach, 0.0)
	return Vector2(delta.x, delta.y).length()


func _height(delta: Vector3) -> float:
	return delta.y if _mode == "3d" else 0.0


static func _jumps(point: Variant) -> bool:
	return (
		point is Array
		and (point as Array).size() > 0
		and (point as Array)[-1] is bool
		and ((point as Array)[-1])
	)


static func _vector(value: Variant) -> Vector3:
	if not value is Array:
		return Vector3.ZERO
	var numbers: Array[float] = []
	for item: Variant in value:
		if item is float or item is int:
			numbers.append(float(item))
	if numbers.size() >= 3:
		return Vector3(numbers[0], numbers[1], numbers[2])
	if numbers.size() == 2:
		return Vector3(numbers[0], numbers[1], 0.0)
	return Vector3.ZERO


static func _where(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at := (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO
