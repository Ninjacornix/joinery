extends RefCounted
## A sound pass under way, as the chat host reports it: what a third-person level was
## missing and its five changes at different steps. Used by the visual suite.


static func making() -> Dictionary:
	var categories := [
		{"id": "footsteps", "label": "Footsteps", "missing": 3, "findings": 2},
		{"id": "jump_land", "label": "Jump and land", "missing": 2, "findings": 1},
		{"id": "pickups", "label": "Pickups", "missing": 1, "findings": 1},
		{"id": "switches", "label": "Doors and switches", "missing": 2, "findings": 2},
		{"id": "ambience", "label": "Ambience", "missing": 1, "findings": 1},
		{"id": "ui", "label": "UI", "missing": 2, "findings": 1},
	]
	var rows := [
		["s1", "Add footsteps, jump and land to the player", ["footsteps", "jump_land"], "working"],
		["s2", "Add wind ambience and floor surfaces to level.tscn", ["ambience"], "in_review"],
		["s3", "Add pickup sound to coin", ["pickups"], "in_review"],
		["s4", "Add lever and plate sounds to 2 props", ["switches"], "queued"],
		["s5", "Add UI click and hover sounds", ["ui"], "queued"],
	]
	var run: Array = []
	for row: Array in rows:
		run.append({"task_id": row[0], "title": row[1], "categories": row[2], "state": row[3]})
	return {
		"kind": "sound_pass",
		"state": "making",
		"audit": {"headline": "11 sounds missing in level.tscn", "categories": categories},
		"tasks": run,
	}
