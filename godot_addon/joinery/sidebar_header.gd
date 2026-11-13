@tool
extends VBoxContainer
## The sidebar's top. When all is well it holds only the settings menu on the right: no name,
## no dot. It says "Connecting…" with the activity dots while Journeyman connects, and a red
## dot with "Not connected" after a failure. When Journeyman is not reachable a banner offers
## the one action that fixes it: a danger-tinted error for a lost connection, and a calm
## accent-tinted card for first-run setup (no account yet), which is a step, not a failure.
## The banner keeps SPACE.5 above and below, like other sections.
##
## Below the banner, one notice can say what Joinery or the project's apps need, with a button
## that does it ([member notice], setup_notice.gd). Connect wins: while the banner asks to
## connect, the notice waits.
##
## A lost connection's banner offers the fix for its reason ([method show_problem]): Get or
## Update (the agent's install page), Sign in (the Connect screen), Repair (rewrites the
## project's policy hook, then reconnects) or Reconnect. "Switch agent" sits under it always,
## and "Copy details" when the agent left error output.
##
## While several agents work at once (parallel tasks, ADR 0036) the row says how many, quietly:
## "3 agents working" with the activity dots ([method show_agents]).
##
## Beside the menu, a small ring shows how full the conversation's context is, with token use
## on hover ([member usage]); it appears only once the provider reports usage.

signal reconnect_pressed
signal connect_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const USAGE_SCRIPT := preload("usage_meter.gd")
const NOTICE_SCRIPT := preload("setup_notice.gd")
const COMMAND := preload("command_job.gd")
## The banner's action for each connection error reason; any other reads "Reconnect".
const FIXES := {
	"not_installed": "Get %s", "signed_out": "Sign in", "outdated": "Update %s", "gate": "Repair"
}

## The header row; the settings page adds its menu button here.
var row: HBoxContainer
## The context ring beside the menu (usage_meter.gd), shown once the provider reports usage.
var usage: USAGE_SCRIPT
## What Joinery or the project's apps need, below the banner (setup_notice.gd).
var notice: NOTICE_SCRIPT
var _dot: Control
var _indicator: Control
var _name: Label
var _banner: PanelContainer
var _banner_gap: MarginContainer
var _banner_style: StyleBoxFlat
var _banner_icon: TextureRect
var _banner_text: Label
var _banner_action: Button
var _banner_kind := ""
var _banner_links: HBoxContainer
var _copy_link: Button
## The last connection error's {reason, agent, name, url, details} (chat_bridge.gd).
var _problem: Dictionary = {}
var _repair_job: RefCounted
## How many agents work now (shown from two up), and the connection state the row last showed.
var _agents := 0
var _state := "connected"


