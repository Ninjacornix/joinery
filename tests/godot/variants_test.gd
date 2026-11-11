extends SceneTree
## Variants side by side: "/variants", "Try 3 ways" and "Try other ways" start them; the ways
## show as one strip in the sidebar (no task card each), with pictures, a sound to play and
## changed lines; Use this, Mix and Discard all send one line each; a refused pick puts the
## strip back; the pick leaves one transcript line; the Review tab shows the same strip above
## a way's full review.

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


func _way(id: String, number: int, note: String, state: String) -> Dictionary:
	return {
		"kind": "task",
		"task_id": id,
		"title": "A wooden door · %d" % number,
		"state": state,
		"variant_group": "g1",
		"variant": number,
		"direction": note,
		"step": "Waiting for a free agent…" if state == "queued" else "",
	}


func _group(states: Array, state := "working", picked := "") -> Dictionary:
	var notes := ["chunky and worn", "slim and clean", "ornate and detailed"]
	var ways: Array = []
	for index: int in 3:
		(
			ways
			. append(
				{
					"task_id": "t%d" % (index + 1),
					"variant": index + 1,
					"direction": notes[index],
					"state": states[index],
					"transaction_id": "tx%d" % (index + 1) if states[index] == "in_review" else "",
				}
			)
		)
	return {
		"kind": "variants",
		"group_id": "g1",
		"title": "A wooden door",
		"state": state,
		"picked": picked,
		"ways": ways,
	}


