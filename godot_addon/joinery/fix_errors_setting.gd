@tool
extends VBoxContainer
## The "Fix console errors automatically" switch in Journeyman settings (ADR 0075): on, each new
## error in Output, the game's log, a playtest or a test run starts a fix in the background
## (`[agents] fix_errors = "auto"` in joinery.toml); off, Joinery offers to fix each one first
## (`"ask"`, the default). Each fix is still a change the user accepts or discards. A project
## that set `"off"` shows the switch off with a line saying the console isn't watched. Read with
## `joinery fix-errors` whenever the settings page shows it; a change applies at once and a
## failed write puts the switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const CAPTION := "Each fix is still a change you review. Off: Joinery asks first."
const OFF := 'The console isn\'t watched (fix_errors = "off" in joinery.toml).'

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "FixErrorsSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row("Fix console errors automatically", CAPTION, false)
	row.switch_button.accessibility_name = "Fix console errors automatically"
	# The whole name, on two lines in a narrow sidebar rather than cut short.
	row.title.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
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
	_run("status", ["fix-errors", "--project", _project(), "--json"])


## Show a `joinery fix-errors` answer ({fix_errors, auto} or {error}).
func apply_status(data: Dictionary) -> void:
	row.set_disabled_reason("")
	if data.has("error"):
		row.reason.text = str(data["error"])
		row.reason.show()
		return
	if str(data.get("fix_errors", "ask")) == "off":
		row.reason.text = OFF
		row.reason.show()
	else:
		row.reason.hide()
	row.set_on(bool(data.get("auto", false)))


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	var value := "auto" if on else "ask"
	_run("save", ["fix-errors", "--project", _project(), "--json", "--set", value])


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
		data = {"error": "Couldn't read the console errors setting. Details are in Output."}
		print("[Joinery] fix-errors\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
