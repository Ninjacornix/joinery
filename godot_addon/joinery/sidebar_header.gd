@tool
extends VBoxContainer
## The sidebar's top: a status dot and the assistant's name (or "Connecting to Codex…" with
## the activity dots), the settings menu on the right, and, when the assistant is not
## reachable, a banner with the one action that fixes it: a danger-tinted error for a lost
## connection, and a calm accent-tinted card for first-run setup (no assistant yet), which is
## a step, not a failure. The banner keeps SPACE.5 above and below, like other sections.

signal reconnect_pressed
signal connect_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")

## The header row; the settings page adds its menu button here.
var row: HBoxContainer
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


func _init() -> void:
	theme_type_variation = "JoineryRow"
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	add_child(inset)
	row = HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	inset.add_child(row)
	_dot = UI.status_dot("off")
	row.add_child(_dot)
	_indicator = INDICATOR.new()
	row.add_child(_indicator)
	_name = UI.label("", "JoineryCaption")
	_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_name)
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


## Show [param state] ("connected", "connecting", "error", "none") for [param agent].
## [param detail] is the error to show in the banner. Without [param with_banner] an error
## only turns the dot red: used when a request that failed to send already offers Retry.
func show_state(state: String, agent: String, detail := "", with_banner := true) -> void:
	var named := agent if not agent.is_empty() else "Assistant"
	_indicator.set("running", state == "connecting")
	_dot.visible = state != "connecting"
	_dot.set("state", {"connected": "ready", "error": "attention", "none": "off"}.get(state, "off"))
	_dot.tooltip_text = (
		{"connected": "Connected", "error": "Not connected", "none": "No assistant"}.get(state, "")
	)
	if state == "error" and not detail.is_empty():
		_dot.tooltip_text = detail
	_name.text = "Connecting to %s…" % named if state == "connecting" else named
	_name.tooltip_text = _name.text
	if state == "none":
		_show_banner("none", "No assistant yet. Connect one to start.", "Connect an assistant")
	elif state == "error" and with_banner:
		var text := detail if not detail.is_empty() else "Lost connection to %s." % named
		_show_banner("error", text, "Reconnect")
	elif _banner.visible:
		UI_MOTION.exit(_banner)
		_banner_kind = ""


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