func _init() -> void:
	theme_type_variation = "JoineryRow"
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryTight"
	add_child(stack)
	# The row's trailing icon buttons pad their glyphs; reach past the gutter by that padding,
	# so the last glyph ends on the same edge as the text and boxes below.
	var inset := MarginContainer.new()
	STYLE.on_theme_overrides(
		inset,
		func() -> void: inset.add_theme_constant_override("margin_right", -STYLE.icon_inset(inset))
	)
	stack.add_child(inset)
	row = HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.set_meta("joinery_gutter", true)
	inset.add_child(row)
	_dot = UI.status_dot("off")
	row.add_child(_dot)
	_indicator = INDICATOR.new()
	row.add_child(_indicator)
	_name = UI.label("", "JoineryCaption")
	_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_name)
	usage = USAGE_SCRIPT.new()
	row.add_child(usage)
	# The gap around the banner comes and goes with it, so the idle header stays tight.
	_banner_gap = MarginContainer.new()
	_banner_gap.hide()
	STYLE.on_theme_overrides(
		_banner_gap,
		func() -> void:
			# The header and dock rows already put SPACE.2 between rows; together, SPACE.5.
			var gap := UI_THEME.space(_banner_gap, 4)
			_banner_gap.add_theme_constant_override("margin_top", gap)
			_banner_gap.add_theme_constant_override("margin_bottom", gap)
	)
	add_child(_banner_gap)
	_banner = PanelContainer.new()
	STYLE.on_theme_overrides(_banner, _restyle_banner)
	_banner.hide()
	_banner.visibility_changed.connect(func() -> void: _banner_gap.visible = _banner.visible)
	_banner_gap.add_child(_banner)
	var body := HBoxContainer.new()
	body.theme_type_variation = "JoineryRow"
	_banner.add_child(body)
	_banner_icon = STYLE.glyph("danger")
	STYLE.set_glyph(_banner_icon, "error", "danger")
	_banner_icon.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	body.add_child(_banner_icon)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_child(column)
	_banner_text = UI.label("", "", true)
	column.add_child(_banner_text)
	_banner_action = UI.primary("")
	_banner_action.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_banner_action.pressed.connect(_fix)
	column.add_child(_banner_action)
	notice = NOTICE_SCRIPT.new()
	add_child(notice)
	_banner_links = HBoxContainer.new()
	_banner_links.theme_type_variation = "JoineryRow"
	column.add_child(_banner_links)
	var switch := UI.link("Switch agent")
	switch.accessibility_name = "Switch to another agent"
	UI.bind_height(switch, "xs")
	switch.pressed.connect(func() -> void: connect_pressed.emit())
	_banner_links.add_child(switch)
	_copy_link = UI.link("Copy details")
	_copy_link.accessibility_name = "Copy the agent's error output"
	UI.bind_height(_copy_link, "xs")
	_copy_link.pressed.connect(
		func() -> void: DisplayServer.clipboard_set(str(_problem.get("details", "")))
	)
	_banner_links.add_child(_copy_link)
	set_process(false)


## Show [param state] ("connected", "connecting", "error", "none"). [param agent] names the
## provider in the lost-connection banner only ("Lost connection to Codex."); the header itself
## never names it. [param detail] is the error to show in the banner. Without
## [param with_banner] an error only turns the dot red: used when a request that failed to
## send already offers Retry.
func show_state(state: String, agent: String, detail := "", with_banner := true) -> void:
	var named := agent if not agent.is_empty() else "Journeyman"
	_state = state
	if state == "error" and (detail.begins_with(notice.ADDON_STALE) or notice.addon_stale()):
		# The chat refused this older add-on: Update and restart fixes it, not Reconnect.
		notice.mark_addon_stale()
		with_banner = false
	_indicator.set("running", state == "connecting")
	# Healthy is quiet: nothing but the menu. While the banner says the connection is lost, the
	# header doesn't say it again.
	var banner := state == "none" or (state == "error" and with_banner)
	_dot.visible = state == "error" and not banner
	_dot.set("state", "attention" if state == "error" else "off")
	_dot.tooltip_text = detail if state == "error" and not detail.is_empty() else "Not connected"
	var words := {"connecting": "Connecting…", "error": "" if banner else "Not connected"}
	_name.text = str(words.get(state, ""))
	_name.tooltip_text = _name.text
	if state == "connected":
		_show_count()
	if state == "none":
		_show_banner("none", "Connect Journeyman to your account to start.", "Connect")
	elif state == "error" and with_banner:
		var text := detail if not detail.is_empty() else "Lost connection to %s." % named
		var fix: String = FIXES.get(str(_problem.get("reason", "")), "Reconnect")
		_show_banner("error", text, fix.replace("%s", str(_problem.get("name", named))))
	elif _banner.visible:
		UI_MOTION.exit(_banner)
		_banner_kind = ""
	# One notice at a time: while the banner asks to connect, it is the one shown.
	notice.set_held(state == "none" or (state == "error" and with_banner))


## The connection error's reason and its details ({} once connected), shown with the next
## error state.
func show_problem(problem: Dictionary) -> void:
	_problem = problem.duplicate()


## The banner's action ("Reconnect", "Sign in"…) and links, for tests ("" when hidden).
func banner_action() -> String:
	return _banner_action.text if _banner.visible else ""


