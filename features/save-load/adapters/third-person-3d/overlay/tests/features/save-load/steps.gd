extends RefCounted
## Save-load test steps for the third-person arena: collect a coin the way touching
## it does (`Game.request_action(coin, &"collect")`).


## Changes saved state and returns the save ids it changed.
func change(play: PlayHarness) -> PackedStringArray:
	var coin: Node = _first(play, &"coin", &"collected")
	play.expect(coin != null, "the level has a coin to collect")
	if coin == null:
		return PackedStringArray()
	Game.request_action(coin, &"collect")
	await play.physics_frames(2)
	play.expect(bool(coin.get(&"collected")), "requesting collect collects the coin")
	return PackedStringArray([str(coin.get(&"save_id"))])


func _first(play: PlayHarness, group: StringName, flag: StringName) -> Node:
	var level: Node = Game.current_level()
	for node: Node in play.tree.get_nodes_in_group(group):
		if level != null and level.is_ancestor_of(node) and not bool(node.get(flag)):
			return node
	return null
