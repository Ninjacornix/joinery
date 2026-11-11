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
	"quit", "exit", "main menu", "menu", "leave", "disconnect", "delete", "settings"
]
const MOVES_3D: Array[StringName] = [&"move_forward", &"move_back", &"move_left", &"move_right"]
const MOVES_2D: Array[StringName] = [&"move_up", &"move_down", &"move_left", &"move_right"]
const EXTRAS: Array[StringName] = [&"jump", &"interact", &"attack", &"dash", &"sprint"]
const LOOKS: Array[StringName] = [&"look_left", &"look_right", &"look_up", &"look_down"]
## How near counts as reaching a goal.
const REACH_3D := 1.6
const REACH_2D := 48.0

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


func _init(scene_tree: SceneTree, generator: RandomNumberGenerator) -> void:
	tree = scene_tree
	rng = generator
	for action: StringName in InputMap.get_actions():
		var name := String(action)
		if name.begins_with("ui_") or name == "pause" or name in ["restart", "quit"]:
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


## Holds the movement keys toward the current goal, or a random direction.
func _walk(frame: int) -> void:
	var direction := Vector2.ZERO
	if _target == null or rng.randf() < 0.3:
		_target = _pick_target(frame) if rng.randf() < 0.7 else null
		_target_since = frame
	if _target != null:
		direction = _toward(_target)
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


## Direction to [param node] in input terms (x right, y forward/up), unit length or zero.
func _toward(node: Node) -> Vector2:
	var delta := _where(node) - _where(_player)
	if style == "3d":
		var camera := tree.root.get_viewport().get_camera_3d()
		var basis := camera.global_basis if camera != null else Basis.IDENTITY
		var forward := Vector3(-basis.z.x, 0.0, -basis.z.z).normalized()
		var right := Vector3(basis.x.x, 0.0, basis.x.z).normalized()
		var flat := Vector3(delta.x, 0.0, delta.z)
		return Vector2(flat.dot(right), flat.dot(forward)).normalized()
	return Vector2(delta.x, -delta.y).normalized()


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
		if name == "pause" or name.begins_with("ui_text") or name.begins_with("ui_graph"):
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
