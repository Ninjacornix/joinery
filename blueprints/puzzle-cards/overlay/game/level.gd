# gdlint: disable=max-public-methods
extends Control
## Memory cards: a seeded deal of pairs laid face down in a grid. Flip two;
## a match clears them and scores, a miss turns them back after a short pause
## (input is ignored meanwhile). Clear the board to win, with 1 to 3 stars by
## moves; run out of moves with pairs left to lose.
##
## Two or more players take turns: a match keeps the turn, a miss passes it.
## Seats are the online peers (`Game.peer_ids()`), or local seats for a
## same-screen game (`Game.start_game({"seats": 2})`).
##
## Rules run on the authority. Cards ask through `Game.request_action`, and
## every change is announced with `GameEvents.world_changed`, so an online
## session can mirror the board with `save_state`/`load_state`.
##
## Options read from `Game.options` (kept across Restart):
##   seed        int, the deal (same seed, same layout); random when absent
##   seats       int, local players taking turns on one screen (default 1)
##   move_limit  int, single-player move limit; 0 turns it off

signal changed

const Deck := preload("res://game/deck.gd")
const Card := preload("res://game/cards/card.gd")
const CardScene := preload("res://game/cards/card.tscn")
const CardStyle := preload("res://game/ui/card_style.gd")
const MISMATCH_SECONDS := 0.8
const OUT_OF_MOVES := "out_of_moves"

@export var save_id: String = "board"
@export_range(1, 12) var columns: int = 4
@export_range(1, 12) var pair_count: int = 8
## Single-player move limit; 0 = none. Two-player games have no limit.
@export_range(0, 999) var move_limit: int = 30
@export var points_per_pair: int = 100
## Extra points for each match in a row after the first.
@export var streak_bonus: int = 50

var deal_seed: int = 0
var moves: int = 0
var score: int = 0
var streak: int = 0
var best_streak: int = 0
var elapsed: float = 0.0
var finished: bool = false
var failed: bool = false
## The seat (peer id, or local seat number) whose turn it is.
var turn: int = 1
var cards: Array[Card] = []

var _pairs_won: Dictionary = {}
var _seats: PackedInt32Array = PackedInt32Array()
var _local_seats: bool = true
var _open: Array[Card] = []
var _resolving: bool = false
var _announced: bool = false
var _resync_queued: bool = false
var _last_focus: Card = null

@onready var grid: GridContainer = %Grid
@onready var hud: Control = %Hud
@onready var result_panel: Control = %Result
@onready var mismatch_timer: Timer = $MismatchTimer


func _ready() -> void:
	add_to_group(&"saveable")
	theme = UiStyle.build_theme()
	(%Felt as PanelContainer).add_theme_stylebox_override(&"panel", CardStyle.table())
	(%Table as ColorRect).color = CardStyle.TABLE
	var margin: int = CardStyle.px(24)
	for side: String in ["left", "right", "top", "bottom"]:
		(%Layout as MarginContainer).add_theme_constant_override("margin_" + side, margin)
	grid.columns = columns
	grid.add_theme_constant_override(&"h_separation", CardStyle.px(22))
	grid.add_theme_constant_override(&"v_separation", CardStyle.px(22))
	mismatch_timer.one_shot = true
	mismatch_timer.timeout.connect(_on_mismatch_timeout)

	var options: Dictionary = Game.options
	move_limit = maxi(0, int(options.get("move_limit", move_limit)))
	var local_seats: int = int(options.get("seats", 1))
	if local_seats > 1:
		var ids: PackedInt32Array = PackedInt32Array()
		for seat: int in range(1, local_seats + 1):
			ids.append(seat)
		_seats = ids
		_local_seats = true
	deal_seed = int(options["seed"]) if options.has("seed") else Deck.random_seed()
	_build_cards(Deck.deal(deal_seed, pair_count))
	_reset_turns()

	hud.call(&"setup", self)
	result_panel.call(&"setup", self)
	GameEvents.paused_changed.connect(_on_paused_changed)
	multiplayer.peer_connected.connect(_on_peers_changed.unbind(1))
	multiplayer.peer_disconnected.connect(_on_peers_changed.unbind(1))
	Game.level_ready(self)
	GameEvents.score_changed.emit(score)
	if _is_authority():
		GameEvents.world_changed.emit(self)
	changed.emit()
	focus_board.call_deferred()


