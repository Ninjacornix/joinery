extends Node2D
## The arena. Runs the waves of chasing enemies (spawned through `Game.spawn`,
## so online play can replicate them), counts score from gems and defeated
## enemies, heals with hearts, and ends with a win screen when the last wave
## is cleared or a lose screen when every player is down.

const Hud := preload("res://game/ui/hud.gd")
const ArenaFloor := preload("res://game/arena/arena_floor.gd")
const CHASER := preload("res://game/enemies/chaser.tscn")
## Each wave: how many enemies of each variant. Add a line for another wave.
const WAVES := [
	{"chaser": 3},
	{"chaser": 4, "brute": 1},
	{"chaser": 6, "brute": 2},
]
const VARIANTS := ["chaser", "brute"]

@export var save_id: String = "level"
## Start waves on their own. Turn off to build or test the arena in peace;
## `start_wave` still works.
@export var auto_waves: bool = true
@export var first_wave_delay: float = 1.5
@export var wave_delay: float = 2.0

var score: int = 0
## The current wave, from 1; 0 before the first.
var wave: int = 0
var elapsed: float = 0.0
var gems_taken: int = 0
var completed: bool = false
var failed: bool = false

var _wave_running: bool = false
## Seconds until the next wave starts; below 0 when none is pending.
var _next_wave_in: float = -1.0

@onready var hud: Hud = $HUD
@onready var arena_floor: ArenaFloor = $Floor
@onready var enemy_root: Node2D = $Enemies
@onready var enemy_spawns: Node2D = $EnemySpawns
@onready var pickups: Node2D = $Pickups


func _ready() -> void:
	_next_wave_in = first_wave_delay
	for pickup: Node in pickups.get_children():
		pickup.connect(&"picked", _on_picked)
	enemy_root.child_entered_tree.connect(_on_enemy_added)
	hud.retry_requested.connect(_on_retry)
	hud.main_menu_requested.connect(_on_main_menu)
	GameEvents.player_spawned.connect(_on_player_spawned)
	Game.level_ready(self)
	for player: Node in Game.players():
		_bind_player(player)
	hud.set_score(score)
	hud.set_wave(wave, WAVES.size())


func _physics_process(delta: float) -> void:
	if completed or failed:
		return
	elapsed += delta
	var me: Node = Game.local_player()
	if me != null:
		hud.set_dash(float(me.call(&"dash_ready")))
	if not Game.session().is_authority():
		return
	if _wave_running and enemies().is_empty():
		_wave_cleared()
	elif auto_waves and _next_wave_in >= 0.0:
		_next_wave_in -= delta
		if _next_wave_in <= 0.0:
			start_wave(wave + 1)


## Spawns wave `index` (from 1) at the spawn markers furthest from players.
func start_wave(index: int) -> void:
	if index < 1 or index > WAVES.size() or completed or failed:
		return
	wave = index
	_wave_running = true
	_next_wave_in = -1.0
	var markers: Array[Node2D] = _spawn_markers()
	var slot: int = 0
	var spec: Dictionary = WAVES[index - 1]
	for variant: String in VARIANTS:
		for _i: int in int(spec.get(variant, 0)):
			spawn_enemy(markers[slot % markers.size()].position, variant)
			slot += 1
	hud.set_wave(wave, WAVES.size())
	if wave < WAVES.size():
		hud.show_banner(tr("Wave %d") % wave)
	else:
		hud.show_banner(tr("Final wave"))


## Adds one enemy under `Enemies` through the session. `extra` sets more
## properties (for example `health` or `spawn_grace`) before it is ready.
func spawn_enemy(at: Vector2, variant: String = "chaser", extra: Dictionary = {}) -> Node:
	var data: Dictionary = {"position": at, "variant": variant}
	data.merge(extra, true)
	return Game.spawn(CHASER, enemy_root, data)


## Enemies still standing.
func enemies() -> Array[Node]:
	var found: Array[Node] = []
	for node: Node in enemy_root.get_children():
		if not node.is_in_group(&"enemy") or node.is_queued_for_deletion():
			continue
		if int(node.get(&"health")) > 0:
			found.append(node)
	return found


func wave_count() -> int:
	return WAVES.size()


## Seconds until the next wave, or -1 when none is pending.
func next_wave_in() -> float:
	return _next_wave_in


func is_wave_running() -> bool:
	return _wave_running


func add_score(points: int) -> void:
	score += points
	hud.set_score(score)
	GameEvents.score_changed.emit(score)


func save_state() -> Dictionary:
	var standing: Array[Dictionary] = []
	for enemy: Node in enemies():
		standing.append(enemy.call(&"save_state"))
	return {
		"time": elapsed,
		"score": score,
		"gems": gems_taken,
		"wave": wave,
		"wave_running": _wave_running,
		"next_wave_in": _next_wave_in,
		"enemies": standing,
	}


