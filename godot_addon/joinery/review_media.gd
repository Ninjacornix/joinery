# gdlint: disable=max-public-methods
@tool
extends VBoxContainer
## The Review panel's stage: one before/after view sized to the panel, and a filmstrip under it
## when the change has several items. Each kind shows the way it is judged fastest:
##
## - Textures, part views and scene snapshots: one frame split by a line you can drag
##   (review_swipe.gd), the original left, the change right. Before and After slide the line
##   all the way, so the switch is a mask reveal.
## - Models: the front and the far side next to each other; Before and After crossfade both.
## - Sounds: one compact audio_preview with its waveform, so Before and After play A/B at the
##   same position.
## - Text: only the changed lines (review_diff.gd).
## - A blockout (ADR 0043): the user's sketch (the item's `reference`) beside the plan of the
##   level built from it; Before and After crossfade the plan.
## - Animations: the original and the change as two looping clips side by side
##   (review_clip.gd); Before and After dim the other clip.
##
## Pictures fill the frame, keeping their shape: a pair shares its width. Pixel art and small
## or flat pictures (a sketch, a plan, a 32 px texture) are blown up crisp, photos and renders
## smooth (review_glance.gd's picture_filter). The frame grows with the panel's width, so a
## wide panel shows a large picture rather than a thin strip.
##
## A side whose file is not written yet shows a pulsing "Rendering preview…" frame until it
## appears; the renders start when the change passes its checks, so the review is on screen
## before they are done ([method update_media] brings their outcome).

## The shown item changed (several items), so Before/After availability may differ.
signal item_changed
## The side changed from inside the stage (dragging the split line makes it "compare").
signal side_changed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const DETAILS := preload("review_details.gd")
const GLANCE := preload("review_glance.gd")
const SWIPE := preload("review_swipe.gd")
const DIFF := preload("review_diff.gd")
const FILMSTRIP := preload("review_filmstrip.gd")
const CLIP := preload("review_clip.gd")
## Seconds to wait for a preview file before giving up.
const LOAD_TIMEOUT := 120.0
## Seconds between checks for a preview file that is still being written.
const POLL := 0.25
const NO_PREVIEW := "No preview. Open Details to see the file changes."
## Files Image.load reads; anything else (a .kra, a .blend) has no preview of its own.
const IMAGE_FORMATS := ["png", "jpg", "jpeg", "webp", "svg", "bmp", "tga", "exr", "hdr"]
## The picture frame's height as a share of its width, between [constant FRAME_MIN] and
## [constant FRAME_MAX] large control heights.
const FRAME_SHARE := 0.3
const FRAME_MIN := 2.0
const FRAME_MAX := 5.0
## The tallest the frame grows under a row of ways (variant_strip.gd), which takes the rest.
const FRAME_UNDER_WAYS := 3.0

## The side on show: "before", "after", or "compare" (a split picture).
var side := "after"
## The tallest the picture frame grows, in large control heights.
var frame_max := FRAME_MAX:
	set(value):
		frame_max = value
		_size_frame()
var _items: Array = []
var _index := 0
var _textures := {}
var _streams := {}
var _frame: PanelContainer
var _front: View
var _back: View
var _swipe: SWIPE
var _diff: DIFF
var _audio_box: VBoxContainer
var _audio: Control
var _loading: PanelContainer
var _loading_dots: INDICATOR
var _failure: Label
var _strip: FILMSTRIP
var _clip: CLIP
var _waiting := ""
var _waited := 0.0
var _since_poll := 0.0


