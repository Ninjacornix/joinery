@tool
extends MarginContainer
## The one notice under the sidebar header about getting Joinery and the project's apps ready,
## with a real button that does the fix in place: no terminal, no command to type.
##
## It says one thing at a time, the one to fix first:
## - this project's copy of the Joinery add-on is older than the Joinery that runs it
##   ([constant ADDON_STALE], Update and restart). The button starts
##   `joinery update-addon --after-exit`, a helper that waits for this editor to close, updates
##   the add-on and opens the project again; the editor then closes itself, asking about unsaved
##   scenes as usual ([signal restart_requested]);
## - the project's apps, from the `notice` of `joinery status` ([method show_readiness]): one
##   sentence that names every app with the same problem, worded by joinery/readiness.py
##   ("Blender and Krita need a refresh after the Joinery update."), and its button: Refresh
##   tools runs the refresh right here, Start Docker opens Docker, Check again asks again.
##
## The header hides it while the connection banner shows ([method set_held]): one notice at a
## time, and Connect wins.

## The helper is waiting: close the editor so it can update the add-on.
signal restart_requested
## Ask about the apps again (Check again, or a refresh finished).
signal check_requested
## A licence is added in Project setup.
signal setup_requested

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const COMMAND := preload("command_job.gd")
const ADDON_STALE := "Joinery's add-on for this project is out of date."
const ADDON_ACTION := "Update and restart"
## Button text per readiness action id, where the sidebar's differs from the label.
const ACTION_TEXT := {"get": "Open Project setup"}

## The command that runs the Joinery CLI and its leading arguments. Empty: the engine
## checkout's `uv run … joinery`. Tests point it at a fixture.
var cli_prefix := PackedStringArray()
## The project the notice is about (empty: the open one).
var project_path := ""
## The editor the project reopens in (empty: this one).
var editor_path := ""
## What "Start Docker" runs (empty: nothing; the sentence still says how).
var start_docker_argv := PackedStringArray(
	["open", "-a", "Docker"] if OS.get_name() == "macOS" else []
)

var _panel: PanelContainer
var _text: Label
var _action: Button
## What the notice says now: "" (nothing), "addon" or "tools".
var _kind := ""
var _held := false
var _addon := {}
## The apps' notice from `joinery status` ({state, text, tools, action}), or {}.
var _tools := {}
## A sentence that replaces the apps' notice until the next report (a failed refresh).
var _said := ""
## Running CLI jobs by kind ("addon_check", "addon_update", "refresh").
var _jobs := {}


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


## The apps' readiness from `joinery status --json`: its `notice`, if any, is shown.
func show_readiness(report: Dictionary) -> void:
	var found: Variant = report.get("notice")
	_tools = found if found is Dictionary else {}
	if _tools.is_empty():
		_said = ""  # A failed refresh is old news once every app is ready.
	_refresh()


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


## True while a refresh runs from here.
func refreshing() -> bool:
	return _jobs.has("refresh")


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
		if _jobs.has("addon_update"):
			words = "Getting ready to restart…"
	elif refreshing():
		kind = "tools"
		words = "Refreshing %s…" % _apps()
	elif not _said.is_empty():
		kind = "tools"
		words = _said
		action = "Try again"
	elif not _tools.is_empty():
		kind = "tools"
		words = str(_tools.get("text", ""))
		var found: Variant = _tools.get("action")
		if found is Dictionary:
			var id := str((found as Dictionary).get("id", ""))
			action = str(ACTION_TEXT.get(id, (found as Dictionary).get("label", "")))
	_kind = kind
	var fresh := _text.text != words or not visible
	_text.text = words
	_text.tooltip_text = words
	_action.text = action
	_action.accessibility_name = action
	_action.visible = not action.is_empty()
	_action.disabled = _jobs.has("addon_update")
	var show_it := not kind.is_empty() and not _held
	if show_it and fresh:
		UI_MOTION.enter(self)
	elif not show_it and visible:
		if is_inside_tree():
			UI_MOTION.exit(self)
		else:
			hide()


## The apps the notice is about, as readiness named them ("Blender and Krita"), or "tools".
func _apps() -> String:
	var words := str(_tools.get("text", ""))
	for verb: String in [" needs ", " need "]:
		if words.contains(verb):
			return words.get_slice(verb, 0)
	return "tools"


func _on_action() -> void:
	if _kind == "addon":
		if _jobs.has("addon_update"):
			return
		var editor := editor_path if not editor_path.is_empty() else OS.get_executable_path()
		var args := ["update-addon", "--project", _project()]
		args += ["--after-exit", str(OS.get_process_id()), "--editor", editor]
		_run("addon_update", args)
	elif _kind == "tools" and not _said.is_empty():
		_said = ""
		_start_refresh()
	elif _kind == "tools":
		var found: Variant = _tools.get("action")
		match str((found as Dictionary).get("id", "")) if found is Dictionary else "":
			"rebuild":
				_start_refresh()
			"start_docker":
				if not start_docker_argv.is_empty():
					OS.create_process(start_docker_argv[0], start_docker_argv.slice(1))
				check_requested.emit()
			"get":
				setup_requested.emit()
			"check_again":
				check_requested.emit()
	_refresh()


func _start_refresh() -> void:
	_run("refresh", ["bootstrap", "--project", _project()])


func _run(kind: String, args: Array) -> void:
	if _jobs.has(kind):
		return
	var job: RefCounted = COMMAND.joinery(cli_prefix, args)
	if job != null:
		_jobs[kind] = job
	set_process(not _jobs.is_empty())


func _process(_delta: float) -> void:
	for kind: String in _jobs.keys():
		var done: Dictionary = (_jobs[kind] as RefCounted).call("poll")
		if done.is_empty():
			continue
		_jobs.erase(kind)
		_finished(kind, int(done.get("code", 1)), _json(str(done.get("output", ""))), done)
	set_process(not _jobs.is_empty())


func _exit_tree() -> void:
	for job: RefCounted in _jobs.values():
		job.call("cancel")
	_jobs.clear()


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
		"refresh":
			if code != 0:
				print("[Joinery] refresh\n", raw.get("output", ""), "\n", raw.get("error", ""))
				var why := str(data.get("error", ""))
				_said = "The refresh didn't finish." + (" " + why if not why.is_empty() else "")
			check_requested.emit()
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