func load_state(state: Dictionary) -> void:
	elapsed = float(state.get("time", elapsed))
	score = int(state.get("score", score))
	gems_taken = int(state.get("gems", gems_taken))
	wave = clampi(int(state.get("wave", wave)), 0, WAVES.size())
	for node: Node in enemy_root.get_children():
		if node.is_in_group(&"enemy"):
			enemy_root.remove_child(node)
			node.queue_free()
	var saved: Variant = state.get("enemies", [])
	if saved is Array:
		for entry: Variant in saved:
			if entry is Dictionary:
				var data: Dictionary = entry
				var at: Variant = data.get("position", Vector2.ZERO)
				spawn_enemy(
					at if at is Vector2 else Vector2.ZERO,
					str(data.get("variant", "chaser")),
					{"health": int(data.get("health", -1))}
				)
	_wave_running = bool(state.get("wave_running", false))
	_next_wave_in = float(state.get("next_wave_in", -1.0))
	completed = false
	failed = false
	hud.hide_end()
	_set_controls(true)
	hud.set_score(score)
	hud.set_wave(wave, WAVES.size())
	# Players load after the level; once everything is in, a state that was
	# saved after the fight ended shows its result again.
	_end_if_over.call_deferred()


## Ends the fight when the restored state is already decided: every player
## down (lost) or the last wave cleared (won). Without this, a save made on
## the result screen would load a dead player and no way on.
func _end_if_over() -> void:
	if completed or failed or not is_inside_tree() or not Game.session().is_authority():
		return
	var players: Array[Node] = Game.players()
	if not players.is_empty() and players.all(_is_down):
		_fail()
	elif wave >= WAVES.size() and not _wave_running and enemies().is_empty():
		_complete()


static func _is_down(player: Node) -> bool:
	return int(player.get(&"health")) <= 0


func _spawn_markers() -> Array[Node2D]:
	var markers: Array[Node2D] = []
	for node: Node in enemy_spawns.get_children():
		if node is Node2D:
			markers.append(node as Node2D)
	var players: Array[Node] = Game.players()
	var distance_to_players: Callable = func(marker: Node2D) -> float:
		var nearest: float = INF
		for player: Node in players:
			nearest = minf(nearest, marker.global_position.distance_to((player as Node2D).global_position))
		return nearest
	markers.sort_custom(func(a: Node2D, b: Node2D) -> bool:
		return float(distance_to_players.call(a)) > float(distance_to_players.call(b)))
	return markers


func _wave_cleared() -> void:
	_wave_running = false
	if wave >= WAVES.size():
		_complete()
		return
	_next_wave_in = wave_delay
	hud.show_banner(tr("Wave cleared"))


func _complete() -> void:
	completed = true
	_end_fight()
	var result: Dictionary = {
		"time": elapsed, "score": score, "waves": WAVES.size(), "gems": gems_taken
	}
	hud.show_end(true, result)
	GameEvents.level_completed.emit(result)


func _fail() -> void:
	failed = true
	_end_fight()
	hud.show_end(false, {"time": elapsed, "score": score, "wave": wave})
	GameEvents.level_failed.emit("defeated")


func _end_fight() -> void:
	_wave_running = false
	_next_wave_in = -1.0
	_set_controls(false)
	for enemy: Node in enemy_root.get_children():
		if enemy.is_in_group(&"enemy"):
			enemy.set(&"frozen", true)


func _set_controls(on: bool) -> void:
	for player: Node in Game.players():
		player.set(&"controls_enabled", on and int(player.get(&"health")) > 0)


func _bind_player(player: Node) -> void:
	if not player.is_connected(&"died", _on_player_died):
		player.connect(&"died", _on_player_died)
	if player.is_multiplayer_authority():
		if not player.is_connected(&"health_changed", hud.set_health):
			player.connect(&"health_changed", hud.set_health)
		hud.set_health(int(player.get(&"health")), int(player.get(&"max_health")))
	player.call(&"set_camera_limits", arena_floor.global_rect())
	if completed or failed:
		player.set(&"controls_enabled", false)


func _on_player_spawned(player: Node, _peer_id: int) -> void:
	if is_ancestor_of(player):
		_bind_player(player)


func _on_player_died() -> void:
	if failed or completed:
		return
	for player: Node in Game.players():
		if int(player.get(&"health")) > 0:
			return
	_fail()


func _on_enemy_added(node: Node) -> void:
	if node.has_signal(&"defeated") and not node.is_connected(&"defeated", _on_enemy_defeated):
		node.connect(&"defeated", _on_enemy_defeated)
	if completed or failed:
		node.set(&"frozen", true)


func _on_enemy_defeated(enemy: Node) -> void:
	if completed or failed:
		return
	add_score(int(enemy.get(&"points")))


func _on_picked(pickup: Node, peer_id: int) -> void:
	var amount: int = int(pickup.get(&"amount"))
	if str(pickup.get(&"kind")) == "heart":
		for player: Node in Game.players():
			if player.get_multiplayer_authority() == peer_id:
				player.call(&"heal", amount)
				return
		return
	gems_taken += 1
	add_score(amount)


func _on_retry() -> void:
	Game.restart_level()


func _on_main_menu() -> void:
	Game.quit_to_menu()


func _exit_tree() -> void:
	if GameEvents.player_spawned.is_connected(_on_player_spawned):
		GameEvents.player_spawned.disconnect(_on_player_spawned)