## One side of a model or picture: the main picture, and the far side of a model beside it.
class View:
	extends HBoxContainer
	var main: TextureRect
	var far: TextureRect

	func _init() -> void:
		theme_type_variation = "JoineryRowTight"
		size_flags_vertical = Control.SIZE_EXPAND_FILL
		# The two angles sit together in the middle, not at the far ends of a wide frame.
		alignment = BoxContainer.ALIGNMENT_CENTER
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		main = _picture()
		far = _picture()
		resized.connect(func() -> void: set_pictures(main.texture, far.texture))
		hide()

	func set_pictures(front: Texture2D, back: Texture2D) -> void:
		main.texture = front
		far.texture = back
		far.visible = back != null
		for picture: TextureRect in [main, far]:
			_fit(picture)
			_sample(picture)

	## Two angles: each picture is as wide as its height allows, so the pair stays together.
	func _fit(picture: TextureRect) -> void:
		var texture := picture.texture
		var paired := far != null and far.visible
		if not paired or texture == null or texture.get_height() == 0:
			picture.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			picture.custom_minimum_size.x = 0.0
			return
		picture.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		var width := size.y * texture.get_width() / texture.get_height()
		width = minf(width, (size.x - get_theme_constant("separation")) / 2.0)
		if not is_equal_approx(picture.custom_minimum_size.x, width):
			picture.custom_minimum_size.x = maxf(0.0, floorf(width))

	func _picture() -> TextureRect:
		var picture := TextureRect.new()
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		picture.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		picture.size_flags_vertical = Control.SIZE_EXPAND_FILL
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		picture.resized.connect(func() -> void: _sample(picture))
		add_child(picture)
		return picture

	## Crisp or smooth for the size the picture is shown at.
	func _sample(picture: TextureRect) -> void:
		picture.texture_filter = GLANCE.picture_filter(picture.texture, picture.size)


func _init() -> void:
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Preview of the proposed change"
	set_process(false)
	_frame = PanelContainer.new()
	_frame.theme_type_variation = "JoineryMediaFrame"
	_frame.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_frame.clip_contents = true
	add_child(_frame)
	_front = View.new()
	_back = View.new()
	_frame.add_child(_front)
	_frame.add_child(_back)
	_swipe = SWIPE.new()
	_swipe.hide()
	_swipe.dragged.connect(_on_dragged)
	_frame.add_child(_swipe)
	_clip = CLIP.new()
	_frame.add_child(_clip)
	_diff = DIFF.new()
	_diff.hide()
	add_child(_diff)
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
	_strip = FILMSTRIP.new()
	_strip.hide()
	_strip.chosen.connect(_on_item_selected)
	add_child(_strip)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		var tall := float(UI_THEME.control_h(self, "lg") * 2)
		for area: Control in [_audio_box, _diff]:
			area.custom_minimum_size.y = tall
		_size_frame()
	elif what == NOTIFICATION_RESIZED:
		_size_frame()


## The picture frame (and the loading frame in its place) grows with the width.
func _size_frame() -> void:
	if not is_inside_tree():
		return
	var large := float(UI_THEME.control_h(self, "lg"))
	var tall := floorf(clampf(size.x * FRAME_SHARE, large * FRAME_MIN, large * frame_max))
	for area: Control in [_frame, _loading]:
		if not is_equal_approx(area.custom_minimum_size.y, tall):
			area.custom_minimum_size.y = tall


## The picture frame's height now.
func frame_height() -> float:
	return _frame.size.y


func _process(delta: float) -> void:
	_waited += delta
	_since_poll += delta
	if _since_poll < POLL:
		return
	_since_poll = 0.0
	if FileAccess.file_exists(_waiting):
		_stop_waiting()
		_show_current(false)
		_refresh_tiles()
		UI_MOTION.fade_in(_frame if _frame.visible else _audio_box)
	elif _waited >= LOAD_TIMEOUT:
		_stop_waiting()
		_fail()


## Show [param media] items ({path, media: "image"|"audio"|"text", before?, after?,
## before_back?, after_back?, diff?}), starting on the first item: a split picture for a
## texture pair, else its After.
func show_media(media: Array) -> void:
	clear_media()
	for item: Variant in media:
		if not item is Dictionary:
			continue
		var entry := item as Dictionary
		if entry.has("before") or entry.has("after") or entry.has("error"):
			_items.append(entry)
		elif str(entry.get("media", "")) == "text" and not str(entry.get("diff", "")).is_empty():
			_items.append(entry)
	visible = not _items.is_empty()
	if _items.is_empty():
		return
	side = "compare" if swipes(_items[0]) else "after"
	_strip.visible = _items.size() > 1
	_strip.show_items(_tile_specs())
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
	_refresh_tiles()


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
	for view: View in [_front, _back]:
		UI_MOTION.stop(view)
		view.set_pictures(null, null)
		view.hide()
	_swipe.set_pictures(null, null)
	_swipe.hide()
	_clip.stop()
	_diff.hide()
	_failure.hide()
	_strip.hide()


