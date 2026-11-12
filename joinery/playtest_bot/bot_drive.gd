extends RefCounted
## Plays a game through its own input map, the way a curious first-time player would:
## walks somewhere for a second or two, jumps, presses interact near things, heads for
## the things a blueprint marks as goals (coins, pickups, checkpoints, switches, the goal),
## turns around when stuck, and clicks the buttons of a game without a player. Every choice
## comes from one seeded random generator, so a seed replays the same run.
##
## Styles: "3d" (camera-relative move_forward/back/left/right), "topdown" (move_up/down/
## left/right), "platformer" (move_left/right and jump), "ui" (no player: click buttons)
## and "generic" (no known movement actions: random presses of every action plus clicks).
##
## With nothing to head for it explores: it walks toward the nearest part of the level the
## player hasn't been to yet (bot_cover.gd), so a run sees as much of the level as it can.
## Places named with --reach come first: it heads for the nodes whose names hold those words
## and records whether, and when, the player got there.

## Groups a blueprint puts its goals in; the bot heads for these.
const GOAL_GROUPS: Array[StringName] = [
	&"coin",
	&"pickup",
	&"collectible",
	&"checkpoint",
	&"goal",
	&"interactable",
	&"pressure_plate",
	&"carryable",
]
## Buttons the bot never clicks: they leave the game instead of playing it.
const SKIP_WORDS: Array[String] = [
	"quit", "exit", "main menu", "menu", "leave", "disconnect", "delete", "settings", "report"
]
const MOVES_3D: Array[StringName] = [&"move_forward", &"move_back", &"move_left", &"move_right"]
const MOVES_2D: Array[StringName] = [&"move_up", &"move_down", &"move_left", &"move_right"]
const EXTRAS: Array[StringName] = [&"jump", &"interact", &"attack", &"dash", &"sprint"]
const LOOKS: Array[StringName] = [&"look_left", &"look_right", &"look_up", &"look_down"]
## How near counts as reaching a goal.
const REACH_3D := 1.6
const REACH_2D := 48.0
## Frames after the player appears before the level is mapped (it may still be loading).
const MAP_AFTER := 30
## Frames between looks for the nodes a --reach word names.
const REACH_LOOK_EVERY := 60

var tree: SceneTree
var rng: RandomNumberGenerator
var style: String = "generic"
## Words from the playtest's focus: goals whose names hold one come first.
var focus: PackedStringArray = PackedStringArray()
## Presses per action, for the report.
var used: Dictionary = {}
var goals_reached: int = 0
## The game gave the bot something to play with: a player, a button, an action of its own.
var interactive: bool = false
## The coverage grid (bot_cover.gd), set by the bot.
var cover: RefCounted
## Places to reach (--reach), as plain lower-case words; and what became of each:
## {target, found, reached, t, closest, node}.
var reach: PackedStringArray = PackedStringArray()
var reach_results: Array[Dictionary] = []

var _player: Node = null
var _held: Dictionary = {}
var _releases: Dictionary = {}
var _segment_end: int = 0
var _target: Node = null
var _target_since: int = 0
var _done_targets: Dictionary = {}
var _skip_until: Dictionary = {}
var _last_at: Vector3 = Vector3.ZERO
var _still: int = 0
var _others: Array[StringName] = []
var _map_at: int = -1
var _reach_nodes: Dictionary = {}
var _reach_looked: int = -REACH_LOOK_EVERY


func _init(scene_tree: SceneTree, generator: RandomNumberGenerator) -> void:
	tree = scene_tree
	rng = generator
	for action: StringName in InputMap.get_actions():
		var name := String(action)
		if name.begins_with("ui_") or name in ["pause", "report_bug", "restart", "quit"]:
			continue
		if action in MOVES_3D or action in MOVES_2D or action in EXTRAS or action in LOOKS:
			continue
		_others.append(action)


