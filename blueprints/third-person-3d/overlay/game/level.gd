extends Node3D
## The arena. Counts coins, keeps the active checkpoint, respawns anything
## that falls below kill_y, times the run and shows the win screen when every
## coin is collected.

const CameraSettingsPage := preload("res://game/ui/camera_settings_page.gd")
const Hud := preload("res://game/ui/hud.gd")

@export var save_id: String = "level"
## Anything below this height has fallen off the arena.
@export var kill_y: float = -12.0

var score: int = 0
var total: int = 0
var elapsed: float = 0.0
var completed: bool = false
var checkpoint: Node = null

@onready var hud: Hud = $HUD
@onready var coins: Node3D = $Coins
@onready var checkpoints: Node3D = $Checkpoints


func _ready() -> void:
	for coin: Node in coins.get_children():
		coin.connect(&"collected_changed", _on_coin_changed)
		total += 1
	for pad: Node in checkpoints.get_children():
		pad.connect(&"reached", _on_checkpoint_reached)
	checkpoint = checkpoints.get_child(0)
	checkpoint.call(&"set_active", true)
	hud.play_again_requested.connect(_on_play_again)
	hud.main_menu_requested.connect(_on_main_menu)
	Menus.add_settings_page(&"camera", "Camera", CameraSettingsPage.build, 30)
	GameEvents.player_spawned.connect(_on_player_spawned)
	Game.level_ready(self)
	_bind_local_player()
	_recount(false)


func _physics_process(delta: float) -> void:
	if not completed:
		elapsed += delta
		hud.set_time(elapsed)
	for player: Node in Game.players():
		var body: Node3D = player as Node3D
		if body != null and body.is_multiplayer_authority() and body.global_position.y < kill_y:
			body.call(&"respawn", checkpoint.call(&"respawn_transform"))
	if Game.session().is_authority():
		for prop: Node in get_tree().get_nodes_in_group(&"carryable"):
			var item: Node3D = prop as Node3D
			if item != null and is_ancestor_of(item) and item.global_position.y < kill_y:
				item.call(&"return_home")


func coin_nodes() -> Array[Node]:
	return coins.get_children()


func save_state() -> Dictionary:
	return {"time": elapsed, "checkpoint": str(checkpoint.get(&"checkpoint_id"))}


func load_state(state: Dictionary) -> void:
	elapsed = float(state.get("time", elapsed))
	var id: String = str(state.get("checkpoint", ""))
	for pad: Node in checkpoints.get_children():
		if str(pad.get(&"checkpoint_id")) == id:
			_set_checkpoint(pad)


func _set_checkpoint(pad: Node) -> void:
	if checkpoint != null:
		checkpoint.call(&"set_active", false)
	checkpoint = pad
	pad.call(&"set_active", true)


func _on_checkpoint_reached(pad: Node) -> void:
	if pad == checkpoint:
		return
	_set_checkpoint(pad)
	GameEvents.checkpoint_reached.emit(str(pad.get(&"checkpoint_id")))


func _on_coin_changed(_coin: Node) -> void:
	_recount(true)


func _recount(announce: bool) -> void:
	var count: int = 0
	for coin: Node in coins.get_children():
		if bool(coin.get(&"collected")):
			count += 1
	var changed: bool = count != score
	score = count
	hud.set_coins(score, total)
	if changed and announce:
		GameEvents.score_changed.emit(score)
	if score >= total and total > 0 and not completed:
		_complete()
	elif score < total and completed:
		completed = false
		hud.hide_win()
		_set_controls(true)


func _complete() -> void:
	completed = true
	_set_controls(false)
	var result: Dictionary = {"time": elapsed, "score": score, "total": total, "coins": score}
	hud.show_win(result)
	GameEvents.level_completed.emit(result)


func _set_controls(on: bool) -> void:
	for player: Node in Game.players():
		player.set(&"controls_enabled", on)


func _bind_local_player() -> void:
	var player: Node = Game.local_player()
	if player != null:
		hud.bind_player(player)


func _on_player_spawned(player: Node, _peer_id: int) -> void:
	if player.is_multiplayer_authority():
		hud.bind_player(player)
	if completed:
		player.set(&"controls_enabled", false)


func _on_play_again() -> void:
	Game.restart_level()


func _on_main_menu() -> void:
	Game.quit_to_menu()


func _exit_tree() -> void:
	if GameEvents.player_spawned.is_connected(_on_player_spawned):
		GameEvents.player_spawned.disconnect(_on_player_spawned)
