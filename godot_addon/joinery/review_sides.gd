@tool
extends Control
## The one Before/After control of a review (ADR 0040): a compact segmented pill floating in
## the stage's top trailing corner, Before | After | Both. "Both" is offered only when both
## sides can be on screen at once (a split picture, two clips, two live models); model renders
## show one side at a time and crossfade. A change with no original shows one quiet badge in
## that corner, "New"; one whose result was removed, "Removed". Neither switches anything.
##
## While both sides show, a tiny label sits in the top leading corner of each, so the halves
## are never ambiguous; with one side in focus the pill alone says which.
##
## Lies over the media and takes no input outside its pill.

## The user picked [param side]: "before", "after" or "compare" (both alike).
signal chosen(side: String)

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const KEYS: Array[String] = ["before", "after", "compare"]
const WORDS := {"before": "Before", "after": "After", "compare": "Both"}
const HINTS := {
	"before": "Show the original (B flips)",
	"after": "Show the change (B flips)",
	"compare": "Show the original and the change together",
}

## The side in focus: "before", "after" or "compare".
var side := "compare"
## True when both sides are on screen at once, each in its own rectangle; false when the media
## shows one side at a time.
var both_shown := true
var _pill: PanelContainer
var _buttons := {}
## The tiny side labels, one per side, shown while both sides are on screen.
var _labels := {}
var _has := {"before": false, "after": false}
var _both_offered := false


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_pill = PanelContainer.new()
	_pill.theme_type_variation = "JoineryPopup"
	_pill.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_pill.accessibility_name = "Before or after"
	add_child(_pill)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryFlush"
	_pill.add_child(row)
	for key: String in KEYS:
		var button := Button.new()
		button.theme_type_variation = "JoinerySegment"
		button.toggle_mode = true
		button.focus_mode = Control.FOCUS_ALL
		button.text = WORDS[key]
		button.tooltip_text = HINTS[key]
		button.accessibility_name = HINTS[key]
		UI.bind_height(button, "sm")
		UI_MOTION.press_feedback(button)
		button.pressed.connect(_on_pressed.bind(key))
		row.add_child(button)
		_buttons[key] = button
	for key: String in ["before", "after"]:
		var label_box := PanelContainer.new()
		label_box.theme_type_variation = "JoineryPopup"
		label_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
		label_box.hide()
		var words := UI.label(WORDS[key], "JoineryCaption")
		words.mouse_filter = Control.MOUSE_FILTER_IGNORE
		label_box.add_child(words)
		add_child(label_box)
		_labels[key] = label_box
	_pill.minimum_size_changed.connect(_place)
	hide()


## Which sides the shown item has, and whether [param both] sides can be on screen at once
## (offers "Both"). Both sides: the switch; one: a single quiet badge, "New" for a change with
## no original, "Removed" for one with no result.
func show_sides(before: bool, after: bool, both := true) -> void:
	_has = {"before": before, "after": after}
	var paired := before and after
	_both_offered = paired and both
	var after_button: Button = _buttons["after"]
	var before_button: Button = _buttons["before"]
	after_button.text = WORDS["after"] if paired else "New"
	before_button.text = WORDS["before"] if paired else "Removed"
	for key: String in KEYS:
		var button: Button = _buttons[key]
		button.visible = _both_offered if key == "compare" else bool(_has[key])
		# A lone badge only names the side: no switch, no hover, no focus stop.
		button.toggle_mode = paired
		button.mouse_filter = Control.MOUSE_FILTER_STOP if paired else Control.MOUSE_FILTER_IGNORE
		button.focus_mode = Control.FOCUS_ALL if paired else Control.FOCUS_NONE
		button.tooltip_text = HINTS[key] if paired else ""
		button.accessibility_name = HINTS[key] if paired else button.text
	visible = before or after
	set_side(side)


## Mark [param value] ("before", "after", "compare") as the side in focus.
func set_side(value: String) -> void:
	side = value
	var paired: bool = _has["before"] and _has["after"]
	for key: String in KEYS:
		(_buttons[key] as Button).set_pressed_no_signal(paired and value == key)
	_place()


## Where each side is drawn (in this control's coordinates): before, after. The same rectangle
## twice when the media shows one side at a time.
func place(before_rect: Rect2, after_rect: Rect2) -> void:
	set_meta("rects", [before_rect, after_rect])
	_place()


## The switch button of [param key] ("before", "after" or "compare"); for a lone side the badge.
func tag(key: String) -> Button:
	return _buttons[key]


## The pill (the switch, or the lone badge).
func pill() -> Control:
	return _pill


## The side labels on screen, in this control's coordinates (empty with one side in view).
func tag_rects() -> Array[Rect2]:
	var found: Array[Rect2] = []
	for key: String in ["before", "after"]:
		var label_box: Control = _labels[key]
		if label_box.visible:
			found.append(Rect2(label_box.position, label_box.size))
	return found


func _place() -> void:
	var rects: Array = get_meta("rects", [Rect2(), Rect2()])
	var margin := float(UI_THEME.space(self, 3)) if is_inside_tree() else 0.0
	var inset := float(UI_THEME.space(self, 2)) if is_inside_tree() else 0.0
	var before_rect: Rect2 = rects[0]
	var after_rect: Rect2 = rects[1]
	both_shown = not before_rect.is_equal_approx(after_rect)
	_pill.size = _pill.get_combined_minimum_size()
	var corner := Vector2(size.x - _pill.size.x - margin, margin)
	if is_layout_rtl():
		corner.x = margin
	_put(_pill, corner)
	var labelled: bool = (
		both_shown and side == "compare" and bool(_has["before"]) and bool(_has["after"])
	)
	for key: String in ["before", "after"]:
		var label_box: Control = _labels[key]
		var under: Rect2 = before_rect if key == "before" else after_rect
		label_box.visible = labelled and under.has_area()
		if not label_box.visible:
			continue
		label_box.size = label_box.get_combined_minimum_size()
		var at := under.position + Vector2(inset, inset)
		if is_layout_rtl():
			at.x = under.end.x - label_box.size.x - inset
		# Under the pill: the label goes to the foot of its side instead.
		if Rect2(at, label_box.size).intersects(Rect2(_pill.position, _pill.size).grow(inset)):
			at.y = under.end.y - label_box.size.y - inset
		_put(label_box, at)


func _put(box: Control, at: Vector2) -> void:
	var limit := (size - box.size).max(Vector2.ZERO)
	box.position = at.round().clamp(Vector2.ZERO, limit)


func _notification(what: int) -> void:
	if what in [NOTIFICATION_RESIZED, NOTIFICATION_THEME_CHANGED]:
		_place()


func _on_pressed(key: String) -> void:
	if not (_has["before"] and _has["after"]):
		return
	set_side(key)
	chosen.emit(key)
