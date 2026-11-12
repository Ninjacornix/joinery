# gdlint: disable=max-returns
class_name BehaviourAgent
extends Node
## Runs a behaviour tree for the enemy it is a child of: a CharacterBody2D or
## CharacterBody3D (moved with move_and_slide), or any Node2D or Node3D (moved
## directly). Add it as a child, give it a tree, and the enemy acts:
##
##   Enemy (CharacterBody2D)
##     Shape (CollisionShape2D)
##     Behaviour (BehaviourAgent)  tree = res://features/behaviour/trees/guard.tres
##
## Built-in conditions (arguments override the exported values):
##   has_target            a target exists (the nearest player, or target_group)
##   sees_target range=    the target is within sight_range
##   target_near range=    the target is within attack_range
##   target_far range=     the target is beyond flee_range
##   health_below value=   the body's health / max_health is below value (0-1)
##   chance value=         true that share of ticks (0-1), seeded for replays
##   blackboard key= value=  the blackboard has key (equal to value when given)
## Built-in actions:
##   patrol    between patrol_points (or back and forth around home); running
##   chase     towards the target; success within attack_range
##   flee      away from the target; success beyond flee_range
##   attack    within attack_range: the body's attack(target), else the target's
##             take_damage(damage); success
##   wander    to a random point within wander_radius of home; success there
##   idle      stand still; success
##   remember key= value=   set a blackboard value; success
## Anything else calls the body: `func bt_<name>(agent: BehaviourAgent, args:
## Dictionary)` returning a bool for a condition, or BehaviourAgent.SUCCESS,
## FAILURE or RUNNING for an action.
##
## The playtester reads playtest_state(): the running branch, every node's last
## status and the blackboard. Only the session's authority runs the tree (online,
## the server); others see the body's synced position.

signal attacked(target: Node)
## The running branch changed ("selector > sequence > action chase").
signal running_changed(path: String)

const Nodes := preload("res://features/behaviour/bt_nodes.gd")
const SUCCESS := Nodes.SUCCESS
const FAILURE := Nodes.FAILURE
const RUNNING := Nodes.RUNNING
const GROUP := &"behaviour_agents"
const STATE_GROUP := &"playtest_state"
## Defaults when an exported value is 0: [2D in pixels, 3D in metres].
const DEFAULTS := {
	"speed": [150.0, 3.5],
	"sight_range": [420.0, 12.0],
	"attack_range": [56.0, 1.8],
	"flee_range": [520.0, 14.0],
	"patrol_radius": [120.0, 4.0],
	"wander_radius": [160.0, 5.0],
	"arrive_distance": [10.0, 0.4],
}

@export var tree: BehaviourTree
## Tree text, used when `tree` is empty (handy in tests and quick experiments).
@export_multiline var tree_text: String = ""
@export var active: bool = true
## Move the body from the tree's actions. Off: read `desired` and move it yourself.
@export var move_body: bool = true
@export_group("Senses")
## Group of nodes to target; empty means the game's players.
@export var target_group: StringName = &""
@export var sight_range: float = 0.0
@export var attack_range: float = 0.0
@export var flee_range: float = 0.0
@export_group("Movement")
@export var speed: float = 0.0
@export var acceleration: float = 0.0
@export var patrol_points: Array[NodePath] = []
@export var patrol_radius: float = 0.0
@export var wander_radius: float = 0.0
@export var arrive_distance: float = 0.0
@export var gravity: float = 9.8
@export_group("Attack")
@export var damage: int = 1

var body: Node
var root: Nodes.BTNode
## Problems in the tree text ("line N: …"); the agent stays idle while there are any.
var errors: Array[String] = []
## Values the tree and the body share. "target", "home" are kept up to date.
var blackboard: Dictionary = {}
## The velocity the tree wants this tick (2D: x, y; 3D: x, z), before acceleration.
var desired: Vector3 = Vector3.ZERO
var attacks: int = 0
var running: String = ""

var _clock: float = 0.0
var _home: Vector3 = Vector3.ZERO
var _patrol_index: int = 0
var _patrol_sign: float = 1.0
var _wander_to: Vector3 = Vector3.INF
var _is_3d: bool = false
var _rng: RandomNumberGenerator


