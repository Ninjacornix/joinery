extends SceneTree
## Project memory and project commands in the sidebar (ADR 0077): "/remember <text>" sends one
## line to the chat host (never writes here), with nothing to remember it says how, and without
## Journeyman it says why; "Remember this" on a reply puts "/remember …" in the message box. A
## project's own commands show under "This project" in the menu; one with every value goes
## straight to Journeyman, one missing a value opens its form, a wrong value comes back on the
## form, and Ask Journeyman sends the filled-in request; the real CLI lists and fills them in.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const SLASH := preload("res://addons/joinery/slash_commands.gd")
const REMEMBER := preload("res://addons/joinery/chat_remember.gd")
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
const ENEMY := {
	"name": "new-enemy",
	"title": "New enemy",
	"description": "Add an enemy with a behaviour",
	"parameters":
	[
		{"name": "name", "type": "string", "description": "What it's called", "required": true},
		{
			"name": "behaviour",
			"type": "choice",
			"choices": ["patrol", "chase"],
			"default": "patrol",
			"required": false
		},
		{"name": "speed", "type": "number", "default": 2.5, "required": false},
		{"name": "boss", "type": "boolean", "default": false, "required": false},
	],
}
const TOML := """title = "New room"
description = "Add a room"
prompt = "Add a {{theme}} room."

[[parameters]]
name = "theme"
"""

var _failed := false
var _calls: Array[PackedStringArray] = []
var _sent_requests: Array[String] = []


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


