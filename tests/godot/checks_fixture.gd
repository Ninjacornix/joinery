extends RefCounted
## `joinery access --json` and `joinery perf --json` for a game with problems (and the same
## game after its changes were accepted), shared by the check cards' widget tests and the
## visual suite. The words are those the checks wrote for tests/fixtures/access_game and
## tests/fixtures/perf_game.


static func access(fixed := false) -> Dictionary:
	var areas := [
		_area("contrast", "Contrast", 100 if fixed else 0, 2, 0 if fixed else 2),
		_area("colour", "Colour", 100 if fixed else 0, 2, 0 if fixed else 2),
		_area("text", "Text size", 100 if fixed else 50, 2, 0 if fixed else 1),
		_area("captions", "Subtitles", 100 if fixed else 0, 2, 0 if fixed else 2),
		_area("controls", "Controls", 100, 3, 0),
		_area("menus", "Screen readers", 100 if fixed else 75, 4, 0 if fixed else 1),
	]
	var findings: Array = []
	if not fixed:
		findings += [
			_finding(
				"contrast",
				"main.tscn",
				"high",
				"1.3:1",
				"≥ 4.5:1",
				'"Score: 12" (#777777 on #888888) has contrast 1.3:1.',
				"typed",
				"Recolour text to #000000 in Godot"
			),
			_finding(
				"colour",
				"main.gd",
				"high",
				"ΔE 4 (deuteranopia)",
				"ΔE ≥ 10",
				"switches modulate between #40a040 and #d04040, which look alike with deuteranopia.",
				"agent",
				"Add a cue besides colour"
			),
			_finding(
				"captions",
				"main.tscn",
				"high",
				"1 voiced, 0 subtitled",
				"subtitles",
				"The game has voice or dialogue sound but shows no subtitles for it.",
				"agent",
				"Show subtitles for voice lines"
			),
			_finding(
				"text",
				"main.tscn",
				"medium",
				"14 px",
				"≥ 18 px",
				'"Score: 12" is 14 px tall at 1080p.',
				"typed",
				"Make text 12 px in Godot"
			),
			_finding(
				"colour",
				"project.godot",
				"medium",
				"missing",
				"Colour-blind filter",
				"The game's settings have no colour-blind filter.",
				"feature",
				"Add the Accessibility settings"
			),
			_finding(
				"captions",
				"project.godot",
				"medium",
				"missing",
				"Subtitles option",
				"The game's settings have no subtitles option.",
				"feature",
				"Add the Accessibility settings"
			),
			_finding(
				"menus",
				"main.tscn",
				"medium",
				"no name",
				"a name screen readers say",
				'The Button for "Gear" on the hud screen has no name a screen reader can say.',
				"typed",
				"Name menu controls for screen readers in Godot"
			),
			_finding(
				"contrast",
				"project.godot",
				"low",
				"missing",
				"High contrast option",
				"The game's settings have no high contrast option.",
				"",
				""
			),
		]
	return {
		"kind": "access",
		"project": "/game",
		"areas": areas,
		"summary": "",
		"findings": findings,
		"errors": [],
		"facts":
		{"controls": 14, "pages": ["pause", "settings", "accessibility"], "xag": xag(findings)},
	}


## The findings by Xbox Accessibility Guideline, as joinery/access_xag.py files them.
static func xag(findings: Array) -> Dictionary:
	var names := {
		"101": "Text display",
		"102": "Contrast",
		"103": "Additional channels for visual and audio cues",
		"104": "Subtitles and captions",
		"105": "Audio accessibility",
		"106": "Screen narration",
		"107": "Input",
		"108": "Game difficulty options",
		"109": "Objective clarity",
		"110": "Haptic feedback",
		"111": "Audio descriptions",
		"112": "UI navigation",
		"113": "UI focus handling",
		"114": "UI context",
		"115": "Error messages and destructive actions",
		"116": "Time limits",
		"117": "Visual distractions and motion settings",
		"118": "Photosensitivity",
		"119": "Speech-to-text and text-to-speech",
		"120": "Communication experiences",
		"121": "Accessible feature documentation",
		"122": "Accessible customer support",
		"123": "Mental health best practices",
	}
	var by_area := {
		"text": "101", "contrast": "102", "colour": "103", "captions": "104", "menus": "106"
	}
	var measured := ["101", "102", "103", "104", "106", "107"]
	var checked := measured + ["108", "117", "118"]
	var rows: Array = []
	for id: String in names:
		var filed: Array = []
		for item: Dictionary in findings:
			if by_area.get(str(item["area"]), "") == id:
				filed.append(item["id"])
		var status := "not_checked"
		if not filed.is_empty():
			status = "fails"
		elif id in measured:
			status = "passes"
		elif id in checked:
			status = "not_run"
		(
			rows
			. append(
				{
					"id": id,
					"name": names[id],
					"checks": ["what Joinery measures"] if id in checked else [],
					"status": status,
					"findings": filed,
				}
			)
		)
	return {"version": "3.2", "checked": checked.size(), "total": 23, "guidelines": rows}