## The bot now plays [param player] (null: a game without one).
func follow(player: Node) -> void:
	release_all()
	_player = player
	_target = null
	_done_targets.clear()
	if player is Node3D and _has_all(MOVES_3D.slice(0, 2)):
		style = "3d"
	elif player is Node2D and _has_all([&"move_up", &"move_down"]):
		style = "topdown"
	elif player is Node2D and _has_all([&"move_left", &"move_right"]):
		style = "platformer"
	elif player is Node3D and _has_all([&"move_left", &"move_right"]):
		style = "3d"
	elif player == null and not _has_any(MOVES_3D + MOVES_2D):
		style = "ui"
	else:
		style = "generic"
	if player != null:
		_last_at = _where(player)
	interactive = player != null or not _others.is_empty()
	# Mapped a little later, once the level around the new player has loaded.
	_map_at = -2 if player != null and style in ["3d", "topdown", "platformer"] else -1


## Whether a movement action is held right now.
func moving() -> bool:
	for action: StringName in _held:
		if action in MOVES_3D or action in MOVES_2D:
			return true
	return false


## One physics frame of play.
func step(frame: int) -> void:
	for action: StringName in _releases.keys():
		if frame >= int(_releases[action]):
			_release(action)
			_releases.erase(action)
	if _player != null and is_instance_valid(_player) and _player.is_inside_tree():
		_map(frame)
		_check_reach(frame)
		_check_stuck(frame)
		_check_target(frame)
	if frame >= _segment_end:
		_new_segment(frame)
	elif style in ["ui", "generic"] and frame % 24 == 0:
		_click_something()


func release_all() -> void:
	for action: StringName in _held.keys():
		_release(action)
	_releases.clear()


## Presses [param action] now and lets go [param frames] frames later.
func tap(action: StringName, frame: int, frames: int = 3) -> void:
	if not InputMap.has_action(action):
		return
	_press(action)
	_releases[action] = frame + frames


func _new_segment(frame: int) -> void:
	release_all()
	_segment_end = frame + rng.randi_range(30, 150)
	match style:
		"3d", "topdown":
			_walk(frame)
			if rng.randf() < 0.25:
				tap(&"jump", frame, rng.randi_range(3, 18))
			if rng.randf() < 0.3:
				_hold(&"sprint")
			if style == "3d" and rng.randf() < 0.2:
				_hold(LOOKS[rng.randi_range(0, LOOKS.size() - 1)])
		"platformer":
			_walk(frame)
			if rng.randf() < 0.45:
				tap(&"jump", frame, rng.randi_range(4, 20))
		"ui":
			_click_something()
		_:
			_random_presses(frame)
			_click_something()
	if rng.randf() < 0.2:
		tap(&"interact", frame)
	if rng.randf() < 0.1:
		tap(&"attack" if rng.randf() < 0.7 else &"dash", frame)
	if not _others.is_empty() and rng.randf() < 0.15:
		tap(_others[rng.randi_range(0, _others.size() - 1)], frame, rng.randi_range(3, 30))


## Holds the movement keys toward a place to reach, the current goal, the nearest part of
## the level not seen yet, or a random direction.
func _walk(frame: int) -> void:
	var direction := Vector2.ZERO
	var place := _reach_target()
	if place != null and rng.randf() < 0.85:
		_target = place
		_target_since = frame
	elif _target == null or rng.randf() < 0.3:
		# Half the time a goal, else (with a map) the nearest part of the level not seen yet.
		var mapping := cover != null and bool(cover.get(&"built"))
		_target = _pick_target(frame) if rng.randf() < (0.5 if mapping else 0.7) else null
		_target_since = frame
	var mapped := cover != null and bool(cover.get(&"built"))
	if _target != null:
		var aim: Vector3 = (
			cover.call(&"route", _where(_player), _where(_target)) if mapped else _where(_target)
		)
		direction = _toward_point(aim)
	elif mapped:
		var aim: Variant = cover.call(&"explore", _where(_player), frame, rng)
		if aim is Vector3:
			direction = _toward_point(aim)
			# Following the map: steer again soon, cell by cell.
			_segment_end = mini(_segment_end, frame + rng.randi_range(20, 45))
	if direction == Vector2.ZERO:
		direction = Vector2.from_angle(rng.randf() * TAU)
	# x: right is positive; y: forward/up is positive.
	if direction.x > 0.35:
		_hold(&"move_right")
	elif direction.x < -0.35:
		_hold(&"move_left")
	if style == "platformer":
		if direction.y > 0.5:
			tap(&"jump", frame, rng.randi_range(8, 20))
		return
	var forward: StringName = &"move_forward" if style == "3d" else &"move_up"
	var back: StringName = &"move_back" if style == "3d" else &"move_down"
	if direction.y > 0.35:
		_hold(forward)
	elif direction.y < -0.35:
		_hold(back)


