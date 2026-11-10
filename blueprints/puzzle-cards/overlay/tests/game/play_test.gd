extends SceneTree
## Play test for memory cards, driven by injected input.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##        -- --autostart --settings-path=user://test_settings.cfg
##
## Cards are flipped with focus navigation (`ui_*`), with mouse clicks at
## their position, or (for the turn checks) as a given peer through
## `Game.perform_action`, the path an online session takes on the server.

const TEST_NAME := "puzzle-cards"
const SEED := 42
## `Deck.deal(42, 8)`: a known layout, so a change to the shuffle is caught.
const KNOWN_LAYOUT := [3, 1, 6, 3, 7, 2, 5, 7, 0, 4, 5, 2, 0, 1, 6, 4]
const MISMATCH_FRAMES := 48

var h: PlayHarness
var game: Node
var events: Node
var menus: Node
var settings: Node
var deck: GDScript
var last_score: int = -1


func _initialize() -> void:
	h = PlayHarness.new(self, TEST_NAME)
	_run.call_deferred()


func _run() -> void:
	game = root.get_node(^"Game")
	events = root.get_node(^"GameEvents")
	menus = root.get_node(^"Menus")
	settings = root.get_node(^"Settings")
	deck = load("res://game/deck.gd") as GDScript
	events.connect(&"score_changed", func(value: int) -> void: last_score = value)
	settings.call(&"set_value", "access", "reduced_motion", false)

	await h.boot()
	h.expect(game.call(&"current_level") != null, "autostart starts a game")
	_check_deck()

	var level: Control = await _start({"seed": SEED})
	if level == null:
		h.finish()
		return
	await _check_layout(level)
	await _check_focus_and_match(level)
	await _check_mismatch_by_click(level)
	await _check_streak(level)
	await _check_save_round_trip(level)
	await _check_reduced_motion(level)
	await _check_pause(level)
	level = await _check_restart(level)
	if level != null:
		h.expect_min_text(level, "the table")
		await _check_play_through(level)
	await _check_lose()
	await _check_turns()
	await _check_two_players_from_menu()
	h.finish()


# --- helpers -----------------------------------------------------------------


## Starts a game with `options` and returns the new level once it started.
func _start(options: Dictionary) -> Control:
	var started: Dictionary = h.watch(events.level_started)
	game.call(&"start_game", options)
	var args: Array = await h.wait_watched(started, 240)
	h.expect(not args.is_empty(), "a game with %s starts" % [options])
	await h.process_frames(3)
	return game.call(&"current_level") as Control


func _card(level: Node, index: int) -> Button:
	return level.call(&"card", index) as Button


func _up(card: Button) -> bool:
	return bool(card.get(&"face_up"))


func _removed(card: Button) -> bool:
	return bool(card.call(&"is_removed"))


func _symbol(card: Button) -> int:
	return int(card.get(&"symbol"))


## Index of the other card with the same symbol.
func _partner(level: Node, index: int) -> int:
	var layout: PackedInt32Array = level.call(&"layout")
	for i: int in layout.size():
		if i != index and layout[i] == layout[index]:
			return i
	return -1


func _click(level: Node, index: int) -> void:
	await h.click_control(_card(level, index))
	await h.physics_frames(1)


## Waits out the delay after a miss.
func _settle(level: Node) -> void:
	for _i: int in MISMATCH_FRAMES + 20:
		if not bool(level.call(&"is_resolving")):
			break
		await h.physics_frames(1)
	await h.physics_frames(2)


func _focused_index() -> int:
	var owner: Control = h.focused()
	if owner != null and owner.has_method(&"is_removed"):
		return int(owner.get(&"index"))
	return -1


func _as_peer(peer: int, level: Node, index: int) -> void:
	game.call(&"perform_action", peer, _card(level, index), &"flip")
	await h.physics_frames(1)


# --- checks ------------------------------------------------------------------


func _check_deck() -> void:
	var a: PackedInt32Array = deck.call(&"deal", SEED, 8)
	h.expect(Array(a) == KNOWN_LAYOUT, "seed %d deals the known layout (got %s)" % [SEED, a])
	h.expect(deck.call(&"deal", SEED, 8) == a, "the same seed deals the same layout")
	h.expect(deck.call(&"deal", 7, 8) != a, "another seed deals another layout")
	var counts: Dictionary = {}
	for symbol: int in a:
		counts[symbol] = int(counts.get(symbol, 0)) + 1
	h.expect(counts.size() == 8, "eight different symbols are dealt")
	for symbol: int in counts:
		h.expect(int(counts[symbol]) == 2, "symbol %d is dealt exactly twice" % symbol)
	h.expect(int(deck.call(&"stars_for", 8, 8)) == 3, "a perfect game earns three stars")
	h.expect(int(deck.call(&"stars_for", 15, 8)) == 2, "fifteen moves earn two stars")
	h.expect(int(deck.call(&"stars_for", 25, 8)) == 1, "a long game still earns one star")
	h.expect(int(deck.call(&"points_for", 3, 100, 50)) == 200, "a third match in a row adds 100")


