extends SceneTree
## Journeyman sidebar states for the visual suite, light and dark, narrow and wide.
## Run with --path <project with addons/joinery> --script; captures go to
## JOINERY_CAPTURE_DIR as sidebar-<state>-<theme>-<width>.png. Each theme and width gets a
## fresh dock, and the request states start from a real submit.

## 340 is the narrowest the sidebar is docked at; 480 its usual width; 1000 a wide dock.
const WIDTHS: Array[int] = [340, 480, 1000]
## The width the accent and Reduce motion captures are taken at.
const NARROW := 480
const HEIGHT := 760
## Extra captures at the narrow width only: each accent, and Reduce motion on.
const ACCENTS: Array[String] = ["brass", "blue", "green", "violet"]
const REQUEST := "Make the door a little wider and darken the wood."
const BIBLE := """# Art Bible — Harbour Keeper

## 1. Vision
Calm, a little lonely, handmade. Never glossy or busy.

## 2. Pillars
**Worn**: every edge is chipped. **Hushed**: music only in safe rooms.

## 6. Visual style
- Overall look: stylised PBR, chunky bevels, soft rim light

## 7. Palette (checked)
Warm wood, sea blue accent, no pure black.

## 9. Materials and texel density (checked)
Roughness 0.4–0.95; metallic 0 or 1, only on metal named so.

## 10. 3D rules (checked)
Metres, +Y up; door 2.1 m; prop ≤ 3 000 triangles.

## 16. Audio
- Overall: soft synths, wooden foley

## 19. Never (hard bans)
- Neon, photo-real faces, lens flares
"""
const BRIEF := """# Harbour Keeper

## Pitch
Keep the harbour lights burning through one long winter night.

## Core loop
Walk the pier, refill the lamps, fix what the storm breaks.

## Online
None. One player.
"""

var _checks: GDScript
var _theme: GDScript
var _motion: GDScript
var _todo: GDScript
var _failed := false
## The width being captured; below the dock's own minimum it is the shared (classic) column.
var _width := 0


func _initialize() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	_theme = load("res://addons/joinery/ui_theme.gd")
	_todo = load(get_script().resource_path.get_base_dir().path_join("visual_todo.gd"))
	# Windowed runs animate; captures must show end states.
	_motion = load("res://addons/joinery/ui_motion.gd")
	_motion.set_enabled(false)
	_theme.set_pref("theme", "match")
	_theme.set_pref("accent", "brass")
	_theme.set_pref("reduce_motion", "off")
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count: int = 3) -> void:
	for index: int in count:
		await process_frame


