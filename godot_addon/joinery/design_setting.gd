@tool
extends VBoxContainer
## "Check the design" in Journeyman settings: whether Joinery checks the game's design by
## itself after every N accepted changes (`[agents] design_check_every` in joinery.toml; Off by
## default). Read with `joinery design --settings` whenever the settings page shows it; a choice
## applies at once, and a failed write puts the picker back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
## Accepted changes between checks, by picker index (0: off), with their words.
const EVERY := [0, 1, 3, 5, 10]
const WORDS := [
	"Off", "After every change", "Every 3 changes", "Every 5 changes", "Every 10 changes"
]

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var picker: OptionButton
var row: Control
var reason: Label
var _job: RefCounted
var _job_kind := ""
var _saved := 0


func _init() -> void:
	name = "DesignSetting"
	theme_type_variation = "JoineryFlush"
	picker = UI.picker(WORDS)
	picker.accessibility_name = "Check the design after accepted changes"
	picker.tooltip_text = (
		"Joinery checks the game against its brief after this many accepted changes, and"
		+ " shows the design card when it finds something. /design checks at any time."
	)
	# Unknown until the project answers.
	picker.disabled = true
	picker.item_selected.connect(_on_selected)
	row = UI.setting_row("Check the design", picker)
	add_child(row)
	reason = UI.label("", "JoineryCaption", true)
	reason.hide()
	add_child(reason)
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
	_run("status", ["design", "--project", _project(), "--settings", "--json"])


## Show a `joinery design --settings` answer ({check_every} or {error}).
func apply_status(data: Dictionary) -> void:
	if data.has("error"):
		reason.text = str(data["error"])
		reason.show()
		picker.select(EVERY.find(_saved))
		picker.disabled = false
		return
	reason.hide()
	_saved = int(data.get("check_every", 0))
	var index := EVERY.find(_saved)
	if index < 0:
		# A number set by hand in joinery.toml: show it as it is.
		picker.add_item("Every %d changes" % _saved)
		index = picker.item_count - 1
	picker.select(index)
	picker.disabled = false


func _on_selected(index: int) -> void:
	var every: int = EVERY[index] if index < EVERY.size() else _saved
	if every == _saved:
		return
	picker.disabled = true
	_run("save", ["design", "--project", _project(), "--every", str(every), "--json"])


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
	_job = null
	_job_kind = ""
	set_process(false)
	var data: Dictionary = {}
	for line: String in str(result.get("output", "")).split("\n", false):
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			data = parsed
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Couldn't read the design check setting. Details are in Output."}
		print("[Joinery] design\n", result.get("output", ""), "\n", result.get("error", ""))
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
