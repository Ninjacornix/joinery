extends SceneTree
## Journeyman sidebar states for the visual suite, light and dark, narrow and wide.
## Run with --path <project with addons/joinery> --script; captures go to
## JOINERY_CAPTURE_DIR as sidebar-<state>-<theme>-<width>.png. Each theme and width gets a
## fresh dock, and the request states start from a real submit.

const WIDTHS: Array[int] = [480, 1000]
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
var _failed := false


func _initialize() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	_theme = load("res://addons/joinery/ui_theme.gd")
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

	chat.append_agent_text("I widened **Door** by 20% in Blender and darkened the wood.", true)
	chat.finish_agent_response(true)
	chat.set_chat_busy(false)
	var still := _thumbnail()
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
	await _changes(light, width)
	built = _build(light)
	host = built[0]
	chat = built[1]
	await _frames()
	chat.set_chat_state("connected", "Codex connected")

	if width == WIDTHS[0]:
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
	if width == WIDTHS[0]:
		_theme.set_pref("reduce_motion", "on")
		await _shot("reduce-motion", light, width, chat)
		_theme.set_pref("reduce_motion", "off")
		_motion.set_enabled(false)
	settings.call("set_open", false)

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
