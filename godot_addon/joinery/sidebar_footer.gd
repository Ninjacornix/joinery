@tool
extends MarginContainer
## The status line above the composer. Hidden when idle (it never says "Ready"). While
## Journeyman works: activity dots and what it is doing. While a change waits: "Waiting for
## your review" with "Show review". While a check runs: "Running /doctor…". Short notes are
## not shown here: they are the transcript's transient caption (chat_history.show_caption).

signal review_requested

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")

## What the footer shows: "idle", "busy", "review" or "command".
var mode := "idle"
var _indicator: Control
var _text: Label
var _show: Button
var _detail := ""
var _leaving := false


func _init() -> void:
	theme_type_variation = "JoineryInset"
	visible = false
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	add_child(row)
	_indicator = INDICATOR.new()
	row.add_child(_indicator)
	_text = UI.label("", "JoineryCaption")
	_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_text)
	_show = UI.link("Show review")
	_show.accessibility_name = "Show the change in the Review panel"
	UI.bind_height(_show, "xs")
	_show.pressed.connect(func() -> void: review_requested.emit())
	row.add_child(_show)


## The current text ("" when hidden).
func text() -> String:
	return _text.text if visible else ""


## Switch to [param state] ("idle", "busy", "review", "command"); [param detail] is the busy
## progress or the running command.
func set_mode(state: String, detail := "") -> void:
	mode = state
	_detail = detail
	_refresh()


func _refresh() -> void:
	var line := ""
	var working := false
	if mode == "busy":
		line = _detail if not _detail.is_empty() else "Thinking…"
		working = true
	elif mode == "command":
		line = "Running %s…" % _detail
		working = true
	elif mode == "review":
		line = "Waiting for your review"
	_text.text = line
	_text.tooltip_text = line
	_indicator.set("running", working)
	_show.visible = mode == "review"
	var should_show := not line.is_empty()
	if should_show and (not visible or _leaving):
		_leaving = false
		UI_MOTION.enter(self)
	elif not should_show and visible and not _leaving:
		_leaving = true
		UI_MOTION.exit(self, null, func() -> void: _leaving = false)
