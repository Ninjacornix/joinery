@tool
extends Control
## Decode the existing stream in bounded main-thread chunks; never alter its source.

signal seek_requested(seconds: float)

const BINS := 1024
var duration := 0.0
var playhead := 0.0
var peaks := PackedVector2Array()
var _decoder: AudioStreamPlayback
var _frames := 0
var _total_frames := 1
var _dragging := false


func _ready() -> void:
	custom_minimum_size = Vector2(200, get_theme_default_font_size() * 9)
	focus_mode = Control.FOCUS_ALL
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	resized.connect(queue_redraw)


func load_stream(stream: AudioStream) -> void:
	duration = stream.get_length()
	_total_frames = maxi(1, ceili(duration * AudioServer.get_mix_rate()))
	peaks.resize(BINS)
	peaks.fill(Vector2.ZERO)
	_decoder = stream.instantiate_playback()
	_decoder.start()


func _exit_tree() -> void:
	if _decoder != null:
		_decoder.stop()
		_decoder = null


func _process(_delta: float) -> void:
	if _decoder == null:
		return
	var deadline := Time.get_ticks_usec() + 3000
	while _frames < _total_frames and Time.get_ticks_usec() < deadline:
		var samples := _decoder.mix_audio(1.0, mini(2048, _total_frames - _frames))
		if samples.is_empty():
			_frames = _total_frames
			break
		for sample: Vector2 in samples:
			var bin := mini(BINS - 1, int(float(_frames) / _total_frames * BINS))
			peaks[bin] = Vector2(
				minf(peaks[bin].x, minf(sample.x, sample.y)),
				maxf(peaks[bin].y, maxf(sample.x, sample.y))
			)
			_frames += 1
	if _frames >= _total_frames:
		_decoder.stop()
		_decoder = null
	queue_redraw()


func _draw() -> void:
	var rect := Rect2(Vector2.ZERO, size)
	draw_style_box(get_theme_stylebox("panel", "Tree"), rect)
	var accent := get_theme_color("accent_color", "Editor") if has_theme_color(
		"accent_color", "Editor"
	) else Color(0.85, 0.63, 0.26)
	var middle := size.y * 0.5
	var amplitude := size.y * 0.4
	var progress := playhead / duration if duration > 0 else 0.0
	draw_line(Vector2(0, middle), Vector2(size.x, middle), Color(0.4, 0.4, 0.4, 0.3))
	for index: int in peaks.size():
		var x := (index + 0.5) / BINS * size.x
		var color := accent if x <= progress * size.x else Color(0.6, 0.65, 0.7)
		draw_line(
			Vector2(x, middle - peaks[index].y * amplitude),
			Vector2(x, middle - peaks[index].x * amplitude), color, maxf(1, size.x / BINS)
		)
	draw_line(Vector2(progress * size.x, 4), Vector2(progress * size.x, size.y - 4), accent, 2)
	if has_focus():
		draw_style_box(get_theme_stylebox("focus", "Button"), rect)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		_dragging = event.pressed
		if event.pressed:
			grab_focus()
			_seek_at(event.position.x)
		accept_event()
	elif event is InputEventMouseMotion and _dragging:
		_seek_at(event.position.x)
		accept_event()


func _seek_at(x: float) -> void:
	seek_requested.emit(clampf(x / maxf(size.x, 1), 0, 1) * duration)
