@tool
extends VBoxContainer
## Audition one sound: a waveform you can click to seek, and one transport row (play, a
## rolling clock, loop, volume, speed). Audition controls affect only the preview player and
## its own copy of the stream, never the imported resource or its import settings.
##
## Works on its own (it creates a player when none is given) or on a shared [member player].
## Keyboard, with focus inside: Space plays or pauses, Left and Right skip five seconds,
## Home restarts.

const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const TIMELINE_SCRIPT := preload("audio_timeline.gd")
const SPEEDS := [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
const SKIP_SECONDS := 5.0
## Volume slider range in dB; the bottom of the range silences the preview.
const MIN_DB := -40.0
const SILENT_DB := -80.0
const _PLAY_STATES := ["normal", "hover", "pressed", "hover_pressed", "disabled"]

## The player to drive. Leave unset to have the preview create its own.
var player: AudioStreamPlayer
## Hide the volume and speed controls (the review panel's small preview).
var compact := false:
	set = set_compact
var timeline: Control
var play_button: Button
var loop_button: Button
var volume_button: Button
var volume: HSlider
var speed: MenuButton
## Current position in seconds (kept while stopped or paused).
var playhead := 0.0
## Stream length in seconds.
var length := 0.0
var _time: Label
var _popover: PanelContainer
var _stream: AudioStream
var _built := false
var _shaping := false
var _shown_playing := false


func _ready() -> void:
	theme_type_variation = "JoineryStack"
	focus_mode = Control.FOCUS_NONE
	if player == null:
		player = AudioStreamPlayer.new()
		add_child(player)
	if _stream == null and player.stream != null:
		_stream = _own(player.stream)
	timeline = TIMELINE_SCRIPT.new()
	timeline.seek_requested.connect(seek_to)
	add_child(timeline)
	_build_transport()
	player.finished.connect(_on_finished)
	_built = true
	set_compact(compact)
	_apply_stream(0.0, false)


func _exit_tree() -> void:
	if is_instance_valid(player) and player.finished.is_connected(_on_finished):
		player.finished.disconnect(_on_finished)


## Show [param stream]. The preview plays its own copy with looping turned off (the Loop
## toggle owns looping). With [param keep_position] the playhead stays where it was (clamped
## to the new length) and playback continues if it was playing, for before/after switching.
func set_stream(stream: AudioStream, keep_position := false) -> void:
	var resume := (
		keep_position and is_instance_valid(player) and player.playing and not player.stream_paused
	)
	var keep := playhead if keep_position else 0.0
	_stream = _own(stream)
	if _built:
		_apply_stream(keep, resume)


func set_compact(value: bool) -> void:
	compact = value
	if not _built:
		return
	volume_button.visible = not value
	speed.visible = not value
	timeline.set("compact", value)
	_shape_play()
	if value:
		_close_volume()


func toggle_play() -> void:
	if player.stream == null:
		return
	if player.playing:
		player.stream_paused = not player.stream_paused
	else:
		player.stream_paused = false
		player.play(0.0 if playhead >= length else playhead)
	_update()


func seek_to(seconds: float) -> void:
	playhead = clampf(seconds, 0.0, length)
	if player.playing:
		player.seek(playhead)
	_update()


func skip(seconds: float) -> void:
	seek_to(playhead + seconds)


func restart() -> void:
	if player.stream == null:
		return
	playhead = 0.0
	player.stream_paused = false
	player.play()
	_update()


func stop() -> void:
	player.stop()
	player.stream_paused = false
	seek_to(0.0)


## Preview gain in dB; at the bottom of the slider the preview is silent.
func set_volume_db(db: float) -> void:
	volume.set_value_no_signal(db)
	_on_volume(db)


## Playback speed as a factor (pitch follows speed).
func set_speed(rate: float) -> void:
	player.pitch_scale = rate
	speed.text = _rate_text(rate)


func _build_transport() -> void:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	add_child(row)
	play_button = UI_COMPONENTS.primary("")
	play_button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	play_button.accessibility_name = "Play"
	play_button.tooltip_text = "Play or pause (Space)"
	play_button.pressed.connect(toggle_play)
	UI_COMPONENTS.bind_icon(play_button, "play")
	play_button.theme_changed.connect(_shape_play)
	row.add_child(play_button)
	_time = UI_COMPONENTS.label("", "JoineryNumeric")
	_time.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_time.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_time.tooltip_text = "Left and Right skip five seconds. Home restarts."
	_time.mouse_filter = Control.MOUSE_FILTER_PASS
	row.add_child(_time)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(spacer)
	loop_button = UI_COMPONENTS.icon_button("loop", "Loop")
	loop_button.toggle_mode = true
	row.add_child(loop_button)
	volume_button = UI_COMPONENTS.icon_button("volume", "Volume")
	volume_button.pressed.connect(_toggle_volume)
	row.add_child(volume_button)
	speed = MenuButton.new()
	speed.theme_type_variation = "JoineryQuietButton"
	speed.flat = false
	speed.focus_mode = Control.FOCUS_ALL
	speed.tooltip_text = "Playback speed (pitch follows speed)"
	speed.accessibility_name = "Playback speed"
	UI_COMPONENTS.bind_height(speed, "md")
	for rate: float in SPEEDS:
		speed.get_popup().add_item(_rate_text(rate))
	speed.get_popup().id_pressed.connect(func(id: int) -> void: set_speed(SPEEDS[id]))
	speed.text = _rate_text(player.pitch_scale)
	row.add_child(speed)
	_build_volume()


func _build_volume() -> void:
	_popover = PanelContainer.new()
	_popover.theme_type_variation = "JoineryPopup"
	_popover.top_level = true
	_popover.hide()
	volume = HSlider.new()
	volume.min_value = MIN_DB
	volume.max_value = 0.0
	volume.step = 1.0
	volume.focus_mode = Control.FOCUS_ALL
	volume.accessibility_name = "Volume"
	volume.value = clampf(player.volume_db, MIN_DB, 0.0)
	volume.value_changed.connect(_on_volume)
	volume.focus_exited.connect(_on_volume_focus_lost)
	volume.theme_changed.connect(
		func() -> void:
			volume.custom_minimum_size = Vector2(
				UI_THEME.control_h(volume, "md") * 4.0, UI_THEME.control_h(volume, "sm")
			)
	)
	_popover.add_child(volume)
	add_child(_popover)
	_on_volume(volume.value)


func _toggle_volume() -> void:
	if _popover.visible:
		_close_volume()
		return
	_popover.size = _popover.get_combined_minimum_size()
	var anchor := volume_button.get_global_rect()
	var gap := float(UI_THEME.space(self, 1))
	_popover.global_position = Vector2(
		anchor.end.x - _popover.size.x, anchor.position.y - _popover.size.y - gap
	)
	UI_MOTION.enter(_popover, volume_button)
	volume.grab_focus()


func _close_volume() -> void:
	if _popover != null and _popover.visible:
		UI_MOTION.exit(_popover, volume_button)


func _on_volume_focus_lost() -> void:
	# Pressing the volume button again also moves focus; let its toggle close the popover.
	var focus := get_viewport().gui_get_focus_owner() if is_inside_tree() else null
	if focus != volume_button:
		_close_volume()


func _on_volume(db: float) -> void:
	player.volume_db = SILENT_DB if db <= MIN_DB else db
	var text := "Muted" if db <= MIN_DB else "%s dB" % _signed(db)
	volume.tooltip_text = "Volume: " + text
	volume_button.tooltip_text = "Volume: " + text


func _process(_delta: float) -> void:
	if player == null or player.stream == null:
		return
	if player.playing and not player.stream_paused:
		playhead = minf(player.get_playback_position(), length)
		_update()


func _update() -> void:
	if not _built:
		return
	var playing := player.playing and not player.stream_paused
	if playing != _shown_playing:
		_shown_playing = playing
		UI_COMPONENTS.bind_icon(play_button, "pause" if playing else "play")
		play_button.accessibility_name = "Pause" if playing else "Play"
	# From an empty label roll_number sets the text plainly and ends any roll in flight.
	UI_MOTION.roll_number(_time, playhead, _clock)
	timeline.playhead = playhead
	timeline.queue_redraw()


func _apply_stream(keep: float, resume: bool) -> void:
	player.stop()
	player.stream_paused = false
	player.stream = _stream
	length = _stream.get_length() if _stream != null else 0.0
	timeline.load_stream(_stream)
	playhead = clampf(keep, 0.0, length)
	play_button.disabled = _stream == null
	# A new length changes the whole clock: show it at once instead of rolling from the old one.
	_time.text = ""
	if resume and _stream != null:
		player.play(playhead)
	_update()


func _on_finished() -> void:
	if loop_button.button_pressed:
		restart()
	else:
		playhead = length
		_update()


## Keys are read before the GUI so Left and Right skip instead of moving focus to a
## neighbouring control. Space still presses a focused button, and the volume slider keeps
## its own arrow keys.
func _input(event: InputEvent) -> void:
	var handled := true
	match _transport_key(event):
		KEY_SPACE:
			toggle_play()
		KEY_LEFT:
			skip(-SKIP_SECONDS)
		KEY_RIGHT:
			skip(SKIP_SECONDS)
		KEY_HOME:
			restart()
		KEY_ESCAPE:
			handled = _popover.visible
			if handled:
				_close_volume()
				volume_button.grab_focus()
		_:
			handled = false
	if handled:
		get_viewport().set_input_as_handled()


## The key this preview should act on, or KEY_NONE: a plain key press while focus is inside
## the preview and the focused control does not need the key itself.
func _transport_key(event: InputEvent) -> Key:
	var key := event as InputEventKey
	if key == null or not key.pressed or key.echo:
		return KEY_NONE
	if key.ctrl_pressed or key.meta_pressed or key.alt_pressed or key.shift_pressed:
		return KEY_NONE
	var focus := get_viewport().gui_get_focus_owner()
	if focus == null or (focus != self and not is_ancestor_of(focus)):
		return KEY_NONE
	if focus == volume and key.keycode in [KEY_LEFT, KEY_RIGHT, KEY_HOME]:
		return KEY_NONE
	if focus is BaseButton and key.keycode in [KEY_SPACE, KEY_ENTER, KEY_KP_ENTER]:
		return KEY_NONE
	return key.keycode


## The play button is a circle one control height across: CONTROL_H.md, the height of the
## loop, volume and speed controls beside it, or CONTROL_H.sm when compact, the height of
## the Review panel's Before/After switch, so it never towers over the review controls.
## Its look is the primary button's, read from the live theme with the corners rounded
## fully; the guard stops the overrides from re-entering this handler through their own
## theme notification.
func _shape_play() -> void:
	if _shaping or not play_button.is_inside_tree():
		return
	_shaping = true
	play_button.begin_bulk_theme_override()
	for state: String in _PLAY_STATES + ["focus"]:
		play_button.remove_theme_stylebox_override(state)
	play_button.end_bulk_theme_override()
	var side := float(UI_THEME.control_h(play_button, "sm" if compact else "md"))
	var glyph := float(play_button.get_theme_constant("icon_max_width"))
	var pad := maxf(0.0, (side - glyph) / 2.0)
	play_button.begin_bulk_theme_override()
	for state: String in _PLAY_STATES + ["focus"]:
		var box := play_button.get_theme_stylebox(state).duplicate() as StyleBox
		if box is StyleBoxFlat:
			var flat := box as StyleBoxFlat
			flat.set_corner_radius_all(roundi(side / 2.0 + maxf(flat.expand_margin_left, 0.0)))
		box.set_content_margin_all(pad)
		play_button.add_theme_stylebox_override(state, box)
	play_button.end_bulk_theme_override()
	# After the overrides: their notification re-runs the shared CONTROL_H.md height binding.
	play_button.custom_minimum_size = Vector2(side, side)
	play_button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_shaping = false


## Own copy of [param stream] with its loop turned off, so auditioning never touches the
## imported resource.
static func _own(stream: AudioStream) -> AudioStream:
	if stream == null:
		return null
	var copy := stream.duplicate() as AudioStream
	if copy is AudioStreamWAV:
		(copy as AudioStreamWAV).loop_mode = AudioStreamWAV.LOOP_DISABLED
	elif copy is AudioStreamOggVorbis or copy is AudioStreamMP3:
		copy.set("loop", false)
	return copy


## "0:03 / 0:12" (tabular digits through JoineryNumeric). A sound shorter than ten seconds
## shows its length to a tenth ("0:00 / 0:01.4"), so a short effect never reads "0:00".
func _clock(seconds: float) -> String:
	return "%s / %s" % [UI_MOTION.format_clock(seconds), _length_text()]


func _length_text() -> String:
	var text := UI_MOTION.format_clock(length)
	if length > 0.0 and length < 10.0:
		text += ".%d" % (floori(length * 10.0) % 10)
	return text


static func _rate_text(rate: float) -> String:
	return ("%s×" % rate).replace(".0×", "×")


static func _signed(db: float) -> String:
	return ("−%d" % roundi(absf(db))) if db < -0.5 else "%d" % roundi(db)
