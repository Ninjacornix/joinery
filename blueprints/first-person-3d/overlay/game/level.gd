extends Node3D
## The first-person level: three switches open the exit door; walking into
## the exit zone beyond it completes the level, stops the players and shows
## the win screen (built by the player's HUD, game/ui/hud.gd).
##
## To add a switch, instance game/props/switch.tscn under `Switches` with its
## own `save_id`; the door then needs every switch, the new one included.

@export var save_id: String = "level"

var elapsed: float = 0.0
var completed: bool = false
var _switches: Array[Node] = []

@onready var door: Node = $Door
@onready var exit_zone: Area3D = $ExitZone


func _ready() -> void:
	add_to_group(&"saveable")
	for child: Node in $Switches.get_children():
		if child.has_signal(&"toggled"):
			_switches.append(child)
			child.connect(&"toggled", _on_switch_toggled.unbind(1))
	door.connect(&"opened", _on_door_opened)
	exit_zone.body_entered.connect(_on_exit_body_entered)
	# Online, joining players learn about the win only through this event.
	GameEvents.level_completed.connect(_on_level_completed)
	GameEvents.player_spawned.connect(_on_player_spawned)
	Game.level_ready(self)
	GameEvents.score_changed.emit(switches_on())


func _physics_process(delta: float) -> void:
	if not completed:
		elapsed += delta


func switch_total() -> int:
	return _switches.size()


func switches_on() -> int:
	var count: int = 0
	for switch: Node in _switches:
		if bool(switch.get(&"on")):
			count += 1
	return count


func switches() -> Array[Node]:
	return _switches.duplicate()


func save_state() -> Dictionary:
	return {"elapsed": elapsed, "completed": completed}


func load_state(state: Dictionary) -> void:
	elapsed = float(state.get("elapsed", elapsed))
	completed = bool(state.get("completed", completed))
	_set_controls(not completed)
	if not completed:
		_local_hud_call(&"hide_win")
		FirstPersonLook.set_playing(true)
	elif _is_authority():
		# Saved after the win: show the result again, not a level nobody can
		# move in.
		GameEvents.level_completed.emit.call_deferred(_result())


func _is_authority() -> bool:
	var session: GameSession = Game.session()
	return session == null or session.is_authority()


func _on_switch_toggled() -> void:
	var count: int = switches_on()
	GameEvents.score_changed.emit(count)
	if count == switch_total() and _is_authority():
		door.call(&"open")


func _on_door_opened() -> void:
	for switch: Node in _switches:
		switch.set(&"locked", true)
		if _is_authority():
			GameEvents.world_changed.emit(switch)


func _on_exit_body_entered(body: Node3D) -> void:
	if completed or not body.is_in_group(&"player") or not _is_authority():
		return
	if not bool(door.get(&"is_open")):
		return
	completed = true
	GameEvents.level_completed.emit(_result())


## The `level_completed` result: time, switches on and how many there are.
func _result() -> Dictionary:
	return {
		"time": elapsed,
		"score": switches_on(),
		"switches": switches_on(),
		"total": switch_total(),
	}


## Runs everywhere the level is won: here on the authority, and on joining
## players when the online feature passes the result on.
func _on_level_completed(_result: Dictionary) -> void:
	completed = true
	_set_controls(false)


func _on_player_spawned(player: Node, _peer_id: int) -> void:
	if is_ancestor_of(player) and completed:
		player.set(&"controls_enabled", false)


func _set_controls(on: bool) -> void:
	for player: Node in Game.players():
		player.set(&"controls_enabled", on)


func _local_hud_call(method: StringName) -> void:
	var me: Node = Game.local_player()
	var hud: Node = me.get_node_or_null(^"Hud") if me != null else null
	if hud != null and hud.has_method(method):
		hud.call(method)


func _exit_tree() -> void:
	if GameEvents.level_completed.is_connected(_on_level_completed):
		GameEvents.level_completed.disconnect(_on_level_completed)
	if GameEvents.player_spawned.is_connected(_on_player_spawned):
		GameEvents.player_spawned.disconnect(_on_player_spawned)
