extends SceneTree
## Memory cards online: two real game processes on localhost share one table
## and take turns. Needs the online-play feature (the `Net` autoload).
##
## Host:   --host --port=N --autostart
## Client: --join=127.0.0.1:N
##
## Proves that the seats are the connected peers, that only the peer whose
## turn it is can flip (a click out of turn is refused by the server), that a
## client's flip runs on the server and comes back, that a miss passes the
## turn and a match keeps it, and that pairs per player, moves and the HUD
## agree on both screens. The two processes coordinate only through the
## board's own state.

const LONG_MS := 30000
## The name the client takes once it has tried to flip out of turn.
const SIGNAL_NAME := "Tried early"

var h: PlayHarness
var net: Node
var replication: Node


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	net = root.get_node_or_null(^"Net")
	if net == null:
		h = PlayHarness.new(self, "cards_online")
		h.expect(false, "the online-play feature is applied")
		h.finish()
		return
	replication = net.get(&"replication")
	if bool(net.call(&"has_arg", "host")):
		h = PlayHarness.new(self, "cards_host")
		await _host()
	else:
		h = PlayHarness.new(self, "cards_client")
		await _client()
	h.finish()


# --- Host ------------------------------------------------------------------------


func _host() -> void:
	await h.boot(true)
	var level: Control = h.game.call(&"current_level") as Control
	h.expect(level != null, "the host deals a table")
	if level == null:
		return
	h.expect(bool(h.game.call(&"session").call(&"is_authority")), "the host runs the rules")
	await _until(func() -> bool: return _client_id() != 0, "a client joins and loads the table")
	var client: int = _client_id()
	if client == 0:
		return
	await _until(func() -> bool: return _seats(level).size() == 2, "both peers are seated")
	h.expect(
		Array(_seats(level)) == [1, client], "the seats are the peers (got %s)" % _seats(level)
	)
	h.expect(bool(level.call(&"is_multiplayer")), "two peers make a two-player game")
	h.expect(not bool(level.call(&"is_local_seats")), "online seats are not shared")
	h.expect(int(level.get(&"turn")) == 1, "the host starts")
	h.expect(bool(level.call(&"is_my_turn")), "the host may play first")
	h.expect(
		_turn_text(level) == "Your turn", "the host's HUD says so (got '%s')" % _turn_text(level)
	)
	# The client tries to flip out of turn, then renames itself in the lobby.
	# Reliable RPCs from one peer arrive in order, so once the new name is
	# here its flip has already been judged.
	var lobby: Node = root.get_node(^"Lobby")
	await _until(
		func() -> bool: return _lobby_name(lobby, client) == SIGNAL_NAME,
		"the client tried to play early"
	)
	h.expect(_hidden(level, PackedInt32Array()) == 0, "the server refused the client's early flip")
	h.expect(
		int(level.get(&"moves")) == 0 and level.call(&"open_cards").is_empty(), "no card is open"
	)

	var miss: PackedInt32Array = _mismatch(level)
	await _click(level, miss[0])
	await _click(level, miss[1])
	h.expect(int(level.get(&"moves")) == 1, "the host's miss counts a move")
	await _until(func() -> bool: return int(level.get(&"turn")) == client, "a miss passes the turn")
	h.expect(not bool(level.call(&"is_my_turn")), "now the host waits")
	h.expect(_turn_text(level) == "Player 2's turn", "the host's HUD names player 2")
	var blocked: int = _hidden(level, PackedInt32Array())
	await _click(level, blocked)
	h.expect(not _up(level, blocked), "the host cannot flip on the client's turn")

	await _until(
		func() -> bool: return int(level.call(&"pairs_for", client)) == 1,
		"the client's match runs on the server"
	)
	h.expect(int(level.get(&"turn")) == client, "a match keeps the client's turn")
	await _until(
		func() -> bool: return int(level.get(&"turn")) == 1 and not _busy(level),
		"the client's miss passes the turn back"
	)
	h.expect(
		int(level.get(&"moves")) == 3, "three moves were played (got %d)" % level.get(&"moves")
	)
	h.expect(int(level.call(&"pairs_left")) == 7, "seven pairs are left")

	var pair: PackedInt32Array = _matching_pair(level)
	await _click(level, pair[0])
	await _click(level, pair[1])
	h.expect(_removed(level, pair[0]) and _removed(level, pair[1]), "the host matches a pair")
	h.expect(int(level.call(&"pairs_for", 1)) == 1, "the host has one pair")
	h.expect(int(level.get(&"turn")) == 1, "the host keeps the turn")
	await _until(
		func() -> bool: return not (net.call(&"peers") as PackedInt32Array).has(client),
		"the client leaves"
	)
	await _until(func() -> bool: return _seats(level).size() == 1, "the client's seat is freed")
	h.expect(int(level.get(&"turn")) == 1, "the turn stays with someone still seated")