## Direction to [param point] in input terms (x right, y forward/up), unit length or zero.
func _toward_point(point: Vector3) -> Vector2:
	var delta := point - _where(_player)
	if style == "3d":
		var camera := tree.root.get_viewport().get_camera_3d()
		var basis := camera.global_basis if camera != null else Basis.IDENTITY
		var forward := Vector3(-basis.z.x, 0.0, -basis.z.z).normalized()
		var right := Vector3(basis.x.x, 0.0, basis.x.z).normalized()
		var flat := Vector3(delta.x, 0.0, delta.z)
		return Vector2(flat.dot(right), flat.dot(forward)).normalized()
	return Vector2(delta.x, -delta.y).normalized()


## Map the level once the player has been there a moment, and mark where it stands.
func _map(frame: int) -> void:
	if cover == null:
		return
	if _map_at == -2:
		_map_at = frame + MAP_AFTER
	# On its feet: a player still falling would map from the wrong height.
	var standing := (
		style == "topdown"
		or not _player.has_method(&"is_on_floor")
		or bool(_player.call(&"is_on_floor"))
		or frame >= _map_at + MAP_AFTER * 4
	)
	if _map_at >= 0 and frame >= _map_at and standing:
		_map_at = -1
		cover.call(&"build", _player, style)
	cover.call(&"visit", _where(_player), frame)


## The nearest node a --reach word names that the player hasn't reached yet, or null.
func _reach_target() -> Node:
	var best: Node = null
	var best_distance := INF
	for entry: Dictionary in reach_results:
		if bool(entry["reached"]):
			continue
		for node: Node in _reach_nodes.get(str(entry["target"]), []):
			if not is_instance_valid(node) or not node.is_inside_tree():
				continue
			var distance := _where(node).distance_to(_where(_player))
			if distance < best_distance:
				best_distance = distance
				best = node
	return best


## Look for the places to reach now and then (levels load and change), and record when the
## player gets to one: inside its bounds, or near its origin.
func _check_reach(frame: int) -> void:
	if reach.is_empty():
		return
	if reach_results.is_empty():
		for word: String in reach:
			reach_results.append(
				{
					"target": word,
					"found": false,
					"reached": false,
					"t": null,
					"closest": null,
					"node": ""
				}
			)
	if frame - _reach_looked >= REACH_LOOK_EVERY:
		_reach_looked = frame
		_find_reach_nodes()
	var at := _where(_player)
	var fps := Engine.physics_ticks_per_second
	for entry: Dictionary in reach_results:
		if bool(entry["reached"]):
			continue
		for node: Node in _reach_nodes.get(str(entry["target"]), []):
			if not is_instance_valid(node) or not node.is_inside_tree():
				continue
			var distance := _flat_distance(_where(node), at)
			if entry["closest"] == null or distance < float(entry["closest"]):
				entry["closest"] = snappedf(distance, 0.01)
			if (
				_inside(node, at)
				or distance <= (REACH_3D * 1.25 if _player is Node3D else REACH_2D * 1.5)
			):
				entry["reached"] = true
				entry["t"] = snappedf(frame / float(fps), 0.01)
				entry["node"] = String(node.name)
				entry["closest"] = 0.0
				if _target == node:
					_target = null
				break


