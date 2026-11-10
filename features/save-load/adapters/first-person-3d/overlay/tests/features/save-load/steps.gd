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
