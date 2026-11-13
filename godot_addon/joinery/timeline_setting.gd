@tool
extends VBoxContainer
## The Timeline switch in Journeyman settings: whether each accepted change becomes an entry
## in the Timeline tab (`[timeline] auto_commit` in the project's joinery.toml). Read with
## `joinery timeline status` whenever the settings page shows it; a change applies at once
## and a failed write puts the switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const NO_TIMELINE := "This project has no timeline yet. Start one in the Timeline tab."

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "TimelineSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row("Timeline", "Each accepted change becomes an entry.", true)
	row.switch_button.accessibility_name = "Add accepted changes to the Timeline"
	# Unknown until the project answers; the row says it is checking.
	row.set_checking()
	row.toggled.connect(_on_toggled)
	add_child(row)
	set_process(false)


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree():
		refresh()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.call("cancel")
		_job = null


## Read the project's choice again.
func refresh() -> void:
	if _job_kind == "save":
		return
	_run("status", ["timeline", "status", "--project", _project(), "--json"])


## Show a `joinery timeline status` (or `settings`) answer.
func apply_status(data: Dictionary) -> void:
	if data.has("auto_commit"):
		row.set_on(bool(data["auto_commit"]))
	var state := str(data.get("state", "ready"))
	if data.has("error"):
		row.set_disabled_reason("")
		row.reason.text = str(data["error"])
		row.reason.show()
	elif state in ["ready"]:
		row.set_disabled_reason("")
	else:
		var why := str(data.get("message", "")) if state in ["no-git", "nested"] else NO_TIMELINE
		row.set_disabled_reason(why)


func _on_toggled(on: bool) -> void:
	var args := ["timeline", "settings", "--project", _project(), "--json"]
	args.append_array(["--auto-commit", "on" if on else "off"])
	row.switch_button.disabled = true
	_run("save", args)


func _run(kind: String, args: Array) -> void:
	if _job != null:
		_job.call("cancel")
	_job = COMMAND.joinery(cli_prefix, args)
	_job_kind = kind if _job != null else ""
	set_process(_job != null)


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	var result: Dictionary = _job.call("poll")
	if result.is_empty():
		return
	var kind := _job_kind
	_job = null
	_job_kind = ""
	set_process(false)
	var data: Dictionary = {}
	for line: String in str(result.get("output", "")).split("\n", false):
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			data = parsed
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Couldn't read the Timeline setting. Details are in Output."}
		print("[Joinery] timeline\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
	apply_status(data)
	if kind == "save" and not data.has("error"):
		refresh()


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