func _find_reach_nodes() -> void:
	_reach_nodes.clear()
	var scene := tree.current_scene
	if scene == null:
		return
	var nodes: Array[Node] = scene.find_children("*", "Node", true, false)
	for entry: Dictionary in reach_results:
		var word := str(entry["target"])
		var found: Array[Node] = []
		for node: Node in nodes:
			if node == _player or not (node is Node3D or node is Node2D):
				continue
			if _plain(String(node.name)).contains(word):
				found.append(node)
		_reach_nodes[word] = found
		if not found.is_empty():
			entry["found"] = true
			if str(entry["node"]).is_empty():
				entry["node"] = String(found[0].name)


## The player stands within [param node]'s shapes or meshes (a room, a zone, a door).
func _inside(node: Node, at: Vector3) -> bool:
	if node is Node3D:
		var parts: Array[Node] = [node]
		parts.append_array(node.find_children("*", "Node3D", true, false))
		for part: Node in parts:
			var box := AABB()
			if part is VisualInstance3D:
				box = (part as Node3D).global_transform * (part as VisualInstance3D).get_aabb()
			elif part is CollisionShape3D and (part as CollisionShape3D).shape is BoxShape3D:
				var size := ((part as CollisionShape3D).shape as BoxShape3D).size
				box = (part as Node3D).global_transform * AABB(-size / 2.0, size)
			else:
				continue
			if box.grow(0.5).has_point(at) or (box.grow(0.5).has_point(at + Vector3.DOWN)):
				return true
		return false
	var parts2: Array[Node] = [node]
	parts2.append_array(node.find_children("*", "Node2D", true, false))
	var flat := Vector2(at.x, at.y)
	for part: Node in parts2:
		if part is CollisionShape2D and (part as CollisionShape2D).shape != null:
			var shape := part as CollisionShape2D
			var local := shape.shape.get_rect().grow(8.0)
			if local.has_point(shape.global_transform.affine_inverse() * flat):
				return true
	return false


static func _plain(text: String) -> String:
	var kept := ""
	for character: String in text.to_lower():
		if character.is_valid_identifier() or character.is_valid_int():
			kept += character
	return kept.replace("_", "")


func _flat_distance(a: Vector3, b: Vector3) -> float:
	if _player is Node3D:
		return Vector2(a.x, a.z).distance_to(Vector2(b.x, b.z))
	return Vector2(a.x, a.y).distance_to(Vector2(b.x, b.y))


func _pick_target(frame: int) -> Node:
	var best: Node = null
	var best_score := INF
	for group: StringName in GOAL_GROUPS:
		for node: Node in tree.get_nodes_in_group(group):
			var id := node.get_instance_id()
			if _done_targets.has(id) or int(_skip_until.get(id, 0)) > frame:
				continue
			if not (node is Node3D or node is Node2D) or not node.is_inside_tree():
				continue
			if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
				continue
			if node is Node3D and not (node as Node3D).is_visible_in_tree():
				continue
			var score := _where(node).distance_to(_where(_player)) * rng.randf_range(0.8, 1.2)
			if _focused(node):
				score *= 0.2
			if score < best_score:
				best_score = score
				best = node
	return best


func _check_target(frame: int) -> void:
	if _target == null:
		return
	if not is_instance_valid(_target) or not _target.is_inside_tree():
		_target = null
		goals_reached += 1
		return
	var reach := REACH_3D if _player is Node3D else REACH_2D
	if _where(_target).distance_to(_where(_player)) <= reach:
		_done_targets[_target.get_instance_id()] = true
		goals_reached += 1
		tap(&"interact", frame)
		_target = null
	elif frame - _target_since > 600:
		# Unreachable for now: try something else, come back later.
		_skip_until[_target.get_instance_id()] = frame + 1800
		_target = null


