@tool
extends VBoxContainer
## The Review panel's media area: the file name, then one large preview that fills the
## remaining height. Images and model renders show in one TextureRect; Before and After swap
## by crossfading. Audio uses one compact audio_preview, so Before and After play A/B on the
## same player at the same position. A side whose file is not written yet shows a pulsing
## "Rendering preview…" frame until it appears, then fades in; the renders start when the
## change passes its checks, so the review is on screen before they are done
## ([method update_media] brings their outcome).

## The shown file changed (several media items), so Before/After availability may differ.
signal item_changed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
## Seconds to wait for a preview file before giving up.
const LOAD_TIMEOUT := 120.0
## Seconds between checks for a preview file that is still being written.
const POLL := 0.25
const NO_PREVIEW := "No preview. Open Details to see the file changes."
## Files Image.load reads; anything else (a .kra, a .blend) has no preview of its own.
const IMAGE_FORMATS := ["png", "jpg", "jpeg", "webp", "svg", "bmp", "tga", "exr", "hdr"]

## The side on show: "before" or "after".
var side := "after"
var _items: Array = []
var _index := 0
var _textures := {}
var _streams := {}
var _name: Label
var _picker: OptionButton
var _frame: PanelContainer
var _front: TextureRect
var _back: TextureRect
var _audio_box: VBoxContainer
var _audio: Control
var _loading: PanelContainer
var _loading_dots: INDICATOR
var _failure: Label
var _waiting := ""
var _waited := 0.0
var _since_poll := 0.0


func _init() -> void:
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Preview of the proposed change"
	set_process(false)
	var names := HBoxContainer.new()
	names.theme_type_variation = "JoineryRow"
	add_child(names)
	_name = UI.label("", "JoineryCaption")
	_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_name.mouse_filter = Control.MOUSE_FILTER_PASS
	names.add_child(_name)
	_picker = UI.picker([], "sm")
	_picker.accessibility_name = "Changed file to preview"
	_picker.tooltip_text = "Choose which changed file to preview"
	_picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_picker.item_selected.connect(_on_item_selected)
	_picker.hide()
	names.add_child(_picker)
	_frame = PanelContainer.new()
	_frame.theme_type_variation = "JoineryMediaFrame"
	_frame.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_frame.clip_contents = true
	add_child(_frame)
	_front = _picture()
	_back = _picture()
	_audio_box = VBoxContainer.new()
	_audio_box.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_audio_box.hide()
	add_child(_audio_box)
	_loading = PanelContainer.new()
	_loading.theme_type_variation = "JoineryLoadingFrame"
	_loading.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_loading.hide()
	add_child(_loading)
	var waiting := HBoxContainer.new()
	waiting.theme_type_variation = "JoineryRow"
	waiting.alignment = BoxContainer.ALIGNMENT_CENTER
	_loading.add_child(waiting)
	_loading_dots = INDICATOR.new()
	waiting.add_child(_loading_dots)
	var rendering := UI.label("Rendering preview…", "JoineryCaption")
	rendering.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	waiting.add_child(rendering)
	_failure = UI.label(NO_PREVIEW, "JoineryCaption", true)
	_failure.hide()
	add_child(_failure)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		var tall := float(UI_THEME.control_h(self, "lg") * 2)
		for area: Control in [_frame, _loading, _audio_box]:
			area.custom_minimum_size.y = tall
		_fit_picker()


func _process(delta: float) -> void:
	_waited += delta
	_since_poll += delta
	if _since_poll < POLL:
		return
	_since_poll = 0.0
	if FileAccess.file_exists(_waiting):
		_stop_waiting()
		_show_current(false)
		UI_MOTION.fade_in(_frame if _frame.visible else _audio_box)
	elif _waited >= LOAD_TIMEOUT:
		_stop_waiting()
		_fail()


## Show [param media] items ({path, media: "image"|"audio", before?, after?}), starting on
## the first item's After side.
func show_media(media: Array) -> void:
	clear_media()
	for item: Variant in media:
		if item is Dictionary and (item.has("before") or item.has("after") or item.has("error")):
			_items.append(item)
	visible = not _items.is_empty()
	if _items.is_empty():
		return
	_picker.clear()
	for item: Dictionary in _items:
		_picker.add_item(str(item.get("path", "")).get_file())
	_picker.visible = _items.size() > 1
	_name.visible = not _picker.visible
	_picker.select(0)
	_fit_picker()
	_show_current(false)


## The renders of the shown items are done: take each item's final sides, or its error. An
## item already on screen stays where it is, and is shown again even after its wait timed out.
func update_media(media: Array) -> void:
	if _items.is_empty():
		show_media(media)
		return
	for item: Variant in media:
		if not item is Dictionary:
			continue
		for index: int in _items.size():
			var shown: Dictionary = _items[index]
			if shown.get("path") == item.get("path") and shown.get("part") == item.get("part"):
				_items[index] = (item as Dictionary).duplicate()
	_show_current(false)


## Stop audio, drop loaded previews and forget the items.
func clear_media() -> void:
	_stop_waiting()
	stop_audio()
	if _audio != null:
		_audio.queue_free()
		_audio = null
	_items.clear()
	_textures.clear()
	_streams.clear()
	_index = 0
	side = "after"
	for picture: TextureRect in [_front, _back]:
		UI_MOTION.stop(picture)
		picture.texture = null
	_failure.hide()
	_name.text = ""


## True when there is something to preview.
func has_media() -> bool:
	return not _items.is_empty()


## True when any item has both a Before and an After.
func has_pairs() -> bool:
	return _items.any(func(item: Dictionary) -> bool: return _paired(item))


## True when the shown item is a new asset (After only).
func current_is_new() -> bool:
	if _items.is_empty():
		return false
	var item: Dictionary = _items[_index]
	return item.has("after") and not item.has("before")


## Switch to "before" or "after". Images crossfade; audio keeps its playback position.
func set_side(value: String) -> void:
	if value == side or not value in ["before", "after"]:
		return
	side = value
	if not _items.is_empty():
		_show_current(true)


## The texture on show (null for audio, while loading, or on failure).
func texture() -> Texture2D:
	return _front.texture if _frame.visible and _front.visible else null


## The shown file name (the caption above the preview).
func file_name() -> String:
	return _name.text


## True while waiting for a preview file to be written.
func is_loading() -> bool:
	return not _waiting.is_empty()


## True when the shown item could not be previewed.
func is_failed() -> bool:
	return _failure.visible


## The audio preview in use, or null.
func audio() -> Control:
	return _audio


## Stop audio playback, if any.
func stop_audio() -> void:
	if _audio == null:
		return
	if _audio.has_method("stop"):
		_audio.call("stop")
	var player: Variant = _audio.get("player")
	if player is AudioStreamPlayer and is_instance_valid(player):
		(player as AudioStreamPlayer).stop()


func _picture() -> TextureRect:
	var picture := TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.size_flags_vertical = Control.SIZE_EXPAND_FILL
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	picture.hide()
	_frame.add_child(picture)
	return picture


static func _paired(item: Dictionary) -> bool:
	return item.has("before") and item.has("after")


func _on_item_selected(index: int) -> void:
	if index == _index or index < 0 or index >= _items.size():
		return
	stop_audio()
	_index = index
	_fit_picker()
	_show_current(false)
	item_changed.emit()