func banner_links() -> PackedStringArray:
	var links := PackedStringArray()
	for link: Node in _banner_links.get_children():
		if (link as Control).visible:
			links.append((link as Button).text)
	return links


func _fix() -> void:
	if _banner_kind == "none":
		connect_pressed.emit()
		return
	var reason := str(_problem.get("reason", ""))
	var url := str(_problem.get("url", ""))
	if reason in ["not_installed", "outdated"] and not url.is_empty():
		OS.shell_open(url)
	elif reason in ["not_installed", "signed_out"]:
		connect_pressed.emit()
	elif reason == "gate" and _repair_job == null:
		_repair()
	else:
		reconnect_pressed.emit()


## Rewrite the project's policy hook for this agent (`joinery connect repair-gate`), then
## reconnect.
func _repair() -> void:
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var agent := str(_problem.get("agent", ""))
	var args := ["connect", "repair-gate", "--agent", agent, "--project", project]
	_repair_job = COMMAND.joinery(PackedStringArray(), args)
	_banner_action.disabled = _repair_job != null
	set_process(_repair_job != null)


func _process(_delta: float) -> void:
	_repair_job = COMMAND.finish(_repair_job, _repaired)
	if _repair_job == null:
		set_process(false)


func _repaired(result: Dictionary) -> void:
	_banner_action.disabled = false
	var answer: Variant = JSON.parse_string(str(result.get("output", "")).get_slice("\n", 0))
	if int(result.get("code", 1)) == 0:
		reconnect_pressed.emit()
	else:
		var error := str(answer.get("error", "")) if answer is Dictionary else ""
		_banner_text.text = error if not error.is_empty() else "Couldn't repair the policy hook."
		print("[Joinery] repair-gate\n", result.get("output", ""), "\n", result.get("error", ""))


## [param count] agents work now, Journeyman's own request included. From two up the row says
## "3 agents working" while connected; one or none leaves it quiet.
func show_agents(count: int) -> void:
	_agents = count
	if _state == "connected":
		_show_count()


## The agent count line ("" when fewer than two work).
func agents_text() -> String:
	return _name.text if _agents >= 2 and _state == "connected" else ""


func _show_count() -> void:
	var several := _agents >= 2
	var text := "%d agents working" % _agents if several else ""
	if _name.text == text:
		return
	var counting := _name.text.ends_with(" agents working")
	_name.tooltip_text = "Journeyman and its parallel tasks" if several else ""
	_indicator.set("running", several)
	if several and counting:
		# The number rolls like an odometer; the words stay put.
		UI_MOTION.roll_number(_name, _agents, "%d agents working")
		return
	_name.text = text
	_name.set_meta("_joinery_value", _agents)
	if several:
		UI_MOTION.fade_in(_name)


func _show_banner(kind: String, text: String, action: String) -> void:
	var fresh := not _banner.visible or kind != _banner_kind
	_banner_kind = kind
	_banner_text.text = text
	_banner_action.text = action
	_banner_action.accessibility_name = action
	_banner_icon.visible = kind == "error"
	_banner_links.visible = kind == "error"
	_copy_link.visible = not str(_problem.get("details", "")).is_empty()
	_paint_banner()
	if fresh:
		UI_MOTION.enter(_banner)


func _restyle_banner() -> void:
	var source := _banner.get_theme_stylebox("panel", "JoineryBanner")
	if not source is StyleBoxFlat:
		return
	_banner_style = source.duplicate() as StyleBoxFlat
	var pad := float(UI_THEME.space(_banner, 2))
	_banner_style.content_margin_left = pad
	_banner_style.content_margin_right = pad
	_paint_banner()
	_banner.add_theme_stylebox_override("panel", _banner_style)


## Errors keep the theme's danger tint; first-run setup reads as information, in accent tint.
func _paint_banner() -> void:
	if _banner_style == null:
		return
	var role := "accent_tint" if _banner_kind == "none" else "danger_tint"
	_banner_style.bg_color = _banner.get_theme_color(role, "Joinery")
