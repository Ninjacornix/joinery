extends Node2D
## A side-scrolling level built from the ASCII map in MAP (legend in
## map_builder.gd). Counts coins, keeps the active checkpoint, sends players
## who touch spikes or fall off the map back to it, times the run, and shows
## the win screen when a player reaches the flag.
##
## To change the level, edit MAP (or fill `map_override` in the inspector):
## every character is one 16 px tile.

const MapBuilder := preload("res://game/map_builder.gd")
const CoinScene := preload("res://game/props/coin.tscn")
const SpikesScene := preload("res://game/props/spikes.tscn")
const CheckpointScene := preload("res://game/props/checkpoint.tscn")
const GoalScene := preload("res://game/props/goal.tscn")
const Hud := preload("res://game/ui/hud.gd")
const TILE := 16
const START_ID := "start"
const MAP: Array[String] = [
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#................................................................................#",
	"#.........................................................................o..F...#",
	"#...........................................................o...........##########",
	"#...........o..........o.......................o...........###..........##########",
	"#..........---......########.................-----....................############",
	"#...................########...o....o.................###.............############",
	"#...............############........................................##############",
	"#..P.....o......############............C.....^^^....^^^^^^.....C...##############",
	"##################################....############################################",
	"##################################....############################################",
	"##################################....############################################",
]

@export var save_id: String = "level"
## Replaces MAP when not empty: one string per row, same legend.
@export var map_override: PackedStringArray = PackedStringArray()
## How far below the bottom of the map a falling player is sent back.
@export var fall_margin: float = 32.0

var rows: Array[String] = []
## The map in world pixels.
var bounds: Rect2 = Rect2()
var kill_y: float = 0.0
var start_position: Vector2 = Vector2.ZERO
var score: int = 0
var total: int = 0
var falls: int = 0
var elapsed: float = 0.0
var completed: bool = false
## The checkpoint players come back to; null means the start.
var checkpoint: Node = null

var _sent_back_frame: Dictionary = {}

@onready var world: Node2D = $World
@onready var hazards: Node2D = $Hazards
@onready var coins: Node2D = $Coins
@onready var checkpoints: Node2D = $Checkpoints
@onready var goals: Node2D = $Goals
@onready var spawns: Node2D = $Spawns
@onready var hud: Hud = $HUD


func _ready() -> void:
	rows = map_rows()
	for problem: String in MapBuilder.problems(rows):
		push_error("Level map: %s." % problem)
	_build()
	hud.play_again_requested.connect(_on_play_again)
	hud.main_menu_requested.connect(_on_main_menu)
	GameEvents.player_spawned.connect(_on_player_spawned)
	Game.level_ready(self)
	_recount(false)
	hud.set_falls(falls)


func _physics_process(delta: float) -> void:
	if not completed:
		elapsed += delta
		hud.set_time(elapsed)
	for player: Node in Game.players():
		var body: Node2D = player as Node2D
		if body == null or not body.is_multiplayer_authority():
			continue
		if body.global_position.y > kill_y:
			send_back(body, true)
		elif (
			not completed
			and InputMap.has_action(&"restart")
			and Input.is_action_just_pressed(&"restart")
		):
			send_back(body, false)


## The rows of the map in use.
func map_rows() -> Array[String]:
	var found: Array[String] = []
	if map_override.is_empty():
		found.assign(MAP)
	else:
		found.assign(map_override)
	return found


func tile_at(cell: Vector2i) -> String:
	return MapBuilder.tile_at(rows, cell)


## Where something standing in `cell` puts its feet, in world pixels.
func cell_bottom(cell: Vector2i) -> Vector2:
	return to_global(MapBuilder.cell_bottom(cell, TILE))


func coin_nodes() -> Array[Node]:
	return coins.get_children()


## Where players come back to: the active checkpoint, or the start.
func respawn_position() -> Vector2:
	if checkpoint != null and is_instance_valid(checkpoint):
		return checkpoint.call(&"respawn_position")
	return start_position


## Sends a player back to the checkpoint. `fell` counts it as a fall.
func send_back(player: Node2D, fell: bool) -> void:
	var frame: int = Engine.get_physics_frames()
	if int(_sent_back_frame.get(player.get_instance_id(), -1)) == frame:
		return
	_sent_back_frame[player.get_instance_id()] = frame
	player.call(&"respawn", respawn_position())
	if fell:
		player.call(&"shake")
		if not completed:
			falls += 1
			hud.set_falls(falls)


func save_state() -> Dictionary:
	var id: String = START_ID
	if checkpoint != null:
		id = str(checkpoint.get(&"checkpoint_id"))
	return {"time": elapsed, "checkpoint": id, "falls": falls}


func load_state(state: Dictionary) -> void:
	elapsed = float(state.get("time", elapsed))
	falls = int(state.get("falls", falls))
	hud.set_falls(falls)
	var id: String = str(state.get("checkpoint", START_ID))
	_set_checkpoint(null)
	for post: Node in checkpoints.get_children():
		if str(post.get(&"checkpoint_id")) == id:
			_set_checkpoint(post)


