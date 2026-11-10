extends NetAdapter
## Online play for memory cards. The board's rules run on the server, every
## card mirrors through the game core, and turns go round the online peers.
## A dedicated server has nobody at the table, so on one this seats only the
## joining players (in peer order) and reseats as they come and go. Seats are
## changed through the board's own save and load, so scores and the board
## stay as they are.


func _ready() -> void:
	multiplayer.peer_disconnected.connect(_on_peer_left)


func _setup() -> void:
	_reseat()


func peer_ready(_peer: int) -> void:
	_reseat()


func _on_peer_left(_id: int) -> void:
	_reseat.call_deferred()


func _reseat() -> void:
	if not serving() or not bool(get_parent().get(&"dedicated")):
		return
	var seats: PackedInt32Array = clients()
	var state: Dictionary = level.call(&"save_state")
	var current: Array = state.get("seats", [])
	if current == Array(seats) and not bool(state.get("local_seats", true)):
		return
	state["seats"] = Array(seats)
	state["local_seats"] = false
	if not seats.is_empty() and not seats.has(int(state.get("turn", 0))):
		state["turn"] = seats[0]
	level.call(&"load_state", state)
	GameEvents.world_changed.emit(level)
