@tool
extends Container
## The review's two parts: the stage (the media, edge to edge) and the rail (where the change
## is, what it is, the checks, the actions). Wide, the stage takes about 70% on the leading side
## at the full height and the rail the rest on the trailing side; narrow (the sidebar's width,
## or a panel under NARROW_EM), the rail stacks under the stage, the stage keeps at least
## STAGE_NARROW of the height, and the rail never gets less than its own minimum, so the
## actions stay on screen.

## The layout switched between side by side and stacked.
signal narrow_changed(narrow: bool)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
## Below this width, in em, the rail stacks under the stage.
const NARROW_EM := 46.0
## The rail's share of a wide panel, and its least and largest width in em.
const RAIL_SHARE := 0.3
const RAIL_MIN_EM := 18.0
const RAIL_MAX_EM := 28.0
## The least share of the height the stage keeps when stacked.
const STAGE_NARROW := 0.55

## True while the rail stacks under the stage.
var narrow := false
var stage: Control
var rail: Control


func _init(stage_part: Control, rail_part: Control) -> void:
	stage = stage_part
	rail = rail_part
	add_child(stage)
	add_child(rail)


func _notification(what: int) -> void:
	if what == NOTIFICATION_SORT_CHILDREN:
		_arrange()
	elif what == NOTIFICATION_THEME_CHANGED:
		update_minimum_size()
		queue_sort()


func _get_minimum_size() -> Vector2:
	if stage == null or rail == null:
		return Vector2.ZERO
	var gap := _gap()
	var a := stage.get_combined_minimum_size()
	var b := rail.get_combined_minimum_size()
	# Never wider than the stacked layout needs: below NARROW_EM the rail stacks anyway.
	var least := maxf(a.x, b.x)
	if narrow:
		return Vector2(least, a.y + gap + b.y)
	return Vector2(least, maxf(a.y, b.y))


func _gap() -> float:
	return float(UI_THEME.space(self, 4)) if is_inside_tree() else 0.0


func _em() -> float:
	return float(UI_THEME.font_size(self, "body")) if is_inside_tree() else 14.0


func _arrange() -> void:
	var gap := _gap()
	var now := size.x < NARROW_EM * _em()
	if now != narrow:
		narrow = now
		update_minimum_size()
		narrow_changed.emit(narrow)
	if narrow:
		var least := rail.get_combined_minimum_size().y
		var wanted: float = (
			rail.call("wanted_height") if rail.has_method("wanted_height") else least
		)
		var tall := maxf(size.y * STAGE_NARROW, size.y - gap - wanted)
		tall = maxf(minf(tall, size.y - gap - least), stage.get_combined_minimum_size().y)
		fit_child_in_rect(stage, Rect2(0.0, 0.0, size.x, tall))
		fit_child_in_rect(rail, Rect2(0.0, tall + gap, size.x, maxf(size.y - tall - gap, least)))
		return
	var wide := clampf(size.x * RAIL_SHARE, RAIL_MIN_EM * _em(), RAIL_MAX_EM * _em())
	wide = maxf(wide, rail.get_combined_minimum_size().x)
	var room := maxf(size.x - wide - gap, 0.0)
	var stage_x := wide + gap if is_layout_rtl() else 0.0
	var rail_x := 0.0 if is_layout_rtl() else room + gap
	fit_child_in_rect(stage, Rect2(stage_x, 0.0, room, size.y))
	fit_child_in_rect(rail, Rect2(rail_x, 0.0, wide, size.y))


## A still of [param part] as it is now, laid over it and fading out (motion rule 16), so the
## next state crossfades in under it. The still lives in [param holder] (default: the part),
## which must stay visible. Does nothing when motion is off or nothing is drawn.
static func ghost(part: Control, holder: Node = null) -> void:
	if not UI_MOTION.is_enabled() or not part.is_visible_in_tree():
		return
	var rect := part.get_global_rect()
	var texture := part.get_viewport().get_texture()
	if texture == null or not rect.has_area():
		return
	var frame := texture.get_image()
	if frame == null:
		return
	var shown := part.get_global_transform_with_canvas()
	var region := Rect2i(Rect2(shown.origin, rect.size * shown.get_scale()))
	region = region.intersection(Rect2i(Vector2i.ZERO, frame.get_size()))
	if not region.has_area():
		return
	var still := TextureRect.new()
	still.texture = ImageTexture.create_from_image(frame.get_region(region))
	still.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	still.stretch_mode = TextureRect.STRETCH_SCALE
	still.mouse_filter = Control.MOUSE_FILTER_IGNORE
	still.top_level = true
	(holder if holder != null else part).add_child(still)
	still.global_position = rect.position
	still.size = rect.size
	var tween := still.create_tween()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
	tween.tween_property(still, "modulate:a", 0.0, UI_MOTION.duration("base"))
	tween.tween_callback(still.queue_free)