# --- Client ----------------------------------------------------------------------


func _client() -> void:
	await h.boot(false)
	await _until(
		func() -> bool: return h.game.call(&"current_level") != null, "the host's table loads here"
	)
	var level: Control = h.game.call(&"current_level") as Control
	if level == null:
		return
	h.expect(
		not bool(h.game.call(&"session").call(&"is_authority")), "the client is not the authority"
	)
	var me: int = root.multiplayer.get_unique_id()
	await _until(func() -> bool: return _seats(level).size() == 2, "both peers are seated here")
	h.expect(int(level.get(&"turn")) == 1, "player 1 starts here too")
	h.expect(not bool(level.call(&"is_my_turn")), "the client waits for the host")
	h.expect(
		_turn_text(level) == "Player 1's turn",
		"the HUD names player 1 (got '%s')" % _turn_text(level)
	)
	var early: int = _hidden(level, PackedInt32Array())
	await _click(level, early)
	h.expect(not _up(level, early), "a flip out of turn does not run here")
	h.expect(not bool(level.call(&"may_flip_here")), "the table says to wait for the host")
	# Send the flip anyway, as a modified client could: the server judges it.
	h.game.call(&"request_action", _card(level, early), &"flip")
	# Tell the host it may play now (see `_host`).
	root.get_node(^"Lobby").rpc_id(1, &"register_player", SIGNAL_NAME)
	await _wait_ms(400)
	h.expect(not _up(level, early), "the server refuses a flip out of turn")

	await _until(
		func() -> bool: return int(level.get(&"turn")) == me, "the host's miss passes the turn here"
	)
	await _until(func() -> bool: return not _busy(level), "the host's miss turns back here")
	h.expect(int(level.get(&"moves")) == 1, "the host's move is mirrored")
	h.expect(_turn_text(level) == "Your turn", "the HUD says it is this player's turn")

	var pair: PackedInt32Array = _matching_pair(level)
	await _click(level, pair[0])
	h.expect(not _up(level, pair[0]), "a client's flip does not run locally first")
	await _until(
		func() -> bool: return _up(level, pair[0]), "the server flips the card for the client"
	)
	await _click(level, pair[1])
	await _until(
		func() -> bool: return _removed(level, pair[0]) and _removed(level, pair[1]),
		"the client's pair is removed"
	)
	await _until(
		func() -> bool: return int(level.call(&"pairs_for", me)) == 1,
		"the client's pair is counted for it"
	)
	h.expect(int(level.get(&"score")) == 100, "the match scores here")
	h.expect(int(level.get(&"turn")) == me, "a match keeps the turn")

	var miss: PackedInt32Array = _mismatch(level)
	await _click(level, miss[0])
	await _until(func() -> bool: return _up(level, miss[0]), "the first card of the miss flips")
	await _click(level, miss[1])
	await _until(func() -> bool: return bool(level.call(&"is_resolving")), "the miss shows here")
	h.expect(_turn_text(level) == "Your turn", "the turn holds while the miss shows")
	await _until(
		func() -> bool: return int(level.get(&"turn")) == 1, "the miss passes the turn back"
	)
	# Once the turn is back the host may already clear one of them.
	await _until(
		func() -> bool: return _settled(level, miss[0]) and _settled(level, miss[1]),
		"the missed cards turn back"
	)

	await _until(
		func() -> bool: return int(level.call(&"pairs_for", 1)) == 1,
		"the host's match reaches this screen"
	)
	h.expect(int(level.call(&"pairs_left")) == 6, "six pairs are left here")
	var hud: Node = level.get_node(^"%Hud")
	h.expect((hud.get(&"seats") as Control).get_child_count() == 2, "the HUD shows both players")
	h.game.call(&"quit_to_menu")
	h.expect(not bool(net.call(&"is_online")), "leaving ends the online session")
	await _wait_ms(500)


