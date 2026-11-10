@tool
extends Node
## Sidebar slash commands. UI commands are handed back to the dock through [signal requested];
## checks run the Joinery CLI off the main thread (command_job.gd) and come back as one plain
## sentence through [signal finished]. Also runs "Revert" for accepted changes.

## A UI command the dock carries out: "/art", "/setup", "/focus", "/toolbar", "/help", "/models".
signal requested(command: String)
## A CLI check started ("/doctor").
signal started(command: String)
## A CLI check finished: [param text] is the reply, [param kind] "success" or "error".
signal finished(command: String, text: String, kind: String)
## A revert finished.
signal reverted(transaction_id: String, ok: bool, message: String)

const COMMAND_SCRIPT := preload("command_job.gd")
## Everyday commands first; descriptions stay at 32 characters or fewer.
const COMMANDS := {
	"/art": ["Review the art direction", "ui"],
	"/setup": ["Change project apps", "ui"],
	"/focus": ["Toggle focus mode", "ui"],
	"/toolbar": ["Show or hide viewport tools", "ui"],
	"/help": ["List commands", "ui"],
	"/tools": ["Check app connections", "cli"],
	"/doctor": ["Check installed apps", "cli"],
	"/check": ["Check assets follow the rules", "cli"],
	"/rebuild": ["Verify assets can be rebuilt", "cli"],
	"/agents": ["List supported assistants", "cli"],
	"/models": ["Refresh the model list", "ui"],
}
## Shown only when typed, or under the Troubleshooting divider.
const TROUBLESHOOTING: Array[String] = [
	"/tools", "/doctor", "/check", "/rebuild", "/agents", "/models"
]

var _job: RefCounted
var _running := ""
var _revert_job: RefCounted
var _revert_id := ""


func _ready() -> void:
	set_process(false)


func _exit_tree() -> void:
	cancel()


## The command [param entry] names, completing a unique prefix ("/doc" -> "/doctor"), or "".
static func resolve(entry: String) -> String:
	var command := entry.strip_edges().split(" ")[0].to_lower()
	if COMMANDS.has(command):
		return command
	var found: Array[String] = []
	for candidate: String in COMMANDS:
		if candidate.begins_with(command):
			found.append(candidate)
	return found[0] if found.size() == 1 else ""


## Run [param entry]. Returns false when it is not a known command.
func run(entry: String) -> bool:
	var command := resolve(entry)
	if command.is_empty():
		return false
	if COMMANDS[command][1] == "ui":
		requested.emit(command)
		return true
	if _job != null:
		finished.emit(command, "Another check is still running.", "error")
		return true
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args_for(command.substr(1))) != OK:
		_job = null
		finished.emit(command, "Couldn't start the check. Is uv installed?", "error")
		return true
	_running = command
	set_process(true)
	started.emit(command)
	return true


## The CLI check in progress ("/doctor"), or "".
func running() -> String:
	return _running


## Revert an accepted change by transaction id; [signal reverted] reports the result.
func revert(transaction_id: String) -> void:
	if _revert_job != null or transaction_id.is_empty():
		reverted.emit(transaction_id, false, "Another revert is still running.")
		return
	var args := _base_args()
	args.append_array(["transactions", "revert", "--project", _project(), "--id", transaction_id])
	_revert_job = COMMAND_SCRIPT.new()
	if _revert_job.start("uv", args) != OK:
		_revert_job = null
		reverted.emit(transaction_id, false, "Couldn't start Joinery.")
		return
	_revert_id = transaction_id
	set_process(true)


## Stop any running check or revert.
func cancel() -> void:
	for job: RefCounted in [_job, _revert_job]:
		if job != null:
			job.cancel()
	_job = null
	_revert_job = null
	_running = ""
	set_process(false)


func _process(_delta: float) -> void:
	if _job != null:
		var result: Dictionary = _job.poll()
		if not result.is_empty():
			_job = null
			var command := _running
			_running = ""
			var output := str(result.get("output", ""))
			print("[Joinery] ", command, "\n", output)
			var code := int(result.get("code", 1))
			finished.emit(
				command, reply(command, code, output), "success" if code == 0 else "error"
			)
	if _revert_job != null:
		var result: Dictionary = _revert_job.poll()
		if not result.is_empty():
			_revert_job = null
			var output := str(result.get("output", ""))
			var ok := int(result.get("code", 1)) == 0
			reverted.emit(_revert_id, ok, "" if ok else _first_error(output))
	if _job == null and _revert_job == null:
		set_process(false)


## One plain sentence for a finished check.
static func reply(command: String, code: int, output: String) -> String:
	var name := command.trim_prefix("/")
	if name == "tools":
		var data: Variant
		for line: String in output.split("\n"):
			if line.begins_with('{"servers"'):
				data = JSON.parse_string(line)
		if data is Dictionary:
			var connected: Array[String] = []
			var unavailable: Array[String] = []
			for server: Dictionary in data.get("servers", []):
				if server.get("state") == "discovered":
					connected.append(str(server["name"]))
				else:
					unavailable.append(str(server["name"]))
			var text := (
				"Connected: " + (", ".join(connected) if not connected.is_empty() else "none")
			)
			if not unavailable.is_empty():
				text += ". Not connected: " + ", ".join(unavailable)
			return text + "."
	if code != 0:
		return "That check couldn't finish. Details are in Output."
	if name == "agents":
		var data: Variant = JSON.parse_string(output)
		var names: Array[String] = []
		if data is Dictionary:
			for agent: Dictionary in data.get("agents", []):
				names.append(str(agent["name"]))
		if names.is_empty():
			return "Couldn't read the supported assistants. Details are in Output."
		return "Supported assistants: " + ", ".join(names) + "."
	return (
		{
			"doctor": "Your apps are ready.",
			"check": "Assets follow the project rules.",
			"rebuild": "Every asset can be rebuilt from its recipe.",
		}
		. get(name, "Done.")
	)


## CLI arguments for a check ("doctor", "tools", …).
static func args_for(command: String) -> PackedStringArray:
	var project := _project()
	var args := _base_args()
	match command:
		"tools":
			args.append_array(["mcp", "status", "--project", project, "--probe"])
		"agents":
			args.append_array(["agents", "--json"])
		"doctor", "rebuild":
			args.append_array([command, "--project", project])
		"check":
			args.append_array(["check", "assets", "--project", project])
		_:
			args.append(command)
	return args


## The Joinery checkout that runs the CLI: JOINERY_HOME, or the folder above the project.
static func engine_dir() -> String:
	var from_env := OS.get_environment("JOINERY_HOME")
	return (
		from_env
		if not from_env.is_empty()
		else ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	)


static func _base_args() -> PackedStringArray:
	return PackedStringArray(["run", "--frozen", "--quiet", "--project", engine_dir(), "joinery"])


static func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


## The readable reason from a failed CLI call: its JSON "error", else its first line.
static func _first_error(output: String) -> String:
	for line: String in output.split("\n", false):
		var data: Variant = JSON.parse_string(line)
		if data is Dictionary and data.has("error"):
			return str(data["error"])
	var lines := output.strip_edges().split("\n", false)
	return lines[0] if not lines.is_empty() else ""
