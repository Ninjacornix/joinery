extends NetAdapter
## Online play for the 2D platformer. Coins, checkpoints and the level itself
## replicate through the game core (`saveable` nodes, `Game.request_action`).
## The goal flag is not saveable (a saved game never stands on a finished
## level), so this adds a reached flag to what a late joiner receives: they
## see the finished level and its win screen too.


func snapshot_extra() -> Dictionary:
	var states: Dictionary = {}
	var goals: Node = level.get_node_or_null(^"Goals") if has_level() else null
	if goals == null:
		return states
	for goal: Node in goals.get_children():
		if goal.has_method(&"save_state") and bool(goal.get(&"done")):
			states[str(level.get_path_to(goal))] = goal.call(&"save_state")
	return states