## Holding a direction and going nowhere: jump and pick another way.
func _check_stuck(frame: int) -> void:
	var at := _where(_player)
	var moved := at.distance_to(_last_at)
	_last_at = at
	if not moving():
		_still = 0
		return
	_still = _still + 1 if moved < (0.01 if _player is Node3D else 0.3) else 0
	if _still == 45:
		tap(&"jump", frame, 12)
		if _target != null:
			_skip_until[_target.get_instance_id()] = frame + 900
			_target = null
		_segment_end = frame + 1


func _random_presses(frame: int) -> void:
	var pool: Array[StringName] = []
	for action: StringName in InputMap.get_actions():
		var name := String(action)
		if (
			name in ["pause", "report_bug"]
			or name.begins_with("ui_text")
			or name.begins_with("ui_graph")
		):
			continue
		if name.begins_with("ui_") and not name in ["ui_left", "ui_right", "ui_up", "ui_down"]:
			continue
		pool.append(action)
	if pool.is_empty():
		return
	for _i: int in rng.randi_range(1, 2):
		var action := pool[rng.randi_range(0, pool.size() - 1)]
		if rng.randf() < 0.5:
			_hold(action)
		else:
			tap(action, frame, rng.randi_range(3, 30))


## Clicks a random visible, enabled button of the game (never one that leaves it).
func _click_something() -> void:
	var buttons: Array[BaseButton] = []
	var scene := tree.current_scene
	if scene == null:
		return
	_collect_buttons(scene, buttons)
	interactive = interactive or not buttons.is_empty()
	if buttons.is_empty():
		if rng.randf() < 0.3:
			_send_key_action(&"ui_accept")
		return
	var button := buttons[rng.randi_range(0, buttons.size() - 1)]
	var rect := button.get_global_rect()
	var at := button.get_viewport().get_screen_transform() * rect.get_center()
	used["click"] = int(used.get("click", 0)) + 1
	var motion := InputEventMouseMotion.new()
	motion.position = at
	motion.global_position = at
	_send(motion)
	for pressed: bool in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.position = at
		event.global_position = at
		event.pressed = pressed
		event.button_mask = MOUSE_BUTTON_MASK_LEFT if pressed else 0 as MouseButtonMask
		_send(event)


func _collect_buttons(node: Node, into: Array[BaseButton]) -> void:
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return
	if node is BaseButton and not (node as BaseButton).disabled:
		var text := ""
		if node is Button:
			text = (node as Button).text.to_lower()
		var skip := false
		for word: String in SKIP_WORDS:
			if text.contains(word):
				skip = true
		if not skip:
			into.append(node as BaseButton)
	for child: Node in node.get_children():
		_collect_buttons(child, into)


func _focused(node: Node) -> bool:
	if focus.is_empty():
		return false
	var name := String(node.name).to_lower()
	for word: String in focus:
		if not word.is_empty() and name.contains(word):
			return true
	return false


func _send_key_action(action: StringName) -> void:
	_press(action)
	_release(action)


func _hold(action: StringName) -> void:
	if InputMap.has_action(action):
		_press(action)


func _press(action: StringName) -> void:
	if not InputMap.has_action(action):
		return
	_held[action] = true
	used[String(action)] = int(used.get(String(action), 0)) + 1
	Input.action_press(action)
	var event := InputEventAction.new()
	event.action = action
	event.pressed = true
	event.strength = 1.0
	_send(event)


func _release(action: StringName) -> void:
	_held.erase(action)
	Input.action_release(action)
	var event := InputEventAction.new()
	event.action = action
	event.pressed = false
	_send(event)


func _has_all(actions: Array[StringName]) -> bool:
	for action: StringName in actions:
		if not InputMap.has_action(action):
			return false
	return true


func _has_any(actions: Array[StringName]) -> bool:
	for action: StringName in actions:
		if InputMap.has_action(action):
			return true
	return false


static func _send(event: InputEvent) -> void:
	Input.parse_input_event(event)
	Input.flush_buffered_events()


static func _where(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at := (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO
