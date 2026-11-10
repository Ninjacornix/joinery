@tool
extends Control
## Waveform and playhead for one stream. Decodes the existing stream in bounded main-thread
## chunks and never alters its source. The drawing is normalised to the loudest sample, so
## quiet sounds still show their shape; the tooltip gives the real peak level.

signal seek_requested(seconds: float)

const UI_THEME := preload("ui_theme.gd")
const BINS := 1024
## Minimum height as a multiple of the editor font size (spec: 9 × font).
const HEIGHT_EM := 9.0
## Per-frame decode budget in microseconds, so long files never stall the editor.
const DECODE_BUDGET_USEC := 3000
## Loudest drawn sample reaches this share of half the height.
const FILL := 0.8
## Below this linear peak the stream is treated as silent.
const SILENCE := 0.0001

var duration := 0.0
var compact := false:
	set = set_compact
var playhead := 0.0
var peaks := PackedVector2Array()
## Largest absolute sample seen so far (linear, 0–1).
var peak := 0.0
var _decoder: AudioStreamPlayback
var _frames := 0
var _total_frames := 1
var _dragging := false


func _init() -> void:
	focus_mode = Control.FOCUS_ALL
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Waveform"
	set_process(false)


func _ready() -> void:
	resized.connect(queue_redraw)


## In a compact preview (the Review panel) the waveform may shrink to two large control
## heights, so the decision buttons below it stay on screen in a short bottom panel.
func set_compact(value: bool) -> void:
	compact = value
	_fit()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_fit()
		queue_redraw()


func _fit() -> void:
	var line := float(UI_THEME.font_size(self, "body"))
	var tall := 2.0 * UI_THEME.control_h(self, "lg") if compact else line * HEIGHT_EM
	custom_minimum_size = Vector2(line * HEIGHT_EM / 2.0, tall)


## Start decoding [param stream] for the waveform; null clears it.
func load_stream(stream: AudioStream) -> void:
	if _decoder != null:
		_decoder.stop()
		_decoder = null
	peaks.resize(BINS)
	peaks.fill(Vector2.ZERO)
	peak = 0.0
	_frames = 0
	duration = stream.get_length() if stream != null else 0.0
	_total_frames = maxi(1, ceili(duration * AudioServer.get_mix_rate()))
	tooltip_text = ""
	if stream != null:
		_decoder = stream.instantiate_playback()
		_decoder.start()
	set_process(_decoder != null)
	queue_redraw()


func _exit_tree() -> void:
	if _decoder != null:
		_decoder.stop()
		_decoder = null


func _process(_delta: float) -> void:
	if _decoder == null:
		set_process(false)
		return
	var deadline := Time.get_ticks_usec() + DECODE_BUDGET_USEC
	while _frames < _total_frames and Time.get_ticks_usec() < deadline:
		var samples := _decoder.mix_audio(1.0, mini(2048, _total_frames - _frames))
		if samples.is_empty():
			_frames = _total_frames
			break
		for sample: Vector2 in samples:
			var bin := mini(BINS - 1, int(float(_frames) / _total_frames * BINS))
			var low := minf(sample.x, sample.y)
			var high := maxf(sample.x, sample.y)
			peaks[bin] = Vector2(minf(peaks[bin].x, low), maxf(peaks[bin].y, high))
			peak = maxf(peak, maxf(absf(low), absf(high)))
			_frames += 1
	if _frames >= _total_frames:
		_decoder.stop()
		_decoder = null
		set_process(false)
		tooltip_text = peak_text()
	queue_redraw()


## "Peak −12.3 dB", or "Silent" for a stream with no signal.
func peak_text() -> String:
	if peak < SILENCE:
		return "Silent"
	var level := linear_to_db(peak)
	var prefix := "−" if level < -0.05 else ""
	return "Peak %s%.1f dB" % [prefix, absf(level)]


func _draw() -> void:
	var rect := Rect2(Vector2.ZERO, size)
	draw_style_box(get_theme_stylebox("panel", "JoineryMediaFrame"), rect)
	var inset := float(UI_THEME.space(self, 2))
	var width := maxf(size.x - inset * 2.0, 1.0)
	var middle := size.y * 0.5
	var gain := (size.y * 0.5 - inset) * FILL / (peak if peak >= SILENCE else 1.0)
	var progress := clampf(playhead / duration, 0.0, 1.0) if duration > 0 else 0.0
	var played := _role("accent")
	var unplayed := _role("text_faint")
	var hairline := maxf(1.0, roundf(UI_THEME.unit(self)))
	draw_line(
		Vector2(inset, middle), Vector2(inset + width, middle), _role("border_subtle"), hairline
	)
	var stroke := maxf(1.0, width / BINS)
	for index: int in peaks.size():
		var x := inset + (index + 0.5) / BINS * width
		var top := middle - peaks[index].y * gain
		var bottom := middle - peaks[index].x * gain
		if bottom - top < hairline:
			continue
		var ink := played if x <= inset + progress * width else unplayed
		draw_line(Vector2(x, top), Vector2(x, bottom), ink, stroke)
	var head := inset + progress * width
	draw_line(Vector2(head, inset), Vector2(head, size.y - inset), played, hairline * 2.0)
	if has_focus():
		draw_style_box(get_theme_stylebox("focus", "Button"), rect)


func _role(name: String) -> Color:
	if has_theme_color(name, "Joinery"):
		return get_theme_color(name, "Joinery")
	return UI_THEME.color(self, name)


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
	var inset := float(UI_THEME.space(self, 2))
	var width := maxf(size.x - inset * 2.0, 1.0)
	seek_requested.emit(clampf((x - inset) / width, 0, 1) * duration)
