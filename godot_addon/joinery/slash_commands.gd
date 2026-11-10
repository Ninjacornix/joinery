@tool
extends Node
## Sidebar slash commands. UI commands are handed back to the dock through [signal requested];
## checks run the Joinery CLI off the main thread (command_job.gd) and come back as plain
## sentences through [signal finished]. Also runs the actions the sidebar offers: Revert for
## accepted changes, Apply for a planned feature, writing the game brief, updating the art
## bible to the new layout, and reading app readiness and which changes can still be reverted.
##
## A feature is only ever planned by "/feature add|remove <id>" ([signal feature_planned]);
## [method apply_feature] runs only when the user presses Apply on that plan.

## A UI command the dock carries out: "/art", "/brief", "/setup", "/focus", "/toolbar",
## "/help", "/models".
signal requested(command: String)
## A CLI check started ("/doctor").
signal started(command: String)
## A CLI check finished: [param text] is the reply, [param kind] "success" or "error".
signal finished(command: String, text: String, kind: String)
## A revert finished.
signal reverted(transaction_id: String, ok: bool, message: String)
## "/feature add|remove <id>" planned the change: `joinery feature … --plan --json`.
signal feature_planned(plan: Dictionary)
## Apply on a feature plan finished; [param transaction_id] is the accepted change.
signal feature_applied(ok: bool, message: String, transaction_id: String)
## App readiness from `joinery status --json` ({tools: [{name, summary, status, action}]}).
signal readiness_changed(report: Dictionary)
## Which accepted changes can still be reverted: {transaction id: revertible}.
signal revertible_changed(states: Dictionary)
## "Update to the new layout" on a first-format art bible finished.
signal bible_upgraded(ok: bool, message: String)

const COMMAND_SCRIPT := preload("command_job.gd")
## Everyday commands first; descriptions stay at 32 characters or fewer.
const COMMANDS := {
	"/art": ["Review the art direction", "ui"],
	"/brief": ["Read the game brief", "ui"],
	"/feature": ["Add or remove a game feature", "cli"],
	"/setup": ["Change project apps", "ui"],
	"/focus": ["Toggle focus mode", "ui"],
	"/toolbar": ["Show or hide viewport tools", "ui"],
	"/help": ["List commands", "ui"],
	"/tools": ["Check what your apps need", "cli"],
	"/doctor": ["Check installed apps", "cli"],
	"/check": ["Check assets follow the rules", "cli"],
	"/rebuild": ["Verify assets can be rebuilt", "cli"],
	"/agents": ["Accounts Journeyman can use", "cli"],
	"/models": ["Refresh the model list", "ui"],
}
## Shown only when typed, or under the Troubleshooting divider.
const TROUBLESHOOTING: Array[String] = [
	"/tools", "/doctor", "/check", "/rebuild", "/agents", "/models"
]
## Feature names as the new-project questionnaire shows them.
const FEATURE_NAMES := {
	"controller": "Controller support",
	"online-play": "Online play",
	"save-load": "Save and load",
	"steam": "Steam",
	"translations": "Translations",
}
## Readiness re-reads while the report still says "checking", this many times at most.
const READINESS_POLLS := 10
const READINESS_WAIT := 2.0
const BRIEF_PATH := "res://design/brief.md"

## Running jobs by slot: {slot: [job, callback]}. One job per slot.
var _jobs: Dictionary = {}
var _running := ""
var _revert_id := ""
var _readiness_polls := 0


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


## The words after the command ("/feature add controller" -> ["add", "controller"]).
static func arguments(entry: String) -> PackedStringArray:
	var words := entry.strip_edges().split(" ", false)
	return words.slice(1) if words.size() > 1 else PackedStringArray()


## A feature's display name ("online-play" -> "Online play").
static func feature_name(id: String) -> String:
	return str(FEATURE_NAMES.get(id, id.replace("-", " ").capitalize()))


## Run [param entry]. Returns false when it is not a known command.
func run(entry: String) -> bool:
	var command := resolve(entry)
	if command.is_empty():
		return false
	if COMMANDS[command][1] == "ui":
		requested.emit(command)
		return true
	if _jobs.has("check") or not _running.is_empty():
		finished.emit(command, "Another check is still running.", "error")
		return true
	var args := args_for(command.substr(1), arguments(entry))
	if args.is_empty():
		finished.emit(command, usage(command), "info")
		return true
	if command == "/tools":
		_readiness_polls = 0
	_running = command
	if not _start("check", args, _on_check.bind(command, arguments(entry))):
		_running = ""
		finished.emit(command, "Couldn't start the check. Is uv installed?", "error")
		return true
	started.emit(command)
	return true


