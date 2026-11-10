extends PanelContainer
## The strip along the top of the table: moves (and the limit), pairs found,
## score, the current matching streak, the stars this game is heading for,
## and in a two-player game a pill per player with the one whose turn it is
## lit up. Built in code; reads the board whenever it changes.

const CardStyle := preload("res://game/ui/card_style.gd")
const StarRow := preload("res://game/ui/star_row.gd")
const Deck := preload("res://game/deck.gd")
const TEXT_DARK := Color("#1b1d24")

var moves_label: Label
var pairs_label: Label
var score_label: Label
var streak_label: Label
var turn_label: Label
var stars: StarRow
var seats: HBoxContainer

var _board: Node = null
var _row: HBoxContainer


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_theme_stylebox_override(&"panel", CardStyle.bar())
	_row = HBoxContainer.new()
	_row.name = "Row"
	_row.add_theme_constant_override(&"separation", CardStyle.px(36))
	add_child(_row)
	moves_label = _label("Moves")
	pairs_label = _label("Pairs")
	score_label = _label("Score")
	streak_label = _label("Streak")
	streak_label.add_theme_color_override(&"font_color", CardStyle.FOCUS)
	var spacer: Control = Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_row.add_child(spacer)
	seats = HBoxContainer.new()
	seats.name = "Seats"
	seats.add_theme_constant_override(&"separation", CardStyle.px(12))
	_row.add_child(seats)
	turn_label = _label("Turn")
	stars = StarRow.new()
	stars.name = "Stars"
	stars.star_size = float(CardStyle.px(34))
	stars.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_row.add_child(stars)


## Follows `board` (the level) from now on.
func setup(board: Node) -> void:
	_board = board
	board.connect(&"changed", refresh)
	refresh()


func refresh() -> void:
	if _board == null:
		return
	var moves: int = int(_board.get(&"moves"))
	var limit: int = int(_board.call(&"active_move_limit"))
	if limit > 0:
		moves_label.text = tr("Moves: %d of %d") % [moves, limit]
	else:
		moves_label.text = tr("Moves: %d") % moves
	var total: int = int(_board.get(&"pair_count"))
	var found: int = total - int(_board.call(&"pairs_left"))
	pairs_label.text = tr("Pairs: %d of %d") % [found, total]
	score_label.text = tr("Score: %d") % int(_board.get(&"score"))
	var streak: int = int(_board.get(&"streak"))
	streak_label.visible = streak >= 2
	streak_label.text = tr("%d in a row!") % streak
	stars.stars = Deck.stars_for(moves, total)
	_refresh_seats()


func _refresh_seats() -> void:
	var ids: PackedInt32Array = _board.call(&"seat_ids")
	var multi: bool = ids.size() > 1
	seats.visible = multi
	turn_label.visible = multi
	for child: Node in seats.get_children():
		seats.remove_child(child)
		child.queue_free()
	if not multi:
		return
	var turn: int = int(_board.get(&"turn"))
	for i: int in ids.size():
		var pill: Label = Label.new()
		pill.name = "Seat%d" % (i + 1)
		pill.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
		var pairs: int = int(_board.call(&"pairs_for", ids[i]))
		pill.text = tr_n("Player %d: %d pair", "Player %d: %d pairs", pairs) % [i + 1, pairs]
		var active: bool = ids[i] == turn
		pill.add_theme_stylebox_override(&"normal", CardStyle.seat(active))
		pill.add_theme_color_override(
			&"font_color", TEXT_DARK if active else Color(1.0, 1.0, 1.0, 0.9)
		)
		pill.add_theme_font_size_override(&"font_size", CardStyle.px(26))
		seats.add_child(pill)
	if bool(_board.call(&"is_my_turn")) and not bool(_board.call(&"is_local_seats")):
		turn_label.text = tr("Your turn")
	else:
		turn_label.text = tr("Player %d's turn") % int(_board.call(&"turn_number"))


func _label(node_name: String) -> Label:
	var label: Label = Label.new()
	label.name = node_name
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	label.add_theme_font_size_override(&"font_size", CardStyle.px(28))
	_row.add_child(label)
	return label
