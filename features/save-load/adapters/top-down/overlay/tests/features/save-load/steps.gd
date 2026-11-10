extends RefCounted
## Save-load test steps for the top-down arena: take a gem the way walking
## over it does (`Game.request_action(gem, &"collect")`). Waves stay off so
## the test is never interrupted by a fight.


## Called for every level start, before its first frame.
func prepare(level: Node) -> void:
	level.set(&"auto_waves", false)


## Changes saved state and returns the save ids it changed.
func change(play: PlayHarness) -> PackedStringArray:
	var level: Node = Game.current_level()
	var gem: Node = null
	for node: Node in play.tree.get_nodes_in_group(&"pickup"):
		var free: bool = not bool(node.get(&"collected"))
		if level != null and level.is_ancestor_of(node) and free and node.get(&"kind") == "gem":
			gem = node
			break
	play.expect(gem != null, "the arena has a gem to take")
	if gem == null:
		return PackedStringArray()
	Game.request_action(gem, &"collect")
	await play.physics_frames(2)
	play.expect(bool(gem.get(&"collected")), "requesting collect takes the gem")
	return PackedStringArray([str(gem.get(&"save_id")), "level"])
