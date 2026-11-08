@tool
extends VBoxContainer
## Audition controls affect only the preview player, never the imported resource.

const TIMELINE_SCRIPT := preload("res://addons/joinery/audio_timeline.gd")
var player: AudioStreamPlayer
var timeline: Control
var seek_slider: HSlider
var play_button: Button
var loop_button: Button
var volume: HSlider
var speed: OptionButton
var _time: Label
var _scrubbing := false


func _ready() -> void:
	add_theme_constant_override("separation", get_theme_default_font_size())
	timeline = TIMELINE_SCRIPT.new()
	add_child(timeline)
	timeline.load_stream(player.stream)
	timeline.seek_requested.connect(seek_to)
	seek_slider = HSlider.new()
	seek_slider.max_value = player.stream.get_length()
	seek_slider.step = 0
	seek_slider.drag_started.connect(func() -> void: _scrubbing = true)
	seek_slider.drag_ended.connect(func(_changed: bool) -> void: _scrubbing = false)
	seek_slider.value_changed.connect(seek_to)
	add_child(seek_slider)
	_time = Label.new()
	_time.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	add_child(_time)
	var transport := HFlowContainer.new()
	transport.alignment = FlowContainer.ALIGNMENT_CENTER
	add_child(transport)
	_button(transport, "Restart", "Restart audio (Home)", restart)
	_button(transport, "−5s", "Back five seconds (Left)", skip.bind(-5.0))
	play_button = _button(transport, "Play", "Play or pause (Space)", toggle_play)
	_button(transport, "+5s", "Forward five seconds (Right)", skip.bind(5.0))
	_button(transport, "Stop", "Stop and return to start", stop)
	loop_button = _button(transport, "Loop", "Loop this preview", func() -> void: pass)
	loop_button.toggle_mode = true
	_add_settings()
	player.finished.connect(_on_finished)
	_update()


func _exit_tree() -> void:
	if is_instance_valid(player) and player.finished.is_connected(_on_finished):
		player.finished.disconnect(_on_finished)


func _button(parent: Control, text: String, hint: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.tooltip_text = hint
	button.pressed.connect(callback)
	parent.add_child(button)
	return button


func _add_settings() -> void:
	var row := HFlowContainer.new()
	row.alignment = FlowContainer.ALIGNMENT_CENTER
	add_child(row)
	speed = OptionButton.new()
	speed.tooltip_text = "Playback speed; pitch changes with speed"
	for rate: float in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
		speed.add_item("%s×" % rate)
		speed.set_item_metadata(speed.item_count - 1, rate)
	speed.select(2)
	speed.item_selected.connect(func(index: int) -> void:
		player.pitch_scale = float(speed.get_item_metadata(index))
	)
	row.add_child(speed)
	var mute := _button(row, "Mute", "Mute this preview", func() -> void: pass)
	mute.toggle_mode = true
	mute.toggled.connect(func(muted: bool) -> void: player.volume_db = -80 if muted else volume.value)
	volume = HSlider.new()
	volume.custom_minimum_size.x = get_theme_default_font_size() * 7
	volume.min_value = -40
	volume.max_value = 0
	volume.step = 1
	volume.value = player.volume_db
	volume.tooltip_text = "Preview volume (dB)"
	volume.value_changed.connect(func(db: float) -> void:
		mute.set_pressed_no_signal(false)
		player.volume_db = db
		volume.tooltip_text = "Preview volume: %d dB" % db
	)
	row.add_child(volume)


func _process(_delta: float) -> void:
	if player == null or player.stream == null:
		return
	if not _scrubbing and player.playing:
		seek_slider.set_value_no_signal(player.get_playback_position())
	_update()


func _update() -> void:
	play_button.text = "Pause" if player.playing and not player.stream_paused else "Play"
	_time.text = "%s / %s" % [_clock(seek_slider.value), _clock(seek_slider.max_value)]
	timeline.playhead = seek_slider.value
	timeline.queue_redraw()


func toggle_play() -> void:
	if player.playing:
		player.stream_paused = not player.stream_paused
	else:
		player.stream_paused = false
		player.play(0 if seek_slider.value >= seek_slider.max_value else seek_slider.value)
	_update()


func seek_to(seconds: float) -> void:
	var target := clampf(seconds, 0, seek_slider.max_value)
	seek_slider.set_value_no_signal(target)
	if player.playing:
		player.seek(target)
	_update()


func skip(seconds: float) -> void:
	seek_to(seek_slider.value + seconds)


func restart() -> void:
	seek_to(0)
	player.stream_paused = false
	player.play()
	_update()


func stop() -> void:
	player.stop()
	player.stream_paused = false
	seek_to(0)


func _on_finished() -> void:
	if loop_button.button_pressed:
		restart()
	else:
		seek_slider.set_value_no_signal(seek_slider.max_value)
		_update()


func _unhandled_key_input(event: InputEvent) -> void:
	if not event is InputEventKey or not event.pressed or event.echo:
		return
	var focus := get_viewport().gui_get_focus_owner()
	if focus == null or (focus != self and not is_ancestor_of(focus)):
		return
	match event.keycode:
		KEY_SPACE:
			toggle_play()
		KEY_LEFT:
			skip(-5)
		KEY_RIGHT:
			skip(5)
		KEY_HOME:
			restart()
		_:
			return
	get_viewport().set_input_as_handled()


func _clock(seconds: float) -> String:
	return "%d:%04.1f" % [floori(seconds / 60.0), fmod(seconds, 60.0)]
