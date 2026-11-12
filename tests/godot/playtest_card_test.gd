extends SceneTree
## The playtest card: "/playtest" starts the playtester, the card says "Playing… 0:42" with its
## own Stop, then shows the findings by severity with each finding opening to its detail, seed
## and screenshot; "Fix this" and the playtester's follow-ups put a request in the composer.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const JOB_SOURCE := """extends RefCounted
var lines: Array[Dictionary] = []
func send_line(line: String) -> void:
	lines.append(JSON.parse_string(line))
func take_lines() -> Array:
	return []
func poll() -> Dictionary:
	return {}
func close_pipes() -> void:
	pass
"""
const SHOT := "user://playtest_shot.png"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _button(node: Node, text: String) -> Button:
	for found: Node in node.find_children("", "Button", true, false):
		if (found as Button).text == text and (found as Button).is_visible_in_tree():
			return found as Button
	return null


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


func _report() -> Dictionary:
	var shot := Image.create(64, 36, false, Image.FORMAT_RGB8)
	shot.fill(Color(0.3, 0.5, 0.7))
	shot.save_png(SHOT)
	var items: Array = []
	for index: int in 6:
		(
			items
			. append(
				{
					"id": "p%d" % index,
					"severity": "high" if index == 0 else "low",
					"title": "Softlock" if index == 0 else "Text doesn't fit",
					"detail":
					(
						"The player couldn't move for 8 s while the bot held movement keys."
						if index == 0
						else '"Settings" is cut off'
					),
					"where": "res://game/player.gd:12" if index == 0 else "",
					"t": 5.0 + index,
					"seed": 4821,
					"repro": "Run `joinery playtest --project . --seed 4821 --seconds 120`.",
					"shot": ProjectSettings.globalize_path(SHOT) if index == 0 else "",
				}
			)
		)
	return {
		"counts": {"critical": 0, "high": 1, "medium": 0, "low": 5, "note": 0},
		"items": items,
		"seed": 4821,
		"played_seconds": 120.0,
		"summary": "Played 2:00 (seed 4821): 1 high, 5 low.",
		"coverage":
		{
			"percent": 40.0,
			"visited": 4,
			"navigable": 10,
			"start": [0, 0],
			"grid": ["##..", "#  .", "#..."],
		},
	}


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var dock: Control = load(DOCK_PATH).new()
	root.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	dock.add_child(backend)
	var fake := GDScript.new()
	fake.source_code = JOB_SOURCE
	fake.reload()
	var job: RefCounted = fake.new()
	backend.set("_job", job)
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})
	backend.set("_job", job)
	dock.call("set_chat_state", "connected", "")
	var tasks: Control = dock.get("_tasks")
	var header: Control = dock.get("_header")
	var composer: Control = dock.get("_composer")
	var prompt: TextEdit = composer.get("prompt")

	# "/playtest the lever" starts the playtester; the card shows at once.
	dock.call("run_command", "/playtest the lever")
	var started := _sent(job, "playtest_start")
	_check(str(started.get("focus", "")) == "the lever", "/playtest sends its focus")
	var card: Control = tasks.get("playtest")
	_check(card != null and tasks.visible, "The playtest card shows at once")
	_check(card.call("step_text") == "Starting the playtester…", "It says it is starting")
	var event := {"kind": "playtest", "playtest_id": "pt1", "state": "starting", "seconds": 120}
	backend.call("_handle", event)
	_check(tasks.get("playtest") == card, "The host's event lands on the same card")
	_check(header.call("agents_text").is_empty(), "One agent besides none: quiet header")

	# Playing: "Playing…" with the game time and its length; its own Stop.
	event = event.merged({"state": "playing", "elapsed": 42.0}, true)
	backend.call("_handle", event)
	_check(card.call("step_text") == "Playing…", "It says it is playing")
	_check(str((card.get("_timer") as Label).text) == "0:42 / 2:00", "Game time of the run")
	dock.call("set_chat_busy", true)
	_check(header.call("agents_text") == "2 agents working", "The playtester counts as an agent")
	dock.call("set_chat_busy", false)
	(card.get("stop") as Button).pressed.emit()
	_check(str(_sent(job, "playtest_cancel").get("playtest_id", "")) == "pt1", "Stop stops it")
	_check(card.call("step_text") == "Stopping…", "Stop answers at once")

	# A second run: the report, at a glance.
	card.queue_free()
	tasks.set("playtest", null)
	event = {"kind": "playtest", "playtest_id": "pt2", "state": "playing", "seconds": 120}
	backend.call("_handle", event)
	card = tasks.get("playtest")
	event = event.merged({"state": "writing", "report": _report()}, true)
	backend.call("_handle", event)
	_check(card.call("step_text") == "Writing the report…", "Then it writes")
	var words: PackedStringArray = card.call("count_words")
	_check(words == PackedStringArray(["1 high", "5 low"]), "Counts by severity: %s" % words)
	# How much of the level the bot reached, over a map of what it missed.
	_check(card.call("coverage_text") == "Explored 40% of the level", card.call("coverage_text"))
	var map: Control = card.find_child("CoverageMap", true, false)
	_check(map != null and map.is_visible_in_tree(), "The map shows")
	_check(map.call("counts") == [4, 6], "Reached and unreached cells: %s" % [map.call("counts")])
	var rows: Array[Control] = card.call("item_rows")
	_check(rows.size() == 4, "The first four findings show")
	var more := _button(card, "Show all 6")
	_check(more != null, "The rest are one click away")
	more.pressed.emit()
	_check((card.call("item_rows") as Array).size() == 6, "Show all shows them all")
	event = (
		event
		. merged(
			{
				"state": "done",
				"summary": "The player gets stuck at 0:05 near the lever.",
				"followups": ["In res://game/player.gd, re-enable input after the cutscene."],
			},
			true
		)
	)
	backend.call("_handle", event)
	_check(card.call("step_text") == "Played 2:00, seed 4821.", "Done: what was played")
	_check(not (card.get("stop") as Button).visible, "No Stop once it is over")
	_check((card.get("close") as Button).visible, "It can be dismissed")

	# A finding opens to its detail, place, seed and screenshot; Fix this fills the composer.
	var first: Control = rows[0]
	var toggle: Button = first.find_children("", "Button", true, false)[0]
	_check(toggle.text == "Softlock · 0:05", "Title and time: %s" % toggle.text)
	toggle.button_pressed = true
	var body: Control = first.get_child(1)
	_check(body.visible, "It opens")
	var texts := PackedStringArray()
	for label: Node in body.find_children("", "Label", true, false):
		texts.append((label as Label).text)
	_check(texts.has("res://game/player.gd:12 · seed 4821"), "Place and seed: %s" % texts)
	_check(body.find_children("", "TextureRect", true, false).size() >= 1, "A screenshot")
	_button(body, "Fix this").pressed.emit()
	_check(prompt.text.begins_with("Fix this playtest finding: Softlock at 0:05"), prompt.text)
	_check(prompt.text.contains("--seed 4821"), "The request carries the seed to check with")
	_check(not _sent(job, "prompt").size(), "Nothing is sent until the user sends it")

	# The playtester's words go to the transcript; its follow-ups are offers.
	var log := str(dock.get("_transcript").get("_log"))
	_check(log.contains("re-enable input after the cutscene"), "The follow-up is offered")
	prompt.text = ""
	var ask := _button(dock.get("_transcript"), "Ask Journeyman to fix")
	_check(ask != null, "Each follow-up has Ask Journeyman to fix")
	ask.pressed.emit()
	_check(prompt.text.begins_with("In res://game/player.gd"), "It fills the composer")

	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("playtest") == null, "Dismissed")

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("playtest card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
