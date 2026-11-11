@tool
extends HBoxContainer
## An animation in review: the original and the change as two short looping clips side by side,
## played in sync from the strips Blender rendered in the worker (one PNG per side, N square
## frames left to right). Choosing Before or After dims the other clip instead of hiding it, so
## the comparison stays in view. A new action shows its clip alone, marked New.

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
## Opacity of the clip that is not chosen.
const DIMMED := 0.35
## Changes smaller than this share of the original are not worth a fact.
const NOTICE := 0.05

## Seconds since the clips started; both sides read their frame from it, so they stay in sync.
var clock := 0.0
var _sides: Array[ClipSide] = []


## One clip: the picture stepping through its strip, and its caption.
class ClipSide:
	extends VBoxContainer
	var picture: TextureRect
	var caption: Label
	var strip: Texture2D
	var frames := 1
	var fps := 12.0
	var frame := 0

	func _init(word: String) -> void:
		theme_type_variation = "JoineryTight"
		size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		size_flags_vertical = Control.SIZE_EXPAND_FILL
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		picture = TextureRect.new()
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		picture.size_flags_vertical = Control.SIZE_EXPAND_FILL
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		picture.texture = AtlasTexture.new()
		add_child(picture)
		caption = UI.label(word, "JoineryCaption")
		caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		add_child(caption)

	func set_strip(texture: Texture2D, meta: Dictionary) -> void:
		strip = texture
		frames = frame_count(texture, meta)
		var seconds := float(meta.get("seconds", 0.0))
		fps = float(meta.get("fps", 0.0))
		if fps <= 0.0:
			fps = frames / seconds if seconds > 0.0 else 12.0
		(picture.texture as AtlasTexture).atlas = texture
		show_frame(0)

	## Frames in a strip: the clip's own count, else as many squares as fit across.
	static func frame_count(texture: Texture2D, meta: Dictionary) -> int:
		var count := int(meta.get("frames", 0))
		if count <= 0 and texture != null and texture.get_height() > 0:
			count = roundi(float(texture.get_width()) / texture.get_height())
		return maxi(count, 1)

	static func region(texture: Texture2D, index: int, count: int) -> Rect2:
		if texture == null:
			return Rect2()
		var width := float(texture.get_width()) / count
		return Rect2(width * index, 0, width, texture.get_height())

	func show_frame(index: int) -> void:
		frame = posmod(index, frames)
		(picture.texture as AtlasTexture).region = region(strip, frame, frames)


func _init() -> void:
	theme_type_variation = "JoineryRow"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	alignment = BoxContainer.ALIGNMENT_CENTER
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	accessibility_name = "Animation before and after"
	for word: String in ["Before", "After"]:
		var side := ClipSide.new(word)
		_sides.append(side)
		add_child(side)
	hide()
	set_process(false)
	resized.connect(_fit)


## Each clip as a square as tall as the frame allows; the two sit together in the middle.
func _fit() -> void:
	for side_view: ClipSide in _sides:
		var room := size.y - side_view.caption.get_combined_minimum_size().y
		room -= side_view.get_theme_constant("separation")
		var width := (size.x - get_theme_constant("separation")) / 2.0
		var square := floorf(maxf(minf(room, width), 0.0))
		if not is_equal_approx(side_view.picture.custom_minimum_size.x, square):
			side_view.picture.custom_minimum_size.x = square


## Play [param item]'s clips: [param before] and [param after] are its strips (null for a
## side it lacks).
func show_clip(item: Dictionary, before: Texture2D, after: Texture2D) -> void:
	var textures := [before, after]
	var keys := ["before", "after"]
	for index: int in 2:
		var side := _sides[index]
		side.visible = textures[index] != null
		if side.visible:
			side.set_strip(textures[index], item.get(keys[index] + "_clip", {}))
	_sides[1].caption.text = "After" if before != null else "New"
	_sides[0].caption.text = "Before" if after != null else "Removed"
	clock = 0.0
	show()
	set_process(true)


## Emphasise "before" or "after" (the other clip's picture dims); "compare" shows both alike.
func set_side(value: String) -> void:
	_sides[0].picture.modulate.a = DIMMED if value == "after" and _sides[1].visible else 1.0
	_sides[1].picture.modulate.a = DIMMED if value == "before" and _sides[0].visible else 1.0


func stop() -> void:
	hide()
	set_process(false)


## The frame each visible side shows.
func frames_shown() -> Array[int]:
	var shown: Array[int] = []
	for side: ClipSide in _sides:
		shown.append(side.frame if side.visible else -1)
	return shown


func side_at(index: int) -> ClipSide:
	return _sides[index]


func _process(delta: float) -> void:
	clock += delta
	for side_view: ClipSide in _sides:
		if side_view.visible:
			side_view.show_frame(floori(clock * side_view.fps))


## The middle frame of a strip, for the filmstrip tile and the chat card.
static func middle_frame(strip: Texture2D, meta: Dictionary) -> Texture2D:
	if strip == null:
		return null
	var frames := ClipSide.frame_count(strip, meta)
	var picture := AtlasTexture.new()
	picture.atlas = strip
	picture.region = ClipSide.region(strip, frames / 2, frames)
	return picture


## One glanceable line: "Run: 0.8 s loop, bounce +30%", "Run: new, 0.8 s loop",
## "Run: 0.8 s loop (was 1.0 s)", "Run: removed".
static func clip_fact(name: String, before: Dictionary, after: Dictionary) -> String:
	var title := name.replace("_", " ").capitalize()
	if after.is_empty():
		return "%s: removed" % title if not before.is_empty() else ""
	var seconds := float(after.get("seconds", 0.0))
	var length := "%.1f s%s" % [seconds, " loop" if bool(after.get("loop", false)) else ""]
	if before.is_empty():
		return "%s: new, %s" % [title, length]
	var parts := PackedStringArray([length])
	var was := float(before.get("seconds", 0.0))
	if was > 0.0 and absf(seconds - was) / was >= NOTICE:
		parts[0] += " (was %.1f s)" % was
	var bounce := float(before.get("bounce", 0.0))
	if bounce > 0.0:
		var change := (float(after.get("bounce", 0.0)) - bounce) / bounce
		if absf(change) >= NOTICE:
			parts.append("bounce %+d%%" % roundi(change * 100.0))
	return "%s: %s" % [title, ", ".join(parts)]
