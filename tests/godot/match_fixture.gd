extends RefCounted
## `joinery match --json` for a project that drifts (and the same project after its changes
## were accepted), shared by the match card's widget test and the visual suite.


static func audit(fixed := false) -> Dictionary:
	var areas := [
		_area("palette", "Palette", 100 if fixed else 72, 25, 0 if fixed else 7, 0 if fixed else 6),
		_area(
			"textures", "Textures", 100 if fixed else 80, 10, 0 if fixed else 2, 0 if fixed else 2
		),
		_area("models", "Models", 100, 6, 0, 0),
		_area("lighting", "Lighting", 100 if fixed else 0, 1, 0 if fixed else 1, 0 if fixed else 3),
		_area("sound", "Sound", 90 if fixed else 70, 10, 1 if fixed else 3, 0 if fixed else 2),
	]
	var findings: Array = []
	if not fixed:
		findings += [
			_finding("palette", "art/textures/wall.png", "high", "100% off-palette", "≤ 5%", true),
			_finding("lighting", "env/world.tres", "high", "filmic", "agx", true),
			_finding("lighting", "env/world.tres", "high", "1.6", "0.9–1.1", true),
			_finding("sound", "audio/sfx/door.wav", "high", "-6.0 LUFS", "-16 LUFS", true),
			_finding("textures", "art/textures/floor.png", "medium", "512×512", "128×128", true),
			_finding("palette", "art/textures/rug.png", "medium", "18% off-palette", "≤ 5%", true),
			_finding("lighting", "env/world.tres", "medium", "on", "off", true),
			_finding("sound", "audio/sfx/rain.wav", "medium", "-12.1 LUFS", "-16 LUFS", true),
		]
	findings.append(
		_finding("sound", "audio/sfx/bought.wav", "medium", "-9.0 LUFS", "-16 LUFS", false)
	)
	return {
		"project": "/game",
		"approved": true,
		"rules": true,
		"areas": areas,
		"summary": "",
		"findings": findings,
		"excluded": ["art/textures/final.png", "art/wip/sketch.png"],
		"errors": [],
	}


static func run_rows(state := "working") -> Array:
	return [
		_row("t1", "Remap 6 textures to the palette", "palette", "in_review"),
		_row("t2", "Match the lighting", "lighting", state),
		_row("t3", "Normalise 2 sounds to −16 LUFS", "sound", state),
	]


static func _row(id: String, title: String, area: String, state: String) -> Dictionary:
	return {
		"key": area,
		"title": title,
		"area": area,
		"apps": [],
		"task_id": id,
		"state": state,
		"message": ""
	}


static func _area(
	id: String, label: String, score: int, checked: int, failing: int, fixable: int
) -> Dictionary:
	return {
		"id": id,
		"label": label,
		"score": score,
		"checked": checked,
		"failing": failing,
		"fixable": fixable,
	}


static func _finding(
	area: String, path: String, severity: String, measured: String, target: String, fix: bool
) -> Dictionary:
	var item := {
		"id": path + measured,
		"area": area,
		"path": path,
		"subject": "",
		"check": area,
		"severity": severity,
		"measured": measured,
		"target": target,
		"message": "%s, the art bible asks for %s" % [measured, target],
		"fix": {"label": "A typed fix", "tool": "x", "server": "y", "apps": []} if fix else null,
		"needs_you":
		"" if fix else "It has no recipe (you imported it), so Joinery won't change it.",
	}
	return item
