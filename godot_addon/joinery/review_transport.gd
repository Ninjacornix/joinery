@tool
extends HBoxContainer
## Play/pause and one scrub bar for an animation in review. Both clips (or both live models)
## read their time from one clock (review_clip.gd's), so they stay in sync whether playing,
## scrubbed or stepped. With the animation in focus, Space plays or pauses and Left and Right
## step a frame. Shown only for animations.

## Play or pause was pressed.
signal play_toggled
## The bar was dragged to [param fraction] of the loop (0..1).
signal scrubbed(fraction: float)

const UI := preload("ui_components.gd")
const CLIP := preload("review_clip.gd")
const MODEL_VIEW := preload("review_model_view.gd")

var play_button: Button
var bar: HSlider
var _playing := true
var _clip: CLIP
var _model: MODEL_VIEW


func _init() -> void:
	theme_type_variation = "JoineryRowTight"
	accessibility_name = "Animation playback"
	play_button = UI.quiet("")
	play_button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	# Pressing play leaves the keys where they were.
	play_button.focus_mode = Control.FOCUS_NONE
	play_button.pressed.connect(func() -> void: play_toggled.emit())
	UI.bind_height(play_button, "sm")
	UI.bind_width(play_button, "sm")
	add_child(play_button)
	bar = HSlider.new()
	bar.min_value = 0.0
	bar.max_value = 1.0
	bar.step = 0.0
	bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# A full hit target, as tall as the play button beside it.
	UI.bind_height(bar, "sm")
	bar.focus_mode = Control.FOCUS_NONE
	bar.accessibility_name = "Position in the loop"
	bar.tooltip_text = "Drag to scrub both clips (Left and Right step a frame)"
	bar.value_changed.connect(func(value: float) -> void: scrubbed.emit(value))
	add_child(bar)
	set_playing(true)
	hide()


## Drive [param clip]'s clock and pose [param model]'s live models from it.
func attach(clip: CLIP, model: MODEL_VIEW) -> void:
	_clip = clip
	_model = model
	clip.ticked.connect(_on_tick)
	clip.key_pressed.connect(handle_key)
	model.key_pressed.connect(handle_key)
	play_toggled.connect(toggle_play)
	scrubbed.connect(
		func(fraction: float) -> void:
			clip.playing = false
			set_playing(false)
			clip.seek(fraction * loop_length())
	)


## Seconds of one loop of the animation on show.
func loop_length() -> float:
	var length := _clip.length()
	return length if length > 0.0 else _model.animation_length()


## Play or pause both sides.
func toggle_play() -> void:
	_clip.toggle_play()
	set_playing(_clip.playing)


## Space plays or pauses, Left and Right step a frame. True when [param event] was used.
func handle_key(event: InputEventKey) -> bool:
	if not visible or not event.pressed or (event.echo and event.keycode == KEY_SPACE):
		return false
	match event.keycode:
		KEY_SPACE:
			_clip.toggle_play()
		KEY_LEFT:
			_clip.step(-1)
		KEY_RIGHT:
			_clip.step(1)
		_:
			return false
	set_playing(_clip.playing)
	if is_inside_tree():
		get_viewport().set_input_as_handled()
	return true


func _on_tick(seconds: float) -> void:
	_model.set_time(seconds)
	var length := loop_length()
	show_position(fposmod(seconds, length) / length if length > 0.0 else 0.0)
	if _playing != _clip.playing:
		set_playing(_clip.playing)


## Show play or pause.
func set_playing(playing: bool) -> void:
	_playing = playing
	UI.bind_icon(play_button, "pause" if playing else "play")
	play_button.accessibility_name = "Pause" if playing else "Play"
	play_button.tooltip_text = ("Pause" if playing else "Play") + " both clips (Space)"


## True while showing pause (the clips play).
func is_playing() -> bool:
	return _playing


## Move the bar to [param fraction] without emitting [signal scrubbed].
func show_position(fraction: float) -> void:
	bar.set_value_no_signal(clampf(fraction, 0.0, 1.0))
