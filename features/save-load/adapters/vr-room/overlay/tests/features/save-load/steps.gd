extends RefCounted
## Save-load test steps for the VR room: press the button the way the pointer
## or interact does (`Game.request_action(button, &"use")`), which opens the
## glass case.


## Changes saved state and returns the save ids it changed.
func change(play: PlayHarness) -> PackedStringArray:
	var level: Node = Game.current_level()
	var button: Node = level.get_node_or_null(^"PushButton") if level != null else null
	play.expect(button != null, "the room has a button to press")
	if button == null or bool(button.get(&"is_pressed")):
		return PackedStringArray()
	Game.request_action(button, &"use")
	await play.physics_frames(2)
	play.expect(bool(button.get(&"is_pressed")), "pressing the button presses it")
	play.expect(bool(level.get(&"case_open")), "and opens the glass case")
	return PackedStringArray(["button", "level"])


## Ends the level the way the cube landing on the table does: the win is
## announced.
func finish(play: PlayHarness) -> void:
	var level: Node = Game.current_level()
	GameEvents.level_completed.emit({"time": float(level.get(&"elapsed")), "score": 1})
	await play.physics_frames(2)
	play.expect(bool(level.get(&"completed")), "the win completes the level")


## The win page is up.
func result_shown(_level: Node) -> bool:
	var me: Node = Game.local_player()
	var hud: Node = me.get_node_or_null(^"Hud") if me != null else null
	return hud != null and is_instance_valid(hud.get(&"win_page"))