func _check_layout(level: Control) -> void:
	var layout: PackedInt32Array = level.call(&"layout")
	h.expect(Array(layout) == KNOWN_LAYOUT, "the table shows the seeded deal (got %s)" % layout)
	h.expect(int(level.get(&"moves")) == 0, "a new game has no moves")
	h.expect(int(level.call(&"pairs_left")) == 8, "a new game has eight pairs left")
	h.expect(last_score == 0, "a new game reports a score of 0")
	for i: int in 16:
		var card: Button = _card(level, i)
		h.expect(not _up(card) and card.text.is_empty(), "card %d starts face down" % i)
	var hud: Node = level.get_node(^"%Hud")
	h.expect(String(hud.get(&"moves_label").text) == "Moves: 0 of 30", "the HUD shows the moves")
	h.expect(String(hud.get(&"pairs_label").text) == "Pairs: 0 of 8", "the HUD shows the pairs")


func _check_focus_and_match(level: Control) -> void:
	h.expect(_focused_index() == 0, "the first card has focus (got %d)" % _focused_index())
	await h.tap(&"ui_right")
	h.expect(_focused_index() == 1, "ui_right moves focus to the next card")
	await h.tap(&"ui_down")
	h.expect(_focused_index() == 5, "ui_down moves focus to the card below")
	await h.tap(&"ui_up")
	await h.tap(&"ui_left")
	h.expect(_focused_index() == 0, "ui_up and ui_left go back to the first card")

	var first: Button = _card(level, 0)
	var partner: int = _partner(level, 0)
	h.expect(partner == 3, "card 0 pairs with card 3 in the known layout")
	await h.tap(&"ui_accept")
	h.expect(_up(first), "ui_accept flips the focused card")
	await h.physics_frames(15)
	h.expect(first.text == String(deck.call(&"symbol", _symbol(first))), "it shows its symbol")
	h.expect(int(level.get(&"moves")) == 0, "one card up is not a move yet")
	await h.tap(&"ui_accept")
	h.expect(
		_up(first) and level.call(&"open_cards").size() == 1,
		"flipping a card that is already up does nothing"
	)

	for _i: int in partner:
		await h.tap(&"ui_right")
	h.expect(_focused_index() == partner, "focus walks to the partner card")
	await h.tap(&"ui_accept")
	var second: Button = _card(level, partner)
	h.expect(_removed(first) and _removed(second), "a matching pair is removed")
	h.expect(int(level.get(&"moves")) == 1, "a pair counts one move")
	h.expect(int(level.call(&"pairs_left")) == 7, "seven pairs are left")
	h.expect(int(level.get(&"score")) == 100, "a match scores 100")
	h.expect(last_score == 100, "score_changed reports the match")
	h.expect(
		first.focus_mode == Control.FOCUS_NONE and first.disabled,
		"a removed card can no longer be chosen"
	)
	await h.physics_frames(2)
	var now: int = _focused_index()
	h.expect(
		now >= 0 and not _removed(_card(level, now)),
		"focus moves on to a card still in play (got %d)" % now
	)
	await h.physics_frames(45)
	h.expect(StringName(second.call(&"look")) == &"slot", "the pair fades to an empty slot")


func _check_mismatch_by_click(level: Control) -> void:
	var a: Button = _card(level, 1)
	var b: Button = _card(level, 2)
	var c: Button = _card(level, 4)
	h.expect(_symbol(a) != _symbol(b), "cards 1 and 2 differ")
	await _click(level, 1)
	h.expect(_up(a), "clicking a card flips it")
	h.expect(bool(a.call(&"is_animating")), "the flip animates")
	await _click(level, 2)
	h.expect(_up(a) and _up(b), "clicking a second card flips it too")
	h.expect(int(level.get(&"moves")) == 2, "the miss counts a move")
	h.expect(bool(level.call(&"is_resolving")), "a miss waits before turning back")
	h.expect(StringName(a.call(&"look")) == &"miss", "a miss is marked")
	h.expect(int(level.get(&"streak")) == 0, "a miss ends the streak")
	await _click(level, 4)
	h.expect(not _up(c), "input is ignored while a miss is showing")
	await h.tap(&"ui_accept")
	h.expect(level.call(&"open_cards").size() == 2, "ui_accept is ignored too")
	await h.physics_frames(24)
	h.expect(_up(a) and _up(b), "the missed cards stay up for a moment")
	await _settle(level)
	h.expect(not _up(a) and not _up(b), "the missed cards turn back after the delay")
	h.expect(not _removed(a) and not _removed(b), "missed cards stay in play")
	h.expect(not bool(level.call(&"is_resolving")), "input is taken again")
	await _click(level, 4)
	h.expect(_up(c), "a card can be flipped after the delay")


