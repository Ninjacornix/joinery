@tool
extends HBoxContainer
## One side of a model or picture in review (review_media.gd): the main picture, and the far
## side of a model beside it.

const GLANCE := preload("review_glance.gd")

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