func _process(delta: float) -> void:
	if not finished and not failed:
		elapsed += delta


func _unhandled_input(event: InputEvent) -> void:
	if InputMap.has_action(&"restart") and event.is_action_pressed(&"restart"):
		get_viewport().set_input_as_handled()
		Game.restart_level()
		return
	if get_viewport().gui_get_focus_owner() != null:
		return
	for action: StringName in [&"ui_left", &"ui_right", &"ui_up", &"ui_down", &"ui_accept"]:
		if event.is_action_pressed(action):
			get_viewport().set_input_as_handled()
			focus_board()
			return


# --- reading the board -------------------------------------------------------


## The symbol index on every slot, in grid order.
func layout() -> PackedInt32Array:
	var symbols: PackedInt32Array = PackedInt32Array()
	for card: Card in cards:
		symbols.append(card.symbol)
	return symbols


func card(index: int) -> Card:
	if index < 0 or index >= cards.size():
		return null
	return cards[index]


func pairs_left() -> int:
	var left: int = 0
	for item: Card in cards:
		if not item.matched:
			left += 1
	return int(left / 2.0)


## Stars for the moves used so far (what the player is heading for).
func stars() -> int:
	return Deck.stars_for(moves, pair_count)


## The move limit that applies now: 0 in a game with more than one player.
func active_move_limit() -> int:
	return 0 if is_multiplayer() else move_limit


## True while a missed pair is waiting to turn back (input is ignored).
func is_resolving() -> bool:
	return _resolving


## Cards turned up in the current move (0, 1 or 2).
func open_cards() -> Array[Card]:
	return _open.duplicate()


# --- seats and turns ---------------------------------------------------------


## The players taking turns: local seats when set, else the online peers.
func seat_ids() -> PackedInt32Array:
	if not _seats.is_empty():
		return _seats
	return Game.peer_ids()


func is_multiplayer() -> bool:
	return seat_ids().size() > 1


## True when the seats share one screen (anyone at it plays the current seat).
func is_local_seats() -> bool:
	return not _seats.is_empty() and _local_seats


## Sets who takes turns. `local` = one shared screen; otherwise each seat is a
## peer id and only that peer's actions count on its turn (what an online
## game does with `Game.peer_ids()`). An empty list goes back to the peers.
func use_seats(ids: PackedInt32Array, local: bool) -> void:
	_seats = ids.duplicate()
	_local_seats = local
	_reset_turns()
	changed.emit()


## 1-based player number of a seat id (0 if it is not seated).
func seat_number(id: int) -> int:
	return seat_ids().find(id) + 1


func turn_number() -> int:
	return maxi(1, seat_number(turn))


func pairs_for(id: int) -> int:
	return int(_pairs_won.get(str(id), 0))


## True when this game instance may play now.
func is_my_turn() -> bool:
	if not is_multiplayer() or is_local_seats():
		return true
	return turn == multiplayer.get_unique_id()


## Asked by a card before it sends a flip: in an online game it is someone
## else's turn, say so here instead of sending a request the authority would
## refuse anyway. The authority still checks every flip (`try_flip`).
func may_flip_here() -> bool:
	if is_my_turn():
		return true
	Menus.toast(tr("Wait for your turn: it is Player %d's turn.") % turn_number(), 1.5)
	return false


# --- rules (authority) -------------------------------------------------------


## Called by a card's `flip` action. Ignores the request when the game is
## over, while a missed pair is showing, for a card that is already up, or
## when it is not the acting player's turn.
func try_flip(target: Card) -> void:
	if not _is_authority() or target == null or not cards.has(target):
		return
	if finished or failed or _resolving:
		return
	if target.matched or target.face_up:
		return
	if not _may_act(Game.actor_id()):
		return
	_open.append(target)
	if _open.size() < 2:
		target.set_face_up(true)
		GameEvents.world_changed.emit(target)
	else:
		_resolve_move()
	changed.emit()
	GameEvents.world_changed.emit(self)


