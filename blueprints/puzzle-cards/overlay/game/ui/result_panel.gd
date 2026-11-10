extends Control
## The end of a game, over the table: stars, moves, time and score when the
## board is cleared, or what was left when the moves ran out. Two buttons,
## reachable by mouse, keyboard or pad: play again, or back to the main menu.

const StarRow := preload("res://game/ui/star_row.gd")
const CardStyle := preload("res://game/ui/card_style.gd")

var title: Label
var stars: StarRow
var details: VBoxContainer
var again_button: Button
var menu_button: Button

var _board: Node = null


func _ready() -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	var shade: ColorRect = ColorRect.new()
	shade.name = "Shade"
	shade.color = UiStyle.DIM
	shade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(shade)
	var center: CenterContainer = CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(center)
	var panel: PanelContainer = PanelContainer.new()
	panel.name = "Panel"
	center.add_child(panel)
	var column: VBoxContainer = VBoxContainer.new()
	column.name = "Column"
	column.alignment = BoxContainer.ALIGNMENT_CENTER
	panel.add_child(column)
	title = UiStyle.title("")
	title.name = "Title"
	title.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	column.add_child(title)
	stars = StarRow.new()
	stars.name = "Stars"
	stars.star_size = float(CardStyle.px(72))
	stars.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	column.add_child(stars)
	details = VBoxContainer.new()
	details.name = "Details"
	column.add_child(details)
	again_button = UiStyle.button("Play again")
	again_button.name = "Again"
	again_button.pressed.connect(_on_again)
	column.add_child(again_button)
	menu_button = UiStyle.button("Main menu")
	menu_button.name = "MainMenu"
	menu_button.pressed.connect(_on_main_menu)
	column.add_child(menu_button)


func setup(board: Node) -> void:
	_board = board


## Shows the win screen for a `level_completed` result.
func show_win(result: Dictionary) -> void:
	_clear_details()
	var winners: Array = result.get("winners", [])
	if winners.size() == 1:
		title.text = tr("Player %d wins!") % int(_board.call(&"seat_number", int(winners[0])))
	elif winners.size() > 1:
		title.text = tr("It's a draw!")
	else:
		title.text = tr("All pairs found!")
	stars.visible = true
	stars.stars = int(result.get("stars", 0))
	_line(tr("Moves: %d") % int(result.get("moves", 0)))
	_line(tr("Time: %s") % _clock(float(result.get("time", 0.0))))
	_line(tr("Score: %d") % int(result.get("score", 0)))
	var best: int = int(result.get("best_streak", 0))
	if best >= 2:
		_line(tr("Best streak: %d in a row") % best)
	var ids: PackedInt32Array = _board.call(&"seat_ids")
	if ids.size() > 1:
		for id: int in ids:
			var pairs: int = int(_board.call(&"pairs_for", id))
			var number: int = int(_board.call(&"seat_number", id))
			_line(tr_n("Player %d: %d pair", "Player %d: %d pairs", pairs) % [number, pairs])
	again_button.text = "Play again"
	_open()
	stars.pop()


## Shows the lose screen: the moves ran out with pairs left.
func show_lose() -> void:
	_clear_details()
	title.text = tr("Out of moves")
	stars.visible = false
	var total: int = int(_board.get(&"pair_count"))
	var found: int = total - int(_board.call(&"pairs_left"))
	_line(tr("You found %d of %d pairs.") % [found, total])
	_line(tr("Moves: %d") % int(_board.get(&"moves")))
	_line(tr("Score: %d") % int(_board.get(&"score")))
	again_button.text = "Try again"
	_open()


func hide_result() -> void:
	visible = false


## Gives keyboard focus to the first button.
func focus_first() -> void:
	if visible:
		again_button.grab_focus()


func _open() -> void:
	visible = true
	focus_first.call_deferred()


func _line(text: String) -> void:
	var label: Label = Label.new()
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.text = text
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	details.add_child(label)


func _clear_details() -> void:
	for child: Node in details.get_children():
		details.remove_child(child)
		child.queue_free()


func _clock(seconds: float) -> String:
	var whole: int = int(seconds)
	return "%d:%02d" % [floori(whole / 60.0), whole % 60]


func _on_again() -> void:
	Game.restart_level()


func _on_main_menu() -> void:
	Game.quit_to_menu()
