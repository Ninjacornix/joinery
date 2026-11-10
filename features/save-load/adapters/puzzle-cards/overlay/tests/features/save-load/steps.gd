extends RefCounted
## Save-load test steps for memory cards: flip a matching pair the way
## pressing the cards does (`Game.request_action(card, &"flip")`).


## Changes saved state and returns the save ids it changed.
func change(play: PlayHarness) -> PackedStringArray:
	var level: Node = Game.current_level()
	var cards: Array = level.get(&"cards") if level != null else []
	var first: Node = null
	var second: Node = null
	for a: int in cards.size():
		for b: int in range(a + 1, cards.size()):
			if int(cards[a].get(&"symbol")) == int(cards[b].get(&"symbol")):
				first = cards[a]
				second = cards[b]
				break
		if first != null:
			break
	play.expect(first != null and second != null, "the board has a matching pair")
	if first == null or second == null:
		return PackedStringArray()
	Game.request_action(first, &"flip")
	await play.physics_frames(2)
	Game.request_action(second, &"flip")
	await play.physics_frames(30)
	play.expect(
		bool(first.get(&"matched")) and bool(second.get(&"matched")), "flipping a pair matches it"
	)
	return PackedStringArray([str(first.get(&"save_id")), str(second.get(&"save_id"))])
