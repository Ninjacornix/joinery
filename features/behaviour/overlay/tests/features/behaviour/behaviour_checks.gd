extends RefCounted
## The checks behind behaviour_test.gd, loaded once the game runs. Works on 2D and 3D
## blueprints: the tree logic runs on a fake body, then enemies made in code chase,
## attack, flee, wander and patrol around the real player.

const BTNodes := preload("res://features/behaviour/bt_nodes.gd")
const FakeBody := preload("res://tests/features/behaviour/fake_body.gd")
const TREES := "res://features/behaviour/trees"

var tree: SceneTree
var h: PlayHarness
var player: Node
var is_3d: bool = false


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	await h.boot()
	player = Game.local_player()
	h.expect(player != null, "the player spawns")
	if player == null:
		return
	is_3d = player is Node3D
	if "controls_enabled" in player:
		player.set(&"controls_enabled", false)
	_check_parse()
	_check_ready_trees()
	_check_logic()
	await _check_chase_and_attack()
	await _check_flee()
	await _check_wander()
	await _check_patrol()


func _check_parse() -> void:
	var good: Dictionary = BTNodes.parse(
		"selector\n\tsequence\n\t\tcondition ready\n\t\taction count\n"
	)
	h.expect((good["errors"] as Array).is_empty(), "a tree reads: %s" % [good["errors"]])
	var bad: Dictionary = (
		BTNodes
		. parse(
			(
				"selector\n\tjump\n\tcondition\n\tcooldown soon\n\t\taction count\n\taction x\n\t\taction y\n"
				+ "sequence\n"
			)
		)
	)
	var joined: String = "\n".join(PackedStringArray(bad["errors"]))
	h.expect(bad["root"] == null, "a tree with problems does not build")
	h.expect(joined.contains("line 2: 'jump' is not a node kind"), "unknown kinds are named")
	h.expect(joined.contains("line 3: condition needs a name"), "a condition needs a name")
	h.expect(joined.contains("line 4: cooldown needs seconds"), "a cooldown needs seconds")
	h.expect(joined.contains("line 7: action holds no children"), "a leaf has no children")
	h.expect(joined.contains("line 8: a tree has one root"), "one root")


func _check_ready_trees() -> void:
	for file: String in ["guard", "chaser", "coward", "wanderer"]:
		var resource: Resource = load("%s/%s.tres" % [TREES, file])
		h.expect(resource is BehaviourTree, "%s.tres is a BehaviourTree" % file)
		if resource is BehaviourTree:
			var errors: Array[String] = (resource as BehaviourTree).errors()
			h.expect(errors.is_empty(), "%s.tres reads: %s" % [file, errors])
			h.expect((resource as BehaviourTree).build() != null, "%s.tres builds" % file)


func _check_logic() -> void:
	var body: Node2D = FakeBody.new()
	body.name = "FakeBody"
	tree.current_scene.add_child(body)
	var agent: BehaviourAgent = BehaviourAgent.new()
	agent.active = false
	agent.move_body = false
	agent.tree_text = (
		"selector\n"
		+ "\tsequence\n\t\tcondition ready\n\t\tcooldown 2\n\t\t\taction count tag=hit\n"
		+ "\tinvert\n\t\taction fail\n"
		+ "\taction busy\n"
	)
	body.add_child(agent)
	h.expect(agent.errors.is_empty(), "the logic tree reads: %s" % [agent.errors])
	var root: BTNodes.BTNode = agent.root
	h.expect(root.tick(agent) == BTNodes.SUCCESS, "invert turns a failure into success")
	h.expect(body.calls == ["fail"], "a sequence stops at a failed condition")
	agent.tree_text = "selector\n\tsequence\n\t\tcondition ready\n\t\taction count\n\taction busy\n"
	agent.rebuild()
	root = agent.root
	body.calls.clear()
	h.expect(root.tick(agent) == BTNodes.RUNNING, "a selector falls through to a running action")
	h.expect(BTNodes.running_path(root) == "selector > action busy", "the running branch reads")
	body.ready_flag = true
	h.expect(root.tick(agent) == BTNodes.SUCCESS, "a higher branch takes over when it can")
	h.expect(root.children[1].status == -1, "the running action below it is interrupted")
	h.expect(body.calls == ["busy", "count:"], "actions ran in that order: %s" % [body.calls])
	agent.tree_text = "sequence\n\tcooldown 2\n\t\taction count\n\twait 1\n"
	agent.rebuild()
	root = agent.root
	body.calls.clear()
	h.expect(root.tick(agent) == BTNodes.RUNNING, "wait runs")
	agent.set(&"_clock", 1.5)
	h.expect(root.tick(agent) == BTNodes.FAILURE, "a cooldown fails while cooling down")
	agent.set(&"_clock", 2.5)
	h.expect(root.tick(agent) == BTNodes.RUNNING, "after the cooldown the child runs again")
	agent.set(&"_clock", 3.6)
	h.expect(root.tick(agent) == BTNodes.FAILURE, "cooling down again after it succeeded")
	h.expect(body.calls.size() == 2, "the cooled action ran twice: %s" % [body.calls])
	h.expect(agent.describe().contains("cooldown 2 [failure]"), "describe() shows statuses")
	body.queue_free()


