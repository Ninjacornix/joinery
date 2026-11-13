@tool
extends VBoxContainer
## Settings → Replies: which agent Journeyman runs ("Agent: Claude Code" with Switch), and the
## question asked in place before the chat restarts while Journeyman works. Restarting stops
## the current request and the parallel tasks; changes waiting in Review stay (the chat host
## keeps them, chat_bridge.gd close()).

## The user chose [param command]: "connect" (the Connect screen) or "reconnect".
signal run_requested(command: String)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const SWITCH_ASK := "Switching stops the current request and tasks. Changes in Review stay."
const RECONNECT_ASK := "Reconnecting stops the current request and tasks. Changes in Review stay."

## The sidebar: its agent name ("_agent_name"), request (_busy) and tasks (_tasks).
var dock: Control
var row: UI.SettingRow
var switch_button: Button
## "Switching stops …" with Cancel and Switch (or Reconnect); hidden until needed.
var confirm: MarginContainer
var _ask: Label
var _go: Button
var _command := ""


func _init() -> void:
	name = "AgentSetting"
	theme_type_variation = "JoineryFlush"
	switch_button = UI.secondary("Switch")
	switch_button.accessibility_name = "Switch agent"
	switch_button.tooltip_text = "Connect Journeyman to Codex, Claude Code, Gemini CLI or opencode"
	switch_button.pressed.connect(func() -> void: request("connect"))
	row = UI.setting_row("Agent", switch_button)
	add_child(row)
	confirm = MarginContainer.new()
	confirm.theme_type_variation = "JoineryInset"
	confirm.name = "RestartConfirm"
	confirm.hide()
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	confirm.add_child(line)
	_ask = UI.label("", "JoineryCaption", true)
	_ask.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.add_child(_ask)
	var cancel := UI.secondary("Cancel")
	cancel.accessibility_name = "Keep the current request running"
	cancel.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	cancel.pressed.connect(close_question)
	line.add_child(cancel)
	_go = UI.primary("Switch")
	_go.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_go.pressed.connect(
		func() -> void:
			var command := _command
			close_question()
			run_requested.emit(command)
	)
	line.add_child(_go)
	add_child(confirm)


## Name the agent in use; Connect while none is.
func refresh() -> void:
	var agent: Variant = dock.get("_agent_name") if dock != null else null
	var named: bool = agent is String and not agent in ["", "Journeyman"]
	row.title.text = "Agent: " + str(agent) if named else "Agent: none connected"
	switch_button.text = "Switch" if named else "Connect"


## Run [param command] ("connect" or "reconnect") now, or, while Journeyman works, ask first
## in place: under [param near] (the button pressed), else under the Agent row.
func request(command: String, near: Control = null) -> void:
	if not working(dock):
		run_requested.emit(command)
		return
	_command = command
	_ask.text = SWITCH_ASK if command == "connect" else RECONNECT_ASK
	_go.text = "Switch" if command == "connect" else "Reconnect"
	_go.accessibility_name = _go.text + " and stop the current request"
	var place: Control = near if near != null and near.get_parent() != null else row
	if confirm.get_parent() != place.get_parent():
		confirm.reparent(place.get_parent(), false)
	place.get_parent().move_child(confirm, place.get_index() + 1)
	if not confirm.visible:
		UI_MOTION.enter(confirm)


func close_question() -> void:
	_command = ""
	confirm.hide()


## True while the sidebar's own request or a parallel task is running.
static func working(sidebar: Object) -> bool:
	if sidebar == null:
		return false
	if sidebar.get("_busy") == true:
		return true
	var tasks: Variant = sidebar.get("_tasks")
	return tasks is Object and tasks.has_method("working_count") and tasks.working_count() > 0
