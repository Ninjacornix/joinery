@tool
extends Control
## What floats over the stage's picture instead of taking a row of its height: the animation
## transport (review_transport.gd) on a small raised bar at the stage's foot. The bar is there
## when a review opens, so it is found, then follows the pointer: it fades out when the pointer
## leaves the stage (never while scrubbing) and back in when it returns. Reduce motion makes
## both instant. Takes no input outside the bar.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")

## The raised bar holding the transport.
var bar: PanelContainer
var _transport: Control
## Where the pointer counts as over the stage (the picture frame).
var _area: Control
var _shown := true


func _init(transport: Control) -> void:
	name = "Overlay"
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	bar = PanelContainer.new()
	bar.theme_type_variation = "JoineryPopup"
	bar.hide()
	add_child(bar)
	_transport = transport
	bar.add_child(transport)
	transport.visibility_changed.connect(_sync)
	bar.minimum_size_changed.connect(_place)
	set_process_input(false)


## Count the pointer as over the stage while it is over [param area].
func watch(area: Control) -> void:
	_area = area


## True while the bar is on screen (shown and not faded out).
func bar_shown() -> bool:
	return bar.visible and _shown


func _notification(what: int) -> void:
	if what in [NOTIFICATION_RESIZED, NOTIFICATION_THEME_CHANGED]:
		_place()


func _input(event: InputEvent) -> void:
	var motion := event as InputEventMouseMotion
	if motion == null or _area == null or not _area.is_visible_in_tree():
		return
	var over := _area.get_global_rect().has_point(_area.get_global_mouse_position())
	# A scrub that drags off the stage keeps its bar until the button is let go.
	if not over and motion.button_mask & MOUSE_BUTTON_MASK_LEFT:
		return
	if over != _shown:
		_show_bar(over)


func _sync() -> void:
	bar.visible = _transport.visible
	set_process_input(bar.visible)
	if bar.visible:
		# A new animation brings its bar back, so it can be found.
		_shown = true
		UI_MOTION.stop(bar)
		bar.mouse_filter = Control.MOUSE_FILTER_STOP
	_place()


func _show_bar(on: bool) -> void:
	_shown = on
	bar.mouse_filter = Control.MOUSE_FILTER_STOP if on else Control.MOUSE_FILTER_IGNORE
	UI_MOTION.stop(bar)
	if not UI_MOTION.is_enabled() or not is_inside_tree():
		bar.modulate.a = 1.0 if on else 0.0
		return
	bar.modulate.a = 0.0 if on else 1.0
	var tween := bar.create_tween()
	# Entrances ease out; the exit is 20% shorter and eases in (motion rules 1 and 15).
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT if on else Tween.EASE_IN)
	tween.tween_property(
		bar, "modulate:a", 1.0 if on else 0.0, UI_MOTION.duration("enter" if on else "exit")
	)


func _place() -> void:
	if not is_inside_tree():
		return
	var margin := float(UI_THEME.space(self, 3))
	var height := bar.get_combined_minimum_size().y
	bar.position = Vector2(margin, maxf(size.y - height - margin, 0.0))
	bar.size = Vector2(maxf(size.x - margin * 2.0, 0.0), height)