static func perf(fixed := false, target := "steam-deck") -> Dictionary:
	var labels := {"steam-deck": "Steam Deck", "web": "Web browser", "mid": "Mid-range PC"}
	var areas := [
		_area("frames", "Frame time", 100 if fixed else 0, 2, 0 if fixed else 2),
		_area("rendering", "Rendering", 100 if fixed else 67, 6, 0 if fixed else 2),
		_area("lighting", "Lights", 100 if fixed else 0, 2, 0 if fixed else 1),
		_area("scripts", "Scripts", 100 if fixed else 50, 2, 0 if fixed else 1),
		_area("memory", "Memory", 100, 2, 0),
	]
	var findings: Array = []
	if not fixed:
		findings += [
			_finding(
				"scripts",
				"hot.gd",
				"high",
				"41.3 ms a frame",
				"≤ 2.5 ms",
				(
					"Costs 41.3 ms of every frame on the target. "
					+ "Line 9 in _process loops 300000 times every frame."
				),
				"agent",
				"Make hot.gd cheaper per frame"
			),
			_finding(
				"frames",
				"project.godot",
				"high",
				"52.4 ms",
				"≤ 25.0 ms",
				"p95 frame time would be 52.4 ms on a Steam Deck (24.1 ms here), over its 25.0 ms budget.",
				"",
				""
			),
			_finding(
				"lighting",
				"main.tscn",
				"medium",
				"6 shadowed lights",
				"≤ 2",
				"has 6 omni and spot lights casting shadows; each renders the scene again.",
				"typed",
				"Turn off shadows on 4 lights in Godot"
			),
			_finding(
				"rendering",
				"main.tscn",
				"medium",
				"24 copies",
				"one MultiMesh",
				"draws one mesh 24 times as separate nodes (Crate1 and others).",
				"agent",
				"Turn 24 copies into one MultiMesh"
			),
		]
	var frame := {"p50": 6.1, "p95": 9.3, "p99": 9.8}
	return {
		"kind": "perf",
		"project": "/game",
		"areas": areas,
		"summary": "",
		"findings": findings,
		"errors": [],
		"facts":
		{
			"target": target,
			"target_label": labels.get(target, "Steam Deck"),
			"measured": {"windowed": true, "frame_ms": frame, "draw_calls": 134},
		},
	}


static func run_rows(state := "working") -> Array:
	return [
		_row("c1", "Recolour text to #000000 in Godot (1 file)", "contrast", "in_review"),
		_row("c2", "Add a cue besides colour", "colour", state),
	]


static func _row(id: String, title: String, area: String, state: String) -> Dictionary:
	return {
		"key": area,
		"title": title,
		"area": area,
		"apps": ["godot"],
		"kind": "scripted",
		"task_id": id,
		"state": state,
		"message": ""
	}


static func _area(id: String, label: String, score: int, checked: int, failing: int) -> Dictionary:
	return {
		"id": id,
		"label": label,
		"score": score,
		"checked": checked,
		"failing": failing,
		# Frame time is fixed by the other areas' changes, not its own.
		"fixable": failing if id != "frames" else 0,
	}


static func _finding(
	area: String,
	path: String,
	severity: String,
	measured: String,
	target: String,
	message: String,
	kind: String,
	label: String
) -> Dictionary:
	var fix: Variant = null
	if not kind.is_empty():
		fix = {
			"kind": kind, "label": label, "feature": "accessibility" if kind == "feature" else ""
		}
	return {
		"id": "%s-%s-%s" % [area, path, measured],
		"area": area,
		"path": path,
		"check": area,
		"severity": severity,
		"measured": measured,
		"target": target,
		"message": message,
		"fix": fix,
		"needs_you": "" if fix != null else "Add it to the game's settings yourself.",
		"evidence": {"seed": 4242},
	}
