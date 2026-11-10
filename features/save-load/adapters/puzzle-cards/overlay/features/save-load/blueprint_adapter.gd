extends "res://features/save-load/save_adapter.gd"
## Card slots show the pairs found and the moves used. Every match is an
## autosave moment (throttled), until the board is won or lost.

var _request: Callable = Callable()


func summary(nodes: Dictionary) -> String:
	var cards: Array[int] = count_flag(nodes, "card_", "matched")
	if cards[1] == 0:
		return ""
	var moves: int = 0
	var board: Variant = nodes.get("board")
	if board is Dictionary:
		moves = int((board as Dictionary).get("moves", 0))
	return tr("Pairs: %d of %d, moves: %d") % [int(cards[0] / 2.0), int(cards[1] / 2.0), moves]


func watch(request: Callable) -> void:
	_request = request
	GameEvents.score_changed.connect(_on_score_changed)


func _on_score_changed(_value: int) -> void:
	var level: Node = Game.current_level()
	if level == null or bool(level.get(&"finished")) or bool(level.get(&"failed")):
		return
	_request.call()
