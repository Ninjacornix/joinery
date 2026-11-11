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