func _cli(args: PackedStringArray) -> Dictionary:
	_calls.append(args)
	if args.has("list"):
		return {"commands": [ENEMY], "problems": []}
	var params := []
	for index in args.size():
		if args[index] == "--param":
			params.append(args[index + 1])
	if params.has("speed=fast"):
		return {"error": "speed is a number."}
	return {"name": "new-enemy", "request": "Make an enemy: " + ", ".join(params)}


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png and check its controls, when asked (a
## windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.4).timeout
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	image.save_png("%s-%s.png" % [prefix, state])
	var checks: GDScript = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	var found: Array[String] = checks.call("run", "command-" + state, [root], Rect2(), image, root)
	_check(found.is_empty(), "%s: %s" % [state, "; ".join(found)])


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		root.size = Vector2i(420, 760)
		var config := OS.get_user_data_dir().path_join("project-commands-test-ui.cfg")
		var settings := ConfigFile.new()
		settings.set_value("appearance", "theme", theme)
		settings.save(config)
		Engine.set_meta("joinery_ui_config", config)
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
	dock.connect(
		"prompt_submitted", func(text: String, _targets: Array) -> void: _sent_requests.append(text)
	)
	var tasks: Control = dock.get("_tasks")

	# "/remember <text>": one line to the host, which makes it a change in Review.
	dock.call("run_command", "/remember Signals are snake_case.")
	await process_frame
	_check(
		_sent(job, "remember").get("text", "") == "Signals are snake_case.",
		"/remember sends the line: %s" % _sent(job, "remember")
	)
	_check(not FileAccess.file_exists("res://JOURNEYMAN.md"), "Nothing is written here")
	var before: int = (job.get("lines") as Array).size()
	dock.call("run_command", "/remember")
	await process_frame
	_check((job.get("lines") as Array).size() == before, "Nothing to remember: nothing sent")
	_check(
		str(dock.get("_transcript").call("caption_text")).begins_with("Type /remember"),
		"It says how: %s" % dock.get("_transcript").call("caption_text")
	)

	# "Remember this" on a reply: the words go in the message box first.
	_check(
		REMEMBER.words("", "\n  The player is 1.8 m tall.\nMore.") == "The player is 1.8 m tall.",
		"The reply's first line"
	)
	_check(REMEMBER.words(" jump\n height ", "x") == "jump height", "Or the selection")
	dock.call("append_agent_text", "Doors open outwards in this game.", true)
	await process_frame
	var reply := _first_reply(dock.get("_transcript"))
	_check(reply != null and reply.context_menu_enabled, "A reply has a right-click menu")
	var menu := reply.get_menu()
	var at := menu.get_item_index(REMEMBER.MENU_ID)
	_check(at >= 0 and menu.get_item_text(at) == "Remember this", "With Remember this")
	menu.id_pressed.emit(REMEMBER.MENU_ID)
	await process_frame
	var composer_text := str(dock.get("_prompt").get("text"))
	_check(
		composer_text == "/remember Doors open outwards in this game.",
		"Remember this fills the message box: " + composer_text
	)
	dock.get("_prompt").call("clear")

	# Project commands: listed under "This project".
	var own: Node = tasks.get_node("ProjectCommands")
	own.set("runner", _cli)
	own.set("_signature", "")
	own.call("_cli", ["list", "--json"], own.take)
	_check(SLASH.project.has("/new-enemy"), "The project's command is known")
	_check(SLASH.resolve("/new-en") == "/new-enemy", "And completes")
	var popup: Control = dock.get("_command_popup")
	popup.call("open_for", "/", true)
	await process_frame
	_check((popup.get("matches") as Array).has("/new-enemy"), "The menu lists it")
	_check(_has_label(popup, "This project"), "Under This project")
	popup.call("close")
	popup.call("open_for", "/ne")
	await process_frame
	await _capture("menu")
	popup.call("close")

	# Every value given: straight to Journeyman.
	dock.call("run_command", "/new-enemy Bat")
	await process_frame
	_check(_calls[-1].has("render") and _calls[-1].has("name=Bat"), "It fills in the request")
	_check(_sent_requests == ["Make an enemy: name=Bat"], "And sends it: %s" % [_sent_requests])
	_check(tasks.get("command_card") == null, "No form needed")

	# A value missing: the form, defaults in.
	dock.call("run_command", "/new-enemy")
	await process_frame
	var card: Control = tasks.get("command_card")
	_check(card != null and tasks.visible, "The form opens")
	_check((card.get("title_label") as Label).text == "New enemy", "Titled")
	var values: Dictionary = card.call("values")
	_check(
		values == {"behaviour": "patrol", "speed": "2.5", "boss": false},
		"Defaults are filled in: %s" % values
	)
	var fields := card.find_children("Field_*", "", true, false)
	_check(fields.size() == 4, "One field per parameter: %d" % fields.size())
	await _capture("card")
	var speed: LineEdit = card.find_child("Field_speed", true, false).get("control")
	speed.text = "fast"
	var name_field: LineEdit = card.find_child("Field_name", true, false).get("control")
	name_field.text = "Bat"
	(card.get("ask") as Button).pressed.emit()
	await process_frame
	_check(card.get("_error").visible, "A wrong value comes back on the form")
	_check(_sent_requests.size() == 1, "And nothing is sent")
	await _capture("card-error")
	speed.text = "4"
	name_field.text_submitted.emit("Bat")
	await process_frame
	await process_frame
	# The first request is still on its way, so this one queues behind it like a typed one.
	var asked := _asked(dock)
	_check(asked.size() == 2, "Enter asks: %s" % [asked])
	_check(asked[-1].contains("speed=4"), "With the form's values")
	_check(tasks.get("command_card") == null, "And the form steps out")

	await _real_cli(own, dock)
	backend.queue_free()
	dock.queue_free()
	await process_frame
	SLASH.project = {}
	print("project commands: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## Requests sent, then the ones queued behind them.
func _asked(dock: Control) -> Array[String]:
	var asked: Array[String] = _sent_requests.duplicate()
	for text: String in dock.get("_transcript").get("queue").call("texts"):
		asked.append(text)
	return asked


func _first_reply(transcript: Node) -> RichTextLabel:
	for found: Node in transcript.find_children("", "RichTextLabel", true, false):
		if (found as RichTextLabel).accessibility_name.ends_with("message"):
			return found as RichTextLabel
	return null


func _has_label(node: Node, text: String) -> bool:
	for found: Node in node.find_children("", "Label", true, false):
		if (found as Label).text == text:
			return true
	return false


## The real `joinery command list|render` on a command planted in this project.
func _real_cli(own: Node, dock: Control) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://.joinery/commands"))
	var file := FileAccess.open("res://.joinery/commands/new-room.toml", FileAccess.WRITE)
	file.store_string(TOML)
	file.close()
	var real := func(args: PackedStringArray) -> Dictionary:
		var at := args.find("command")
		var argv := PackedStringArray(
			["-c", "import sys; from joinery.cli import main; sys.exit(main())"]
		)
		argv.append_array(args.slice(at))
		var output: Array = []
		OS.execute(python, argv, output, true)
		return SLASH.parse_json("".join(output))
	own.set("runner", real)
	own.set("_signature", "")
	own.call("look")
	_check(SLASH.project.get("/new-room", []) == ["Add a room", "project"], "%s" % SLASH.project)
	own.call("open", "/new-room", PackedStringArray(["theme=ice"]))
	var asked := _asked(dock)
	_check(asked[-1] == "Add a ice room.", "The real CLI fills it in: %s" % [asked])
	DirAccess.remove_absolute(
		ProjectSettings.globalize_path("res://.joinery/commands/new-room.toml")
	)
