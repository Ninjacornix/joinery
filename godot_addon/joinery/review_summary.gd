@tool
extends VBoxContainer
## The review rail's glance under the title: what changed in plain words ("Door 20% wider ·
## wood darker · 2 files", up to two lines), the checks as small chips ("Checks passed",
## "Reviewer checking…", "1 warning"), and the reviewer's worst note as one quiet line when
## there are any ("· 2 more"). The full texts are in tooltips and in Details ([method info]).

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const GLANCE := preload("review_glance.gd")
## Severity -> [icon, tint role, word].
const NOTE_LOOK := {
	0: ["", "text_muted", "note"],
	1: ["warning", "text_muted", "warning"],
	2: ["error", "danger", "blocker"],
}
## Lines the facts take at most before they end in an ellipsis.
const FACT_LINES := 2

var _review: Dictionary = {}
var _measured: Array = []
var _notes: Array[Dictionary] = []
var _reviewer := ""
var _facts: Label
var _facts_line := ""
var _chips: HFlowContainer
var _status: PanelContainer
var _status_icon: TextureRect
var _status_text: Label
var _reviewer_chip: PanelContainer
var _reviewer_text: Label
## One chip per note severity present: {severity: chip}.
var _note_chips := {}
var _notes_row: HBoxContainer
var _notes_text: Label
var _info: VBoxContainer
var _info_checks: Label
var _info_notes: VBoxContainer


func _init() -> void:
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "What changed"
	_facts = UI.label("", "", true)
	_facts.max_lines_visible = FACT_LINES
	_facts.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_facts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_facts.mouse_filter = Control.MOUSE_FILTER_PASS
	_facts.accessibility_name = "What changed"
	add_child(_facts)
	_chips = HFlowContainer.new()
	_chips.name = "Checks"
	_chips.accessibility_name = "Checks"
	add_child(_chips)
	var status := _chip("success", "success")
	_status = status[0]
	_status_icon = status[1]
	_status_text = status[2]
	var reviewer := _chip("", "text_muted")
	_reviewer_chip = reviewer[0]
	_reviewer_text = reviewer[2]
	_reviewer_chip.hide()
	for severity: int in [2, 1, 0]:
		var look: Array = NOTE_LOOK[severity]
		var made := _chip(look[0], look[1])
		(made[0] as Control).hide()
		_note_chips[severity] = made
	_notes_row = HBoxContainer.new()
	_notes_row.theme_type_variation = "JoineryRowTight"
	_notes_row.accessibility_name = "Reviewer notes"
	add_child(_notes_row)
	# The chips carry the severity; the line only says what the worst note is.
	_notes_text = UI.label("", "JoineryCaption", true)
	_notes_text.max_lines_visible = FACT_LINES
	_notes_text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_notes_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_notes_text.mouse_filter = Control.MOUSE_FILTER_PASS
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
	var fresh := false
	if extra.has("review_notes"):
		var arrived := GLANCE.sorted_notes(extra["review_notes"])
		fresh = arrived.size() > _notes.size()
		_notes = arrived
	var status := _status_of(extra)
	if not status.is_empty() or extra.has("reviewer_status"):
		_reviewer = status
	_sync()
	if fresh and is_inside_tree():
		UI_MOTION.fade_in(_notes_row)
		for chip: Array in _note_chips.values():
			if (chip[0] as Control).visible:
				UI_MOTION.fade_in(chip[0])


## Forget the review.
func clear() -> void:
	show_review({})


## The facts line as shown.
func facts_text() -> String:
	return _facts_line


## The checks as shown, chip by chip ("Checks passed", "Checks passed · Reviewer checking…").
func status_text() -> String:
	if _reviewer_chip.visible:
		return _status_text.text + " · " + _reviewer_text.text
	return _status_text.text


## The notes line as shown ("" without notes).
func notes_text() -> String:
	return _notes_text.text if _notes_row.visible else ""


## The note chips as shown, worst first ("1 warning", "2 notes").
func note_chips() -> Array[String]:
	var words: Array[String] = []
	for severity: int in [2, 1, 0]:
		var chip: Array = _note_chips[severity]
		if (chip[0] as Control).visible:
			words.append((chip[2] as Label).text)
	return words


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
	_facts_line = line
	# A wrapped line never starts with its separator: the dot stays with the fact before it.
	_facts.text = line.replace(" · ", "\u00a0· ")
	_facts.tooltip_text = line
	_facts.visible = not line.is_empty()
	_status_text.text = GLANCE.checks_text(_review)
	var detail := GLANCE.checks_detail(_review)
	_status.tooltip_text = detail
	_status.visible = not _review.is_empty()
	var reviewer := GLANCE.reviewer_text(_reviewer)
	_reviewer_text.text = reviewer
	_reviewer_chip.visible = not reviewer.is_empty() and not _review.is_empty()
	_reviewer_chip.tooltip_text = "The reviewer agent reads the change before you decide"
	var counts := {0: 0, 1: 0, 2: 0}
	for note: Dictionary in _notes:
		counts[GLANCE.severity(note)] += 1
	var words := PackedStringArray()
	for note: Dictionary in _notes:
		words.append(_note_words(note))
	for severity: int in [2, 1, 0]:
		var chip: Array = _note_chips[severity]
		var count: int = counts[severity]
		(chip[0] as Control).visible = count > 0
		var word: String = NOTE_LOOK[severity][2]
		(chip[2] as Label).text = "%d %s%s" % [count, word, "" if count == 1 else "s"]
		(chip[0] as Control).tooltip_text = "\n".join(words)
	_chips.visible = _status.visible or _reviewer_chip.visible or not _notes.is_empty()
	var summary := GLANCE.notes_line(_notes)
	_notes_text.text = summary
	_notes_text.tooltip_text = "\n".join(words)
	_notes_row.visible = not summary.is_empty()
	_info_checks.text = detail
	_info_checks.visible = not detail.is_empty()
	for child: Node in _info_notes.get_children():
		_info_notes.remove_child(child)
		child.queue_free()
	for note: Dictionary in _notes:
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryRowTight"
		_info_notes.add_child(row)
		var look: Array = NOTE_LOOK[GLANCE.severity(note)]
		var icon := _glyph(look[0] if not str(look[0]).is_empty() else "chats")
		icon.set_meta("role", look[1])
		row.add_child(icon)
		var text := UI.label(_note_words(note), "JoineryCaption", true)
		text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(text)
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
	var icons: Array = _info_notes.find_children("*", "TextureRect", true, false)
	icons.append_array(_chips.find_children("*", "TextureRect", true, false))
	for icon: Node in icons:
		var role := str(icon.get_meta("role", "text_muted"))
		(icon as TextureRect).self_modulate = UI_THEME.color(self, role)


## A status chip in the checks row: [the chip, its icon (or null), its label].
func _chip(icon_name: String, role: String) -> Array:
	var chip := PanelContainer.new()
	chip.theme_type_variation = "JoineryStatusChip"
	chip.mouse_filter = Control.MOUSE_FILTER_PASS
	_chips.add_child(chip)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	chip.add_child(row)
	var icon: TextureRect = null
	if not icon_name.is_empty():
		icon = _glyph(icon_name)
		icon.set_meta("role", role)
		row.add_child(icon)
	var words := UI.label("", "JoineryCaption")
	words.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(words)
	return [chip, icon, words]


func _glyph(icon_name: String) -> TextureRect:
	var icon := TextureRect.new()
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI.bind_icon(icon, icon_name, "texture")
	return icon