func _check_streak(level: Control) -> void:
	# Card 4 is up; its partner is card 7. Then 5 and 11 make two in a row.
	await _click(level, 7)
	h.expect(_removed(_card(level, 4)) and _removed(_card(level, 7)), "clicked pair matches")
	h.expect(int(level.get(&"score")) == 200, "the first match after a miss scores 100")
	await _click(level, 5)
	await _click(level, 11)
	h.expect(_removed(_card(level, 11)), "a second pair in a row matches")
	h.expect(int(level.get(&"streak")) == 2, "two matches in a row make a streak")
	h.expect(
		int(level.get(&"score")) == 350,
		"the streak adds a bonus (got %d)" % int(level.get(&"score"))
	)
	var hud: Node = level.get_node(^"%Hud")
	var streak_label: Label = hud.get(&"streak_label")
	h.expect(streak_label.visible, "the HUD shows the streak")
	h.expect(int(level.get(&"moves")) == 4, "four moves so far")


func _check_save_round_trip(level: Control) -> void:
	var saved: Dictionary = game.call(&"capture_state")
	h.expect(
		saved.has("board") and saved.has("card_0") and saved.has("card_15"),
		"capture_state includes the board and every card"
	)
	var eight: Button = _card(level, 8)
	await _click(level, 8)
	h.expect(_up(eight), "card 8 is up before loading")
	game.call(&"apply_state", saved)
	await h.physics_frames(2)
	h.expect(not _up(eight), "apply_state turns card 8 back down")
	h.expect(_removed(_card(level, 0)), "apply_state keeps cleared pairs")
	h.expect(
		int(level.get(&"moves")) == 4 and int(level.get(&"score")) == 350,
		"apply_state restores moves and score"
	)
	h.expect(level.call(&"open_cards").is_empty(), "no card is open after loading")

	# A save taken while a miss is showing turns the cards back after loading.
	await _click(level, 8)
	await _click(level, 9)
	h.expect(bool(level.call(&"is_resolving")), "cards 8 and 9 miss")
	var mid: Dictionary = game.call(&"capture_state")
	await _settle(level)
	h.expect(not _up(eight), "the miss resolves")
	game.call(&"apply_state", mid)
	await h.physics_frames(2)
	h.expect(_up(eight) and _up(_card(level, 9)), "loading shows the missed pair again")
	h.expect(bool(level.call(&"is_resolving")), "and waits before turning it back")
	await _settle(level)
	h.expect(not _up(eight) and not _up(_card(level, 9)), "then turns it back")
	h.expect(int(level.get(&"moves")) == 5, "the loaded miss counts once")

	# Loading a different deal puts its symbols back on the cards.
	var shuffled: Dictionary = level.call(&"save_state")
	var other: Array = Array(deck.call(&"deal", 7, 8))
	shuffled["layout"] = other
	level.call(&"load_state", shuffled)
	await h.physics_frames(2)
	h.expect(Array(level.call(&"layout")) == other, "a saved layout is applied to the cards")
	game.call(&"apply_state", saved)
	await h.physics_frames(2)
	h.expect(Array(level.call(&"layout")) == KNOWN_LAYOUT, "and the original comes back")


func _check_reduced_motion(level: Control) -> void:
	settings.call(&"set_value", "access", "reduced_motion", true)
	var card: Button = _card(level, 10)
	await _click(level, 10)
	h.expect(
		_up(card) and not bool(card.call(&"is_animating")),
		"with reduced motion a card flips without animating"
	)
	h.expect(StringName(card.call(&"look")) == &"face", "and shows its face at once")
	h.expect_near(card.scale.x, 1.0, 0.001, "and keeps its size")
	settings.call(&"set_value", "access", "reduced_motion", false)
	await _click(level, 6)
	await _settle(level)


