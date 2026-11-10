extends NetAdapter
## Online play for the top-down arena. Enemies are network-spawned by the
## server (`online/spawn_parents`) and every player's health is synchronised
## from the machine that owns it. The waves, the score and the end of the
## fight run on the server only, so this adapter:
## - sends the arena's progress (score, wave, gems) to the joining players,
##   with the same wave banners the server shows;
## - ends the fight for them with the server's win or loss screen;
## - on the server, notices a joining player going down: their health arrives
##   by synchronisation, so their own `died` only fires on their machine;
## - on the server, hands a heart's healing to the player who picked it up,
##   because only that player's machine owns their health.

const PICKUPS := ^"Pickups"
const ENEMIES := ^"Enemies"

var _sent: Dictionary = {}
var _shown: Dictionary = {}
var _outcome: Dictionary = {}
var _ended: bool = false
var _alive: Dictionary = {}


func _ready() -> void:
	GameEvents.level_completed.connect(_on_level_completed)
	GameEvents.level_failed.connect(_on_level_failed)


func _setup() -> void:
	_sent = {}
	_shown = {}
	_outcome = {}
	_ended = false
	_alive = {}
	if not server:
		return
	var pickups: Node = level.get_node_or_null(PICKUPS)
	if pickups != null:
		for pickup: Node in pickups.get_children():
			if pickup.has_signal(&"picked"):
				pickup.connect(&"picked", _on_picked)


func _physics_process(_delta: float) -> void:
	if not serving():
		return
	var status: Dictionary = _status()
	if status != _sent:
		_sent = status
		send_to_clients(&"_apply_status", [status])
	_watch_health()


func peer_ready(peer: int) -> void:
	if not serving():
		return
	send_to(peer, &"_apply_status", [_status()])
	if not _outcome.is_empty():
		send_to(peer, &"_apply_outcome", [bool(_outcome["won"]), _outcome["result"]])


func _status() -> Dictionary:
	return {
		"score": int(level.get(&"score")),
		"wave": int(level.get(&"wave")),
		"running": bool(level.call(&"is_wave_running")),
		"gems": int(level.get(&"gems_taken")),
	}


## Server: raises `died` on a joining player's copy when their synchronised
## health reaches 0, so the arena can tell when everyone is down.
func _watch_health() -> void:
	for player: Node in Game.players():
		if player.is_multiplayer_authority() or not "health" in player:
			continue
		var id: int = player.get_instance_id()
		var alive: bool = int(player.get(&"health")) > 0
		var was_alive: bool = bool(_alive.get(id, true))
		_alive[id] = alive
		if was_alive and not alive and player.has_signal(&"died"):
			player.emit_signal(&"died")


func _on_picked(pickup: Node, peer_id: int) -> void:
	if not serving() or peer_id == SERVER or str(pickup.get(&"kind")) != "heart":
		return
	send_to(peer_id, &"_heal", [int(pickup.get(&"amount"))])


func _on_level_completed(result: Dictionary) -> void:
	if serving():
		_finish(true, result.duplicate(true))


func _on_level_failed(reason: String) -> void:
	if serving():
		_finish(false, {
			"time": float(level.get(&"elapsed")),
			"score": int(level.get(&"score")),
			"wave": int(level.get(&"wave")),
			"reason": reason,
		})


func _finish(won: bool, result: Dictionary) -> void:
	_outcome = {"won": won, "result": result}
	send_to_clients(&"_apply_status", [_status()])
	send_to_clients(&"_apply_outcome", [won, result])


@rpc("authority", "call_remote", "reliable")
func _apply_status(status: Dictionary) -> void:
	if server or not has_level():
		return
	var hud: Node = level.get(&"hud")
	var score: int = int(status.get("score", 0))
	if score != int(level.get(&"score")):
		level.set(&"score", score)
		hud.call(&"set_score", score)
		GameEvents.score_changed.emit(score)
	level.set(&"gems_taken", int(status.get("gems", 0)))
	var wave: int = int(status.get("wave", 0))
	var running: bool = bool(status.get("running", false))
	var total: int = int(level.call(&"wave_count"))
	var was_wave: int = int(_shown.get("wave", 0))
	var was_running: bool = bool(_shown.get("running", false))
	level.set(&"wave", wave)
	hud.call(&"set_wave", wave, total)
	if running and wave != was_wave and wave > 0:
		hud.call(&"show_banner", tr("Wave %d") % wave if wave < total else tr("Final wave"))
	elif was_running and not running and wave > 0 and wave < total:
		hud.call(&"show_banner", tr("Wave cleared"))
	_shown = status


@rpc("authority", "call_remote", "reliable")
func _apply_outcome(won: bool, result: Dictionary) -> void:
	if server or _ended or not has_level():
		return
	_ended = true
	level.set(&"completed" if won else &"failed", true)
	for player: Node in Game.players():
		player.set(&"controls_enabled", false)
	var enemies: Node = level.get_node_or_null(ENEMIES)
	if enemies != null:
		for enemy: Node in enemies.get_children():
			if enemy.is_in_group(&"enemy"):
				enemy.set(&"frozen", true)
	level.get(&"hud").call(&"show_end", won, result)
	if won:
		GameEvents.level_completed.emit(result)
	else:
		GameEvents.level_failed.emit(str(result.get("reason", "defeated")))


@rpc("authority", "call_remote", "reliable")
func _heal(amount: int) -> void:
	var mine: Node = Game.local_player()
	if has_level() and mine != null and mine.has_method(&"heal"):
		mine.call(&"heal", amount)
