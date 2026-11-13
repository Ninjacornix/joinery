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
##   tools runs the refresh right here, Start Docker opens Docker and asks it again every 3 s
##   for 2 minutes (no Check again needed), Check again asks again.
##
## A refresh stays on show while it runs (`joinery bootstrap --progress`): which step, a bar,
## Cancel and Show log. If it fails, the notice says why in plain words and keeps Show log.
## Only one refresh runs at a time: one started anywhere else (the app rail, Project setup, a
## terminal, another editor) is followed here too, through `joinery bootstrap --join`
## ([member watch_refreshes]), and pressing Refresh tools while one runs joins it.
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
const DOCKER_OPENING := "Opening Docker Desktop. Joinery checks again by itself once it runs."
## After Start Docker: ask Docker every 3 s, 40 times (2 minutes).
const DOCKER_INTERVAL := 3.0
const DOCKER_POLLS := 40

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
## Look every 2 s for a refresh started elsewhere and show its progress (on in the editor).
var watch_refreshes := Engine.is_editor_hint()
## Where a running refresh says how far it is (empty: Joinery's readiness cache).
var refresh_state_path := ""

var _panel: PanelContainer
var _text: Label
var _action: Button
var _bar: ProgressBar
var _links: HBoxContainer
var _cancel: Button
var _show_log: Button
## The last progress line of a refresh ({progress, step, steps, text, log}), or {}.
var _progress := {}
## The refresh's result line (its last), when it came with the progress lines.
var _result := {}
## The process of the last refresh followed from elsewhere: followed once, never again.
var _gone := 0
## The refresh log, once a refresh has run.
var _log := ""
## What the notice says now: "" (nothing), "addon" or "tools".
var _kind := ""
var _held := false
var _addon := {}
## The apps' notice from `joinery status` ({state, text, tools, action}), or {}.
var _tools := {}
## A sentence that replaces the apps' notice until the next report (a failed refresh).
var _said := ""
## Running CLI jobs by kind ("addon_check", "addon_update", "refresh", "cancel", "docker").
var _jobs := {}
## Questions left to ask Docker after Start Docker, and seconds to the next one.
var _docker_polls := 0
var _docker_in := 0.0


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
	_bar = ProgressBar.new()
	_bar.name = "Progress"
	_bar.show_percentage = false
	_bar.max_value = 1.0
	_bar.step = 0.001
	_bar.accessibility_name = "Refresh progress"
	STYLE.on_theme_overrides(
		_bar, func() -> void: _bar.custom_minimum_size.y = UI_THEME.space(_bar, 1)
	)
	column.add_child(_bar)
	_links = HBoxContainer.new()
	_links.theme_type_variation = "JoineryRow"
	column.add_child(_links)
	_cancel = UI.quiet("Cancel")
	_cancel.name = "Cancel"
	_cancel.pressed.connect(cancel_refresh)
	_links.add_child(_cancel)
	_show_log = UI.link("Show log")
	_show_log.name = "ShowLog"
	_show_log.pressed.connect(show_log)
	_links.add_child(_show_log)
	set_process(false)


func _ready() -> void:
	if Engine.is_editor_hint() and project_path.is_empty():
		# Is this project's add-on older than the Joinery running it (after an update)?
		check_addon.call_deferred()
	if watch_refreshes:
		var timer := Timer.new()
		timer.wait_time = 2.0
		timer.timeout.connect(_look_for_refresh)
		add_child(timer)
		timer.start()
		_look_for_refresh.call_deferred()


## A refresh another process runs (refresh.json says so, and its process is alive): follow it.
func _look_for_refresh() -> void:
	if refreshing():
		return
	var path := refresh_state_path if not refresh_state_path.is_empty() else _state_file()
	if not FileAccess.file_exists(path):
		return
	var state: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not state is Dictionary or str((state as Dictionary).get("state", "")) != "running":
		return
	# Godot can't ask about a process it didn't start: --join says whether it still runs.
	var pid := int((state as Dictionary).get("pid", 0))
	if pid <= 0 or pid == _gone:
		return
	_gone = pid
	_progress = state
	_log = str((state as Dictionary).get("log", _log))
	_said = ""
	_result = {}
	_run("refresh", ["bootstrap", "--project", _project(), "--progress", "--join"])
	_refresh()


