@tool
extends Container
## The stage of a change with several files: the file list (review_files.gd) down the leading
## side and the preview filling the rest. Below STACK_EM wide (the sidebar, a narrow panel) the
## list folds above the preview to at most FOLD of the height, never less than one row, so the
## preview keeps the room and the review's actions stay on screen. With one file the list is
## hidden and the preview takes the whole stage.
##
## [member room] is an empty control laid exactly over the preview: the pictures, clips and live
## views size themselves from it (review_view.gd), so the preview can shrink as well as grow.

## Below this width, in em, the list folds above the preview.
const STACK_EM := 36.0
## The list's share of a wide stage, and its least and largest width in em.
const LIST_SHARE := 0.35
const LIST_MIN_EM := 15.0
const LIST_MAX_EM := 26.0
## The largest share of the height a folded list takes.
const FOLD := 0.3

const UI_THEME := preload("ui_theme.gd")

## True while the list sits above the preview.
var stacked := false
var list: Control
var preview: Control
var room: Control


func _init(list_part: Control, preview_part: Control) -> void:
	list = list_part
	preview = preview_part
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	mouse_filter = Control.MOUSE_FILTER_PASS
	room = Control.new()
	room.name = "Room"
	room.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(room)
	add_child(list)
	add_child(preview)
	list.visibility_changed.connect(queue_sort)
	list.visibility_changed.connect(update_minimum_size)


func _notification(what: int) -> void:
	if what == NOTIFICATION_SORT_CHILDREN:
		_arrange()
	elif what == NOTIFICATION_THEME_CHANGED:
		update_minimum_size()
		queue_sort()


## Only heights count: the stage's width is the layout's to give, so a wide picture never
## holds the stage open.
func _get_minimum_size() -> Vector2:
	if preview == null:
		return Vector2.ZERO
	var tall := preview.get_combined_minimum_size().y
	if not _listed():
		return Vector2(0.0, tall)
	var row: float = list.call("row_height")
	if stacked:
		return Vector2(0.0, tall + _gap() + row)
	return Vector2(0.0, maxf(tall, row))


func _listed() -> bool:
	return list != null and list.visible


func _gap() -> float:
	return float(UI_THEME.space(self, 3)) if is_inside_tree() else 0.0


func _em() -> float:
	return float(UI_THEME.font_size(self, "body")) if is_inside_tree() else 14.0


func _arrange() -> void:
	var gap := _gap()
	var now := size.x < STACK_EM * _em()
	if now != stacked:
		stacked = now
		update_minimum_size()
	var shown := Rect2(Vector2.ZERO, size)
	if _listed() and stacked:
		var row: float = list.call("row_height")
		var wanted: float = list.call("content_height")
		var tall := clampf(wanted, row, maxf(row, floorf(size.y * FOLD)))
		fit_child_in_rect(list, Rect2(0.0, 0.0, size.x, tall))
		shown = Rect2(0.0, tall + gap, size.x, maxf(size.y - tall - gap, 0.0))
	elif _listed():
		var wide := clampf(size.x * LIST_SHARE, LIST_MIN_EM * _em(), LIST_MAX_EM * _em())
		var rest := maxf(size.x - wide - gap, 0.0)
		var list_x := rest + gap if is_layout_rtl() else 0.0
		fit_child_in_rect(list, Rect2(list_x, 0.0, wide, size.y))
		shown = Rect2(0.0 if is_layout_rtl() else wide + gap, 0.0, rest, size.y)
	fit_child_in_rect(room, shown)
	fit_child_in_rect(preview, shown)