## Size the picker to its chosen file name, capped at 16em, so the chevron hugs the text
## (an ellipsizing picker would otherwise shrink to its chevron).
func _fit_picker() -> void:
	if not _picker.is_inside_tree():
		return
	var font := _picker.get_theme_font("font")
	var font_size := _picker.get_theme_font_size("font_size")
	var box := _picker.get_theme_stylebox("normal")
	var arrow := _picker.get_theme_icon("arrow")
	var width := font.get_string_size(_picker.text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
	width += box.get_margin(SIDE_LEFT) + box.get_margin(SIDE_RIGHT)
	width += (
		(arrow.get_width() if arrow != null else 0) + _picker.get_theme_constant("arrow_margin")
	)
	_picker.custom_minimum_size.x = minf(ceilf(width), 16.0 * UI_THEME.font_size(self, "body"))


func _show_current(swap: bool) -> void:
	var item: Dictionary = _items[_index]
	var path := str(item.get("path", ""))
	_name.text = path.get_file()
	_name.tooltip_text = "res://" + path
	_picker.tooltip_text = "res://" + path
	if not (item.has("before") or item.has("after")):
		_stop_waiting()
		_fail(str(item.get("error", "")))
		return
	if item.has("error") and not item.has(side):
		# That side's render failed: say so rather than show the other side as if it were it.
		_stop_waiting()
		_fail(str(item["error"]), side)
		return
	var chosen := side if item.has(side) else ("after" if item.has("after") else "before")
	var source := str(item[chosen])
	_failure.hide()
	if not FileAccess.file_exists(source):
		_wait_for(source)
		return
	_stop_waiting()
	if str(item.get("media", "image")) == "audio":
		_show_audio(source, swap)
	else:
		_show_image(source, swap)


func _show_image(source: String, swap: bool) -> void:
	_audio_box.hide()
	var picture := _texture(source)
	if picture == null:
		_fail()
		return
	_frame.show()
	if swap and _front.visible and _front.texture != null and _front.texture != picture:
		_back.texture = picture
		UI_MOTION.crossfade(_front, _back)
		var shown := _back
		_back = _front
		_front = shown
		return
	UI_MOTION.stop(_back)
	_back.hide()
	_front.texture = picture
	_front.show()


func _show_audio(source: String, swap: bool) -> void:
	_frame.hide()
	var stream := _stream(source)
	if stream == null:
		_fail()
		return
	_audio_box.show()
	if _audio == null:
		_audio = _make_audio(stream)
	elif _audio.has_method("set_stream"):
		_audio.call("set_stream", stream, swap)


## A compact audio_preview (it owns its player). Falls back to handing it a player for the
## older preview that needs one before it enters the tree.
func _make_audio(stream: AudioStream) -> Control:
	var here := (get_script() as Script).resource_path.get_base_dir()
	var script: Script = load(here.path_join("audio_preview.gd"))
	var preview: Control = script.new()
	preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	if "compact" in preview:
		preview.set("compact", true)
	if preview.has_method("set_stream"):
		_audio_box.add_child(preview)
		preview.call("set_stream", stream, false)
		return preview
	var player := AudioStreamPlayer.new()
	player.stream = stream
	_audio_box.add_child(player)
	preview.set("player", player)
	_audio_box.add_child(preview)
	return preview


func _texture(source: String) -> Texture2D:
	if _textures.has(source):
		return _textures[source]
	if not source.get_extension().to_lower() in IMAGE_FORMATS:
		return null
	var image := Image.new()
	if image.load(source) != OK or image.is_empty():
		return null
	var result := ImageTexture.create_from_image(image)
	_textures[source] = result
	return result


func _stream(source: String) -> AudioStream:
	if _streams.has(source):
		return _streams[source]
	var result: AudioStream = null
	match source.get_extension().to_lower():
		"wav":
			result = AudioStreamWAV.load_from_file(source)
		"ogg":
			result = AudioStreamOggVorbis.load_from_file(source)
		"mp3":
			result = AudioStreamMP3.load_from_file(source)
	if result != null:
		_streams[source] = result
	return result


func _wait_for(source: String) -> void:
	_frame.hide()
	_audio_box.hide()
	if _waiting == source:
		return
	_waiting = source
	_waited = 0.0
	_since_poll = 0.0
	_loading.show()
	_loading_dots.running = true
	UI_MOTION.breathe(_loading)
	set_process(true)


func _stop_waiting() -> void:
	_waiting = ""
	set_process(false)
	_loading_dots.running = false
	UI_MOTION.stop(_loading)
	_loading.hide()


## No preview: [param reason] (a render's error) says why, or the generic line. [param missing]
## names the side that failed when the other one exists.
func _fail(reason := "", missing := "") -> void:
	_frame.hide()
	_audio_box.hide()
	var lead := "No preview."
	if missing == "after":
		lead = "No preview of the change."
	elif missing == "before":
		lead = "No preview of the original."
	_failure.text = NO_PREVIEW if reason.is_empty() and missing.is_empty() else lead
	if not reason.is_empty():
		_failure.text += " " + reason
	_failure.show()
