extends "res://features/save-load/save_adapter.gd"
## First-person slots show how many switches are on. Every switch the player
## turns is an autosave moment (throttled).

var _request: Callable = Callable()


func summary(nodes: Dictionary) -> String:
	var switches: Array[int] = count_flag(nodes, "switch_", "on")
	if switches[1] == 0:
		return ""
	return tr("Switches on: %d of %d") % [switches[0], switches[1]]


func watch(request: Callable) -> void:
	_request = request
	GameEvents.score_changed.connect(_on_score_changed)


func _on_score_changed(_value: int) -> void:
	var level: Node = Game.current_level()
	if level != null and not bool(level.get(&"completed")):
		_request.call()
