extends Node
## A greybox level Joinery built from a blockout layout (design/blockouts/<name>.toml).
##
## The layout is the source: change the plan there (or ask Journeyman) and rebuild,
## rather than moving the scene's pieces by hand. Markers are areas in groups:
## `goal` ends the level, `coin` and `pickup` are collected for score, `checkpoint`
## moves where a player comes back, and `hazard` sends the player back there, as does
## falling far below the level. With the game core the level starts like any other
## (`Game.level_ready`), so players appear at the `player_spawn` marker under
## `Players`, and what happens goes out through `GameEvents`. Without the core it
## still plays: put a player in group `player` under `Players`.

## "3d", "2d-topdown" or "2d-side"; the builder sets it.
@export var mode: String = "3d"
@export var save_id: String = "level"
## How far below the lowest floor counts as fallen (metres in 3D, pixels in 2D).
@export var fall_depth: float = 20.0

var score: int = 0
var completed: bool = false
var elapsed: float = 0.0

var _respawn: Vector3 = Vector3.ZERO
var _lowest: float = 0.0
var _taken: Array[String] = []


func _ready() -> void:
	add_to_group(&"saveable")
	var markers: Node = get_node_or_null(^"Markers")
	if markers != null:
		for marker: Node in markers.get_children():
			if marker is Area3D:
				(marker as Area3D).body_entered.connect(_on_entered.bind(marker))
			elif marker is Area2D:
				(marker as Area2D).body_entered.connect(_on_entered.bind(marker))
		var spawn: Node = markers.get_node_or_null(^"Spawn")
		if spawn != null:
			_respawn = _where(spawn)
	_lowest = _lowest_floor()
	var game: Node = get_node_or_null(^"/root/Game")
	if game != null and game.has_method(&"level_ready"):
		game.call(&"level_ready", self)


func _physics_process(delta: float) -> void:
	if not completed:
		elapsed += delta
	for player: Node in players():
		if _fallen(player):
			send_back(player)


## The players in this level (group `player`).
func players() -> Array[Node]:
	var found: Array[Node] = []
	for node: Node in get_tree().get_nodes_in_group(&"player"):
		if is_ancestor_of(node):
			found.append(node)
	return found


## Puts [param player] back at the spawn or the last checkpoint reached, standing still.
func send_back(player: Node) -> void:
	if player is Node3D:
		(player as Node3D).global_position = _respawn + Vector3.UP * 0.1
	elif player is Node2D:
		(player as Node2D).global_position = Vector2(_respawn.x, _respawn.y)
	if "velocity" in player:
		player.set(&"velocity", Vector3.ZERO if player is Node3D else Vector2.ZERO)


func save_state() -> Dictionary:
	return {"score": score, "time": elapsed, "taken": _taken.duplicate()}


func load_state(state: Dictionary) -> void:
	score = int(state.get("score", score))
	elapsed = float(state.get("time", elapsed))
	var taken: Variant = state.get("taken", [])
	if taken is Array:
		for name: Variant in taken:
			var marker: Node = get_node_or_null(NodePath("Markers/" + str(name)))
			if marker != null:
				_take(marker)


func _on_entered(body: Node, marker: Node) -> void:
	if not body.is_in_group(&"player") or completed:
		return
	if marker.is_in_group(&"goal"):
		_complete()
	elif marker.is_in_group(&"coin") or marker.is_in_group(&"pickup"):
		_take(marker)
		score += 1
		_emit(&"score_changed", [score])
	elif marker.is_in_group(&"checkpoint"):
		_respawn = _where(marker)
		_emit(&"checkpoint_reached", [str(marker.name)])
	elif marker.is_in_group(&"hazard"):
		send_back(body)


func _take(marker: Node) -> void:
	if str(marker.name) in _taken:
		return
	_taken.append(str(marker.name))
	marker.set_deferred(&"monitoring", false)
	if marker is Node3D:
		(marker as Node3D).hide()
	elif marker is CanvasItem:
		(marker as CanvasItem).hide()


func _complete() -> void:
	completed = true
	_emit(&"level_completed", [{"time": elapsed, "score": score}])


func _emit(signal_name: StringName, arguments: Array) -> void:
	var events: Node = get_node_or_null(^"/root/GameEvents")
	if events != null and events.has_signal(signal_name):
		events.callv(&"emit_signal", [signal_name] + arguments)


func _fallen(player: Node) -> bool:
	if player is Node3D:
		return (player as Node3D).global_position.y < _lowest - fall_depth
	if player is Node2D and mode == "2d-side":
		return (player as Node2D).global_position.y > _lowest + fall_depth * 50.0
	return false


## The lowest floor top in 3D, the lowest ground in a side view.
func _lowest_floor() -> float:
	var found: float = 0.0
	var first: bool = true
	for node: Node in find_children("*", "Node", true, false):
		var value: float
		if node is CSGShape3D:
			value = (node as Node3D).global_position.y
		elif node is StaticBody2D:
			value = (node as Node2D).global_position.y
		else:
			continue
		var lower: bool = value < found if node is CSGShape3D else value > found
		if first or lower:
			found = value
			first = false
	return found


static func _where(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at: Vector2 = (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO
