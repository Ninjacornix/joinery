extends "res://features/save-load/save_adapter.gd"
## Third-person slots show how many coins were collected. Checkpoint pads
## autosave (the level announces them through `GameEvents.checkpoint_reached`).


func summary(nodes: Dictionary) -> String:
	var coins: Array[int] = count_flag(nodes, "coin_", "collected")
	if coins[1] == 0:
		return ""
	return tr("Coins: %d of %d") % [coins[0], coins[1]]
