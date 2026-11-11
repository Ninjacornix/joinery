@tool
extends VBoxContainer
## The sidebar's top. When all is well it holds only the settings menu on the right: no name,
## no dot. It says "Connecting…" with the activity dots while Journeyman connects, and a red
## dot with "Not connected" after a failure. When Journeyman is not reachable a banner offers
## the one action that fixes it: a danger-tinted error for a lost connection, and a calm
## accent-tinted card for first-run setup (no account yet), which is a step, not a failure.
## The banner keeps SPACE.5 above and below, like other sections.
##
## Under the row, one quiet line can name an app that needs something, with its fix and a
## "Check again" link ([method show_readiness]).
##
## While several agents work at once (parallel tasks, ADR 0036) the row says how many, quietly:
## "3 agents working" with the activity dots ([method show_agents]).
##
## Beside the menu, a small ring shows how full the conversation's context is, with token use
## on hover ([member usage]); it appears only once the provider reports usage.

signal reconnect_pressed
signal connect_pressed
## "Check again" on the app readiness line.
signal readiness_check_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const USAGE_SCRIPT := preload("usage_meter.gd")

## The header row; the settings page adds its menu button here.
var row: HBoxContainer
## The context ring beside the menu (usage_meter.gd), shown once the provider reports usage.
var usage: USAGE_SCRIPT
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
var _readiness: HBoxContainer
var _readiness_text: Label
var _readiness_action: Button
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
	_build_readiness(stack)
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
	_banner_action.pressed.connect(
		func() -> void:
			if _banner_kind == "none":
				connect_pressed.emit()
			else:
				reconnect_pressed.emit()
	)
	column.add_child(_banner_action)


## Show [param state] ("connected", "connecting", "error", "none"). [param agent] names the
## provider in the lost-connection banner only ("Lost connection to Codex."); the header itself
## never names it. [param detail] is the error to show in the banner. Without
## [param with_banner] an error only turns the dot red: used when a request that failed to
## send already offers Retry.
func show_state(state: String, agent: String, detail := "", with_banner := true) -> void:
	var named := agent if not agent.is_empty() else "Journeyman"
	_state = state
	_indicator.set("running", state == "connecting")
	# Healthy is quiet: nothing but the menu.
	_dot.visible = state == "error"
	_dot.set("state", "attention" if state == "error" else "off")
	_dot.tooltip_text = detail if state == "error" and not detail.is_empty() else "Not connected"
	_name.text = str({"connecting": "Connecting…", "error": "Not connected"}.get(state, ""))
	_name.tooltip_text = _name.text
	if state == "connected":
		_show_count()
	if state == "none":
		_show_banner("none", "Connect Journeyman to your account to start.", "Connect")
	elif state == "error" and with_banner:
		var text := detail if not detail.is_empty() else "Lost connection to %s." % named
		_show_banner("error", text, "Reconnect")
	elif _banner.visible:
		UI_MOTION.exit(_banner)
		_banner_kind = ""


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


## One line about an app that needs something ("Blender needs Docker. Start Docker Desktop,
## then Check again."), with a "Check again" link; "" hides it.
func show_readiness(text: String) -> void:
	var fresh := not _readiness.visible or _readiness_text.text != text
	_readiness_text.text = text
	_readiness_text.tooltip_text = text
	if text.is_empty():
		if _readiness.visible:
			UI_MOTION.exit(_readiness)
	elif fresh:
		UI_MOTION.enter(_readiness)


## The readiness line's text ("" when hidden).
func readiness_text() -> String:
	return _readiness_text.text if _readiness.visible else ""


func _build_readiness(column: VBoxContainer) -> void:
	_readiness = HBoxContainer.new()
	_readiness.theme_type_variation = "JoineryRowTight"
	_readiness.accessibility_name = "App status"
	_readiness.hide()
	column.add_child(_readiness)
	_readiness_text = UI.label("", "JoineryCaption", true)
	_readiness_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_readiness.add_child(_readiness_text)
	_readiness_action = UI.link("Check again")
	_readiness_action.accessibility_name = "Check the apps again"
	_readiness_action.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(_readiness_action, "xs")
	_readiness_action.pressed.connect(func() -> void: readiness_check_pressed.emit())
	_readiness.add_child(_readiness_action)


func _show_banner(kind: String, text: String, action: String) -> void:
	var fresh := not _banner.visible or kind != _banner_kind
	_banner_kind = kind
	_banner_text.text = text
	_banner_action.text = action
	_banner_action.accessibility_name = action
	_banner_icon.visible = kind == "error"
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
