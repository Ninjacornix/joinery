@tool
extends PanelContainer
## The console errors Joinery is watching (ADR 0075), with the agents' cards in the sidebar: one
## row per distinct error (same message, same place), how often it showed, where, and what is
## happening to it: waiting, being fixed, a fix waiting for review, stopped after two tries, or
## ignored. "Fix" on a row, or "Fix all" when the project asks first ([agents] fix_errors =
## "ask"), starts the debugger and a worker; the fix is a normal change the user reviews.
## The host sends the whole list each time it changes (chat_bridge.gd's errors_event).

signal fix_requested(ids: Array)
signal ignore_requested(id: String, ignore: bool)
signal review_requested(transaction_id: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## Rows shown before "Show all".
const FOLDED := 4
## status -> its row's status icon (ui_components.gd STATUS_ICONS); the rest show "failed".
const MARKS := {"in_review": "done", "ignored": "stopped", "from_change": "warning"}
## status -> what the row says about it.
const WORDS := {
	"open": "",
	"queued": "Waiting for a free agent",
	"fixing": "Fixing…",
	"in_review": "Fix ready for review",
	"stopped": "Stopped after 2 tries",
	"from_change": "Came with a change in review",
	"ignored": "Ignored",
}

var mode := "ask"
var headline: Label
var fix_all: Button
var _rows: VBoxContainer
var _more: Button
var _groups: Array = []
var _open := false
## Rows already on screen: only a new one enters with motion.
var _shown: Dictionary = {}


func _init() -> void:
	name = "ErrorsCard"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Console errors"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	headline = UI.label("")
	headline.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	headline.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	headline.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(headline)
	fix_all = UI.secondary("Fix all")
	fix_all.accessibility_name = "Fix all console errors"
	UI.bind_height(fix_all, "sm")
	fix_all.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(fix_all)
	fix_all.pressed.connect(_on_fix_all)
	head.add_child(fix_all)
	_rows = VBoxContainer.new()
	_rows.theme_type_variation = "JoineryTight"
	column.add_child(_rows)
	_more = UI.link("")
	UI.bind_height(_more, "xs")
	STYLE.hug_left(_more)
	_more.pressed.connect(_toggle_more)
	_more.hide()
	column.add_child(_more)


## The host's list: {mode, groups: [{id, label, message, place, count, status, note, …}]}.
func apply(event: Dictionary) -> void:
	mode = str(event.get("mode", mode))
	var groups: Variant = event.get("groups", [])
	_groups = groups if groups is Array else []
	_render()


## Rows that count as errors still to deal with (ignored ones are listed, not counted).
func open_count() -> int:
	var count := 0
	for group: Dictionary in _groups:
		if str(group.get("status", "")) != "ignored":
			count += 1
	return count


func has_rows() -> bool:
	return not _groups.is_empty() and mode != "off"


## The rows' error labels as shown, for tests.
func row_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for row: Node in _rows.get_children():
		if not row.is_queued_for_deletion():
			texts.append(str(row.get_meta("joinery_error", "")))
	return texts


func _render() -> void:
	var count := open_count()
	headline.text = "1 console error" if count == 1 else "%d console errors" % count
	headline.tooltip_text = "Errors from Output, the game's log, playtests and tests"
	var fixable := 0
	for group: Dictionary in _groups:
		if str(group.get("status", "")) in ["open", "stopped"]:
			fixable += 1
	fix_all.visible = mode == "ask" and fixable > 1
	for row: Node in _rows.get_children():
		row.queue_free()
	var shown := _groups if _open else _groups.slice(0, FOLDED)
	var entering: Array = []
	var now: Dictionary = {}
	for group: Dictionary in shown:
		var row := _row(group)
		_rows.add_child(row)
		var id := str(group.get("id", ""))
		now[id] = true
		if not _shown.has(id):
			entering.append(row)
	_shown = now
	if not entering.is_empty():
		UI_MOTION.stagger(entering)
	var hidden := _groups.size() - FOLDED
	_more.visible = hidden > 0
	_more.text = "Show fewer" if _open else "Show all %d" % _groups.size()


func _row(group: Dictionary) -> Control:
	var id := str(group.get("id", ""))
	var status := str(group.get("status", "open"))
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.set_meta("joinery_error", str(group.get("label", "")))
	row.accessibility_name = "Error: " + str(group.get("label", ""))
	var mark: Control
	if status in ["fixing", "queued"]:
		mark = INDICATOR.new()
		mark.set("running", status == "fixing")
	else:
		mark = UI.status_icon(str(MARKS.get(status, "failed")))
	mark.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(mark)
	var text := VBoxContainer.new()
	text.theme_type_variation = "JoineryTight"
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(text)
	var message := UI.label(str(group.get("message", "")), "", false)
	# Expanding: the row's width decides where the text ends, never the text.
	message.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	message.tooltip_text = str(group.get("message", ""))
	if status == "ignored":
		message.theme_type_variation = "JoineryCaption"
	text.add_child(message)
	var caption := UI.label(_caption(group), "JoineryCaption", false)
	caption.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var note := str(group.get("note", ""))
	caption.tooltip_text = note if not note.is_empty() else caption.text
	text.add_child(caption)
	var action := _action(group)
	if action != null:
		action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(action)
	if status in ["open", "stopped", "from_change"]:
		var ignore := UI.icon_button("close", "Ignore this error")
		ignore.accessibility_name = "Ignore: " + str(group.get("label", ""))
		ignore.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		STYLE.on_theme_overrides(
			ignore,
			func() -> void:
				var side := float(UI_THEME.control_h(ignore, "sm"))
				ignore.custom_minimum_size = Vector2(side, side)
		)
		ignore.pressed.connect(_on_ignore.bind(id, row))
		row.add_child(ignore)
	return row


## "player.gd:42 · 3 times · Fixing…": where, how often, and what is happening.
func _caption(group: Dictionary) -> String:
	var parts := PackedStringArray()
	var place := str(group.get("place", ""))
	if not place.is_empty():
		parts.append(place)
	var count := int(group.get("count", 1))
	parts.append("once" if count == 1 else "%d times" % count)
	var words := str(WORDS.get(str(group.get("status", "open")), ""))
	if str(group.get("status", "")) == "open" and bool(group.get("declined", false)):
		words = "Fix discarded"
	if not words.is_empty():
		parts.append(words)
	return " · ".join(parts)


func _action(group: Dictionary) -> Button:
	var id := str(group.get("id", ""))
	var label := str(group.get("label", ""))
	var button: Button
	match str(group.get("status", "open")):
		"open", "stopped":
			button = UI.link("Fix" if str(group.get("status")) == "open" else "Try again")
			button.accessibility_name = "Fix: " + label
			button.pressed.connect(_on_fix.bind(id, button))
		"in_review":
			button = UI.link("Review")
			button.accessibility_name = "Review the fix for: " + label
			var transaction := str(group.get("transaction_id", ""))
			button.pressed.connect(func() -> void: review_requested.emit(transaction))
		"ignored":
			button = UI.link("Restore")
			button.accessibility_name = "Stop ignoring: " + label
			button.pressed.connect(func() -> void: ignore_requested.emit(id, false))
		_:
			return null
	UI.bind_height(button, "xs")
	# A short word ("Fix") is still a full target.
	UI.bind_width(button, "xs")
	UI_MOTION.press_feedback(button)
	return button


## Optimistic: the row says it is waiting at once; the host's next list confirms it.
func _on_fix(id: String, button: Button) -> void:
	button.disabled = true
	button.text = "Starting…"
	fix_requested.emit([id])


func _on_fix_all() -> void:
	fix_all.disabled = true
	var ids: Array = []
	for group: Dictionary in _groups:
		if str(group.get("status", "")) in ["open", "stopped"]:
			ids.append(str(group.get("id", "")))
	fix_requested.emit(ids)
	get_tree().create_timer(1.0).timeout.connect(func() -> void: fix_all.disabled = false)


func _on_ignore(id: String, row: Control) -> void:
	UI_MOTION.exit(row, null, func() -> void: row.hide())
	ignore_requested.emit(id, true)


func _toggle_more() -> void:
	_open = not _open
	_render()


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[headline, "body"]]
