extends SceneTree
## The sound pass card: "/sound-pass [focus]" asks the chat host to listen to the level; the card
## shows one headline ("11 sounds missing in level.tscn") and a row per kind of sound that
## follows its tasks (making, ready to review, accepted); Stop stops the pass; the sound
## designer's report goes to the transcript; Review opens the first change that waits.

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

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


func _audit() -> Dictionary:
	return {
		"headline": "11 sounds missing in level.tscn",
		"level": "res://game/level.tscn",
		"categories":
		[
			{"id": "footsteps", "label": "Footsteps", "missing": 3, "findings": 2},
			{"id": "jump_land", "label": "Jump and land", "missing": 2, "findings": 1},
			{"id": "pickups", "label": "Pickups", "missing": 1, "findings": 1},
			{"id": "hits", "label": "Hits", "missing": 0, "findings": 0},
			{"id": "switches", "label": "Doors and switches", "missing": 2, "findings": 2},
			{"id": "ambience", "label": "Ambience", "missing": 1, "findings": 1},
			{"id": "ui", "label": "UI", "missing": 2, "findings": 1},
		],
		"findings":
		[
			{
				"category": "pickups",
				"scene": "res://game/props/coin.tscn",
				"missing": "Coin is picked up in silence",
				"evidence": "the playtest bot picked up 4 things",
			},
		],
	}


func _tasks(states: Array) -> Array:
	var rows := [
		["t1", "Add footsteps, jump and land to the player", ["footsteps", "jump_land"]],
		["t2", "Add wind ambience and floor surfaces to level.tscn", ["footsteps", "ambience"]],
		["t3", "Add pickup sound to coin", ["pickups"]],
		["t4", "Add lever and plate sounds to 2 props", ["switches"]],
		["t5", "Add UI click and hover sounds", ["ui"]],
	]
	var out: Array = []
	for index: int in rows.size():
		var row: Array = rows[index]
		(
			out
			. append(
				{
					"key": row[0],
					"title": row[1],
					"category": row[2][0],
					"categories": row[2],
					"task_id": row[0],
					"state": states[index],
					"sounds": [],
				}
			)
		)
	return out


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

	# "/sound-pass footsteps and ambience": the host is asked, the card shows at once.
	dock.call("run_command", "/sound-pass footsteps and ambience")
	await process_frame
	var sent := _sent(job, "sound_pass_start")
	_check(sent.get("focus", "") == "footsteps and ambience", "The focus is sent: %s" % sent)
	var card: Control = tasks.get("sound_card")
	_check(card != null and tasks.visible, "The sound pass card shows")
	_check(card.call("step_text") == "Listening to the level…", "It listens first")
	_check((card.get("stop") as Button).visible, "Stop shows while it runs")

	# The host's audit and plan: one headline, one row per kind of sound with something missing.
	(
		backend
		. call(
			"_handle",
			{
				"kind": "sound_pass",
				"state": "making",
				"audit": _audit(),
				"tasks": _tasks(["working", "working", "queued", "queued", "queued"]),
			}
		)
	)
	_check(
		(card.get("title_label") as Label).text == "11 sounds missing in level.tscn",
		"The headline says how much is missing: " + (card.get("title_label") as Label).text
	)
	var words: PackedStringArray = card.call("row_words")
	_check(
		(
			words
			== PackedStringArray(
				[
					"Footsteps  Making…",
					"Jump and land  Making…",
					"Pickups  Waiting",
					"Doors and switches  Waiting",
					"Ambience  Making…",
					"UI  Waiting",
				]
			)
		),
		"One row per kind of sound, none for hits: " + ", ".join(words)
	)
	_check(card.call("step_text") == "Making 5 changes…", "It counts the changes")
	_check(not (card.get("_review") as Button).visible, "Nothing to review yet")

	# A task's events move its rows; Review shows once one is ready.
	backend.call(
		"_handle", {"kind": "task", "task_id": "t3", "state": "in_review", "title": "Pickup"}
	)
	words = card.call("row_words")
	_check(words[2] == "Pickups  Ready to review", "The pickup row follows its task: " + words[2])
	_check(
		card.call("step_text") == "Making 5 changes… 1 ready to review",
		"It counts what is ready: " + card.call("step_text")
	)
	_check((card.get("_review") as Button).visible, "Review shows once one is ready")
	backend.call("_handle", {"kind": "task", "task_id": "t1", "state": "in_review", "title": "P"})
	words = card.call("row_words")
	_check(
		words[0] == "Footsteps  Making…", "A row covered by two tasks waits for both: " + words[0]
	)

	# The sound designer's report goes to the transcript once.
	var transcript: Node = dock.get("_transcript")
	var report := "The level was silent; footsteps on grass and stone are on the way."
	var event := {
		"kind": "sound_pass",
		"state": "making",
		"summary": report,
		"tasks": _tasks(["in_review", "working", "in_review", "queued", "queued"]),
	}
	backend.call("_handle", event)
	backend.call("_handle", event)
	var found := str(transcript.call("get_parsed_text")).count(
		"Sound designer  The level was silent"
	)
	_check(found == 1, "The report is in the transcript once (%d)" % found)

	# Stop stops the pass; the host confirms.
	(card.get("stop") as Button).pressed.emit()
	_check(_sent(job, "sound_pass_cancel").get("kind") == "sound_pass_cancel", "Stop sends cancel")
	_check(card.call("step_text") == "Stopping…", "Stopping shows at once")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "sound_pass",
				"state": "stopped",
				"tasks": _tasks(["in_review", "in_review", "in_review", "stopped", "stopped"]),
			}
		)
	)
	_check(not card.call("working"), "The pass is over")
	_check(
		card.call("step_text") == "Stopped. 3 ready to review, 2 didn't finish",
		"It says what waits: " + card.call("step_text")
	)
	words = card.call("row_words")
	_check(words[3] == "Doors and switches  Stopped", "Stopped rows say so: " + words[3])
	_check((card.get("close") as Button).visible, "It can be dismissed")

	# An accepted change shows in its row.
	backend.call("_handle", {"kind": "task", "task_id": "t5", "state": "accepted", "title": "UI"})
	_check((card.call("row_words") as PackedStringArray)[5] == "UI  Accepted", "Accepted shows")

	# A failed pass says why; approval first opens the art direction.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("sound_card") == null, "Dismissed")
	dock.call("run_command", "/sound-pass")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "sound_pass",
				"state": "failed",
				"audit": _audit(),
				"art_gate": true,
				"message": "Before Journeyman makes anything, agree how your game should look.",
			}
		)
	)
	card = tasks.get("sound_card")
	_check(card != null and not card.call("working"), "The failed pass has its card")
	_check(
		str(card.call("step_text")).begins_with("Before Journeyman makes anything"),
		"It says why: " + card.call("step_text")
	)

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("sound pass card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