func _check_chase_and_attack() -> void:
	var agent: BehaviourAgent = _spawn(_away(6.0), "guard")
	var paths: Array[String] = []
	agent.running_changed.connect(func(path: String) -> void: paths.append(path))
	var start: float = agent.distance_to_target()
	h.expect(start < 1000.0, "the guard finds the player")
	await h.physics_frames(150)
	var near: float = agent.distance_to_target()
	h.expect(near < start * 0.5, "chase closes in (%.2f -> %.2f)" % [start, near])
	h.expect(agent.attacks >= 1, "attack hits once in range")
	h.expect(agent.attacks <= 3, "the cooldown spaces attacks out (%d)" % agent.attacks)
	var seen: String = " | ".join(PackedStringArray(paths))
	h.expect(seen.contains("action chase"), "the running branch showed chase: %s" % seen)
	var state: Dictionary = agent.playtest_state()
	h.expect(state["kind"] == "behaviour", "the playtester sees the tree")
	h.expect(str(state["tree"]).ends_with("guard.tres"), "and which tree it is")
	h.expect(str(state["statuses"]).contains("action attack"), "with every node's status")
	h.expect(
		str((state["blackboard"] as Dictionary).get("target", "")) == str(player.get_path()),
		"and its blackboard target"
	)
	h.expect(tree.get_nodes_in_group(&"playtest_state").has(agent), "in the playtest group")
	agent.get_parent().queue_free()


func _check_flee() -> void:
	var agent: BehaviourAgent = _spawn(_away(2.0), "")
	agent.tree_text = "selector\n\taction flee\n"
	agent.rebuild()
	var start: float = agent.distance_to_target()
	await h.physics_frames(60)
	h.expect(agent.distance_to_target() > start * 1.5, "flee runs away")
	agent.get_parent().queue_free()


func _check_wander() -> void:
	var agent: BehaviourAgent = _spawn(_away(8.0), "wanderer")
	agent.target_group = &"nobody"
	agent.blackboard["target"] = null
	var home: Vector3 = agent.position3()
	var farthest: float = 0.0
	for _i: int in 12:
		await h.physics_frames(10)
		farthest = maxf(farthest, agent.position3().distance_to(home))
	h.expect(farthest > agent.arrive_distance, "wander moves (%.2f)" % farthest)
	h.expect(farthest <= agent.wander_radius * 1.1, "wander stays near home (%.2f)" % farthest)
	agent.get_parent().queue_free()


func _check_patrol() -> void:
	var agent: BehaviourAgent = _spawn(_away(8.0), "")
	agent.target_group = &"nobody"
	agent.blackboard["target"] = null
	agent.tree_text = "selector\n\taction patrol\n"
	agent.rebuild()
	var home: Vector3 = agent.position3()
	await h.physics_frames(90)
	var moved: Vector3 = agent.position3() - home
	h.expect(absf(moved.x) > agent.arrive_distance, "patrol walks along its beat")
	h.expect(absf(moved.x) <= agent.patrol_radius * 1.1, "within the patrol radius")
	agent.get_parent().queue_free()


## A point `metres` from the player (pixels: x 40), off to one side.
func _away(metres: float) -> Vector3:
	var at: Vector3 = _position(player)
	return at + (Vector3(metres, 0.0, 0.0) if is_3d else Vector3(metres * 40.0, 0.0, 0.0))


## An enemy made in code with a BehaviourAgent running a ready tree (or none yet).
func _spawn(at: Vector3, tree_name: String) -> BehaviourAgent:
	var agent: BehaviourAgent = BehaviourAgent.new()
	agent.name = "Behaviour"
	if not tree_name.is_empty():
		agent.tree = load("%s/%s.tres" % [TREES, tree_name]) as BehaviourTree
	else:
		agent.tree_text = "selector\n\taction idle\n"
	var body: Node
	if is_3d:
		var body3: CharacterBody3D = CharacterBody3D.new()
		body3.collision_layer = 0
		body3.collision_mask = 0
		var shape3: CollisionShape3D = CollisionShape3D.new()
		shape3.shape = CapsuleShape3D.new()
		body3.add_child(shape3)
		agent.gravity = 0.0
		body = body3
	else:
		var body2: CharacterBody2D = CharacterBody2D.new()
		body2.collision_layer = 0
		body2.collision_mask = 0
		var shape2: CollisionShape2D = CollisionShape2D.new()
		shape2.shape = CircleShape2D.new()
		body2.add_child(shape2)
		body = body2
	body.name = "TestEnemy"
	body.add_child(agent)
	Game.current_level().add_child(body)
	if body is Node3D:
		(body as Node3D).global_position = at
	else:
		(body as Node2D).global_position = Vector2(at.x, at.y)
	agent.set(&"_home", agent.position3())
	agent.blackboard["home"] = agent.position3()
	agent.blackboard["target"] = agent.find_target()
	return agent


static func _position(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	var at: Vector2 = (node as Node2D).global_position
	return Vector3(at.x, at.y, 0.0)
