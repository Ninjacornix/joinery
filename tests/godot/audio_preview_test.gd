extends SceneTree

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	root.size = Vector2i(1000, 700)
	var workspace := load("res://addons/joinery/tool_workspace.gd").new() as VBoxContainer
	root.add_child(workspace)
	# This exercises one transport directly, without selecting an indexed asset.
	# An unrelated automatic catalog refresh would replace that preview midway.
	workspace.set_process(false)
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	workspace._show_preview("res://audio/sfx/door_creak.wav")
	var panel: VBoxContainer = workspace._audio_panel
	var player: AudioStreamPlayer = workspace._audio
	_check(panel.player == player, "The workspace preview must share the workspace player")
	var source := load("res://audio/sfx/door_creak.wav") as AudioStreamWAV
	var original_loop := source.loop_mode
	var checksum := FileAccess.get_sha256("res://audio/sfx/door_creak.wav")
	_check(player.stream != source, "Preview must use its own stream resource")
	_check(
		player.stream.loop_mode == AudioStreamWAV.LOOP_DISABLED, "Transport owns preview looping"
	)
	await process_frame
	await process_frame
	_check_layout(panel)
	# A clip under ten seconds shows its length to a tenth, so a short effect never reads 0:00.
	var tenths := floori(panel.length * 10.0)
	_check(panel._time.text == "0:00 / 0:0%d.%d" % [tenths / 10, tenths % 10], panel._time.text)
	panel.seek_to(player.stream.get_length() * 0.5)
	_check(not player.playing, "Seeking a stopped preview must not start it")
	_check(panel.playhead > 0, "Stopped seeking must retain its position")
	panel.toggle_play()
	_check(player.playing, "Play must start audio at the chosen position")
	_check(panel.play_button.accessibility_name == "Pause", "Playing shows the pause action")
	panel.toggle_play()
	_check(player.stream_paused, "Pause must retain playback")
	panel.seek_to(0.1)
	_check(player.stream_paused, "Seeking paused audio must keep it paused")
	panel.skip(5)
	_check(is_equal_approx(panel.playhead, player.stream.get_length()), "Skip must clamp at end")
	panel.skip(-100)
	_check(panel.playhead == 0, "Skip must clamp at start")
	panel.speed.get_popup().id_pressed.emit(5)
	_check(player.pitch_scale == 2, "Speed menu must affect playback")
	_check(panel.speed.text == "2×", "Speed chip must name the speed: " + panel.speed.text)
	panel.volume.value = -12
	_check(player.volume_db == -12, "Volume must control preview gain")
	panel.volume.value = panel.MIN_DB
	_check(player.volume_db <= -80, "The bottom of the volume slider must silence playback")
	panel.volume.value = -12
	_check(player.volume_db == -12, "Raising the volume must restore preview gain")
	panel.volume_button.pressed.emit()
	_check(panel._popover.visible, "The volume button must open its popover")
	panel.volume_button.pressed.emit()
	_check(not panel._popover.visible, "The volume button must close its popover")
	panel.stop()
	_check(not player.playing and not player.stream_paused, "Stop must clear pause")
	_check(panel.playhead == 0, "Stop must reset playhead")
	# Click the real waveform, then use the keyboard with focus inside the player.
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	click.position = panel.timeline.global_position + panel.timeline.size * Vector2(0.75, 0.5)
	root.push_input(click)
	click.pressed = false
	root.push_input(click)
	_check(
		absf(panel.playhead - player.stream.get_length() * 0.75) < 0.02,
		"Clicking the waveform must seek to that time"
	)
	_press(KEY_HOME)
	_check(player.playing and panel.playhead < 0.1, "Home must restart when the waveform has focus")
	panel.stop()
	_press(KEY_RIGHT)
	_check(is_equal_approx(panel.playhead, panel.length), "Right must skip forward")
	_press(KEY_LEFT)
	_check(panel.playhead == 0, "Left must skip back")
	_press(KEY_SPACE)
	_check(player.playing, "Space must play when the waveform has focus")
	panel.stop()
	panel.set_speed(1.0)
	var finished_events: Array[int] = [0]
	player.finished.connect(func() -> void: finished_events[0] += 1)
	panel.loop_button.button_pressed = true
	panel.seek_to(player.stream.get_length() - 0.1)
	panel.toggle_play()
	var loop_started := Time.get_ticks_msec()
	var deadline := loop_started + 3000
	while (
		(finished_events[0] == 0 or not player.playing or player.get_playback_position() >= 0.8)
		and Time.get_ticks_msec() < deadline
	):
		await process_frame
	_check(finished_events[0] > 0, "Loop must observe a real playback-end event")
	_check(
		player.playing and player.get_playback_position() < 0.8, "Loop must restart after the end"
	)
	print(
		"Audio loop end events=",
		finished_events[0],
		" elapsed_ms=",
		Time.get_ticks_msec() - loop_started
	)
	panel.loop_button.button_pressed = false
	var previous_finished := finished_events[0]
	var end_started := Time.get_ticks_msec()
	panel.seek_to(player.stream.get_length() - 0.1)
	# Seek must actually affect playback; waiting for the full unseeked clip to
	# finish would hide a broken seek. Mixer commands and finished notifications
	# are asynchronous, and SceneTree timer time is not the audio driver's clock.
	deadline = end_started + 1000
	while (
		(
			finished_events[0] == previous_finished
			and player.get_playback_position() < player.stream.get_length() - 0.2
		)
		and Time.get_ticks_msec() < deadline
	):
		await process_frame
	_check(
		(
			finished_events[0] > previous_finished
			or player.get_playback_position() >= player.stream.get_length() - 0.2
		),
		"Playing seek must reach the requested end region"
	)
	deadline = end_started + 3000
	while (
		(finished_events[0] == previous_finished or player.playing)
		and Time.get_ticks_msec() < deadline
	):
		await process_frame
	_check(finished_events[0] > previous_finished, "Non-looping playback must emit its end event")
	_check(not player.playing, "Non-looping audio must stop at the end")
	_check(is_equal_approx(panel.playhead, panel.length), "End position must remain")
	print(
		"Audio final end events=",
		finished_events[0],
		" elapsed_ms=",
		Time.get_ticks_msec() - end_started,
		" position=",
		panel.playhead,
		" length=",
		panel.length
	)
	deadline = Time.get_ticks_msec() + 3000
	while panel.timeline._decoder != null and Time.get_ticks_msec() < deadline:
		await process_frame
	var peak := 0.0
	for bin: Vector2 in panel.timeline.peaks:
		peak = maxf(peak, bin.y - bin.x)
	_check(peak > 0.01, "Waveform must contain decoded source audio")
	_check(panel.timeline.tooltip_text.begins_with("Peak "), "Waveform tooltip names the peak")
	_check(
		source.loop_mode == original_loop, "Audition controls must preserve imported loop settings"
	)
	_check(
		FileAccess.get_sha256("res://audio/sfx/door_creak.wav") == checksum,
		"Audition controls must preserve source audio bytes"
	)
	workspace._show_preview("res://art/props/door.blend")
	_check(not player.playing, "Changing assets must stop playback")
	workspace.queue_free()
	await process_frame
	await _check_standalone(source)
	print("audio playback: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _press(keycode: Key) -> void:
	var key := InputEventKey.new()
	key.keycode = keycode
	key.pressed = true
	root.push_input(key)


## One transport row; no seek slider, Stop, Mute, Restart or skip buttons.
func _check_layout(panel: VBoxContainer) -> void:
	var labels: Array[String] = []
	for button: Node in panel.find_children("*", "BaseButton", true, false):
		labels.append((button as BaseButton).get("text"))
	for removed: String in ["Stop", "Mute", "Restart", "Play", "Pause", "−5s", "+5s", "Loop"]:
		_check(not labels.has(removed), "Removed transport text button: " + removed)
	var sliders := panel.find_children("*", "HSlider", true, false)
	_check(
		sliders.size() == 1 and sliders[0] == panel.volume, "Only the volume popover has a slider"
	)
	_check(not panel.volume.is_visible_in_tree(), "Volume slider lives in a closed popover")
	_check(panel.timeline.size.y >= panel.get_theme_default_font_size() * 9 - 1, "Timeline height")
	_check(panel.timeline.size.x >= panel.size.x - 1, "Timeline fills the width")
	var play: Button = panel.play_button
	_check(absf(play.size.x - play.size.y) < 1, "Play button is a circle")
	_check(play.theme_type_variation == "JoineryPrimaryButton", "Play is the primary action")
	_check(panel._time.theme_type_variation == "JoineryNumeric", "Clock uses tabular digits")
	_check(play.get_parent() == panel.loop_button.get_parent(), "One transport row")


## Created with no player: it makes its own, keeps position across streams, and compact hides
## volume and speed.
func _check_standalone(source: AudioStream) -> void:
	var preview := load("res://addons/joinery/audio_preview.gd").new() as VBoxContainer
	preview.compact = true
	preview.set_stream(source)
	root.add_child(preview)
	await process_frame
	_check(preview.player != null and preview.player.get_parent() == preview, "Own player")
	_check(preview.player.stream != source and preview.length > 0, "Own copy of the stream")
	_check(not preview.volume_button.visible and not preview.speed.visible, "Compact hides extras")
	preview.compact = false
	_check(preview.volume_button.visible and preview.speed.visible, "Full shows volume and speed")
	preview.seek_to(preview.length * 0.5)
	var kept: float = preview.playhead
	preview.set_stream(source, true)
	_check(is_equal_approx(preview.playhead, kept), "keep_position must keep the playhead")
	preview.set_stream(source)
	_check(preview.playhead == 0, "A new stream starts at the beginning")
	preview.set_stream(null)
	_check(preview.play_button.disabled and preview.length == 0, "No stream disables play")
	preview.queue_free()
	await process_frame