# --- Helpers ---------------------------------------------------------------------


func _lobby_name(lobby: Node, id: int) -> String:
	var players: Dictionary = lobby.get(&"players")
	var entry: Variant = players.get(id, {})
	return str((entry as Dictionary).get("name", "")) if entry is Dictionary else ""


func _seats(level: Node) -> PackedInt32Array:
	return level.call(&"seat_ids") as PackedInt32Array


func _card(level: Node, index: int) -> Button:
	return level.call(&"card", index) as Button


func _up(level: Node, index: int) -> bool:
	return bool(_card(level, index).get(&"face_up"))


func _removed(level: Node, index: int) -> bool:
	return bool(_card(level, index).call(&"is_removed"))


## Face down again, or cleared by a later match.
func _settled(level: Node, index: int) -> bool:
	return not _up(level, index) or _removed(level, index)


## True while a missed pair is showing or a card is open.
func _busy(level: Node) -> bool:
	return bool(level.call(&"is_resolving")) or not (level.call(&"open_cards") as Array).is_empty()


func _turn_text(level: Node) -> String:
	var label: Label = level.get_node(^"%Hud").get(&"turn_label") as Label
	return label.text if label.visible else ""


## The first hidden card not in `skip`, or -1.
func _hidden(level: Node, skip: PackedInt32Array) -> int:
	for i: int in (level.call(&"layout") as PackedInt32Array).size():
		if not skip.has(i) and bool(_card(level, i).call(&"is_hidden")):
			return i
	return -1


## Two hidden cards with the same symbol.
func _matching_pair(level: Node) -> PackedInt32Array:
	var layout: PackedInt32Array = level.call(&"layout")
	for i: int in layout.size():
		if not bool(_card(level, i).call(&"is_hidden")):
			continue
		for j: int in range(i + 1, layout.size()):
			if layout[j] == layout[i] and bool(_card(level, j).call(&"is_hidden")):
				return PackedInt32Array([i, j])
	return PackedInt32Array([-1, -1])


## Two hidden cards with different symbols.
func _mismatch(level: Node) -> PackedInt32Array:
	var layout: PackedInt32Array = level.call(&"layout")
	var first: int = _hidden(level, PackedInt32Array())
	for j: int in range(first + 1, layout.size()):
		if layout[j] != layout[first] and bool(_card(level, j).call(&"is_hidden")):
			return PackedInt32Array([first, j])
	return PackedInt32Array([-1, -1])


func _click(level: Node, index: int) -> void:
	if index < 0:
		h.expect(false, "a card to click exists")
		return
	await h.click_control(_card(level, index))
	await h.physics_frames(1)


## The first other peer that has the table loaded, or 0.
func _client_id() -> int:
	for id: int in replication.call(&"ready_peers") as PackedInt32Array:
		if id != 1:
			return id
	return 0


func _until(condition: Callable, message: String, limit_ms: int = LONG_MS) -> bool:
	var deadline: int = Time.get_ticks_msec() + limit_ms
	while not bool(condition.call()):
		if Time.get_ticks_msec() > deadline:
			h.expect(false, "%s (waited %d ms)" % [message, limit_ms])
			return false
		await process_frame
	return true


func _wait_ms(ms: int) -> void:
	var deadline: int = Time.get_ticks_msec() + ms
	while Time.get_ticks_msec() < deadline:
		await process_frame