## The CLI check in progress ("/doctor"), or "".
func running() -> String:
	return _running


## Revert an accepted change by transaction id; [signal reverted] reports the result.
func revert(transaction_id: String) -> void:
	if _jobs.has("revert") or transaction_id.is_empty():
		reverted.emit(transaction_id, false, "Another revert is still running.")
		return
	var args := _base_args()
	args.append_array(["transactions", "revert", "--project", _project(), "--id", transaction_id])
	_revert_id = transaction_id
	if not _start("revert", args, _on_revert_done):
		reverted.emit(transaction_id, false, "Couldn't start Joinery.")


## Apply a planned feature change. Only the plan card's Apply calls this.
func apply_feature(action: String, id: String) -> void:
	if _jobs.has("apply"):
		feature_applied.emit(false, "Another change is still being applied.", "")
		return
	var args := _base_args()
	args.append_array(["feature", action, id, "--project", _project(), "--json", "--yes"])
	if not _start("apply", args, _on_applied):
		feature_applied.emit(false, "Couldn't start Joinery.", "")


## Write design/brief.md from the project's setup answers (`joinery brief`).
func write_brief() -> void:
	if _jobs.has("brief"):
		return
	var args := _base_args()
	args.append_array(["brief", "--project", _project()])
	_running = "/brief"
	if not _start("brief", args, _on_brief_written):
		_running = ""
		finished.emit("/brief", "Couldn't start Joinery.", "error")
		return
	started.emit("/brief")


## Bring a first-format art bible to the new layout (`joinery bible --upgrade`). The change
## is recorded like any other, so it can be reverted; the bible needs approving again.
func upgrade_bible() -> void:
	if _jobs.has("upgrade"):
		return
	var args := _base_args()
	args.append_array(["bible", "--project", _project(), "--upgrade", "--json", "--yes"])
	if not _start("upgrade", args, _on_upgraded):
		bible_upgraded.emit(false, "Couldn't start Joinery.")


## Read app readiness quietly (cached, refreshed in the background) for the header line.
func check_readiness() -> void:
	if _jobs.has("readiness") or _jobs.has("check"):
		return
	_start("readiness", readiness_args(), _on_readiness)


## Ask which accepted changes can still be reverted.
func check_revertible() -> void:
	if _jobs.has("list"):
		return
	var args := _base_args()
	args.append_array(["transactions", "list", "--project", _project()])
	_start("list", args, _on_listed)


## Stop every running job.
func cancel() -> void:
	for slot: String in _jobs:
		var job: RefCounted = _jobs[slot][0]
		job.cancel()
	_jobs.clear()
	_running = ""
	set_process(false)


func _start(slot: String, args: PackedStringArray, callback: Callable) -> bool:
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start("uv", args) != OK:
		return false
	_jobs[slot] = [job, callback]
	set_process(true)
	return true


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		if not _jobs.has(slot):
			continue
		var job: RefCounted = _jobs[slot][0]
		var result: Dictionary = job.poll()
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		callback.call(result)
	if _jobs.is_empty():
		set_process(false)


func _on_check(result: Dictionary, command: String, words: PackedStringArray) -> void:
	var output := str(result.get("output", ""))
	var code := int(result.get("code", 1))
	if code != 0 or not command in ["/feature", "/tools"]:
		# Plans and readiness are JSON for the cards; Output gets them only when they fail.
		print("[Joinery] ", command, "\n", output)
	if command == "/tools":
		var report := parse_json(output)
		if code == 0 and bool(report.get("checking", false)) and _readiness_polls < READINESS_POLLS:
			# The cache is still filling in the background: read it again shortly.
			_readiness_polls += 1
			await get_tree().create_timer(READINESS_WAIT).timeout
			if (
				_running == command
				and not _start("check", readiness_args(), _on_check.bind(command, words))
			):
				_running = ""
				finished.emit(command, "Couldn't start the check. Is uv installed?", "error")
			return
		if code == 0 and report.has("tools"):
			readiness_changed.emit(report)
	_running = ""
	if command == "/feature" and code == 0 and words.size() >= 2 and words[0] != "list":
		var plan := parse_json(output)
		if plan.has("feature"):
			feature_planned.emit(plan)
			finished.emit(command, "", "info")
			return
	finished.emit(command, reply(command, code, output, words), "success" if code == 0 else "error")