func _resolve_move() -> void:
	moves += 1
	var first: Card = _open[0]
	var second: Card = _open[1]
	if first.symbol == second.symbol:
		_open.clear()
		second.set_matched(true)
		first.set_matched(true)
		streak += 1
		best_streak = maxi(best_streak, streak)
		score += Deck.points_for(streak, points_per_pair, streak_bonus)
		_pairs_won[str(turn)] = pairs_for(turn) + 1
		GameEvents.world_changed.emit(first)
		GameEvents.world_changed.emit(second)
		GameEvents.score_changed.emit(score)
		_refocus.call_deferred(second.index)
		if pairs_left() == 0:
			finished = true
			_announce()
		elif _out_of_moves():
			failed = true
			_announce()
	else:
		streak = 0
		second.missed = true
		second.set_face_up(true)
		first.set_missed(true)
		GameEvents.world_changed.emit(first)
		GameEvents.world_changed.emit(second)
		_resolving = true
		mismatch_timer.start(MISMATCH_SECONDS)


func _on_mismatch_timeout() -> void:
	if not _resolving:
		return
	_resolving = false
	for item: Card in _open:
		item.set_face_up(false)
		GameEvents.world_changed.emit(item)
	_open.clear()
	_advance_turn()
	if _out_of_moves():
		failed = true
		_announce()
	changed.emit()
	GameEvents.world_changed.emit(self)


func _out_of_moves() -> bool:
	var limit: int = active_move_limit()
	return limit > 0 and moves >= limit and pairs_left() > 0


func _may_act(actor: int) -> bool:
	var ids: PackedInt32Array = seat_ids()
	if ids.size() <= 1:
		return true
	if not ids.has(turn):
		turn = ids[0]
	if is_local_seats():
		return true
	return actor == turn


func _advance_turn() -> void:
	var ids: PackedInt32Array = seat_ids()
	if ids.size() <= 1:
		return
	var at: int = ids.find(turn)
	turn = ids[(at + 1) % ids.size()] if at >= 0 else ids[0]


func _reset_turns() -> void:
	var ids: PackedInt32Array = seat_ids()
	turn = ids[0] if not ids.is_empty() else 1
	_pairs_won = {}
	for id: int in ids:
		_pairs_won[str(id)] = 0


## Seats with the most pairs (more than one = a draw).
func winners() -> Array[int]:
	var best: int = -1
	var found: Array[int] = []
	for id: int in seat_ids():
		var pairs: int = pairs_for(id)
		if pairs > best:
			best = pairs
			found = [id]
		elif pairs == best:
			found.append(id)
	return found


## The `level_completed` result: time, score, moves, stars and, with more
## than one player, pairs per player and the winners.
func result() -> Dictionary:
	var info: Dictionary = {
		"time": elapsed,
		"score": score,
		"moves": moves,
		"stars": stars(),
		"pairs": pair_count,
		"best_streak": best_streak,
		"seed": deal_seed,
	}
	if is_multiplayer():
		var by_player: Dictionary = {}
		for id: int in seat_ids():
			by_player[id] = pairs_for(id)
		info["pairs_by_player"] = by_player
		info["winners"] = winners()
	return info


func _announce() -> void:
	if _announced:
		return
	_announced = true
	_lock_cards(true)
	if finished:
		var info: Dictionary = result()
		result_panel.call(&"show_win", info)
		GameEvents.level_completed.emit(info)
	elif failed:
		result_panel.call(&"show_lose")
		GameEvents.level_failed.emit(OUT_OF_MOVES)


func _lock_cards(on: bool) -> void:
	for item: Card in cards:
		item.locked = on


func _is_authority() -> bool:
	var session: GameSession = Game.session()
	return session == null or session.is_authority()


# --- cards and focus ---------------------------------------------------------


func _build_cards(symbols: PackedInt32Array) -> void:
	for child: Node in grid.get_children():
		grid.remove_child(child)
		child.queue_free()
	cards.clear()
	for i: int in symbols.size():
		var item: Card = CardScene.instantiate() as Card
		item.name = "Card%d" % i
		item.save_id = "card_%d" % i
		item.index = i
		item.board = self
		grid.add_child(item)
		item.deal(symbols[i])
		cards.append(item)


## Puts keyboard focus on the board: the last focused card if it can still
## be played, else the first card that can. Skipped while a menu is open.
func focus_board() -> void:
	if not is_inside_tree() or Menus.is_open():
		return
	if finished or failed:
		result_panel.call(&"focus_first")
		return
	var target: Card = _last_focus
	if target == null or not is_instance_valid(target) or target.focus_mode == FOCUS_NONE:
		target = _nearest_open(0)
	if target != null:
		target.grab_focus()


