@tool
extends VBoxContainer
## Sharing in Journeyman settings → Project (ADR 0071): the remote this project is shared
## through and the teammates seen in its history, from `joinery team status`, read whenever the
## settings page shows it. A project not shared yet offers one field for the remote and Connect
## (`joinery team connect`); git's own login is used, Joinery never asks for a password.

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND := preload("command_job.gd")
const HINT := "Pull and Push are in the Timeline, with your own git login."

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
## Off in tests: nothing runs the CLI; answers come through [method apply_status].
var cli_enabled := true
var remote_row: UI.SettingRow
var remote_label: Label
var people: Label
var lfs_note: Label
var connect_row: UI.SettingRow
var address: LineEdit
var connect_button: Button
var note: Label
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "TeamSetting"
	theme_type_variation = "JoineryFlush"
	remote_row = UI.setting_row("Shared with")
	remote_row.hide()
	add_child(remote_row)
	# A whole address under the title: a remote cut short can't be told from another one.
	remote_label = UI.label("", "JoineryCaption", true)
	remote_label.name = "Remote"
	remote_label.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	remote_label.hide()
	add_child(_inset(remote_label))
	people = UI.label("", "JoineryCaption", true)
	people.name = "Teammates"
	people.hide()
	add_child(_inset(people))
	address = LineEdit.new()
	address.name = "Address"
	address.placeholder_text = "git@host:team/game.git"
	address.accessibility_name = "The shared remote's address"
	address.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(address, "md")
	address.text_submitted.connect(func(_text: String) -> void: _connect())
	connect_button = UI.secondary("Connect")
	connect_button.name = "Connect"
	connect_button.tooltip_text = "Share this project through that remote"
	connect_button.pressed.connect(_connect)
	UI_MOTION.press_feedback(connect_button)
	var field := HBoxContainer.new()
	field.theme_type_variation = "JoineryRowTight"
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	field.add_child(address)
	field.add_child(connect_button)
	connect_row = UI.setting_row("Share", field)
	connect_row.hide()
	add_child(connect_row)
	lfs_note = UI.label("", "JoineryError", true)
	lfs_note.name = "Lfs"
	lfs_note.hide()
	add_child(_inset(lfs_note))
	note = UI.label(HINT, "JoineryFaint", true)
	note.name = "Note"
	note.hide()
	add_child(_inset(note))
	set_process(false)


func _inset(label: Label) -> MarginContainer:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(label)
	label.visibility_changed.connect(func() -> void: inset.visible = label.visible)
	inset.visible = label.visible
	return inset


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree():
		refresh()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.call("cancel")
		_job = null


## Read the project's sharing again.
func refresh() -> void:
	if _job_kind == "connect" or not cli_enabled:
		return
	_run("status", ["team", "status", "--project", _project(), "--json"])


## Show a `joinery team status` (or `connect`) answer.
func apply_status(data: Dictionary) -> void:
	var state := str(data.get("state", ""))
	var connected := bool(data.get("connected", false))
	var sharable := state in ["ready", "none", "empty"]
	remote_row.visible = connected
	remote_label.text = str(data.get("remote", ""))
	remote_label.tooltip_text = remote_label.text
	remote_label.visible = connected
	connect_row.visible = not connected and sharable
	connect_button.disabled = _job_kind == "connect"
	connect_button.text = "Connecting…" if _job_kind == "connect" else "Connect"
	var names: Array[String] = []
	for person: Variant in data.get("teammates", []):
		if person is Dictionary:
			names.append(str(person.get("name", "")))
	people.text = (
		"Teammates in the history: " + ", ".join(names)
		if not names.is_empty()
		else "No teammates in the history yet."
	)
	people.visible = connected
	var lfs: Variant = data.get("lfs", {})
	lfs_note.text = str((lfs as Dictionary).get("message", "")) if lfs is Dictionary else ""
	if data.has("error"):
		lfs_note.text = str(data["error"])
	lfs_note.visible = not lfs_note.text.is_empty()
	var message := str(data.get("message", "")) if data.has("reachable") else ""
	note.text = message if not message.is_empty() else HINT
	note.visible = connected or connect_row.visible


func _connect() -> void:
	var remote := address.text.strip_edges()
	if remote.is_empty() or _job_kind == "connect":
		address.grab_focus()
		return
	if not cli_enabled:
		return
	_run("connect", ["team", "connect", remote, "--project", _project(), "--json"])
	connect_button.disabled = true
	connect_button.text = "Connecting…"


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
		data = {"error": "Couldn't read how this project is shared. Details are in Output."}
		print("[Joinery] team\n", result.get("output", ""), "\n", result.get("error", ""))
	if data.has("error"):
		# Still not shared: the field stays, with the reason under it.
		data["state"] = "none"
	apply_status(data)
	if kind == "connect" and not data.has("error"):
		UI_MOTION.pulse(remote_row)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