func _on_revert_done(result: Dictionary) -> void:
	var output := str(result.get("output", ""))
	var ok := int(result.get("code", 1)) == 0
	reverted.emit(_revert_id, ok, "" if ok else _first_error(output))
	check_revertible()


func _on_applied(result: Dictionary) -> void:
	var output := str(result.get("output", ""))
	var data := parse_json(output)
	var ok := int(result.get("code", 1)) == 0 and str(data.get("state", "")) == "accepted"
	if not ok:
		print("[Joinery] feature\n", output, "\n", result.get("error", ""))
		var why := _first_error(output)
		feature_applied.emit(false, why if not why.is_empty() else "It couldn't be applied.", "")
		return
	var notes: Array[String] = []
	for step: Variant in data.get("steps", []):
		if step is Dictionary and not str(step.get("message", "")).is_empty():
			notes.append(str(step["message"]))
	feature_applied.emit(true, " ".join(notes), str(data.get("transaction_id", "")))
	check_revertible()


func _on_brief_written(result: Dictionary) -> void:
	_running = ""
	var output := str(result.get("output", "")) + "\n" + str(result.get("error", ""))
	print("[Joinery] brief\n", output)
	if int(result.get("code", 1)) != 0:
		finished.emit("/brief", "Couldn't write the brief. Details are in Output.", "error")
	elif FileAccess.file_exists(BRIEF_PATH):
		finished.emit("/brief", "Wrote the game brief. Type /brief to read it.", "success")
	else:
		finished.emit(
			"/brief",
			"There were no setup answers to write it from. Ask Journeyman to draft it with you.",
			"info"
		)


func _on_upgraded(result: Dictionary) -> void:
	var output := str(result.get("output", ""))
	var ok := (
		int(result.get("code", 1)) == 0 and str(parse_json(output).get("state", "")) == "accepted"
	)
	bible_upgraded.emit(ok, "" if ok else _first_error(output))
	check_revertible()


func _on_readiness(result: Dictionary) -> void:
	var report := parse_json(str(result.get("output", "")))
	if int(result.get("code", 1)) == 0 and report.has("tools"):
		readiness_changed.emit(report)


func _on_listed(result: Dictionary) -> void:
	var data := parse_json(str(result.get("output", "")))
	var states := {}
	for entry: Variant in data.get("transactions", []):
		if entry is Dictionary and str(entry.get("state", "")) == "accepted":
			states[str(entry.get("id", ""))] = bool(entry.get("revertible", true))
	if int(result.get("code", 1)) == 0:
		revertible_changed.emit(states)


## The JSON object in a CLI's output (one object, or the last object line), or {}.
static func parse_json(output: String) -> Dictionary:
	var whole_json := JSON.new()
	var whole: Variant = whole_json.data if whole_json.parse(output) == OK else null
	if whole is Dictionary:
		return whole
	var lines := output.split("\n", false)
	lines.reverse()
	for line: String in lines:
		var data_json := JSON.new()
		var data: Variant = data_json.data if data_json.parse(line) == OK else null
		if data is Dictionary:
			return data
	return {}


## How to use a command that needs words after it.
static func usage(command: String) -> String:
	if command == "/feature":
		return (
			"Type /feature add or /feature remove with a feature: "
			+ ", ".join(FEATURE_NAMES.keys())
			+ "."
		)
	return "Type / to see commands."


## Plain sentences for a finished check.
static func reply(
	command: String, code: int, output: String, words := PackedStringArray()
) -> String:
	var name := command.trim_prefix("/")
	if name == "tools":
		var report := parse_json(output)
		if report.has("tools"):
			return readiness_reply(report)
	if name == "feature":
		var data := parse_json(output)
		if data.has("error"):
			return str(data["error"])
		if code == 0 and data.has("game"):
			return features_reply(data.get("game"))
		if code != 0 and words.size() >= 2:
			return "Couldn't plan that change. Details are in Output."
	if code != 0:
		return "That check couldn't finish. Details are in Output."
	if name == "agents":
		var data_json := JSON.new()
		var data: Variant = data_json.data if data_json.parse(output) == OK else null
		var names: Array[String] = []
		if data is Dictionary:
			for agent: Dictionary in data.get("agents", []):
				names.append(str(agent["name"]))
		if names.is_empty():
			return "Couldn't read the accounts Journeyman can use. Details are in Output."
		return "Journeyman can use: " + ", ".join(names) + "."
	return (
		{
			"doctor": "Your apps are ready.",
			"check": "Assets follow the project rules.",
			"rebuild": "Every asset can be rebuilt from its recipe.",
		}
		. get(name, "Done.")
	)


