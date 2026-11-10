extends "res://features/save-load/save_adapter.gd"
## Top-down slots show the wave and the score. Score changes (gems, defeated
## enemies) are autosave moments, throttled, and never once the fight is over.

var _request: Callable = Callable()


func summary(nodes: Dictionary) -> String:
	var level: Variant = nodes.get("level")
	if not (level is Dictionary):
		return ""
	var state: Dictionary = level
	return tr("Wave %d, score %d") % [int(state.get("wave", 0)), int(state.get("score", 0))]


func watch(request: Callable) -> void:
	_request = request
	GameEvents.score_changed.connect(_on_score_changed)


func _on_score_changed(_value: int) -> void:
	var level: Node = Game.current_level()
	if level == null or bool(level.get(&"completed")) or bool(level.get(&"failed")):
		return
	_request.call()
