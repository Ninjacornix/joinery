@tool
extends MarginContainer
## The one notice under the sidebar header about getting Joinery itself ready, with a real
## button that does the fix in place: no terminal, no command to type.
##
## Today it knows one thing: this project's copy of the Joinery add-on is older than the
## Joinery that runs it ("Joinery's add-on for this project is out of date" with
## Update and restart). The button starts `joinery update-addon --after-exit`, a helper that
## waits for this editor to close, updates the add-on and opens the project again; the editor
## then closes itself, asking about unsaved scenes as usual ([signal restart_requested]).
##
## The header hides it while the connection banner shows ([method set_held]): one notice at a
## time, and Connect wins.

## The helper is waiting: close the editor so it can update the add-on.
signal restart_requested

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const COMMAND := preload("command_job.gd")
const ADDON_STALE := "Joinery's add-on for this project is out of date."
const ADDON_ACTION := "Update and restart"

## The command that runs the Joinery CLI and its leading arguments. Empty: the engine
## checkout's `uv run … joinery`. Tests point it at a fixture.
var cli_prefix := PackedStringArray()
## The project the notice is about (empty: the open one).
var project_path := ""
## The editor the project reopens in (empty: this one).
var editor_path := ""

var _panel: PanelContainer
var _text: Label
var _action: Button
## What the notice says now: "" (nothing) or "addon".
var _kind := ""
var _held := false
var _addon := {}
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "SetupNotice"
	hide()
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			# The header and dock rows already put SPACE.2 between rows; with this, SPACE.4.
			var gap := UI_THEME.space(self, 2)
			add_theme_constant_override("margin_top", gap)
			add_theme_constant_override("margin_bottom", gap)
	)
	_panel = UI.card()
	_panel.accessibility_name = "Joinery setup"
	add_child(_panel)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	_panel.add_child(column)
	_text = UI.label("", "", true)
	_text.name = "Text"
	column.add_child(_text)
	_action = UI.primary("")
	_action.name = "Action"
	_action.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_action.pressed.connect(_on_action)
	column.add_child(_action)
	set_process(false)


func _ready() -> void:
	if Engine.is_editor_hint() and project_path.is_empty():
		# Is this project's add-on older than the Joinery running it (after an update)?
		check_addon.call_deferred()


## Ask whether this project's add-on is older than Joinery's (`joinery update-addon --check`).
func check_addon() -> void:
	_run("addon_check", ["update-addon", "--project", _project(), "--check"])


## The add-on is out of date (the chat refused an older add-on): offer the update now.
func mark_addon_stale() -> void:
	if str(_addon.get("state", "")) != "stale":
		_addon = {"state": "stale"}
	_refresh()


## True while the add-on is known to be out of date.
func addon_stale() -> bool:
	return str(_addon.get("state", "")) == "stale"


## Hide the notice while [param held] (the connection banner is up); show it again after.
func set_held(held: bool) -> void:
	if _held == held:
		return
	_held = held
	_refresh()


## What the notice says now ("" when hidden), and its button's text.
func text() -> String:
	return _text.text if visible else ""


func action_text() -> String:
	return _action.text if visible and _action.visible else ""


## Press the notice's button (tests, and keyboard users through the button itself).
func press() -> void:
	_on_action()


func _refresh() -> void:
	var kind := ""
	var words := ""
	var action := ""
	if addon_stale():
		kind = "addon"
		var failed := str(_addon.get("last_error", ""))
		words = (
			ADDON_STALE
			if failed.is_empty()
			else "Joinery couldn't update this project's add-on: %s" % failed
		)
		action = ADDON_ACTION if failed.is_empty() else "Try again"
		if _job_kind == "addon_update":
			words = "Getting ready to restart…"
	_kind = kind
	var fresh := _text.text != words or not visible
	_text.text = words
	_text.tooltip_text = words
	_action.text = action
	_action.accessibility_name = action
	_action.visible = not action.is_empty()
	_action.disabled = not _job_kind.is_empty() and _job_kind != "addon_check"
	var show_it := not kind.is_empty() and not _held
	if show_it and fresh:
		UI_MOTION.enter(self)
	elif not show_it and visible:
		if is_inside_tree():
			UI_MOTION.exit(self)
		else:
			hide()


func _on_action() -> void:
	if _kind == "addon" and _job_kind != "addon_update":
		var editor := editor_path if not editor_path.is_empty() else OS.get_executable_path()
		var args := ["update-addon", "--project", _project()]
		args += ["--after-exit", str(OS.get_process_id()), "--editor", editor]
		_run("addon_update", args)
		_refresh()


func _run(kind: String, args: Array) -> void:
	if _job != null:
		if _job_kind == kind:
			return
		_job.call("cancel")
	_job = COMMAND.joinery(cli_prefix, args)
	_job_kind = kind if _job != null else ""
	set_process(_job != null)


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	var done: Dictionary = _job.call("poll")
	if done.is_empty():
		return
	var kind := _job_kind
	_job = null
	_job_kind = ""
	set_process(false)
	_finished(kind, int(done.get("code", 1)), _json(str(done.get("output", ""))), done)


func _finished(kind: String, code: int, data: Dictionary, raw: Dictionary) -> void:
	match kind:
		"addon_check":
			if code == 0 and data.has("state"):
				_addon = data
		"addon_update":
			if code == 0 and str(data.get("state", "")) == "scheduled":
				restart_requested.emit()
				if Engine.is_editor_hint() and is_inside_tree():
					# The editor's own close asks about unsaved scenes; the helper waits for it.
					get_tree().root.propagate_notification(NOTIFICATION_WM_CLOSE_REQUEST)
			else:
				print("[Joinery] update-addon\n", raw.get("output", ""), "\n", raw.get("error", ""))
				_addon["last_error"] = str(data.get("error", "Details are in Output."))
	_refresh()


func _project() -> String:
	if not project_path.is_empty():
		return project_path
	return ProjectSettings.globalize_path("res://").rstrip("/")


## The last JSON object line in [param output], or {}.
static func _json(output: String) -> Dictionary:
	var lines := output.split("\n", false)
	lines.reverse()
	for line: String in lines:
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			return parsed
	return {}
