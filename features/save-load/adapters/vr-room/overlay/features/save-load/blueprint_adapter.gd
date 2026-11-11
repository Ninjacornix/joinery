extends "res://features/save-load/save_adapter.gd"
## VR room slots say how far the player got. Opening the glass case is an
## autosave moment (throttled).

var _request: Callable = Callable()


func summary(nodes: Dictionary) -> String:
	var level: Dictionary = nodes.get("level", {}) if nodes.get("level") is Dictionary else {}
	if bool(level.get("completed", false)):
		return tr("The cube is on the table")
	if bool(level.get("case_open", false)):
		return tr("Glass case open")
	return tr("Glass case closed")


func watch(request: Callable) -> void:
	_request = request
	GameEvents.world_changed.connect(_on_world_changed)


func _on_world_changed(node: Node) -> void:
	var level: Node = Game.current_level()
	if level != null and node.get(&"save_id") == "button" and not bool(level.get(&"completed")):
		_request.call()