## Cards report focus here so the board can return to it after a menu.
func remember_focus(item: Card) -> void:
	_last_focus = item


func _refocus(from_index: int) -> void:
	var owner_control: Control = get_viewport().gui_get_focus_owner()
	if owner_control != null and not (owner_control is Card):
		return
	if owner_control is Card and (owner_control as Card).focus_mode != FOCUS_NONE:
		return
	var target: Card = _nearest_open(from_index)
	if target != null:
		target.grab_focus()


func _nearest_open(from_index: int) -> Card:
	var best: Card = null
	var best_distance: int = 1 << 30
	for item: Card in cards:
		if item.focus_mode == FOCUS_NONE:
			continue
		var distance: int = absi(item.index - from_index)
		if distance < best_distance:
			best = item
			best_distance = distance
	return best


func _on_paused_changed(is_paused: bool) -> void:
	if not is_paused:
		focus_board.call_deferred()


## A peer joined or left an online game: keep the turn on someone seated.
func _on_peers_changed() -> void:
	var ids: PackedInt32Array = seat_ids()
	if not ids.is_empty() and not ids.has(turn):
		turn = ids[0]
		if _is_authority():
			GameEvents.world_changed.emit(self)
	changed.emit()


# --- saving and mirroring ----------------------------------------------------


func save_state() -> Dictionary:
	return {
		"seed": deal_seed,
		"layout": Array(layout()),
		"moves": moves,
		"score": score,
		"streak": streak,
		"best_streak": best_streak,
		"elapsed": elapsed,
		"finished": finished,
		"failed": failed,
		"turn": turn,
		"pairs_won": _pairs_won.duplicate(),
		"seats": Array(_seats),
		"local_seats": _local_seats,
		"move_limit": move_limit,
	}


func load_state(state: Dictionary) -> void:
	deal_seed = int(state.get("seed", deal_seed))
	var saved_layout: Array = state.get("layout", [])
	if saved_layout.size() == cards.size():
		for i: int in cards.size():
			if cards[i].symbol != int(saved_layout[i]):
				cards[i].symbol = int(saved_layout[i])
				cards[i].show_state(false)
	moves = int(state.get("moves", moves))
	score = int(state.get("score", score))
	streak = int(state.get("streak", streak))
	best_streak = int(state.get("best_streak", best_streak))
	elapsed = float(state.get("elapsed", elapsed))
	move_limit = int(state.get("move_limit", move_limit))
	if state.has("seats"):
		var ids: PackedInt32Array = PackedInt32Array()
		for id: Variant in state["seats"]:
			ids.append(int(id))
		_seats = ids
		_local_seats = bool(state.get("local_seats", true))
	turn = int(state.get("turn", turn))
	var won: Dictionary = {}
	var saved_won: Dictionary = state.get("pairs_won", {})
	for key: Variant in saved_won:
		won[str(int(str(key)))] = int(saved_won[key])
	_pairs_won = won
	finished = bool(state.get("finished", finished))
	failed = bool(state.get("failed", failed))
	if _announced and not finished and not failed:
		_announced = false
		result_panel.call(&"hide_result")
	resync()
	GameEvents.score_changed.emit(score)
	changed.emit()


## Rebuilds the open move from the cards after they were loaded or mirrored
## (deferred, so a whole `apply_state` lands first).
func resync() -> void:
	if _resync_queued:
		return
	_resync_queued = true
	_do_resync.call_deferred()


func _do_resync() -> void:
	_resync_queued = false
	if not is_inside_tree():
		return
	mismatch_timer.stop()
	_resolving = false
	_open.clear()
	for item: Card in cards:
		if item.face_up and not item.matched:
			_open.append(item)
	if _open.size() == 2 and _open[0].symbol != _open[1].symbol:
		for item: Card in _open:
			item.set_missed(true)
		_resolving = true
		if _is_authority():
			mismatch_timer.start(MISMATCH_SECONDS)
	elif _open.size() >= 2 and _is_authority():
		for item: Card in _open:
			item.set_face_up(false, false)
			GameEvents.world_changed.emit(item)
		_open.clear()
	_lock_cards(finished or failed)
	if finished or failed:
		_announce()
	changed.emit()
