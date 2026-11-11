extends RefCounted
## `joinery design --json` for a game that breaks its own brief (and, with the agent's pass,
## one finding more), and for a sound one; shared by the design card's widget test and the
## visual suite.


static func report(agent := false) -> Dictionary:
	var findings: Array = [
		_finding(
			"unbound-verb",
			"high",
			"Dash: `dash` isn't in the input map",
			"The player has no way to dash.",
			"The brief binds Dash to `dash`, but project.godot has no such input action.",
			[["design/brief.md", 12, ""], ["project.godot", 40, "[input]"]]
		),
		_finding(
			"unreachable-win",
			"high",
			"The win state is never reached",
			"The player can never win: the code that would end the level never runs.",
			"`_complete` emits it, but nothing the game runs calls or connects it.",
			[["game/level.gd", 257, "_complete"], ["design/brief.md", 30, ""]]
		),
		_finding(
			"softlock",
			"high",
			"Room pit traps the player",
			"A player who goes into pit can't get back out to the goal and has to restart.",
			"The spawn reaches pit, but from inside it no walk, jump or drop reaches exit.",
			[["design/blockouts/caves.toml", 11, "pit"]]
		),
		_finding(
			"controls-hidden",
			"medium",
			"The controls are never shown",
			"A new player has to guess which keys do what.",
			"The game has 4 gameplay actions, but no menu, label or hint shows them.",
			[["project.godot", 40, "[input]"]]
		),
		_finding(
			"silent-pickup",
			"medium",
			"Picking up a gem gives no feedback",
			"It just disappears: no sound, no effect, no count goes up.",
			"gem.gd removes it on contact and nothing else happens.",
			[["game/props/gem.gd", 9, ""]]
		),
		_finding(
			"uneven-tuning",
			"medium",
			"Jump velocity differs between scenes (level.tscn 300, level_2.tscn 500)",
			"The same character jumps differently from one level to the next.",
			"player.tscn is used in 2 scenes with different jump_velocity values.",
			[["game/level.tscn", 9, "jump_velocity"], ["game/level_2.tscn", 10, "jump_velocity"]]
		),
		_finding(
			"missing-mechanic",
			"low",
			"Wall jump from the design document isn't in the game",
			"A player who read the design would look for wall jump and never find it.",
			'"Mechanics" in design/docs/gdd.md lists Wall jump; nothing in the game mentions it.',
			[["design/docs/gdd.md", 4, ""]]
		),
	]
	if agent:
		var told := _finding(
			"agent",
			"medium",
			"The flag is never pointed out",
			"The player doesn't know the flag ends the level.",
			"The brief's win is touching the flag; nothing on screen says so.",
			[["design/brief.md", 30, ""]]
		)
		told["source"] = "agent"
		findings.insert(6, told)
	return {
		"project": "/tmp/game",
		"summary": "%d design problems" % findings.size(),
		"findings": findings,
		"read":
		["design/brief.md", "design/docs/gdd.md", "6 input actions", "25 scripts", "8 scenes"],
		"skipped": [],
		"check_every": 0,
	}


static func clean() -> Dictionary:
	return {
		"project": "/tmp/game",
		"summary": "No design problems found",
		"findings": [],
		"read": ["design/brief.md", "6 input actions", "25 scripts", "8 scenes"],
		"skipped": [],
		"check_every": 0,
	}


static func _finding(
	check: String, severity: String, title: String, player: String, why: String, places: Array
) -> Dictionary:
	var evidence: Array = []
	for place: Variant in places:
		var entry := place as Array
		evidence.append({"path": entry[0], "line": entry[1], "node": entry[2], "quote": ""})
	return {
		"id": "%s-%d" % [check, title.hash() % 100000],
		"check": check,
		"severity": severity,
		"title": title,
		"player": player,
		"why": why,
		"evidence": evidence,
		"source": "check",
		"request":
		"The design check found: %s. %s %s Change only what this needs." % [title, player, why],
	}