func _check_pause(level: Control) -> void:
	var before: int = int(level.get(&"moves"))
	await h.tap(&"pause")
	h.expect(paused, "pause pauses the tree")
	h.expect(bool(menus.call(&"is_open")), "pause opens the pause menu")
	h.expect(h.focused_text() == "Resume", "Resume has focus (got '%s')" % h.focused_text())
	await _click(level, 13)
	h.expect(not _up(_card(level, 13)), "cards cannot be flipped while paused")
	var elapsed: float = float(level.get(&"elapsed"))
	await h.physics_frames(20)
	h.expect_near(float(level.get(&"elapsed")), elapsed, 0.0001, "the clock stops while paused")
	await h.tap(&"ui_accept")
	h.expect(not paused, "Resume with ui_accept unpauses")
	await h.process_frames(3)
	h.expect(_focused_index() >= 0, "focus returns to a card after the pause menu")
	h.expect(int(level.get(&"moves")) == before, "pausing does not change the moves")


func _check_restart(level: Control) -> Control:
	await h.tap(&"pause")
	h.expect(await h.focus_button("Restart"), "the pause menu has Restart")
	var started: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	await h.wait_watched(started, 240)
	await h.process_frames(3)
	h.expect(not paused, "the restarted game is not paused")
	var fresh: Control = game.call(&"current_level") as Control
	h.expect(fresh != null and fresh != level, "Restart deals a fresh table")
	if fresh == null:
		return null
	h.expect(Array(fresh.call(&"layout")) == KNOWN_LAYOUT, "Restart keeps the seeded deal")
	h.expect(
		int(fresh.get(&"moves")) == 0 and int(fresh.get(&"score")) == 0,
		"Restart clears moves and score"
	)
	h.expect(int(fresh.call(&"pairs_left")) == 8, "every pair is back")
	h.expect(_focused_index() == 0, "the first card has focus again")
	return fresh


func _check_play_through(level: Control) -> void:
	var completed: Dictionary = h.watch(events.level_completed)
	var done: Array[int] = []
	for i: int in 16:
		if i in done:
			continue
		var partner: int = _partner(level, i)
		await _click(level, i)
		await _click(level, partner)
		done.append_array([i, partner])
	var args: Array = await h.wait_watched(completed, 30)
	h.expect(not args.is_empty(), "clearing the board completes the level")
	if args.is_empty():
		return
	var result: Dictionary = args[0]
	h.expect(int(result.get("moves", 0)) == 8, "a perfect game takes eight moves")
	h.expect(int(result.get("stars", 0)) == 3, "a perfect game earns three stars")
	h.expect(
		int(result.get("score", 0)) == 2200,
		"eight in a row scores 2200 (got %d)" % int(result.get("score", 0))
	)
	h.expect(float(result.get("time", 0.0)) > 0.0, "the result has a time")
	h.expect(int(result.get("best_streak", 0)) == 8, "the result has the best streak")
	var panel: Control = level.get_node(^"%Result")
	h.expect(panel.visible, "the win screen shows")
	await h.process_frames(2)
	h.expect(h.focused_text() == "Play again", "Play again has focus (got '%s')" % h.focused_text())
	h.expect(int(panel.get(&"stars").get(&"stars")) == 3, "the win screen shows three stars")
	h.expect_min_text(panel, "the win screen")
	h.expect(_card(level, 0).focus_mode == Control.FOCUS_NONE, "cards are locked after a win")
	var started: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	var again: Array = await h.wait_watched(started, 240)
	h.expect(not again.is_empty(), "Play again starts a new game")


func _check_lose() -> void:
	var level: Control = await _start({"seed": SEED, "move_limit": 2})
	if level == null:
		return
	var hud: Node = level.get_node(^"%Hud")
	h.expect(String(hud.get(&"moves_label").text) == "Moves: 0 of 2", "the HUD shows the limit")
	var failed: Dictionary = h.watch(events.level_failed)
	await _click(level, 0)
	await _click(level, 1)
	await _settle(level)
	h.expect(h.fire_count(failed) == 0, "one miss is not a loss yet")
	await _click(level, 0)
	await _click(level, 2)
	var args: Array = await h.wait_watched(failed, MISMATCH_FRAMES + 30)
	h.expect(
		not args.is_empty() and String(args[0]) == "out_of_moves",
		"running out of moves fails the level"
	)
	var panel: Control = level.get_node(^"%Result")
	h.expect(
		panel.visible and String(panel.get(&"title").text) == "Out of moves",
		"the lose screen shows"
	)
	await h.process_frames(2)
	h.expect(h.focused_text() == "Try again", "Try again has focus (got '%s')" % h.focused_text())
	h.expect(not bool(level.get(&"finished")) and bool(level.get(&"failed")), "the game is lost")
	await _click(level, 5)
	h.expect(not _up(_card(level, 5)), "cards cannot be flipped after losing")
	await h.tap(&"ui_down")
	h.expect(h.focused_text() == "Main menu", "the lose screen offers the main menu")
	var opened: Dictionary = h.watch(events.menu_opened)
	await h.tap(&"ui_accept")
	await h.wait_watched(opened, 240)
	h.expect(StringName(menus.call(&"current_menu")) == &"main", "Main menu returns to the menu")


