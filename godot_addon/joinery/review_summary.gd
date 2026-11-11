@tool
extends VBoxContainer
## The lines under the review's picture: what changed in plain words ("Door 20% wider · wood
## darker · 2 files"), the checks as one quiet status ("Checks passed"), and the reviewer's
## notes as one line when there are any (the worst first, "· 2 more"). Each is one line that
## ends in an ellipsis; the full text is in its tooltip and in Details ([method info]).

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const GLANCE := preload("review_glance.gd")
## Severity -> [icon, tint role].
const NOTE_LOOK := {
	0: ["chats", "text_muted"],
	1: ["warning", "text_muted"],
	2: ["error", "danger"],
}

var _review: Dictionary = {}
var _measured: Array = []
var _notes: Array[Dictionary] = []
var _reviewer := ""
var _facts: Label
var _status: HBoxContainer
var _status_icon: TextureRect
var _status_text: Label
var _notes_row: HBoxContainer
var _notes_icon: TextureRect
var _notes_text: Label
var _info: VBoxContainer
var _info_checks: Label
var _info_notes: VBoxContainer


func _init() -> void:
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "What changed"
	_facts = _line("", "")
	_facts.accessibility_name = "What changed"
	add_child(_facts)
	_status = _row()
	_status_icon = _glyph("success")
	_status.add_child(_status_icon)
	_status_text = _line("", "JoineryCaption")
	_status.add_child(_status_text)
	_notes_row = _row()
	_notes_row.accessibility_name = "Reviewer notes"
	_notes_icon = _glyph("warning")
	_notes_row.add_child(_notes_icon)
	_notes_text = _line("", "JoineryCaption")
	_notes_row.add_child(_notes_text)
	_notes_row.hide()
	_info = VBoxContainer.new()
	_info.theme_type_variation = "JoineryTight"
	_info.accessibility_name = "Checks and notes"
	_info_checks = UI.label("", "JoineryCaption", true)
	_info.add_child(_info_checks)
	_info_notes = VBoxContainer.new()
	_info_notes.theme_type_variation = "JoineryTight"
	_info.add_child(_info_notes)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_tint()
	elif what == NOTIFICATION_PREDELETE and is_instance_valid(_info) and _info.get_parent() == null:
		_info.free()


## Show [param review] ({facts?, changes?, diff?, scene_count?, checks?, review_notes?,
## reviewer_status?}). Measured facts are cleared until [method set_measured].
func show_review(review: Dictionary) -> void:
	_review = review.duplicate()
	_measured = []
	_notes = GLANCE.sorted_notes(review.get("review_notes", []))
	_reviewer = _status_of(review)
	_sync()


## Add what the media measured (an image darker, a sound shorter) to the facts line.
func set_measured(facts: Array) -> void:
	if facts == _measured:
		return
	_measured = facts.duplicate()
	_sync()


## Take extra fields of the review event: the agent's facts, the checks, the reviewer's notes
## and status. Missing keys keep what is shown.
func update(extra: Dictionary) -> void:
	for key: String in ["facts", "checks"]:
		if extra.has(key):
			_review[key] = extra[key]
	if extra.has("review_notes"):
		var arrived := GLANCE.sorted_notes(extra["review_notes"])
		var fresh := arrived.size() > _notes.size()
		_notes = arrived
		if fresh and is_inside_tree():
			UI_MOTION.fade_in(_notes_row)
	var status := _status_of(extra)
	if not status.is_empty() or extra.has("reviewer_status"):
		_reviewer = status
	_sync()


## Forget the review.
func clear() -> void:
	show_review({})


## The facts line as shown.
func facts_text() -> String:
	return _facts.text


## The status line as shown ("Checks passed", "Checks passed · Reviewer checking…").
func status_text() -> String:
	return _status_text.text


## The notes line as shown ("" without notes).
func notes_text() -> String:
	return _notes_text.text if _notes_row.visible else ""


## The reviewer's notes, worst first.
func notes() -> Array[Dictionary]:
	return _notes.duplicate()


## True when the change is small enough to decide from the chat card.
func simple(assets: int) -> bool:
	return GLANCE.is_simple(_review, assets, _notes)


## The checks and notes in full, for Details. The panel places it.
func info() -> Control:
	return _info


## True when Details have checks or notes to show.
func has_info() -> bool:
	return not _info_checks.text.is_empty() or _info_notes.get_child_count() > 0


func _sync() -> void:
	var line := GLANCE.facts_line(_review, _measured)
	_facts.text = line
	_facts.tooltip_text = line
	_facts.visible = not line.is_empty()
	var status := GLANCE.checks_text(_review)
	var reviewer := GLANCE.reviewer_text(_reviewer)
	_status_text.text = status if reviewer.is_empty() else status + " · " + reviewer
	var detail := GLANCE.checks_detail(_review)
	_status_text.tooltip_text = detail
	_status.tooltip_text = detail
	_status.visible = not _review.is_empty()
	var summary := GLANCE.notes_line(_notes)
	_notes_text.text = summary
	_notes_row.visible = not summary.is_empty()
	var words := PackedStringArray()
	for note: Dictionary in _notes:
		words.append(_note_words(note))
	_notes_text.tooltip_text = "\n".join(words)
	_info_checks.text = detail
	_info_checks.visible = not detail.is_empty()
	for child: Node in _info_notes.get_children():
		_info_notes.remove_child(child)
		child.queue_free()
	for note: Dictionary in _notes:
		var row := _row(_info_notes)
		var look: Array = NOTE_LOOK[GLANCE.severity(note)]
		var icon := _glyph(look[0])
		icon.set_meta("role", look[1])
		row.add_child(icon)
		var text := UI.label(_note_words(note), "JoineryCaption", true)
		text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(text)
	if not _notes.is_empty():
		var look: Array = NOTE_LOOK[GLANCE.severity(_notes[0])]
		UI.bind_icon(_notes_icon, look[0], "texture")
		_notes_icon.set_meta("role", look[1])
	_tint()


static func _note_words(note: Dictionary) -> String:
	var text := str(note.get("text", "")).strip_edges()
	var file := str(note.get("file", ""))
	return text if file.is_empty() else "%s · %s" % [text, file.get_file()]


## The reviewer's status from an event: "reviewer_status", or "reviewer": {status}.
static func _status_of(event: Dictionary) -> String:
	var reviewer: Variant = event.get("reviewer", {})
	if reviewer is Dictionary and (reviewer as Dictionary).has("status"):
		return str((reviewer as Dictionary)["status"])
	return str(event.get("reviewer_status", ""))


func _tint() -> void:
	if not is_inside_tree():
		return
	_status_icon.self_modulate = UI_THEME.color(self, "success")
	for icon: Node in [_notes_icon] + _info_notes.find_children("*", "TextureRect", true, false):
		var role := str(icon.get_meta("role", "text_muted"))
		(icon as TextureRect).self_modulate = UI_THEME.color(self, role)


func _row(parent: Control = self) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	parent.add_child(row)
	return row


func _glyph(icon_name: String) -> TextureRect:
	var icon := TextureRect.new()
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI.bind_icon(icon, icon_name, "texture")
	return icon


## A one-line label that ends in an ellipsis and keeps the full text in its tooltip.
static func _line(text: String, variation: String) -> Label:
	var label := UI.label(text, variation)
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	label.mouse_filter = Control.MOUSE_FILTER_PASS
	return label