func _picture(path: String, colour: Color) -> String:
	var image := Image.create(32, 24, false, Image.FORMAT_RGB8)
	image.fill(colour)
	image.save_png(path)
	return ProjectSettings.globalize_path(path)


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
	dock.call("attach_bridge", backend)
	dock.call("set_chat_state", "connected", "")
	var tasks: Control = dock.get("_tasks")
	var variants: Node = tasks.get("variants")
	_check(variants != null, "The sidebar runs variants")

	# "/variants 3 a wooden door" asks the host for three ways.
	var slash: Object = dock.get("_slash")
	_check(slash.call("run", "/variants 3 A wooden door"), "/variants is a command")
	var started := _sent(job, "variants_start")
	_check(str(started.get("text", "")) == "3 A wooden door", "/variants sends the request")
	slash.call("run", "/variants")
	var log: Array = dock.get("_transcript").get("_log")
	_check(str(log).contains("/variants 3 a wooden door"), "Bare /variants says how")

	# "Try 3 ways" in the composer's options sends the typed message as three ways.
	var composer: Control = dock.get("_composer")
	var prompt: TextEdit = composer.get("prompt")
	prompt.text = "A creaky door sound"
	var options: MenuButton = composer.get("options")
	var popup := options.get_popup()
	var item := popup.get_item_index(200)
	_check(item >= 0 and popup.get_item_text(item) == "Try 3 ways", "The menu offers Try 3 ways")
	popup.id_pressed.emit(200)
	started = _sent(job, "variants_start")
	_check(int(started.get("count", 0)) == 3, "Try 3 ways asks for three")
	_check(str(started.get("text", "")) == "A creaky door sound", "…of the typed message")
	_check(prompt.text.is_empty(), "The message left the composer")

	# The host starts the ways: one strip, not a task card each.
	backend.call("_handle", _way("t1", 1, "chunky and worn", "working"))
	backend.call("_handle", _way("t2", 2, "slim and clean", "working"))
	backend.call("_handle", _way("t3", 3, "ornate and detailed", "queued"))
	backend.call("_handle", _group(["working", "working", "queued"]))
	_check(tasks.call("cards").is_empty(), "Ways don't get task cards")
	var strip: Control = variants.call("strip_for", "g1")
	_check(strip != null and strip.is_visible_in_tree(), "One strip shows the ways")
	var ways: Array = strip.call("ways")
	_check(ways.size() == 3, "Three ways side by side")
	_check(str(ways[1].call("note_text")) == "2 · slim and clean", "Each way says its direction")
	_check(
		str(ways[2].call("words_text")) == "Waiting for a free agent",
		"A way over the limit waits its turn"
	)
	var header: Control = dock.get("_header")
	_check(header.call("agents_text") == "2 agents working", "Working ways count as agents")
	_check(not (ways[0].get("use_button") as Button).visible, "No pick while a way works")

	# The ways are ready: a picture, a sound and changed lines.
	var picture := _picture("user://door_after.png", Color(0.6, 0.4, 0.2))
	var sound := ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav")
	var previews := [
		{
			"media": [{"path": "art/door.png", "media": "image", "after": picture}],
			"facts": ["chunky"]
		},
		{"media": [{"path": "audio/door.wav", "media": "audio", "after": sound}]},
		{"diff": "--- a/door.gd\n+++ b/door.gd\n+var ornate := true\n", "media": []},
	]
	for index: int in 3:
		var event: Dictionary = previews[index]
		event["kind"] = "file_preview"
		event["task_id"] = "t%d" % (index + 1)
		event["transaction_id"] = "tx%d" % (index + 1)
		backend.call("_handle", event)
	backend.call("_handle", _group(["in_review", "in_review", "in_review"]))
	_check(
		(ways[0].get("picture") as TextureRect).texture != null, "An image way shows its picture"
	)
	_check(str(ways[0].call("facts_text")).contains("Chunky"), "…and its facts")
	var play := ways[1].get("_play") as Button
	_check(play.visible, "A sound way has a play button")
	play.pressed.emit()
	_check(ways[1].call("playing"), "Play plays that way's sound")
	ways[1].call("stop_sound")
	_check(str(ways[2].get("_code").text) == "var ornate := true", "A text way shows its lines")
	var use := ways[1].get("use_button") as Button
	_check(use.visible and not use.disabled, "Each ready way offers Use this")

	# Mix asks for one more way from notes on these.
	(strip.get("mix_button") as Button).pressed.emit()
	var field := strip.get("mix_field") as LineEdit
	_check(field.is_visible_in_tree(), "Mix opens a field")
	field.text = "door shape from 2, colour from 3"
	(strip.get("mix_go") as Button).pressed.emit()
	var mixed := _sent(job, "variants_mix")
	_check(str(mixed.get("note", "")) == "door shape from 2, colour from 3", "Mix sends the notes")
	_check(str(mixed.get("group_id", "")) == "g1", "…for this request")

	# Use this: optimistic, one line to the host; a refusal puts the strip back.
	use.pressed.emit()
	var pick := _sent(job, "variants_pick")
	_check(str(pick.get("task_id", "")) == "t2", "Use this keeps that way")
	_check(use.text == "Keeping…", "The pick shows at once")
	_check((ways[0].get("use_button") as Button).disabled, "One pick at a time")
	backend.call(
		"_handle",
		{"kind": "notice", "level": "warn", "text": "That way has no change", "group_id": "g1"}
	)
	_check(use.text == "Use this" and not use.disabled, "A refused pick puts the strip back")
	use.pressed.emit()
	backend.call("_handle", _group(["discarded", "accepted", "discarded"], "picked", "t2"))
	_check(
		str(strip.call("result_text")) == "Kept 2 · slim and clean. The other ways were discarded.",
		"The strip says what was kept"
	)
	log = dock.get("_transcript").get("_log")
	_check(str(log).contains("A wooden door: kept 2 · slim and clean."), "One transcript line")
	_check(header.call("agents_text") == "", "Nothing works any more")

	# Discard all is one click.
	var second: Dictionary = _group(["in_review", "in_review", "in_review"])
	second["group_id"] = "g2"
	backend.call("_handle", second)
	var other: Control = variants.call("strip_for", "g2")
	(other.get("discard_button") as Button).pressed.emit()
	_check(str(_sent(job, "variants_discard").get("group_id", "")) == "g2", "Discard all")

	# "Try other ways" on a change card keeps that change as the first way.
	var row: Control = load("res://addons/joinery/sidebar_review_row.gd").new()
	var asked: Array[bool] = []
	row.connect("other_ways_pressed", func() -> void: asked.append(true))
	(row.get("ways") as Button).pressed.emit()
	_check(asked == [true], "The change card offers Try other ways")
	row.free()
	get_root().get_tree().call_group(
		&"joinery_variants", &"try_other_ways", "A door", "tx9", "Wider"
	)
	started = _sent(job, "variants_start")
	_check(str(started.get("keep_transaction_id", "")) == "tx9", "Try other ways keeps the change")

	# A saved chat says what was kept in one line.
	var line: String = (
		load("res://addons/joinery/sidebar_variants.gd")
		. call(
			"saved_line",
			{
				"title": "A wooden door",
				"state": "picked",
				"picked": {"variant": 2, "direction": "slim and clean"},
			}
		)
	)
	_check(line == "A wooden door: kept 2 · slim and clean. The other ways were discarded.", line)

	# The Review tab shows the same strip above a way's full review.
	var tab: Control = load("res://addons/joinery/review_tab.gd").new()
	tab.set("cli_enabled", false)
	root.add_child(tab)
	tab.call("follow", backend)
	var third: Dictionary = _group(["in_review", "in_review", "in_review"])
	third["group_id"] = "g3"
	for way: Dictionary in third["ways"]:
		way["task_id"] = "r" + str(way["variant"])
		way["transaction_id"] = "rx" + str(way["variant"])
	backend.call("_handle", third)
	for index: int in 3:
		var preview := {
			"kind": "file_preview",
			"task_id": "r%d" % (index + 1),
			"transaction_id": "rx%d" % (index + 1),
			"summary": "Door",
			"media": [{"path": "art/door.png", "media": "image", "after": picture}],
		}
		backend.call("_handle", preview)
	tab.call("show_task", "rx2")
	var review_strip: Control = tab.get("_tasks").get("strip")
	_check(review_strip != null and review_strip.is_visible_in_tree(), "The review shows the strip")
	_check((review_strip.call("ways") as Array).size() == 3, "…with every way")
	var shown: Control = review_strip.call("way", "r2")
	_check(bool(shown.get("chosen")), "The way on show is ringed")
	(review_strip.call("way", "r3").get("use_button") as Button).pressed.emit()
	_check(str(_sent(job, "variants_pick").get("task_id", "")) == "r3", "Use this from the review")

	# "Try other ways" kept the change in review as the "As made" way: its picture opens the
	# main review, under the same ways.
	var main: Control = load("res://addons/joinery/review_panel.gd").new()
	tab.call("set_detail", main)
	tab.call("show_live", {"transaction_id": "lx", "summary": "Door, as made"})
	var kept: Dictionary = _group(["in_review", "in_review", "in_review"])
	kept["group_id"] = "g3"
	for way: Dictionary in kept["ways"]:
		way["task_id"] = "r" + str(way["variant"])
		way["transaction_id"] = "rx" + str(way["variant"])
	(kept["ways"] as Array).push_front(
		{
			"task_id": "lead",
			"variant": 0,
			"direction": "As made",
			"state": "in_review",
			"transaction_id": "lx"
		}
	)
	backend.call("_handle", kept)
	var made: Control = review_strip.call("way", "lead")
	_check(made != null and str(made.call("note_text")) == "As made", "The strip has As made")
	(made.get("_frame") as Button).pressed.emit()
	_check(bool(tab.get("showing_detail")) and main.visible, "As made opens the main review")
	_check(not (tab.get("_tasks").get("panel") as Control).visible, "…in place of the way's")
	_check(review_strip.is_visible_in_tree(), "…under the same ways")
	_check(bool(made.get("chosen")) and not bool(shown.get("chosen")), "…As made ringed")
	(review_strip.call("way", "r2").get("_frame") as Button).pressed.emit()
	_check(not main.visible and bool(shown.get("chosen")), "A task way swaps back")

	tab.queue_free()
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("variants: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