## refresh.json in Joinery's readiness cache (joinery/readiness.py cache_dir).
static func _state_file() -> String:
	var folder := OS.get_environment("JOINERY_READINESS_CACHE")
	if folder.is_empty():
		var home := OS.get_environment("USERPROFILE" if OS.get_name() == "Windows" else "HOME")
		folder = home.path_join(".cache/joinery")
	return folder.path_join("refresh.json")


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


## Refresh tools now (the rail's or the notice's button): the progress shows here.
func start_refresh() -> void:
	_said = ""
	_progress = {}
	_result = {}
	_run("refresh", ["bootstrap", "--project", _project(), "--progress"])
	_refresh()


## Stop the refresh that is running; the notice goes back to what the apps need.
func cancel_refresh() -> void:
	if refreshing():
		_run("cancel", ["bootstrap", "--project", _project(), "--cancel"])
		_cancel.disabled = true


## Open the refresh log in the system's text viewer.
func show_log() -> void:
	if not _log.is_empty() and FileAccess.file_exists(_log):
		OS.shell_open(_log)


## How far the refresh is (0 to 1), and the log it writes.
func progress() -> float:
	return float(_progress.get("progress", 0.0))


func log_path() -> String:
	return _log


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
		words = str(_progress.get("text", "Refreshing %s…" % _apps()))
	elif _docker_polls > 0 and str(_tools.get("state", "")) == "needs_docker":
		kind = "tools"
		words = DOCKER_OPENING
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
	_bar.visible = kind == "tools" and refreshing()
	_bar.value = progress()
	_cancel.visible = _bar.visible
	_cancel.disabled = _jobs.has("cancel")
	_show_log.visible = (
		kind == "tools" and not _log.is_empty() and (_bar.visible or not _said.is_empty())
	)
	_links.visible = _cancel.visible or _show_log.visible
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
		start_refresh()
	elif _kind == "tools":
		var found: Variant = _tools.get("action")
		match str((found as Dictionary).get("id", "")) if found is Dictionary else "":
			"rebuild":
				start_refresh()
			"start_docker":
				if not start_docker_argv.is_empty():
					OS.create_process(start_docker_argv[0], start_docker_argv.slice(1))
				_docker_polls = DOCKER_POLLS
				_docker_in = DOCKER_INTERVAL
				set_process(true)
			"get":
				setup_requested.emit()
			"check_again":
				check_requested.emit()
	_refresh()


func _run(kind: String, args: Array) -> void:
	if _jobs.has(kind):
		return
	var job: RefCounted = COMMAND.joinery(cli_prefix, args)
	if job != null:
		_jobs[kind] = job
	set_process(not _jobs.is_empty() or _docker_polls > 0)


func _process(delta: float) -> void:
	if _docker_polls > 0 and not _jobs.has("docker"):
		_docker_in -= delta
		if _docker_in <= 0.0:
			# Docker itself, not the cached answer, which still says it isn't running.
			_run("docker", ["status", "--project", _project(), "--json"])
	if _jobs.has("refresh"):
		var moved := false
		for line: String in (_jobs["refresh"] as RefCounted).call("take_lines"):
			moved = _on_progress(_json(line)) or moved
		if moved:
			_refresh()
	for kind: String in _jobs.keys():
		var done: Dictionary = (_jobs[kind] as RefCounted).call("poll")
		if done.is_empty():
			continue
		_jobs.erase(kind)
		var data := _json(str(done.get("output", "")))
		if kind == "refresh" and data.is_empty():
			data = _result  # The result line already came through take_lines.
		_finished(kind, int(done.get("code", 1)), data, done)
	set_process(not _jobs.is_empty() or _docker_polls > 0)


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
		"docker":
			_docker_in = DOCKER_INTERVAL
			var up := str(data.get("docker", "")) == "ok"
			_docker_polls = 0 if up else _docker_polls - 1
			if data.has("tools"):
				show_readiness(data)
			if up:
				check_requested.emit()  # The checks Docker kept waiting can run now.
		"refresh":
			_log = str(data.get("log", _log))
			_progress = {}
			if code != 0:
				print("[Joinery] refresh\n", raw.get("output", ""), "\n", raw.get("error", ""))
				var why := str(data.get("error", ""))
				_said = why if not why.is_empty() else "The refresh didn't finish."
			check_requested.emit()
	_refresh()


## A line of a running refresh: progress (true when it moved the notice on) or its result.
func _on_progress(event: Dictionary) -> bool:
	if not event.has("progress") or str(event.get("state", "running")) != "running":
		if not event.is_empty():
			_result = event
		return false
	_progress = event
	_log = str(event.get("log", _log))
	return true


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
