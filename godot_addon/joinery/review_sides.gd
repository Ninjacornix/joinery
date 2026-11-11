@tool
extends Control
## The one Before/After control of a review: two small tags on the media itself, each sitting
## on the side it names, so they say which side is which and are the switch at once (ADR 0040).
## Clicking a tag focuses that side (the other dims or is hidden, per kind); clicking the
## focused tag again shows both alike where both are on screen. A change with no original has
## one tag, "New"; one whose result was removed has "Removed". Neither switches anything.
##
## Lies over the media frame and takes no input outside its tags.

## The user picked [param side]: "before", "after" or "compare" (both alike).
signal chosen(side: String)

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const HINTS := {
	"before": "Show the original (A or B flips)",
	"after": "Show the change (A or B flips)",
}

## The side in focus: "before", "after" or "compare".
var side := "compare"
## True when both sides are on screen at once (each tag under its own side); false when the
## media shows one side at a time and the tags sit together as a switch.
var both_shown := true
var _tags := {}
var _buttons := {}
var _has := {"before": false, "after": false}


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for key: String in ["before", "after"]:
		var tag := PanelContainer.new()
		tag.theme_type_variation = "JoineryPopup"
		tag.mouse_filter = Control.MOUSE_FILTER_IGNORE
		tag.hide()
		add_child(tag)
		var button := Button.new()
		button.theme_type_variation = "JoinerySegment"
		button.toggle_mode = true
		button.focus_mode = Control.FOCUS_ALL
		button.text = key.capitalize()
		button.tooltip_text = HINTS[key]
		button.accessibility_name = HINTS[key]
		UI.bind_height(button, "sm")
		UI_MOTION.press_feedback(button)
		button.pressed.connect(_on_pressed.bind(key))
		tag.add_child(button)
		tag.minimum_size_changed.connect(_place)
		_tags[key] = tag
		_buttons[key] = button
	hide()


## Which sides the shown item has. Both: two tags that switch; one: a single plain tag, "New"
## for a change with no original, "Removed" for one with no result.
func show_sides(before: bool, after: bool) -> void:
	_has = {"before": before, "after": after}
	var paired := before and after
	var after_button: Button = _buttons["after"]
	var before_button: Button = _buttons["before"]
	after_button.text = "After" if paired else "New"
	before_button.text = "Before" if paired else "Removed"
	for key: String in ["before", "after"]:
		var button: Button = _buttons[key]
		# A lone tag only names the side: no switch, no hover, no focus stop.
		button.toggle_mode = paired
		button.mouse_filter = Control.MOUSE_FILTER_STOP if paired else Control.MOUSE_FILTER_IGNORE
		button.focus_mode = Control.FOCUS_ALL if paired else Control.FOCUS_NONE
		button.tooltip_text = HINTS[key] if paired else ""
		button.accessibility_name = HINTS[key] if paired else button.text
		(_tags[key] as Control).visible = bool(_has[key])
	visible = before or after
	set_side(side)


## Mark [param value] ("before", "after", "compare") as the side in focus.
func set_side(value: String) -> void:
	side = value
	var paired: bool = _has["before"] and _has["after"]
	for key: String in ["before", "after"]:
		(_buttons[key] as Button).set_pressed_no_signal(paired and value == key)


## Put each tag at the foot of the rectangle (in this control's coordinates) it names. When
## the two rectangles are the same, the media shows one side at a time: the tags sit together
## in the middle of its foot.
func place(before_rect: Rect2, after_rect: Rect2) -> void:
	set_meta("rects", [before_rect, after_rect])
	_place()


## The tag button of [param key] ("before" or "after"), for tests.
func tag(key: String) -> Button:
	return _buttons[key]


## The tags' rectangles that are on screen, in this control's coordinates.
func tag_rects() -> Array[Rect2]:
	var found: Array[Rect2] = []
	for key: String in ["before", "after"]:
		var tag_box: Control = _tags[key]
		if tag_box.visible:
			found.append(Rect2(tag_box.position, tag_box.size))
	return found


func _place() -> void:
	var rects: Array = get_meta("rects", [Rect2(), Rect2()])
	var margin := maxf(float(UI_THEME.space(self, 1)), 4.0) if is_inside_tree() else 4.0
	var before_rect: Rect2 = rects[0]
	var after_rect: Rect2 = rects[1]
	both_shown = not before_rect.is_equal_approx(after_rect)
	var shown: Array[String] = []
	for key: String in ["before", "after"]:
		if (_tags[key] as Control).visible:
			shown.append(key)
	var sizes := {}
	for key: String in shown:
		sizes[key] = (_tags[key] as Control).get_combined_minimum_size()
		(_tags[key] as Control).size = sizes[key]
	if not both_shown or shown.size() == 1:
		# Together under the one picture: [Before][After], or the lone tag in the middle.
		var under := after_rect if after_rect.has_area() else before_rect
		var gap := margin / 2.0
		var total := -gap
		for key: String in shown:
			total += (sizes[key] as Vector2).x + gap
		var x := under.get_center().x - total / 2.0
		for key: String in shown:
			var tag_size: Vector2 = sizes[key]
			_put(key, Vector2(x, under.end.y - tag_size.y - margin))
			x += tag_size.x + gap
		return
	for key: String in shown:
		var under: Rect2 = before_rect if key == "before" else after_rect
		var tag_size: Vector2 = sizes[key]
		_put(
			key, Vector2(under.get_center().x - tag_size.x / 2.0, under.end.y - tag_size.y - margin)
		)


func _put(key: String, at: Vector2) -> void:
	var tag_box: Control = _tags[key]
	var limit := (size - tag_box.size).max(Vector2.ZERO)
	tag_box.position = at.round().clamp(Vector2.ZERO, limit)


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED:
		_place()


func _on_pressed(key: String) -> void:
	if not (_has["before"] and _has["after"]):
		return
	var next := key
	if side == key:
		# Pressing the focused side again shows both alike where both are on screen; a switch
		# keeps its side.
		next = "compare" if both_shown else key
	set_side(next)
	chosen.emit(next)