## One sentence per app from `joinery status --json`: its summary, then what to do.
static func readiness_reply(report: Dictionary) -> String:
	var lines: Array[String] = []
	for entry: Variant in report.get("tools", []):
		if not entry is Dictionary:
			continue
		var line := _sentence(str(entry.get("summary", entry.get("name", ""))))
		var action: Variant = entry.get("action")
		if action is Dictionary and str(entry.get("status", "ready")) != "ready":
			line += (
				" "
				+ _sentence(_action_hint(str(action.get("id", "")), str(action.get("label", ""))))
			)
		lines.append(line.strip_edges())
	if lines.is_empty():
		return "This project uses no apps yet. Add some with /setup."
	return "\n".join(lines)


## The header's one line for the first enabled app that needs something, or "".
static func readiness_line(report: Dictionary) -> String:
	for entry: Variant in report.get("tools", []):
		if not entry is Dictionary or not bool(entry.get("selected", true)):
			continue
		var status := str(entry.get("status", "ready"))
		if status == "ready" or status == "preparing" or bool(entry.get("custom", false)):
			continue
		var name := str(entry.get("name", ""))
		var first: String = (
			{
				"needs_docker": "%s needs Docker." % name,
				"needs_licence": "%s needs your licence." % name,
				"unavailable": "%s isn't available yet." % name,
			}
			. get(status, _sentence(str(entry.get("summary", name))))
		)
		var hint := ""
		var action: Variant = entry.get("action")
		if action is Dictionary:
			hint = _action_hint(str(action.get("id", "")), str(action.get("label", "")))
		return (first + " " + _sentence(hint)).strip_edges()
	return ""


## What a person does for an action id, in words.
static func _action_hint(id: String, label: String) -> String:
	match id:
		"start_docker":
			return "Start Docker Desktop"
		"rebuild":
			return "Rebuild its worker in /setup"
		"get":
			return "Add your licence in /setup"
		"check_again":
			return ""
	return label


static func _sentence(text: String) -> String:
	var line := text.strip_edges()
	if line.is_empty() or line.ends_with(".") or line.ends_with("…"):
		return line
	return line + "."


## "/feature list": the starting point and the features it has.
static func features_reply(game: Variant) -> String:
	if not game is Dictionary:
		return "This project wasn't started from a blueprint, so it has no features to add."
	var added: Array[String] = []
	for id: Variant in game.get("features", []):
		added.append(feature_name(str(id)))
	var free: Array[String] = []
	for id: Variant in game.get("can_add", []):
		free.append(str(id))
	var text := (
		"%s has %s."
		% [
			str(game.get("name", game.get("blueprint", "This game"))),
			", ".join(added) if not added.is_empty() else "no features yet",
		]
	)
	if not free.is_empty():
		text += " You can add: " + ", ".join(free) + ". Type /feature add and a name."
	return text


## CLI arguments for a check ("doctor", "tools", …) and the words typed after it. Empty when
## the words don't make a command.
static func args_for(command: String, words := PackedStringArray()) -> PackedStringArray:
	var project := _project()
	var args := _base_args()
	match command:
		"tools":
			return readiness_args()
		"agents":
			args.append_array(["agents", "--json"])
		"doctor", "rebuild":
			args.append_array([command, "--project", project])
		"check":
			args.append_array(["check", "assets", "--project", project])
		"feature":
			var action := words[0].to_lower() if not words.is_empty() else "list"
			if action == "list":
				args.append_array(["feature", "list", "--project", project, "--json"])
			elif action in ["add", "remove"] and words.size() == 2:
				args.append_array(
					[
						"feature",
						action,
						words[1].to_lower(),
						"--project",
						project,
						"--plan",
						"--json"
					]
				)
			else:
				return PackedStringArray()
		_:
			args.append(command)
	return args


## `joinery status` for this project: cached answers, missing ones refreshed in the background.
static func readiness_args() -> PackedStringArray:
	var args := _base_args()
	args.append_array(["status", "--project", _project(), "--json", "--background"])
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
	var data := parse_json(output)
	if data.has("error"):
		return str(data["error"])
	var lines := output.strip_edges().split("\n", false)
	return lines[0] if not lines.is_empty() else ""