func _build() -> void:
	var size: Vector2i = Vector2i(rows[0].length() if not rows.is_empty() else 0, rows.size())
	bounds = Rect2(to_global(Vector2.ZERO), Vector2(size * TILE))
	kill_y = bounds.end.y + fall_margin
	MapBuilder.build_solids(world, rows, TILE)
	MapBuilder.build_one_ways(world, rows, TILE)

	for r: int in rows.size():
		for run: Vector2i in MapBuilder.runs(rows[r], MapBuilder.SPIKES):
			var spikes: Node2D = SpikesScene.instantiate() as Node2D
			spikes.name = "Spikes_%d_%d" % [run.x, r]
			spikes.set(&"tiles", run.y - run.x + 1)
			spikes.set(&"tile_size", TILE)
			spikes.position = Vector2(float(run.x * TILE), float((r + 1) * TILE))
			spikes.connect(&"touched", _on_hazard_touched)
			hazards.add_child(spikes)

	for cell: Vector2i in MapBuilder.cells(rows, MapBuilder.COIN):
		var coin: Node2D = CoinScene.instantiate() as Node2D
		coin.name = "Coin_%d_%d" % [cell.x, cell.y]
		coin.set(&"save_id", "coin_%d_%d" % [cell.x, cell.y])
		coin.position = MapBuilder.cell_centre(cell, TILE)
		coin.connect(&"collected_changed", _on_coin_changed)
		coins.add_child(coin)

	var number: int = 0
	for cell: Vector2i in MapBuilder.cells(rows, MapBuilder.CHECKPOINT):
		number += 1
		var post: Node2D = CheckpointScene.instantiate() as Node2D
		post.name = "Checkpoint%d" % number
		post.set(&"checkpoint_id", "checkpoint_%d" % number)
		post.position = MapBuilder.cell_bottom(cell, TILE)
		post.connect(&"reached", _on_checkpoint_reached)
		checkpoints.add_child(post)

	for cell: Vector2i in MapBuilder.cells(rows, MapBuilder.GOAL):
		var goal: Node2D = GoalScene.instantiate() as Node2D
		goal.name = "Goal_%d_%d" % [cell.x, cell.y]
		goal.position = MapBuilder.cell_bottom(cell, TILE)
		goal.connect(&"reached", _on_goal_reached)
		goals.add_child(goal)

	var starts: Array[Vector2i] = MapBuilder.cells(rows, MapBuilder.PLAYER)
	var start: Vector2i = starts[0] if not starts.is_empty() else Vector2i(1, 1)
	start_position = cell_bottom(start)
	# Extra markers beside the start for more players (online play).
	var index: int = 0
	for offset: int in [0, 1, -1, 2, -2, 3]:
		var cell: Vector2i = start + Vector2i(offset, 0)
		if offset != 0 and not _standable(cell):
			continue
		index += 1
		var marker: Marker2D = Marker2D.new()
		marker.name = "Spawn%d" % index
		marker.position = MapBuilder.cell_bottom(cell, TILE)
		marker.add_to_group(&"player_spawn")
		spawns.add_child(marker)


func _standable(cell: Vector2i) -> bool:
	var here: String = tile_at(cell)
	var free: bool = here != MapBuilder.SOLID and here != MapBuilder.SPIKES
	return free and tile_at(cell + Vector2i.DOWN) == MapBuilder.SOLID


func _set_checkpoint(post: Node) -> void:
	if checkpoint != null and is_instance_valid(checkpoint):
		checkpoint.call(&"set_active", false)
	checkpoint = post
	if post != null:
		post.call(&"set_active", true)


func _setup_player(player: Node) -> void:
	if player.has_method(&"set_camera_limits"):
		player.call(&"set_camera_limits", bounds)
	if completed:
		player.set(&"controls_enabled", false)


func _recount(announce: bool) -> void:
	var count: int = 0
	var all: int = 0
	for coin: Node in coins.get_children():
		all += 1
		if bool(coin.get(&"collected")):
			count += 1
	var changed: bool = count != score
	score = count
	total = all
	hud.set_coins(score, total)
	if changed and announce:
		GameEvents.score_changed.emit(score)


func _complete() -> void:
	if completed:
		return
	completed = true
	for player: Node in Game.players():
		player.set(&"controls_enabled", false)
	var result: Dictionary = {
		"time": elapsed, "score": score, "coins": score, "total": total, "falls": falls
	}
	hud.show_win(result)
	GameEvents.level_completed.emit(result)


func _on_coin_changed(_coin: Node) -> void:
	_recount(true)


func _on_checkpoint_reached(post: Node) -> void:
	if post == checkpoint:
		return
	_set_checkpoint(post)
	GameEvents.checkpoint_reached.emit(str(post.get(&"checkpoint_id")))


func _on_goal_reached(_goal: Node) -> void:
	_complete()


func _on_hazard_touched(body: Node2D) -> void:
	send_back(body, true)


func _on_player_spawned(player: Node, _peer_id: int) -> void:
	if is_ancestor_of(player):
		_setup_player(player)


func _on_play_again() -> void:
	Game.restart_level()


func _on_main_menu() -> void:
	Game.quit_to_menu()


func _exit_tree() -> void:
	if GameEvents.player_spawned.is_connected(_on_player_spawned):
		GameEvents.player_spawned.disconnect(_on_player_spawned)