func _ready() -> void:
	body = get_parent()
	_is_3d = body is Node3D
	add_to_group(GROUP)
	add_to_group(STATE_GROUP)
	for key: String in DEFAULTS:
		if float(get(key)) <= 0.0:
			set(key, float(DEFAULTS[key][1 if _is_3d else 0]))
	if acceleration <= 0.0:
		acceleration = speed * 8.0
	_rng = Replays.rng("behaviour:%s" % body.name)
	_home = position3()
	blackboard["home"] = _home
	rebuild()


## Builds the tree again from `tree` (or `tree_text`); call after changing either.
func rebuild() -> void:
	var text: String = tree.source if tree != null else tree_text
	var parsed: Dictionary = Nodes.parse(text)
	errors.assign(parsed["errors"])
	root = parsed["root"]
	if not errors.is_empty():
		push_warning("Behaviour %s: %s" % [get_path(), "; ".join(PackedStringArray(errors))])


func _physics_process(delta: float) -> void:
	if not active or root == null or body == null:
		return
	if not Game.session().is_authority():
		return
	_clock += delta
	desired = Vector3.ZERO
	var target: Node = find_target()
	blackboard["target"] = target
	root.tick(self)
	var path: String = Nodes.running_path(root)
	if path != running:
		running = path
		running_changed.emit(path)
	if move_body:
		_move(delta)


## Seconds this agent has run (cooldowns and waits count in it).
func clock() -> float:
	return _clock


func check(condition: String, args: Dictionary) -> bool:
	match condition:
		"has_target":
			return target() != null
		"sees_target":
			return _target_within(float(args.get("range", sight_range)))
		"target_near":
			return _target_within(float(args.get("range", attack_range)))
		"target_far":
			return target() != null and distance_to_target() > float(args.get("range", flee_range))
		"health_below":
			return _health_share() < float(args.get("value", 0.5))
		"chance":
			return _rng.randf() < float(args.get("value", 0.5))
		"blackboard":
			var key: String = str(args.get("key", ""))
			if not blackboard.has(key):
				return false
			return not args.has("value") or blackboard[key] == args["value"]
	return _call_body(condition, args, false) == SUCCESS


func act(action: String, args: Dictionary, first: bool, _node: Nodes.BTNode) -> int:
	var pace: float = float(args.get("speed", speed))
	match action:
		"patrol":
			return _patrol(pace)
		"chase":
			if target() == null:
				return FAILURE
			if distance_to_target() <= float(args.get("range", attack_range)):
				return SUCCESS
			_steer(target_position() - position3(), pace)
			return RUNNING
		"flee":
			if target() == null:
				return FAILURE
			if distance_to_target() >= float(args.get("range", flee_range)):
				return SUCCESS
			_steer(position3() - target_position(), pace)
			return RUNNING
		"attack":
			return _attack(args)
		"wander":
			return _wander(first, pace)
		"idle":
			return SUCCESS
		"remember":
			blackboard[str(args.get("key", "flag"))] = args.get("value", true)
			return SUCCESS
	return _call_body(action, args, true)


## A running action was cut off by a higher branch.
func stop_action(action: String, _node: Nodes.BTNode) -> void:
	if action == "wander":
		_wander_to = Vector3.INF


## The current target: the nearest living one in target_group (or the players).
func target() -> Node:
	var found: Variant = blackboard.get("target")
	return found if found is Node and is_instance_valid(found) else null


func find_target() -> Node:
	var candidates: Array[Node] = []
	if target_group == &"":
		candidates = Game.players()
	else:
		candidates = get_tree().get_nodes_in_group(target_group)
	var best: Node = null
	var best_distance: float = INF
	for node: Node in candidates:
		if not (node is Node2D or node is Node3D) or node == body:
			continue
		if node.has_method(&"is_alive") and not bool(node.call(&"is_alive")):
			continue
		var distance: float = position3().distance_to(_position_of(node))
		if distance < best_distance:
			best_distance = distance
			best = node
	return best


func distance_to_target() -> float:
	var found: Node = target()
	return INF if found == null else position3().distance_to(_position_of(found))


func target_position() -> Vector3:
	var found: Node = target()
	return position3() if found == null else _position_of(found)


## The body's position as a Vector3 (2D: x, y, 0).
func position3() -> Vector3:
	return _position_of(body)


## The tree with every node's last status, as indented text.
func describe() -> String:
	return Nodes.describe(root) if root != null else "\n".join(PackedStringArray(errors))


## For the playtester: the tree, the running branch, statuses and the blackboard.
func playtest_state() -> Dictionary:
	var board: Dictionary = {}
	for key: Variant in blackboard:
		var value: Variant = blackboard[key]
		board[str(key)] = str(value.get_path()) if value is Node else var_to_str(value)
	return {
		"kind": "behaviour",
		"body": str(body.get_path()) if body != null else "",
		"tree": tree.resource_path if tree != null else "(tree_text)",
		"running": running,
		"statuses": describe(),
		"blackboard": board,
		"attacks": attacks,
		"errors": errors.duplicate(),
	}


func _target_within(distance: float) -> bool:
	return target() != null and distance_to_target() <= distance


func _health_share() -> float:
	if body == null or not ("health" in body):
		return 1.0
	var most: float = float(body.get(&"max_health")) if "max_health" in body else 1.0
	return float(body.get(&"health")) / maxf(most, 0.0001)


func _attack(args: Dictionary) -> int:
	var found: Node = target()
	if found == null or distance_to_target() > float(args.get("range", attack_range)):
		return FAILURE
	var amount: int = int(args.get("damage", damage))
	if body.has_method(&"attack"):
		body.call(&"attack", found)
	elif found.has_method(&"take_damage"):
		found.call(&"take_damage", amount)
	attacks += 1
	attacked.emit(found)
	return SUCCESS


func _patrol(pace: float) -> int:
	var points: Array[Vector3] = []
	for path: NodePath in patrol_points:
		var point: Node = get_node_or_null(path)
		if point is Node2D or point is Node3D:
			points.append(_position_of(point))
	var goal: Vector3
	if points.is_empty():
		var side: Vector3 = Vector3.RIGHT * patrol_radius * _patrol_sign
		goal = _home + side
		if _flat(goal - position3()).length() <= arrive_distance:
			_patrol_sign = -_patrol_sign
	else:
		_patrol_index = _patrol_index % points.size()
		goal = points[_patrol_index]
		if _flat(goal - position3()).length() <= arrive_distance:
			_patrol_index = (_patrol_index + 1) % points.size()
			goal = points[_patrol_index]
	_steer(goal - position3(), pace)
	return RUNNING


func _wander(first: bool, pace: float) -> int:
	if first or _wander_to == Vector3.INF:
		var angle: float = _rng.randf() * TAU
		var reach: float = sqrt(_rng.randf()) * wander_radius
		var offset: Vector3 = (
			Vector3(cos(angle), 0.0, sin(angle)) if _is_3d else Vector3(cos(angle), sin(angle), 0.0)
		)
		_wander_to = _home + offset * reach
	if _flat(_wander_to - position3()).length() <= arrive_distance:
		_wander_to = Vector3.INF
		return SUCCESS
	_steer(_wander_to - position3(), pace)
	return RUNNING


func _steer(direction: Vector3, pace: float) -> void:
	var flat: Vector3 = _flat(direction)
	if flat.length_squared() > 0.000001:
		desired = flat.normalized() * pace


func _move(delta: float) -> void:
	var step: float = acceleration * delta
	if body is CharacterBody2D:
		var mover: CharacterBody2D = body
		mover.velocity = mover.velocity.move_toward(Vector2(desired.x, desired.y), step)
		mover.move_and_slide()
	elif body is CharacterBody3D:
		var mover3: CharacterBody3D = body
		var flat: Vector3 = Vector3(mover3.velocity.x, 0.0, mover3.velocity.z)
		flat = flat.move_toward(desired, step)
		var fall: float = mover3.velocity.y
		fall = 0.0 if mover3.is_on_floor() else fall - gravity * delta
		mover3.velocity = Vector3(flat.x, fall, flat.z)
		mover3.move_and_slide()
	elif body is Node2D:
		(body as Node2D).global_position += Vector2(desired.x, desired.y) * delta
	elif body is Node3D:
		(body as Node3D).global_position += desired * delta


func _call_body(name: String, args: Dictionary, is_action: bool) -> int:
	var method: StringName = StringName("bt_" + name)
	if body == null or not body.has_method(method):
		push_warning("Behaviour: %s has no bt_%s(agent, args)" % [body.name if body else "?", name])
		return FAILURE
	var result: Variant = body.call(method, self, args)
	if is_action and result is int:
		return clampi(result, SUCCESS, RUNNING)
	return SUCCESS if (result is bool and result) else FAILURE


## Horizontal part: 3D drops the height, 2D keeps x and y.
func _flat(vector: Vector3) -> Vector3:
	return Vector3(vector.x, 0.0, vector.z) if _is_3d else vector


static func _position_of(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at: Vector2 = (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO
