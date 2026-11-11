@tool
extends VBoxContainer
## The Reviewer agent switch and its model in Journeyman settings: whether a second agent reads
## each change before the user sees it (`[agents] reviewer` in joinery.toml), and with which model
## (`[agents] reviewer_model`, empty for the one Journeyman uses). Read with `joinery reviewer`
## whenever the settings page shows it; a change applies at once, and a failed write puts the
## switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var model: LineEdit
var model_row: Control
var _job: RefCounted
var _job_kind := ""
var _saved_model := ""


func _init() -> void:
	name = "ReviewerSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row(
		"Reviewer agent", "Another agent reads each change before you see it.", true
	)
	row.switch_button.accessibility_name = "Review each change with a second agent"
	# Unknown until the project answers.
	row.switch_button.disabled = true
	row.toggled.connect(_on_toggled)
	add_child(row)
	model = LineEdit.new()
	model.placeholder_text = "Same as Journeyman"
	model.accessibility_name = "Reviewer model"
	model.custom_minimum_size.x = 160
	model.editable = false
	model.text_submitted.connect(func(_text: String) -> void: _save_model())
	model.focus_exited.connect(_save_model)
	# Under "Reviewer agent", so "Model" says enough and fits narrow sidebars.
	model_row = UI.setting_row("Model", model)
	add_child(model_row)
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
	_run("status", ["reviewer", "--project", _project(), "--json"])


## Show a `joinery reviewer` answer ({enabled, model} or {error}).
func apply_status(data: Dictionary) -> void:
	if data.has("error"):
		row.set_disabled_reason("")
		row.reason.text = str(data["error"])
		row.reason.show()
		model.editable = true
		return
	row.set_disabled_reason("")
	row.reason.hide()
	var on := bool(data.get("enabled", true))
	row.set_on(on)
	_saved_model = str(data.get("model", ""))
	if not model.has_focus():
		model.text = _saved_model
	model.editable = true
	model_row.visible = on


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	model_row.visible = on
	_run("save", ["reviewer", "--project", _project(), "--json", "--set", "on" if on else "off"])


func _save_model() -> void:
	var text := model.text.strip_edges()
	if text == _saved_model or not model.editable:
		return
	model.editable = false
	_run("save", ["reviewer", "--project", _project(), "--json", "--model", text])


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
		data = {"error": "Couldn't read the reviewer setting. Details are in Output."}
		print("[Joinery] reviewer\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
		model.text = _saved_model
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
