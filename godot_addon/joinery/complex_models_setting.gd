@tool
extends VBoxContainer
## The "Complex models in Blender" switch in Journeyman settings: whether agents make every
## object with more than one part in Blender or with a kit (`[agents] complex_models =
## "blender"` in joinery.toml, the default), or may build quick placeholder shapes in Godot
## (`"placeholders"`). Read with `joinery complex-models` whenever the settings page shows it;
## a change applies at once and a failed write puts the switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const CAPTION := "Off lets agents use Godot placeholder shapes for quick prototyping."

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "ComplexModelsSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row("Complex models in Blender", CAPTION, true)
	row.switch_button.accessibility_name = "Make complex models in Blender"
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
	_run("status", ["complex-models", "--project", _project(), "--json"])


## Show a `joinery complex-models` answer ({complex_models, blender} or {error}).
func apply_status(data: Dictionary) -> void:
	row.set_disabled_reason("")
	if data.has("error"):
		row.reason.text = str(data["error"])
		row.reason.show()
		return
	row.reason.hide()
	row.set_on(bool(data.get("blender", true)))


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	var value := "blender" if on else "placeholders"
	_run("save", ["complex-models", "--project", _project(), "--json", "--set", value])


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
		data = {"error": "Couldn't read the complex models setting. Details are in Output."}
		print("[Joinery] complex-models\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
