@tool
extends MarginContainer
## The status line above the composer. Hidden when idle (it never says "Ready"). While the
## assistant works: activity dots and what it is doing. While a change waits: "Waiting for
## your review" with "Show review". While a check runs: "Running /doctor…". Short notes from
## [method show_status] replace it for a few seconds, then it returns to the current state.

signal review_requested

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")

## What the footer shows when no transient note is up: "idle", "busy", "review" or "command".
var mode := "idle"
var _indicator: Control
var _text: Label
var _show: Button
var _detail := ""
var _note := ""
var _note_serial := 0
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


## A transient line that clears after [param seconds].
func show_status(text: String, seconds := 3.0) -> void:
	_note = text
	_note_serial += 1
	var serial := _note_serial
	_refresh()
	if is_inside_tree():
		get_tree().create_timer(seconds).timeout.connect(
			func() -> void:
				if serial == _note_serial:
					_note = ""
					_refresh()
		)


func _refresh() -> void:
	var line := ""
	var working := false
	if not _note.is_empty():
		line = _note
	elif mode == "busy":
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
	_show.visible = mode == "review" and _note.is_empty()
	var should_show := not line.is_empty()
	if should_show and (not visible or _leaving):
		_leaving = false
		UI_MOTION.enter(self)
	elif not should_show and visible and not _leaving:
		_leaving = true
		UI_MOTION.exit(self, null, func() -> void: _leaving = false)
