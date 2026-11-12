@tool
extends Node
## The project's own commands in the sidebar (ADR 0077): every `.joinery/commands/<name>.toml`
## is a "/<name>" entry under "This project" in the command menu. The list comes from
## `joinery command list --json`, read when the sidebar opens and again whenever a file in that
## folder changes (looked at every few seconds, no model).
##
## "/new-enemy name=Bat" with every value it needs goes straight to Journeyman; otherwise a small
## form (command_card.gd) opens with the defaults filled in, and its Ask Journeyman sends it.
## Either way `joinery command render` fills in the request, which is sent like a typed one.
## A bare word after the command fills its first parameter that has no default
## ("/new-room cave").

const CARD := preload("command_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const FOLDER := "res://.joinery/commands"
## Seconds between looks at the folder.
const LOOK_EVERY := 3.0
## The command menu's descriptions stay at 32 characters or fewer.
const DESCRIPTION := 32

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the form sits in it.
var tasks: Object
## The form, while there is one.
var card: Control
## Runs `joinery command …`; tests replace it with Callable(args) -> Dictionary (the JSON).
var runner := Callable()
## The project's commands by "/name", as `joinery command list --json` gives them.
var commands: Dictionary = {}
var _signature := ""
var _since_look := 0.0
## Running CLI jobs: {job: Callable(data)}.
var _jobs: Dictionary = {}


static func attach(sidebar: Control, list: Object) -> Node:
	var own: Node = load("res://addons/joinery/sidebar_commands.gd").new()
	own.set("dock", sidebar)
	own.set("tasks", list)
	(list as Node).add_child(own)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("project_command_requested"):
		slash.connect("project_command_requested", own.open)
	return own


func _init() -> void:
	name = "ProjectCommands"


func _ready() -> void:
	set_process(Engine.is_editor_hint())
	look()


## Read the list again when the folder changed since the last look.
func look() -> void:
	var now := folder_signature()
	if now == _signature:
		return
	_signature = now
	if now.is_empty():
		take({"commands": []})
		return
	_cli(["list", "--json"], take)


## The folder's files and when each last changed, as one string ("" when there are none).
static func folder_signature() -> String:
	var path := ProjectSettings.globalize_path(FOLDER)
	var dir := DirAccess.open(path)
	if dir == null:
		return ""
	var parts: Array[String] = []
	for file: String in dir.get_files():
		if file.ends_with(".toml"):
			parts.append("%s@%d" % [file, FileAccess.get_modified_time(path.path_join(file))])
	parts.sort()
	return "|".join(parts)


## Take a listing: {commands: [{name, title, description, parameters}], problems: […]}.
func take(data: Dictionary) -> void:
	commands.clear()
	var shown := {}
	for entry: Variant in data.get("commands", []):
		if not entry is Dictionary:
			continue
		var command := "/" + str(entry.get("name", ""))
		if SLASH.COMMANDS.has(command) or command == "/":
			continue
		commands[command] = entry
		shown[command] = [_short(str(entry.get("description", entry.get("title", "")))), "project"]
	SLASH.project = shown
	for problem: Variant in data.get("problems", []):
		print("[Joinery] project command: ", problem)


## "/name [words]": straight to Journeyman when nothing is missing, else the form.
func open(command: String, words: PackedStringArray) -> void:
	if not commands.has(command):
		dock.call("show_status", "Unknown command. Type / to see commands.", 3.0)
		return
	var entry: Dictionary = commands[command]
	var given := given_values(entry, words)
	if not words.is_empty() and missing(entry, given).is_empty():
		_render(entry, given)
		return
	_ensure_card()
	card.call("show_command", entry, given)


## The values typed after the command: name=value pairs; the rest fills the first parameter
## without a default.
static func given_values(entry: Dictionary, words: PackedStringArray) -> Dictionary:
	var given := {}
	var loose: Array[String] = []
	for word: String in words:
		var pair := word.split("=", true, 1)
		if pair.size() == 2 and not pair[0].is_empty():
			given[pair[0]] = pair[1]
		else:
			loose.append(word)
	if not loose.is_empty():
		for parameter: Variant in entry.get("parameters", []):
			if parameter is Dictionary and bool(parameter.get("required", false)):
				var name := str(parameter.get("name", ""))
				if not given.has(name):
					given[name] = " ".join(loose)
					break
	return given


## The parameters that need a value and have none.
static func missing(entry: Dictionary, given: Dictionary) -> Array[String]:
	var names: Array[String] = []
	for parameter: Variant in entry.get("parameters", []):
		if parameter is Dictionary and bool(parameter.get("required", false)):
			var name := str(parameter.get("name", ""))
			if str(given.get(name, "")).strip_edges().is_empty():
				names.append(name)
	return names


func close() -> void:
	var leaving := card
	card = null
	tasks.set("command_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


func _ensure_card() -> void:
	if is_instance_valid(card):
		return
	card = CARD.new()
	card.connect(
		"run_requested", func(values: Dictionary) -> void: _render(card.get("command"), values)
	)
	card.connect("close_pressed", close)
	(tasks as Node).add_child(card)
	tasks.set("command_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


## `joinery command render`, then the request goes to Journeyman like a typed one.
func _render(entry: Dictionary, values: Dictionary) -> void:
	var args: Array = ["render", str(entry.get("name", "")), "--json"]
	for key: String in values:
		var value: Variant = values[key]
		args.append_array(["--param", "%s=%s" % [key, _text(value)]])
	if is_instance_valid(card):
		card.call("set_running", true)
	_cli(args, _sent)


func _sent(data: Dictionary) -> void:
	var request := str(data.get("request", ""))
	if request.is_empty():
		var why := str(data.get("error", "Couldn't fill in the command. Details are in Output."))
		if is_instance_valid(card):
			card.call("set_running", false)
			card.call("show_error", why)
		else:
			dock.call("show_status", why, 5.0)
		return
	if is_instance_valid(card):
		close()
	dock.call("_on_submit", request)


static func _text(value: Variant) -> String:
	if value is bool:
		return "true" if value else "false"
	return str(value)


static func _short(text: String) -> String:
	var line := text.strip_edges()
	return line if line.length() <= DESCRIPTION else line.left(DESCRIPTION - 1).strip_edges() + "…"


func _process(delta: float) -> void:
	for job: RefCounted in _jobs.keys():
		var result: Dictionary = job.poll()
		if result.is_empty():
			continue
		var done: Callable = _jobs[job]
		_jobs.erase(job)
		var output := str(result.get("output", ""))
		if int(result.get("code", 1)) != 0:
			print("[Joinery] command\n", output, "\n", result.get("error", ""))
		var data := SLASH.parse_json(output)
		if data.is_empty():
			data = {"error": "Joinery didn't answer. Details are in Output."}
		done.call(data)
	_since_look += delta
	if _since_look >= LOOK_EVERY:
		_since_look = 0.0
		look()


func _cli(extra: Array, done: Callable) -> void:
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", "command"]
	)
	args.append(str(extra[0]))
	for item: Variant in extra.slice(1):
		args.append(str(item))
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/")])
	if runner.is_valid():
		done.call(runner.call(args))
		return
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start("uv", args) != OK:
		done.call({"error": "Couldn't start Joinery."})
		return
	_jobs[job] = done
	set_process(true)
