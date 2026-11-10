@tool
extends Control
## Three accent dots pulsing in turn, sized to the caption line, shown beside a sentence that
## says what is happening ("Exporting the model…"). Set [member running]: when false it is
## hidden and does not process, so an idle editor never animates. With motion off (Reduce
## motion, or headless) the dots are drawn still.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
## Seconds for one full cycle of the three dots.
const PERIOD := 1.2

## Show and animate the indicator while true; hide it and stop processing when false.
var running := false:
	set = set_running
var _time := 0.0


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	size_flags_vertical = Control.SIZE_SHRINK_CENTER
	visible = false
	set_process(false)


func _ready() -> void:
	set_process(running and UI_MOTION.is_enabled())


func set_running(on: bool) -> void:
	running = on
	visible = on
	_time = 0.0
	set_process(on and UI_MOTION.is_enabled())
	queue_redraw()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		var line := get_theme_font("font", "Label").get_height(UI_THEME.font_size(self, "caption"))
		var dot := _dot()
		custom_minimum_size = Vector2(dot * 3.0 + dot * 0.75 * 2.0, line)
		queue_redraw()
	elif what == NOTIFICATION_VISIBILITY_CHANGED and not is_visible_in_tree():
		set_process(false)
	elif what == NOTIFICATION_VISIBILITY_CHANGED and running:
		set_process(UI_MOTION.is_enabled())


func _process(delta: float) -> void:
	_time = fmod(_time + delta, PERIOD)
	queue_redraw()


func _draw() -> void:
	var ink := (
		get_theme_color("accent", "Joinery")
		if has_theme_color("accent", "Joinery")
		else UI_THEME.color(self, "accent")
	)
	var dot := _dot()
	var animate := is_processing()
	for index: int in 3:
		var alpha := [1.0, 0.65, 0.35][index] as float
		if animate:
			var phase := fmod(_time / PERIOD - index / 3.0 + 1.0, 1.0)
			alpha = 0.35 + 0.65 * pow(sin(PI * phase), 2.0)
		var center := Vector2(dot / 2.0 + index * dot * 1.75, size.y / 2.0)
		draw_circle(center, dot / 2.0, Color(ink, ink.a * alpha), true, -1.0, true)


func _dot() -> float:
	return roundf(UI_THEME.font_size(self, "caption") * 0.4)
