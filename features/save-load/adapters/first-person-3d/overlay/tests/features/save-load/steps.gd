extends RefCounted
## Save-load test steps for the first-person level: turn a switch on the way
## using it does (`Game.request_action(switch, &"use")`).


## Changes saved state and returns the save ids it changed.
func change(play: PlayHarness) -> PackedStringArray:
	var level: Node = Game.current_level()
	var switch: Node = null
	for node: Node in play.tree.get_nodes_in_group(&"fp_switch"):
		if level != null and level.is_ancestor_of(node) and not bool(node.get(&"on")):
			switch = node
			break
	play.expect(switch != null, "the level has a switch to turn on")
	if switch == null:
		return PackedStringArray()
	Game.request_action(switch, &"use")
	await play.physics_frames(2)
	play.expect(bool(switch.get(&"on")), "using the switch turns it on")
	return PackedStringArray([str(switch.get(&"save_id"))])


## Ends the level the way reaching the open exit does: the win is announced.
func finish(play: PlayHarness) -> void:
	var level: Node = Game.current_level()
	GameEvents.level_completed.emit(level.call(&"_result"))
	await play.physics_frames(2)
	play.expect(bool(level.get(&"completed")), "the win completes the level")


## The win panel is up.
func result_shown(_level: Node) -> bool:
	var me: Node = Game.local_player()
	var hud: Node = me.get_node_or_null(^"Hud") if me != null else null
	return hud != null and bool((hud.get(&"win_panel") as Control).visible)