## True when there is something to preview.
func has_media() -> bool:
	return not _items.is_empty()


## True when any item has both a Before and an After.
func has_pairs() -> bool:
	return _items.any(func(item: Dictionary) -> bool: return _paired(item))


## True when the shown item has both a Before and an After.
func current_paired() -> bool:
	return not _items.is_empty() and _paired(_items[_index])


## True when the shown item is a new asset (After only).
func current_is_new() -> bool:
	if _items.is_empty():
		return false
	var item: Dictionary = _items[_index]
	return item.has("after") and not item.has("before")


## The kind of the shown item: "image", "model", "audio" or "text" ("" without items).
func current_kind() -> String:
	return kind_of(_items[_index]) if not _items.is_empty() else ""


## How many assets the change shows (text is not one).
func asset_count() -> int:
	var paths: Array[String] = []
	for item: Dictionary in _items:
		var path := str(item.get("path", ""))
		if kind_of(item) != "text" and not path in paths:
			paths.append(path)
	return paths.size()


## Switch to "before", "after" or "compare". A split picture slides its line; a model or a
## single picture crossfades; audio keeps its playback position.
func set_side(value: String) -> void:
	if value == side or not value in ["before", "after", "compare"]:
		return
	if value == "compare" and not (not _items.is_empty() and swipes(_items[_index])):
		value = "after"
	side = value
	if not _items.is_empty():
		_show_current(true)


## Flip between the original and the change (A or B): a comparison goes to the original
## first. Returns the side now on show.
func flip() -> String:
	if _items.is_empty() or not current_paired():
		return side
	set_side("after" if side == "before" else "before")
	return side


## Show the item at [param index] (the filmstrip's order).
func select_item(index: int) -> void:
	_strip.select(index)
	_on_item_selected(index)


## The index of the item on show.
func current_index() -> int:
	return _index


## The filmstrip under the preview.
func filmstrip() -> FILMSTRIP:
	return _strip


## The split picture (texture pairs).
func swipe() -> SWIPE:
	return _swipe


## The changed lines view (text items).
func diff_view() -> DIFF:
	return _diff


## The picture on show (null for audio, text, while loading, or on failure). For a split
## picture: the original when Before is on, else the change.
func texture() -> Texture2D:
	if _swipe.visible:
		return _swipe.before if side == "before" else _swipe.after
	return _front.main.texture if _frame.visible and _front.visible else null


## The far side of a model on show, or null.
func far_texture() -> Texture2D:
	if _frame.visible and _front.visible and _front.far.visible:
		return _front.far.texture
	return null


## The shown file name.
func file_name() -> String:
	return _item_name(_items[_index]) if not _items.is_empty() else ""


## A small picture of the change for the chat card: the first item's After (or its only
## side), null while none is ready.
func thumbnail() -> Texture2D:
	for item: Dictionary in _items:
		var kind := kind_of(item)
		if kind in ["audio", "text"]:
			continue
		var picture := _tile_picture(item)
		if picture != null:
			return picture
	return null


## What the review can measure on its own: images darker, lighter, warmer or resized, sounds
## longer or shorter. Only for items whose two sides are both ready.
func measured_facts() -> Array[String]:
	var found: Array[String] = []
	for item: Dictionary in _items:
		if kind_of(item) == "clip":
			var line := CLIP.clip_fact(
				str(item.get("label", "")), item.get("before_clip", {}), item.get("after_clip", {})
			)
			if (
				not line.is_empty()
				and item.has("after_clip" if item.has("after") else "before_clip")
			):
				found.append(line)
			continue
		if not _paired(item) or item.has("error") or item.has("reference"):
			continue
		var before := str(item["before"])
		var after := str(item["after"])
		if not (FileAccess.file_exists(before) and FileAccess.file_exists(after)):
			continue
		var name := str(item.get("label", ""))
		if name.is_empty():
			name = GLANCE.plain_name(str(item.get("path", "")))
		match kind_of(item):
			"image":
				var old := _texture(before)
				var new := _texture(after)
				if old != null and new != null:
					found.append_array(GLANCE.image_facts(name, old.get_image(), new.get_image()))
			"audio":
				var old_sound := _stream(before)
				var new_sound := _stream(after)
				if old_sound != null and new_sound != null:
					var fact := GLANCE.audio_fact(
						name, old_sound.get_length(), new_sound.get_length()
					)
					if not fact.is_empty():
						found.append(fact)
	return found


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


