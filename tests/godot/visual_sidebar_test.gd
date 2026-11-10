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


func _states(light: bool, width: int) -> void:
	root.size = Vector2i(width, HEIGHT)
	var built := _build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await _frames()
	var header: Node = chat.get("_header")
	var footer: Node = chat.get("_footer")

	# Connection states before an assistant is ready.
	chat.set_chat_agent("codex")
	chat.set_chat_state("connecting", "")
	await _shot("connecting", light, width, chat)
	chat.set_chat_agent("")
	chat.set_chat_state("error", "No assistant connected.")
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
	_check(not str(footer.call("text")).contains("Ready"), "The sidebar never says Ready")
	await _shot("empty", light, width, chat)

	chat.set_resource_target("res://art/props/door_with_long_descriptive_name.glb", "PackedScene")
	await _shot("chips", light, width, chat)

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

	# A request that never reached the assistant says so in place, with Retry.
	chat.call("_on_submit", "Now add a brass handle on the left side.")
	card = transcript.call("current_card")
	if card != null:
		card.call("set_failed", "Lost connection to Codex.")
	await _shot("not-sent", light, width, chat)

	chat.set_chat_state("error", "Lost connection to Codex.")
	_check((header.get("_banner") as CanvasItem).visible, "A lost connection shows the banner")
	await _shot("error", light, width, chat)
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
	if width == WIDTHS[0]:
		_theme.set_pref("reduce_motion", "on")
		await _shot("reduce-motion", light, width, chat)
		_theme.set_pref("reduce_motion", "off")
		_motion.set_enabled(false)
	settings.call("set_open", false)

	host.queue_free()
	await _frames(2)