func _check_turns() -> void:
	var level: Control = await _start({"seed": SEED})
	if level == null:
		return
	# Two seats with peer ids 1 and 2, checked like an online game: only the
	# peer whose turn it is may flip.
	level.call(&"use_seats", PackedInt32Array([1, 2]), false)
	h.expect(bool(level.call(&"is_multiplayer")), "two seats make a two-player game")
	h.expect(int(level.call(&"active_move_limit")) == 0, "two-player games have no move limit")
	h.expect(int(level.get(&"turn")) == 1, "player 1 starts")
	await _as_peer(2, level, 0)
	h.expect(not _up(_card(level, 0)), "player 2 cannot flip on player 1's turn")
	await _as_peer(1, level, 0)
	await _as_peer(1, level, 1)
	h.expect(_up(_card(level, 1)), "player 1 flips two cards")
	await _settle(level)
	h.expect(int(level.get(&"turn")) == 2, "a miss passes the turn to player 2")
	await _as_peer(1, level, 0)
	h.expect(not _up(_card(level, 0)), "player 1 now has to wait")
	await _as_peer(2, level, 0)
	await _as_peer(2, level, 3)
	h.expect(_removed(_card(level, 3)), "player 2 matches a pair")
	h.expect(int(level.get(&"turn")) == 2, "a match keeps the turn")
	h.expect(int(level.call(&"pairs_for", 2)) == 1, "player 2 has one pair")
	var hud: Node = level.get_node(^"%Hud")
	var seats: Control = hud.get(&"seats")
	h.expect(seats.visible and seats.get_child_count() == 2, "the HUD shows both players")
	h.expect(String(hud.get(&"turn_label").text) == "Player 2's turn", "the HUD names the turn")

	var completed: Dictionary = h.watch(events.level_completed)
	var done: Array[int] = [0, 3]
	for i: int in 16:
		if i in done:
			continue
		var partner: int = _partner(level, i)
		await _as_peer(2, level, i)
		await _as_peer(2, level, partner)
		done.append_array([i, partner])
	var args: Array = await h.wait_watched(completed, 30)
	h.expect(not args.is_empty(), "the two-player game completes")
	if not args.is_empty():
		var result: Dictionary = args[0]
		var winners: Array = result.get("winners", [])
		h.expect(winners.size() == 1 and int(winners[0]) == 2, "player 2 wins with the most pairs")
		var by_player: Dictionary = result.get("pairs_by_player", {})
		h.expect(
			int(by_player.get(2, 0)) == 8 and int(by_player.get(1, -1)) == 0,
			"the result counts pairs per player"
		)
		var panel: Control = level.get_node(^"%Result")
		h.expect(String(panel.get(&"title").text) == "Player 2 wins!", "the win screen names them")


func _check_two_players_from_menu() -> void:
	var opened: Dictionary = h.watch(events.menu_opened)
	game.call(&"quit_to_menu")
	await h.wait_watched(opened, 240)
	await h.process_frames(2)
	var ids: Array = []
	for entry: Dictionary in menus.call(&"entries", &"main"):
		ids.append(entry["id"])
	h.expect(&"two_players" in ids, "the main menu offers a two-player game")
	h.expect(await h.focus_button("Two players"), "Two players can be reached by keyboard")
	var started: Dictionary = h.watch(events.level_started)
	await h.tap(&"ui_accept")
	await h.wait_watched(started, 240)
	await h.process_frames(3)
	var level: Control = game.call(&"current_level") as Control
	h.expect(level != null, "Two players starts a game")
	if level == null:
		return
	h.expect(bool(level.call(&"is_local_seats")), "the seats share one screen")
	h.expect(_focused_index() >= 0, "a card has focus")
	var first: int = 0
	var other: int = 1 if _symbol(_card(level, 1)) != _symbol(_card(level, 0)) else 2
	await _click(level, first)
	await _click(level, other)
	await _settle(level)
	h.expect(int(level.get(&"turn")) == 2, "a miss passes the turn on the same screen")
	var hud: Node = level.get_node(^"%Hud")
	h.expect(String(hud.get(&"turn_label").text) == "Player 2's turn", "the HUD shows it")
	h.expect_min_text(level, "the two-player table")
	await _click(level, first)
	h.expect(_up(_card(level, first)), "player 2 plays with the same mouse")