## "image", "model", "audio", "clip" (an animation) or "text" for a media [param item].
static func kind_of(item: Dictionary) -> String:
	var media := str(item.get("media", "image"))
	if media in ["audio", "text", "clip"]:
		return media
	if item.has("before_back") or item.has("after_back"):
		return "model"
	var extension := str(item.get("path", "")).get_extension().to_lower()
	if extension in DETAILS.KINDS["model"] and not item.has("part"):
		return "model"
	return "image"


## True when [param item] shows both sides at once: a split picture (texture, part view or scene
## snapshot) or two clips. Models have their own framing per side, so they crossfade.
static func swipes(item: Dictionary) -> bool:
	return (
		kind_of(item) in ["image", "clip"]
		and _paired(item)
		and not item.has("error")
		and not item.has("reference")
	)


static func _paired(item: Dictionary) -> bool:
	return item.has("before") and item.has("after")


static func _item_name(item: Dictionary) -> String:
	if kind_of(item) == "text":
		return str(item.get("path", "Text changes"))
	var label := str(item.get("label", ""))
	var file := str(item.get("path", "")).get_file()
	return file if label.is_empty() else "%s · %s" % [label, file]


func _tile_specs() -> Array:
	var specs: Array = []
	for item: Dictionary in _items:
		var spec := {"name": _item_name(item)}
		match kind_of(item):
			"audio":
				spec["glyph"] = "sound"
			"text":
				spec["word"] = str(item.get("word", "Aa"))
			_:
				spec["picture"] = _tile_picture(item)
		specs.append(spec)
	return specs


func _tile_picture(item: Dictionary) -> Texture2D:
	for key: String in ["after", "before"]:
		if item.has(key) and FileAccess.file_exists(str(item[key])):
			if kind_of(item) == "clip":
				return CLIP.middle_frame(_texture(str(item[key])), item.get(key + "_clip", {}))
			return _texture(str(item[key]))
	return null


func _refresh_tiles() -> void:
	if not _strip.visible:
		return
	for index: int in _items.size():
		var item: Dictionary = _items[index]
		if not kind_of(item) in ["audio", "text"]:
			_strip.set_picture(index, _tile_picture(item))


func _on_item_selected(index: int) -> void:
	if index == _index or index < 0 or index >= _items.size():
		return
	stop_audio()
	_index = index
	side = "compare" if swipes(_items[index]) else "after"
	_show_current(false)
	item_changed.emit()


func _on_dragged() -> void:
	if side != "compare":
		side = "compare"
		side_changed.emit()


func _show_current(swap: bool) -> void:
	var item: Dictionary = _items[_index]
	var path := str(item.get("path", ""))
	tooltip_text = "res://" + path if kind_of(item) != "text" else ""
	_failure.hide()
	if kind_of(item) == "text":
		_stop_waiting()
		_hide_stage()
		_diff.show_diff(str(item.get("diff", "")))
		_diff.show()
		return
	_diff.hide()
	if not (item.has("before") or item.has("after")):
		_stop_waiting()
		_fail(str(item.get("error", "")))
		return
	if kind_of(item) == "clip" or swipes(item):
		_show_both(item, swap)
		return
	_clip.stop()
	var wanted := "after" if side == "compare" else side
	if item.has("error") and not item.has(wanted):
		# That side's render failed: say so rather than show the other side as if it were it.
		_stop_waiting()
		_fail(str(item["error"]), wanted)
		return
	var chosen := wanted if item.has(wanted) else ("after" if item.has("after") else "before")
	var source := str(item[chosen])
	if not FileAccess.file_exists(source):
		_wait_for(source)
		return
	_stop_waiting()
	if kind_of(item) == "audio":
		_show_audio(source, swap)
	elif item.has("reference") and FileAccess.file_exists(str(item["reference"])):
		# The sketch stays on the left; the plan beside it is what changes.
		_show_image(str(item["reference"]), source, swap)
	else:
		_show_image(source, str(item.get(chosen + "_back", "")), swap)