func _run() -> void:
	for light: bool in [false, true]:
		for width: int in WIDTHS:
			await _states(light, width)
	print("visual sidebar: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _build(light: bool) -> Array:
	var host := Control.new()
	host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	host.theme = Theme.new()
	host.theme.set_color("base_color", "Editor", Color("f7f6f3") if light else Color("202123"))
	# Paint the editor ground the sidebar sits on, so captures match the dock.
	var ground := ColorRect.new()
	ground.color = Color("f7f6f3") if light else Color("202123")
	ground.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	ground.mouse_filter = Control.MOUSE_FILTER_IGNORE
	host.add_child(ground)
	root.add_child(host)
	var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	# 340 px is below the dock's own minimum: the narrower column it gets below the Inspector.
	if _width < NARROW:
		chat.min_em = chat.SHARED_MIN_EM
	host.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	# Stand in for the chat bridge, so a submit becomes a request in flight.
	chat.prompt_submitted.connect(func(_text: String, _targets: Array) -> void: pass)
	return [host, chat]


func _shot(state: String, light: bool, width: int, chat: Control) -> void:
	await _frames()
	# A TextEdit settles its height one frame later than everything else.
	await _frames(2)
	var screen := "sidebar-%s-%s-%d" % [state, "light" if light else "dark", width]
	_check(_checks.snapshot(screen, root, [chat]), "Capture " + screen)


func _thumbnail() -> String:
	DirAccess.make_dir_recursive_absolute(OS.get_cache_dir())
	var path := OS.get_cache_dir().path_join("joinery-visual-door.png")
	var image := Image.create(320, 240, false, Image.FORMAT_RGBA8)
	image.fill(Color("3a3d42"))
	image.fill_rect(Rect2i(110, 30, 100, 190), Color("6b4423"))
	image.fill_rect(Rect2i(186, 120, 12, 12), Color("c9a14a"))
	_check(image.save_png(path) == OK, "Save the review thumbnail fixture")
	return path


## A stand-in for the editor's 3D viewport: a floor, a crate and a door frame against the sky.
func _shared_view() -> Dictionary:
	var image := Image.create(640, 360, false, Image.FORMAT_RGBA8)
	image.fill(Color("4b5d6e"))
	image.fill_rect(Rect2i(0, 230, 640, 130), Color("5a5246"))
	image.fill_rect(Rect2i(140, 150, 110, 100), Color("8a6a3c"))
	image.fill_rect(Rect2i(380, 70, 90, 170), Color("6b4423"))
	image.fill_rect(Rect2i(450, 150, 10, 10), Color("c9a14a"))
	var entry: Dictionary = load("res://addons/joinery/chat_attachments.gd").save(
		image, "3D view", "view"
	)
	return {"success": not entry.is_empty(), "entry": entry}


func _states(light: bool, width: int) -> void:
	root.size = Vector2i(width, HEIGHT)
	_width = width
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	var header: Node = chat.get("_header")
	var footer: Node = chat.get("_footer")

	# Connection states before Journeyman is ready.
	chat.set_chat_agent("codex")
	chat.set_chat_state("connecting", "")
	_check(str(header.get("_name").text) == "Connecting…", "Connecting names nobody")
	await _shot("connecting", light, width, chat)
	chat.set_chat_agent("")
	chat.set_chat_state("error", "Journeyman isn't connected.")
	_check(chat.get("connection_state") == "none", "No agent reads as none")
	await _shot("no-agent", light, width, chat)

	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	(
		chat
		. set_model_options(
			[
				{"id": "fixture-sol", "name": "GPT-6.1 Sol", "efforts": ["low", "medium", "high"]},
				{"id": "fixture-luna", "name": "GPT-6 Luna", "efforts": ["low", "high"]},
			]
		)
	)
	_check(not (footer as CanvasItem).visible, "The footer is hidden while idle")
	_check(str(header.get("_name").text).is_empty(), "A healthy header shows no name")
	_check(not str(footer.call("text")).contains("Ready"), "The sidebar never says Ready")
	await _shot("empty", light, width, chat)

	chat.set_resource_target("res://art/props/door_with_long_descriptive_name.glb", "PackedScene")
	await _shot("chips", light, width, chat)

	# "Share my view": the viewport goes with the next message as a thumbnail.
	var composer: Control = chat.get("_composer")
	composer.set("view_provider", _shared_view)
	composer.call("share_view")
	await _shot("shared-view", light, width, chat)
	composer.get("attachments").call("clear")

	var prompt: TextEdit = chat.get("_prompt")
	prompt.text = "/"
	chat.call("_update_commands", "/")
	await _frames(4)
	await _shot("commands", light, width, chat)
	prompt.text = ""
	chat.call("_update_commands", "")

	# A real submit: the request shows at once in its sending state (optimistic).
	chat.call("_on_submit", REQUEST)
	var transcript: Node = chat.get("_transcript")
	var card: Control = transcript.call("current_card")
	_check(card != null and card.get("state") == "sending", "A submit shows a sending card")
	await _shot("sending", light, width, chat)

	chat.set_chat_busy(true)
	chat.show_execution({"name": "GPT-6.1 Sol", "mode": "fast", "effort": "low"})
	chat.set_chat_state("working", "Opening door.blend in Blender")
	chat.set_chat_state("working", "Widening the door")
	chat.set_chat_state("working", "Exporting the model…")
	# Fourteen seconds in, so the card's timer is showing.
	card.call("set_elapsed", 14)
	var timer: Label = card.get("_timer")
	_check(timer.visible and timer.text == "0:14", "A working card shows its timer")
	await _shot("working", light, width, chat)
	# Journeyman handed its change over: Joinery checks it, one quiet status line.
	chat.set_chat_state("working", "Checking the change…")
	await _shot("submitting", light, width, chat)
	# It passed: the reviewer agent reads it before the user sees it.
	chat.set_chat_state("working", "Reviewing the change…")
	await _shot("reviewing", light, width, chat)

	# Messages sent while Journeyman works wait at the end of the transcript.
	chat.call("_on_submit", "Then add a brass handle on the left side.")
	chat.call("_on_submit", "/feature add controller")
	var queue: Node = transcript.get("queue")
	_check(queue.call("texts").size() == 2, "Messages sent while working are queued")
	await _shot("queued", light, width, chat)
	# After a stopped turn the queue waits for the user.
	queue.call("pause")
	await _shot("queued-paused", light, width, chat)
	for index: int in 2:
		queue.call("remove", queue.call("first"))
	await _parallel(light, width, chat)
	await _playtest(light, width, chat)
	await _todo.shots(self, light, width, chat)
	await _match(light, width, chat)
	await _check_cards(light, width, chat)
	await _design(light, width, chat)
	await _sound_pass(light, width, chat)

	chat.append_agent_text("I widened **Door** by 20% in Blender and darkened the wood.", true)
	chat.finish_agent_response(true)
	chat.set_chat_busy(false)
	var still := _thumbnail()
	# A bigger change (several assets) offers Review on the card; a small one Accept.
	(
		chat
		. show_file_review(
			"Widen the door and add a creak",
			"",
			[
				{"path": "art/props/door.glb", "media": "image", "after": still},
				{"path": "audio/sfx/door_creak.wav", "media": "audio", "after": still},
			],
			[
				{"path": "art/props/door.glb", "change": "modified"},
				{"path": "audio/sfx/door_creak.wav", "change": "created"},
			],
		)
	)
	await _shot("review-waiting-big", light, width, chat)
	(
		chat
		. show_file_review(
			"Widen the door",
			"~ art/props/door.blend\n~ art/props/door.glb",
			[{"path": "art/props/door.glb", "media": "image", "after": still}],
		)
	)
	_check(str(footer.call("text")).contains("Waiting for your review"), "Footer waits")
	await _shot("review-waiting", light, width, chat)

	chat.call("_on_accept")
	chat.hide_edit_review("Change accepted", "success")
	chat.show_timing({"outcome": "done", "total_ms": 72000, "first_response_ms": 250})
	await _shot("accepted", light, width, chat)

	chat.show_agent_message(
		(
			"The **Door** is 20% wider and the `Wood` material is darker. "
			+ "The source file and the exported model both changed."
		)
	)
	await _shot("conversation", light, width, chat)

	# Joinery's own lines: a fresh session from a summary, and a change that didn't pass.
	(
		chat
		. call(
			"_on_notice",
			(
				"The conversation was getting long, so Journeyman continues in a fresh "
				+ "session from a short summary."
			),
			"info",
		)
	)
	await _shot("compacted", light, width, chat)
	(
		chat
		. call(
			"_on_notice",
			(
				"Journeyman's change didn't pass Joinery's checks: scenes/room.tscn uses "
				+ "art/models/lamp.glb, which isn't in the project or in this change. "
				+ "Nothing was changed."
			),
			"error",
		)
	)
	await _shot("check-failed", light, width, chat)

	# A request that never reached Journeyman says so in place, with Retry.
	chat.call("_on_submit", "Now add a brass handle on the left side.")
	card = transcript.call("current_card")
	if card != null:
		card.call("set_failed", "Lost connection to Codex.")
	await _shot("not-sent", light, width, chat)

	chat.set_chat_state("error", "Lost connection to Codex.")
	_check((header.get("_banner") as CanvasItem).visible, "A lost connection shows the banner")
	await _shot("error", light, width, chat)
	chat.set_chat_state("connected", "Codex connected")
	host.queue_free()
	await _frames(2)
	await _images(light, width)
	await _changes(light, width)
	await _saved_chats(light, width)
	await _variants(light, width)
	built = _build(light)
	host = built[0]
	chat = built[1]
	await _frames()
	chat.set_chat_state("connected", "Codex connected")

	if width == NARROW:
		for accent: String in ACCENTS:
			_check(_theme.set_pref("accent", accent) == OK, "Save accent " + accent)
			await _frames(2)
			await _shot("accent-" + accent, light, width, chat)
		_theme.set_pref("accent", "brass")

	var settings: Node = chat.get("_settings")
	settings.call("set_open", true)
	await _shot("settings", light, width, chat)
	settings.call("_ask_compact", true)
	await _shot("settings-compact", light, width, chat)
	settings.call("_ask_compact", false)
	# Older versions: a smaller choice says what it removes; Custom asks for two numbers.
	var saved := {"keep": 50, "max_mb": 2048, "preset": "keep-50", "label": "Last 50 changes"}
	settings.set("_policy", saved)
	(settings.get("_policy_picker") as OptionButton).select(2)
	settings.set("_pending_policy", {"keep": 1, "max_mb": 1024})
	settings.call("_policy_checked", {"pruned": 3, "freed_bytes": 120_000_000})
	await _frames()
	(settings as ScrollContainer).scroll_vertical = 100000
	await _shot("settings-policy", light, width, chat)
	(settings.get("_policy_picker") as OptionButton).select(3)
	settings.call("_choose_policy", 3)
	await _frames()
	(settings as ScrollContainer).scroll_vertical = 100000
	await _shot("settings-policy-custom", light, width, chat)
	settings.call("_close_policy")
	# The Project section's last switches: the Reviewer agent and Complex models in Blender.
	var models: Node = settings.find_child("ComplexModelsSetting", true, false)
	_check(models != null, "Settings shows Complex models in Blender")
	if models != null:
		models.call("apply_status", {"complex_models": "blender", "blender": true})
	await _frames()
	(settings as ScrollContainer).scroll_vertical = 100000
	await _shot("settings-models", light, width, chat)
	(settings as ScrollContainer).scroll_vertical = 0
	if width == NARROW:
		_theme.set_pref("reduce_motion", "on")
		await _shot("reduce-motion", light, width, chat)
		_theme.set_pref("reduce_motion", "off")
		_motion.set_enabled(false)
	settings.call("set_open", false)

	host.queue_free()
	await _frames(2)


## Images with a message: the shared view, a pasted screenshot and a chosen file in the
## composer, then on the sent request's card.
## Agents working alongside Journeyman: a card per task, the header's count, Run alongside in
## the composer, and Stop asking whether to stop the tasks too.
func _parallel(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	backend.set("parallel", 4)
	tasks.set("bridge", backend)
	# A task just started: its card names the provider until its first step.
	var starting := {"kind": "task", "task_id": "t0", "title": "Make a side table"}
	starting["state"] = "working"
	starting["step"] = "Starting Codex CLI…"
	tasks.call("on_event", starting)
	await _shot("parallel-starting", light, width, chat)
	tasks.call("_remove", "t0")
	var lamp := {"kind": "task", "task_id": "t1", "title": "Make a desk lamp", "state": "working"}
	lamp["apps"] = ["blender"]
	tasks.call("on_event", lamp)
	tasks.call(
		"on_event",
		{"kind": "progress", "task_id": "t1", "text": "Bevelling the lamp shade in Blender"}
	)
	tasks.call("card", "t1").call("set_elapsed", 38)
	var rug := {"kind": "task", "task_id": "t2", "title": "Paint a rug texture", "state": "working"}
	rug["apps"] = ["krita"]
	tasks.call("on_event", rug)
	rug["state"] = "in_review"
	rug["transaction_id"] = "fixture"
	tasks.call("on_event", rug)
	var prompt: TextEdit = chat.get("_prompt")
	prompt.text = "Add a small side table"
	prompt.text_changed.emit()
	var header: Node = chat.get("_header")
	_check(header.call("agents_text") == "2 agents working", "The header counts the agents")
	var alongside: Button = chat.get("_composer").get("alongside_button")
	_check(alongside.visible, "Run alongside is offered while Journeyman works")
	await _shot("parallel", light, width, chat)
	chat.call("_request_cancel")
	await _shot("parallel-stop", light, width, chat)
	prompt.text = ""
	tasks.call("_drop_offer")
	await _debugging(light, width, chat, tasks, backend)
	for id: String in ["t1", "t2"]:
		tasks.call("_remove", id)
	tasks.set("bridge", null)
	backend.free()
	var composer: Node = chat.get("_composer")
	composer.get("stop").disabled = false


## Debugging: the offer after a new error in Output, and the "Debugging: <error>" card.
func _debugging(light: bool, width: int, chat: Control, tasks: Node, backend: Node) -> void:
	for id: String in ["t1", "t2"]:
		tasks.call("_remove", id)
	backend.set("_connected", true)
	var watcher: Node = tasks.find_child("Debugging", false, false)
	var error := "SCRIPT" + " ERROR: Invalid call. Nonexistent function 'jump' in base 'Player'."
	watcher.call("take_output", error + "\n   at: _physics_process (res://player.gd:42)\n")
	var debug := {"kind": "task", "task_id": "d1", "title": "Debugging: " + error.substr(14)}
	debug.merge({"role": "debugger", "error": error, "state": "working", "origin": "debug"})
	debug["step"] = "Reproducing the error offline…"
	tasks.call("on_event", debug)
	tasks.call("card", "d1").call("set_elapsed", 21)
	await _shot("debugging", light, width, chat)
	watcher.call("_drop_offer")
	tasks.call("_remove", "d1")
	backend.set("_connected", false)


## The playtester: its card while the bot plays, then the findings with one opened.
func _playtest(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(get_script().resource_path.get_base_dir() + "/playtest_fixture.gd")
	var event := {"kind": "playtest", "playtest_id": "pt", "state": "playing", "seconds": 120}
	event["elapsed"] = 42.0
	tasks.call("on_playtest", event)
	await _shot("playtest", light, width, chat)
	var items: Array = [
		{
			"severity": "high",
			"title": "Softlock",
			"detail": "The player couldn't move for 15 s while the bot held movement keys.",
			"where": "res://game/player/player.gd:88",
			"t": 65.0,
			"seed": 4821,
			"repro": "Run `joinery playtest --project . --seed 4821 --seconds 120`.",
			"shot": _thumbnail(),
		},
		{
			"severity": "medium",
			"title": "Frame hitches",
			"detail": "3 frames took far longer than usual (worst 310 ms; typical 2.10 ms).",
			"t": 4.0,
			"seed": 4821,
		},
		{
			"severity": "note",
			"title": "Balance",
			"detail": "The bot died or respawned 9 times in 2:00 without finishing.",
			"seed": 4821,
		},
	]
	event["state"] = "done"
	event["report"] = {
		"counts": {"critical": 0, "high": 1, "medium": 1, "low": 0, "note": 1},
		"items": items,
		"seed": 4821,
		"played_seconds": 120.0,
		"coverage": fixture.call("coverage"),
	}
	tasks.call("on_playtest", event)
	var card: Control = tasks.get("playtest")
	var first: Control = (card.call("item_rows") as Array)[0]
	(first.find_children("", "Button", true, false)[0] as Button).button_pressed = true
	await _shot("playtest-report", light, width, chat)
	card.free()
	# The last five runs' heat on the same map, time spent on too.
	tasks.set("playtest", null)
	event["report"]["heatmap"] = fixture.call("heat")
	tasks.call("on_playtest", event)
	card = tasks.get("playtest")
	card.call("show_layer", "time", true)
	await _shot("playtest-heat", light, width, chat)
	card.free()
	tasks.set("playtest", null)
	tasks.hide()


## A request tried three ways: the strip while the ways work (one waiting for an agent), then
## side by side with Use this, Mix and Discard all, then the line once one is kept.
func _variants(light: bool, width: int) -> void:
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	chat.call("_on_submit", "/variants 3 A wooden door for the cabin")
	var tasks: Node = chat.get("_tasks")
	var notes := ["chunky and worn", "slim and clean", "ornate and detailed"]
	var group := {"kind": "variants", "group_id": "g", "title": "A wooden door", "ways": []}
	group["state"] = "working"
	var variants: Node = tasks.get("variants")
	for index: int in 3:
		var way := {"task_id": "v%d" % index, "variant": index + 1, "direction": notes[index]}
		way["state"] = "queued" if index == 2 else "working"
		group["ways"].append(way)
	variants.call("on_group", group)
	var steps := ["Bevelling the frame in Blender", "Starting Codex CLI…"]
	for index: int in 2:
		var event := {"kind": "task", "task_id": "v%d" % index, "variant_group": "g"}
		event.merge({"state": "working", "step": steps[index]})
		tasks.call("on_event", event)
	await _shot("variants-working", light, width, chat)
	var shapes := [
		[70, 180, Color("5a3a22")], [40, 200, Color("8a6a4a")], [60, 190, Color("6b4423")]
	]
	for index: int in 3:
		var image := Image.create(320, 240, false, Image.FORMAT_RGBA8)
		image.fill(Color("3a3d42"))
		var shape: Array = shapes[index]
		var door := Rect2i(160 - int(shape[0]), 30, int(shape[0]) * 2, int(shape[1]))
		image.fill_rect(door, shape[2])
		if index == 2:
			image.fill_rect(door.grow(-14), Color("7d5230"))
		var path := OS.get_cache_dir().path_join("joinery-visual-way-%d.png" % index)
		image.save_png(path)
		group["ways"][index]["state"] = "in_review"
		group["ways"][index]["transaction_id"] = "w%d" % index
		var preview := {
			"kind": "file_preview", "task_id": "v%d" % index, "transaction_id": "w%d" % index
		}
		preview["media"] = [{"path": "art/props/door.glb", "media": "image", "after": path}]
		preview["facts"] = [["door 40% wider", "door 20% narrower", "carved panels"][index]]
		preview["changes"] = [{"path": "art/props/door.glb", "change": "modified"}]
		tasks.call("on_event", preview)
	variants.call("on_group", group)
	await _shot("variants", light, width, chat)
	group["state"] = "picked"
	group["picked"] = "v1"
	variants.call("on_group", group)
	await _shot("variants-picked", light, width, chat)
	host.queue_free()
	await _frames(2)


func _images(light: bool, width: int) -> void:
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	var composer: Control = chat.get("_composer")
	composer.set("view_provider", _shared_view)
	composer.call("share_view")
	var screenshot := Image.create(480, 300, false, Image.FORMAT_RGBA8)
	screenshot.fill(Color("2b2d30"))
	screenshot.fill_rect(Rect2i(40, 40, 400, 220), Color("6cc38a"))
	composer.get("prompt").call("paste_clipboard", "", screenshot)
	composer.call("attach_files", PackedStringArray([_thumbnail()]))
	var prompt: TextEdit = chat.get("_prompt")
	prompt.text = "Match the door to my sketch and keep this lighting."
	await _shot("attachments", light, width, chat)
	prompt.text_submitted.emit(prompt.text)
	chat.set_chat_busy(true)
	chat.set_chat_state("working", "Looking at your view")
	await _shot("image-sent", light, width, chat)

	# The context ring in the header, and its hover card as the tooltip shows it.
	chat.append_agent_text("The door now matches your sketch.", true)
	chat.finish_agent_response(true)
	chat.set_chat_busy(false)
	var meter: Control = chat.get("_header").get("usage")
	(
		meter
		. call(
			"show_usage",
			{
				"model_name": "GPT-6.1 Sol",
				"context_used": 61_400,
				"context_window": 272_000,
				"input_tokens": 182_000,
				"output_tokens": 6_300,
				"cached_tokens": 140_000,
			}
		)
	)
	await _frames()
	var card := PanelContainer.new()
	card.theme_type_variation = "JoineryHoverCard"
	card.theme = chat.theme
	card.add_child(meter.call("card"))
	host.add_child(card)
	await _frames()
	card.size = card.get_combined_minimum_size()
	var corner := meter.get_global_rect()
	card.global_position = Vector2(
		maxf(8.0, corner.end.x - card.get_combined_minimum_size().x), corner.end.y + 4.0
	)
	await _shot("usage", light, width, chat)
	host.queue_free()
	await _frames(2)


## Change history and the sidebar's offers: a feature plan, the art gate, "Too old to
## revert", "Reverting…", a discard, the app readiness line, an offer and the caption.
func _changes(light: bool, width: int) -> void:
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	(
		chat
		. call(
			"_on_readiness",
			{
				"tools":
				[
					{
						"name": "Blender",
						"status": "needs_docker",
						"summary": "Blender runs offline in Docker.",
						"action": {"id": "start_docker", "label": "Start Docker"},
					}
				]
			}
		)
	)
	(
		chat
		. call(
			"_on_suggestions",
			[
				{
					"text": "The brief says online play, but online play is not added.",
					"command": "/feature add online-play",
				}
			]
		)
	)
	(
		chat
		. call(
			"_on_feature_planned",
			{
				"action": "add",
				"feature": "controller",
				"changes":
				[
					"features/controller/bindings.gd",
					"features/controller/controller.gd",
					"features/controller/controls_page.gd",
					"joinery.toml",
					"project.godot",
					"tests/features/controller/controller_test.gd",
				],
				"config_edits":
				[
					{"section": "autoload", "key": "Controller", "before": null, "after": "x"},
					{"section": "input", "key": "move_left", "before": "a", "after": "b"},
					{"section": "input", "key": "move_right", "before": "a", "after": "b"},
				],
				"notes": [],
				"conflicts": [],
			}
		)
	)
	await _shot("feature-plan", light, width, chat)

	var transcript: Node = chat.get("_transcript")
	# An old accepted change, a revert on its way, and a discard.
	for item: Array in [["Darken the floor", "tx-old"], ["Widen the door", "tx-new"]]:
		chat.call("_on_submit", item[0])
		chat.set_chat_busy(true)
		chat.show_file_review(item[0], "", [], [], item[1])
		chat.set_chat_busy(false)
		chat.call("_on_accept")
		chat.hide_edit_review("Change accepted", "success")
	chat.call("_on_revertible", {"tx-old": false, "tx-new": true})
	var newest: Control = transcript.call("current_card")
	_check(newest.get("_revert").visible, "A recent change offers Revert")
	newest.get("_revert").pressed.emit()
	chat.call("_on_submit", "Add a creak to the door")
	chat.set_chat_busy(true)
	chat.show_file_review("Add a creak", "", [], [], "tx-creak")
	chat.set_chat_busy(false)
	chat.call("_on_reject")
	chat.hide_edit_review("Change rejected. Original restored.", "rejected")
	await _shot("history", light, width, chat)

	chat.call("_on_submit", "Make the door look older")
	chat.set_chat_busy(true)
	chat.set_chat_state("working", "Opening door.blend in Blender")
	chat.call("_on_art_gate", {"needs_art_approval": true})
	chat.set_chat_busy(false)
	chat.show_status("Focus mode on", 30.0, "Undo", func() -> void: pass)
	await _shot("art-gate", light, width, chat)
	chat.show_status("")
	if width == WIDTHS[-1]:
		await _documents(light, width, chat)
	host.queue_free()
	await _frames(2)


## The art direction and the game brief in the document view, at the wide size only.
func _documents(light: bool, width: int, chat: Control) -> void:
	var dialogs: GDScript = load("res://addons/joinery/art_review_dialog.gd")
	var picture := _thumbnail()
	var reference := "art/reference/door_sketch.png"
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://art/reference"))
	DirAccess.copy_absolute(picture, ProjectSettings.globalize_path("res://" + reference))
	var event := {
		"text": BIBLE,
		"approved": false,
		"version": 1,
		"references":
		[
			{
				"id": "R1",
				"reference": "my door sketch",
				"kind": "sketch",
				"take": "the brass handle",
				"avoid": "the heavy frame",
				"file": reference,
			},
			{
				"id": "R2",
				"reference": "Hob",
				"kind": "game",
				"take": "chunky silhouettes",
				"avoid": "its saturated greens",
				"file": "",
			},
		],
		"suggestions":
		[
			"Before approving the bible: the palette section is not set yet. Ask about it.",
			"When adding sound: the audio section is open. Ask about it.",
		],
	}
	var size := Vector2i(roundi(width * 0.8), roundi(HEIGHT * 0.9))
	var dialog: Window = dialogs.open(chat, event, size, func() -> void: pass)
	await _frames(4)
	var screen := "sidebar-art-direction-%s-%d" % ["light" if light else "dark", width]
	_check(_checks.snapshot(screen, root, [dialog]), "Capture " + screen)
	# The references board and the folded technical rules, further down.
	var scroll := dialog.find_children("*", "ScrollContainer", true, false)[0] as ScrollContainer
	var technical: Node = dialog.find_child("Technical", true, false)
	technical.call("set_open", true)
	await _frames(2)
	scroll.scroll_vertical = int(dialog.find_child("References", true, false).position.y)
	await _frames(2)
	screen = "sidebar-art-board-%s-%d" % ["light" if light else "dark", width]
	_check(_checks.snapshot(screen, root, [dialog]), "Capture " + screen)
	dialog.hide()
	dialog.queue_free()
	await _frames(2)
	var brief: Window = dialogs.open_document(
		chat, "Game brief", BRIEF, size, "Change the game brief: "
	)
	await _frames(4)
	screen = "sidebar-brief-%s-%d" % ["light" if light else "dark", width]
	_check(_checks.snapshot(screen, root, [brief]), "Capture " + screen)
	brief.hide()
	brief.queue_free()
	await _frames(2)


## A saved chat, written as the chat host writes it ([param seconds_ago]: its last turn).
func _save_chat(chat_id: String, meta: Dictionary, entries: Array, seconds_ago: int) -> void:
	var folder := ProjectSettings.globalize_path("res://.joinery/chats")
	DirAccess.make_dir_recursive_absolute(folder)
	var updated := Time.get_datetime_string_from_unix_time(
		int(Time.get_unix_time_from_system()) - seconds_ago
	)
	var lines := PackedStringArray(
		[JSON.stringify(meta.merged({"type": "chat", "id": chat_id, "updated": updated}))]
	)
	for entry: Dictionary in entries:
		lines.append(JSON.stringify(entry))
	var file := FileAccess.open(folder.path_join(chat_id + ".jsonl"), FileAccess.WRITE)
	file.store_string("\n".join(lines) + "\n")
	file.close()


## Saved chats: the latest one back after the editor reopened, the Chats list (and renaming a
## chat in it), and an older chat open read-only with Continue.
func _saved_chats(light: bool, width: int) -> void:
	var accepted := "a".repeat(32)
	var reverted := "b".repeat(32)
	_save_chat(
		"20261110-090000-aaaaaa",
		{"title": REQUEST, "provider": "codex", "handle": "thread-1"},
		[
			{"type": "user", "text": REQUEST},
			{"type": "step", "text": "Opening door.blend in Blender"},
			{"type": "step", "text": "Exporting the model…"},
			{
				"type": "agent",
				"text": "I widened **Door** by 20% in Blender and darkened the wood."
			},
			{"type": "change", "transaction_id": accepted, "state": "accepted"},
			{"type": "turn", "outcome": "done", "total_ms": 72000},
			{"type": "user", "text": "Then add a brass handle on the left side."},
			{"type": "agent", "text": "The handle is on the left, in brass."},
			{"type": "change", "transaction_id": reverted, "state": "accepted"},
			{"type": "turn", "outcome": "done", "total_ms": 41000},
		],
		7200
	)
	_save_chat(
		"20261107-090000-bbbbbb",
		{"title": "Make the harbour lamps flicker in the wind", "provider": "claude"},
		[
			{"type": "user", "text": "Make the harbour lamps flicker in the wind"},
			{"type": "agent", "text": "The lamps flicker now, faster when the wind rises."},
			{"type": "change", "transaction_id": "c".repeat(32), "state": "accepted"},
			{"type": "turn", "outcome": "done", "total_ms": 38000},
		],
		3 * 86400
	)
	_save_chat(
		"20261101-090000-cccccc",
		{"title": "Rope coils for the pier", "provider": "gemini"},
		[{"type": "user", "text": "Rope coils for the pier"}],
		9 * 86400
	)
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	var sessions: Node = chat.get("_chats")
	# Nothing runs in the background; the states come from here.
	sessions.set("runner", func(_args: PackedStringArray, _done: Callable) -> void: pass)
	sessions.call("restore_latest")
	(
		sessions
		. call(
			"show_states",
			{
				"transactions":
				[
					{"id": accepted, "state": "accepted", "revertible": true},
					{"id": reverted, "state": "reverted", "revertible": false},
				]
			}
		)
	)
	_check(sessions.call("current_id") == "20261110-090000-aaaaaa", "The latest chat is back")
	await _shot("chat-restored", light, width, chat)
	sessions.call("open_list")
	await _shot("chats", light, width, chat)
	var page: Node = sessions.get("page")
	var row: Node = page.call("row_for", "20261107-090000-bbbbbb")
	row.call("begin_rename")
	await _shot("chats-rename", light, width, chat)
	row.get("_field").call("hide")
	row.get("title").call("show")
	sessions.call("open_chat", "20261107-090000-bbbbbb")
	sessions.call("show_states", {"transactions": []})
	_check(sessions.get("viewing"), "An older chat opens read-only")
	await _shot("chat-earlier", light, width, chat)
	host.queue_free()
	await _frames(2)
	var folder := ProjectSettings.globalize_path("res://.joinery/chats")
	for file_name: String in DirAccess.get_files_at(folder):
		DirAccess.remove_absolute(folder.path_join(file_name))


## Make it match: the scores and the top findings, then its fixes running as tasks.
func _match(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("match_fixture.gd")
	)
	var matcher: Node = tasks.get_node("Match")
	matcher.set(
		"audit_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("audit")
	)
	matcher.call("open")
	var card: Control = tasks.get("match_card")
	_check(card != null, "The match card shows")
	await _shot("match", light, width, chat)
	var rows: Array = fixture.call("run_rows")
	card.call("apply_run", {"state": "fixing", "tasks": rows})
	var apps := {"palette": ["krita"], "lighting": ["godot"], "sound": ["supercollider"]}
	for row: Dictionary in rows:
		var event := {"kind": "task", "task_id": row["task_id"], "title": row["title"]}
		event.merge({"state": "working", "apps": apps[row["area"]], "origin": "match"})
		tasks.call("on_event", event)
		if row["state"] == "in_review":
			event.merge({"state": "in_review", "transaction_id": "fixture"}, true)
			tasks.call("on_event", event)
	await _shot("match-fixing", light, width, chat)
	for row: Dictionary in rows:
		tasks.call("_remove", row["task_id"])
	card.free()
	tasks.set("match_card", null)
	tasks.call("sync_visible")


## The design check: its findings at a glance, the agent reading, and a sound game.
func _design(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("design_fixture.gd")
	)
	var designer: Node = tasks.get_node("Design")
	designer.set(
		"check_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("report")
	)
	designer.call("open")
	var card: Control = tasks.get("design_card")
	_check(card != null, "The design card shows")
	await _shot("design", light, width, chat)
	card.call("show_report", fixture.call("report", true), false)
	card.call("set_reading")
	await _shot("design-reading", light, width, chat)
	card.call("show_report", fixture.call("clean"), true)
	await _shot("design-clean", light, width, chat)
	card.free()
	tasks.set("design_card", null)
	tasks.call("sync_visible")


## The accessibility and performance checks: each card at a glance, the fixes as tasks.
func _check_cards(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("checks_fixture.gd")
	)
	for check: String in ["access", "perf"]:
		var checker: Node = tasks.get_node("Access" if check == "access" else "Perf")
		checker.set(
			"audit_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call(check)
		)
		checker.call("open")
		var card: Control = tasks.get("%s_card" % check)
		_check(card != null, "The %s card shows" % check)
		await _shot(check, light, width, chat)
		if check == "access":
			var rows: Array = fixture.call("run_rows")
			card.call("apply_run", {"state": "fixing", "tasks": rows})
			for row: Dictionary in rows:
				var event := {"kind": "task", "task_id": row["task_id"], "title": row["title"]}
				event.merge({"state": "working", "apps": ["godot"], "origin": "user"})
				tasks.call("on_event", event)
			await _shot("access-fixing", light, width, chat)
			for row: Dictionary in rows:
				tasks.call("_remove", row["task_id"])
		card.free()
		tasks.set("%s_card" % check, null)
		tasks.call("sync_visible")


## The sound pass: what the level is missing, its rows following the sounds being made.
func _sound_pass(light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var sounder: Node = tasks.get_node("SoundPass")
	sounder.call("_open")
	var card: Control = tasks.get("sound_card")
	_check(card != null, "The sound pass card shows")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("sound_pass_fixture.gd")
	)
	card.call("apply", fixture.call("making"))
	await _shot("sound-pass", light, width, chat)
	card.free()
	tasks.set("sound_card", null)
	tasks.call("sync_visible")
