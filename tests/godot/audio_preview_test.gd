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
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	workspace._show_preview("res://audio/sfx/door_creak.wav")
	var panel: VBoxContainer = workspace._audio_panel
	var player: AudioStreamPlayer = workspace._audio
	var source := load("res://audio/sfx/door_creak.wav") as AudioStreamWAV
	var original_loop := source.loop_mode
	var checksum := FileAccess.get_sha256("res://audio/sfx/door_creak.wav")
	_check(player.stream != source, "Preview must use its own stream resource")
	_check(player.stream.loop_mode == AudioStreamWAV.LOOP_DISABLED, "Transport owns preview looping")
	await process_frame
	await process_frame
	panel.seek_to(player.stream.get_length() * 0.5)
	_check(not player.playing, "Seeking a stopped preview must not start it")
	_check(panel.seek_slider.value > 0, "Stopped seeking must retain its position")
	panel.toggle_play()
	_check(player.playing, "Play must start audio at the chosen position")
	panel.toggle_play()
	_check(player.stream_paused, "Pause must retain playback")
	panel.seek_to(0.1)
	_check(player.stream_paused, "Seeking paused audio must keep it paused")
	panel.skip(5)
	_check(
		is_equal_approx(panel.seek_slider.value, player.stream.get_length()), "Skip must clamp at end"
	)
	panel.skip(-100)
	_check(panel.seek_slider.value == 0, "Skip must clamp at start")
	panel.speed.select(5)
	panel.speed.item_selected.emit(5)
	_check(player.pitch_scale == 2, "Speed control must affect playback")
	panel.volume.value = -12
	_check(player.volume_db == -12, "Volume must control preview gain")
	var mute: Button
	for button: Node in panel.find_children("*", "Button", true, false):
		if button.text == "Mute":
			mute = button
	mute.button_pressed = true
	_check(player.volume_db <= -80, "Mute must silence playback")
	mute.button_pressed = false
	_check(player.volume_db == -12, "Unmute must restore preview gain")
	panel.stop()
	_check(not player.playing and not player.stream_paused, "Stop must clear pause")
	_check(panel.seek_slider.value == 0, "Stop must reset playhead")
	# Click the real waveform, then use the keyboard with focus inside the player.
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	click.position = panel.timeline.global_position + panel.timeline.size * Vector2(0.75, 0.5)
	root.push_input(click)
	click.pressed = false
	root.push_input(click)
	_check(
		absf(panel.seek_slider.value - player.stream.get_length() * 0.75) < 0.02,
		"Clicking the waveform must seek to that time"
	)
	var space := InputEventKey.new()
	space.keycode = KEY_SPACE
	space.pressed = true
	root.push_input(space)
	_check(player.playing, "Space must play when the waveform has focus")
	panel.stop()
	panel.speed.select(2)
	panel.speed.item_selected.emit(2)
	panel.loop_button.button_pressed = true
	panel.seek_to(player.stream.get_length() - 0.1)
	panel.toggle_play()
	await create_timer(0.3).timeout
	_check(player.playing and player.get_playback_position() < 0.8, "Loop must restart after the end")
	panel.loop_button.button_pressed = false
	panel.seek_to(player.stream.get_length() - 0.1)
	await create_timer(0.3).timeout
	_check(not player.playing, "Non-looping audio must stop at the end")
	_check(
		is_equal_approx(panel.seek_slider.value, panel.seek_slider.max_value), "End position must remain"
	)
	var deadline := Time.get_ticks_msec() + 3000
	while panel.timeline._decoder != null and Time.get_ticks_msec() < deadline:
		await process_frame
	var peak := 0.0
	for bin: Vector2 in panel.timeline.peaks:
		peak = maxf(peak, bin.y - bin.x)
	_check(peak > 0.01, "Waveform must contain decoded source audio")
	_check(source.loop_mode == original_loop, "Audition controls must preserve imported loop settings")
	_check(
		FileAccess.get_sha256("res://audio/sfx/door_creak.wav") == checksum,
		"Audition controls must preserve source audio bytes"
	)
	workspace._show_preview("res://art/props/door.blend")
	_check(not player.playing, "Changing assets must stop playback")
	workspace.queue_free()
	await process_frame
	print("audio playback: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