## Both sides at once (a split picture, or an animation's clips), once each side it has is
## rendered.
func _show_both(item: Dictionary, swap: bool) -> void:
	for key: String in ["before", "after"]:
		if item.has(key) and not FileAccess.file_exists(str(item[key])):
			_clip.stop()
			_wait_for(str(item[key]))
			return
	_stop_waiting()
	if kind_of(item) != "clip":
		_clip.stop()
		_show_swipe(item, swap)
		return
	_audio_box.hide()
	_swipe.hide()
	for view: View in [_front, _back]:
		view.hide()
	var before := _texture(str(item["before"])) if item.has("before") else null
	var after := _texture(str(item["after"])) if item.has("after") else null
	if before == null and after == null:
		_clip.stop()
		_fail(str(item.get("error", "")))
		return
	_frame.show()
	if not _clip.visible or _clip.get_meta("item", {}) != item:
		_clip.show_clip(item, before, after)
		_clip.set_meta("item", item)
	_clip.set_side(side)


func _hide_stage() -> void:
	_clip.stop()
	_frame.hide()
	_audio_box.hide()
	_loading.hide()


func _show_swipe(item: Dictionary, swap: bool) -> void:
	_audio_box.hide()
	var old := _texture(str(item["before"]))
	var new := _texture(str(item["after"]))
	if old == null or new == null:
		_fail()
		return
	_frame.show()
	_set_ground(Color.TRANSPARENT)
	for view: View in [_front, _back]:
		view.hide()
	var fresh := not _swipe.visible or _swipe.after != new
	_swipe.set_pictures(old, new)
	_swipe.show()
	var target: float = {"before": 1.0, "after": 0.0}.get(side, 0.5)
	if swap and not fresh:
		_swipe.slide_to(target)
	else:
		_swipe.split = target


func _show_image(source: String, far: String, swap: bool) -> void:
	_audio_box.hide()
	_swipe.hide()
	var picture := _texture(source)
	if picture == null:
		_fail()
		return
	var back: Texture2D = null
	if not far.is_empty() and FileAccess.file_exists(far):
		back = _texture(far)
	_frame.show()
	_set_ground(_common_ground(picture, back))
	var changed := _front.main.texture != picture or _front.far.texture != back
	if swap and _front.visible and _front.main.texture != null and changed:
		_back.set_pictures(picture, back)
		UI_MOTION.crossfade(_front, _back)
		var shown := _back
		_back = _front
		_front = shown
		return
	UI_MOTION.stop(_back)
	_back.hide()
	_front.set_pictures(picture, back)
	_front.show()


## The background the shown pictures share (a model's two renders), else clear.
static func _common_ground(picture: Texture2D, back: Texture2D) -> Color:
	var colour := GLANCE.ground(picture)
	if back != null and not GLANCE.ground(back).is_equal_approx(colour):
		return Color.TRANSPARENT
	return colour


## Fill the frame with [param colour] (the pictures' own background); clear: the theme's.
func _set_ground(colour: Color) -> void:
	if colour.a <= 0.0:
		_frame.remove_theme_stylebox_override("panel")
		return
	var box := get_theme_stylebox("panel", "JoineryMediaFrame").duplicate() as StyleBoxFlat
	if box == null:
		return
	box.bg_color = colour
	_frame.add_theme_stylebox_override("panel", box)


## The frame's background now (the pictures' own, or the theme's).
func frame_ground() -> Color:
	var box := _frame.get_theme_stylebox("panel") as StyleBoxFlat
	return box.bg_color if box != null else Color.TRANSPARENT


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
