extends "res://tests/features/online-play/pair_check.gd"
## Online checks for memory cards. With a host: the host and the joining
## player take turns; the joining player cannot flip out of turn; the host
## matches a pair (and keeps the turn), misses (and passes it); then the
## joining player matches a pair picked from its own copy of the board, which
## proves both boards hold the same deal. With a dedicated server: only the
## joining player is seated and plays.


func client_check(level: Node) -> void:
	var me_id: int = me()
	if server_plays():
		await until(
			func() -> bool: return Array(level.call(&"seat_ids")) == [SERVER, me_id],
			"the host and the client are seated"
		)
		play.expect(int(level.get(&"turn")) == SERVER, "the host plays first")
		var card: Node = level.call(&"card", 0)
		play.game.request_action(card, &"flip", [])
		await wait_ms(500)
		play.expect(not bool(card.get(&"face_up")), "the client cannot flip out of turn")
		return
	await until(
		func() -> bool: return Array(level.call(&"seat_ids")) == [me_id],
		"a dedicated server seats only the client: %s" % [level.call(&"seat_ids")]
	)
	await _match_a_pair(level, me_id)


func host_check(level: Node) -> void:
	var client: int = client_id()
	if not server_plays():
		play.expect(not Array(level.call(&"seat_ids")).has(SERVER), "the dedicated server has no seat")
		play.expect(int(level.call(&"pairs_for", client)) == 1, "the client's pair counts on the server")
		return
	var pair: Array = _pair(level, true)
	for index: int in pair:
		play.game.request_action(level.call(&"card", index), &"flip", [])
	play.expect(int(level.call(&"pairs_for", SERVER)) == 1, "the host's pair counts")
	play.expect(int(level.get(&"turn")) == SERVER, "a match keeps the turn")
	var miss: Array = _pair(level, false)
	for index: int in miss:
		play.game.request_action(level.call(&"card", index), &"flip", [])
	await until(
		func() -> bool: return int(level.get(&"turn")) == client,
		"a miss passes the turn to the client"
	)


func client_verify(level: Node) -> void:
	if server_plays():
		await until(
			func() -> bool: return int(level.get(&"turn")) == me(),
			"the client's turn comes"
		)
		play.expect(int(level.call(&"pairs_for", SERVER)) == 1, "the host's pair shows here")
		await _match_a_pair(level, me())


## Flips a pair with the same symbol on this copy of the board and waits for
## the server to match it.
func _match_a_pair(level: Node, player: int) -> void:
	var pair: Array = _pair(level, true)
	if pair.size() != 2:
		play.expect(false, "the board has a pair left")
		return
	for index: int in pair:
		play.game.request_action(level.call(&"card", index), &"flip", [])
	var first: Node = level.call(&"card", pair[0])
	var second: Node = level.call(&"card", pair[1])
	await until(
		func() -> bool: return bool(first.get(&"matched")) and bool(second.get(&"matched")),
		"the pair this player picked matches on the server"
	)
	# The cards and the board's tally replicate as separate messages, which
	# may arrive a frame apart; wait for the tally too.
	await until(
		func() -> bool: return int(level.call(&"pairs_for", player)) == 1,
		"the pair counts for this player"
	)


## Two face-down cards: the same symbol (`same`) or different symbols.
func _pair(level: Node, same: bool) -> Array:
	var cards: Array = level.get(&"cards")
	for i: int in cards.size():
		var a: Node = cards[i]
		if bool(a.get(&"matched")) or bool(a.get(&"face_up")):
			continue
		for j: int in range(i + 1, cards.size()):
			var b: Node = cards[j]
			if bool(b.get(&"matched")) or bool(b.get(&"face_up")):
				continue
			if (int(a.get(&"symbol")) == int(b.get(&"symbol"))) == same:
				return [i, j]
	return []
