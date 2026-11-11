extends Node3D
## The VR room: the button opens the glass case; once the cube rests on the
## table the level is complete, the players stop and the win page opens
## (built by the player's HUD, game/ui/hud.gd). The ball and the can are
## there to pick up and throw.
##
## Floors are where teleports land; anything in group `no_teleport` (walls,
## table, posts, shelf) is not.

## The cube counts as on the table inside this box above the top (metres,
## table space): half width, half depth, and height above the top.
const ON_TABLE := Vector3(0.68, 0.3, 0.38)
const TABLE_TOP := 0.75
const RESTING_SPEED := 0.3

@export var save_id: String = "level"

var elapsed: float = 0.0
var completed: bool = false
var case_open: bool = false

@onready var button: Node = $PushButton
@onready var glass_case: StaticBody3D = $Case
@onready var table: Node3D = $Table
@onready var cube: RigidBody3D = $Pickups/Cube


func _ready() -> void:
	add_to_group(&"saveable")
	button.connect(&"pressed", _on_button_pressed)
	# Online, joining players learn about the win only through this event.
	GameEvents.level_completed.connect(_on_level_completed)
	GameEvents.player_spawned.connect(_on_player_spawned)
	Game.level_ready(self)


func _physics_process(delta: float) -> void:
	if completed:
		return
	elapsed += delta
	if _is_authority() and cube_on_table():
		completed = true
		GameEvents.level_completed.emit({"time": elapsed, "score": 1})


## True when the cube rests on the table top and nobody holds it.
func cube_on_table() -> bool:
	if not is_instance_valid(cube) or bool(cube.call(&"is_held")):
		return false
	var local: Vector3 = table.to_local(cube.global_position)
	return (
		absf(local.x) <= ON_TABLE.x
		and absf(local.z) <= ON_TABLE.z
		and local.y >= TABLE_TOP
		and local.y <= TABLE_TOP + ON_TABLE.y
		and cube.linear_velocity.length() <= RESTING_SPEED
	)


## Lifts the glass case away so the cube can be taken.
func open_case(animate: bool = true) -> void:
	if case_open:
		return
	case_open = true
	cube.set(&"can_be_grabbed", true)
	for child: Node in glass_case.get_children():
		if child is CollisionShape3D:
			(child as CollisionShape3D).set_deferred(&"disabled", true)
	if not animate or Settings.reduced_motion():
		glass_case.visible = false
		return
	var tween: Tween = create_tween()
	tween.tween_property(glass_case, "position:y", glass_case.position.y + 0.6, 0.5)
	tween.tween_callback(glass_case.hide)


func save_state() -> Dictionary:
	return {"elapsed": elapsed, "completed": completed, "case_open": case_open}


func load_state(state: Dictionary) -> void:
	elapsed = float(state.get("elapsed", elapsed))
	completed = bool(state.get("completed", completed))
	if bool(state.get("case_open", case_open)):
		open_case(false)
	_set_controls(not completed)
	if not completed:
		_local_hud_call(&"hide_win")
	elif _is_authority():
		# Saved after the win: show the result again, not a level nobody can
		# move in.
		GameEvents.level_completed.emit.call_deferred({"time": elapsed, "score": 1})


func _is_authority() -> bool:
	var session: GameSession = Game.session()
	return session == null or session.is_authority()


func _on_button_pressed() -> void:
	open_case()


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
