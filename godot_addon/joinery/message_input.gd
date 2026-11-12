@tool
extends TextEdit
## Multiline chat input: Enter sends, Shift+Enter inserts a line break. It sits inside the
## composer box, so its own styleboxes are empty apart from a 2u left inset that puts the caret
## just before the placeholder. It grows with its text from two lines up to MAX_LINES, then
## scrolls, and shrinks back when the text is cleared or replaced (sending clears it).
##
## Pasting (Cmd/Ctrl+V) an image, or image files copied in the file manager, attaches them
## instead of inserting text: [signal image_pasted] or [signal files_pasted].
##
## Up and Down at the first and last rows go through the messages sent in this chat
## ([member recall], composer_recall.gd).

signal text_submitted(value: String)
## An image was pasted from the clipboard.
signal image_pasted(image: Image)
## Image files were pasted (copied in the file manager): their absolute paths.
signal files_pasted(paths: PackedStringArray)

const UI_THEME := preload("ui_theme.gd")
const RECALL := preload("composer_recall.gd")
const IMAGE_EXTENSIONS: Array[String] = ["png", "jpg", "jpeg", "webp"]
const MIN_LINES := 2
const MAX_LINES := 5

var caret_column: int:
	get:
		return get_caret_column()
	set(value):
		set_caret_column(value)

## The sent-message history Up and Down go through.
var recall := RECALL.new(self)
var _restyling := false


func _ready() -> void:
	wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	scroll_fit_content_height = false
	text_changed.connect(_fit)
	# text_changed fires only for typing; clear() and setting text fire text_set.
	text_set.connect(_fit)
	_restyle()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and is_inside_tree():
		_restyle()
	elif what == NOTIFICATION_RESIZED:
		_fit.call_deferred()


func _gui_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_paste", false, true) and editable:
		var image: Image = null
		if DisplayServer.clipboard_has_image():
			image = DisplayServer.clipboard_get_image()
		if paste_clipboard(DisplayServer.clipboard_get(), image):
			accept_event()
			return
	# After the gui_input signal: a menu open over the field has had its arrow keys first.
	if recall.handle(event):
		accept_event()
		return
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode in [KEY_ENTER, KEY_KP_ENTER]:
			if event.shift_pressed:
				insert_text_at_caret("\n")
			else:
				text_submitted.emit(text)
			accept_event()


## Handle a paste of clipboard [param clip_text] and [param image] (null when none). Image
## files take precedence, then an image with no text beside it; anything else pastes as text
## (returns false).
func paste_clipboard(clip_text: String, image: Image) -> bool:
	var paths := image_paths(clip_text)
	if not paths.is_empty():
		files_pasted.emit(paths)
		return true
	if clip_text.strip_edges().is_empty() and image != null and not image.is_empty():
		image_pasted.emit(image)
		return true
	return false


## Image files dragged from the FileSystem dock onto the field attach like pasted ones; text
## drops stay TextEdit's own.
func _can_drop_data(_at: Vector2, data: Variant) -> bool:
	return not dropped_images(data).is_empty()


func _drop_data(_at: Vector2, data: Variant) -> void:
	files_pasted.emit(dropped_images(data))


## The image files in FileSystem dock drag [param data] ({type: "files", files}).
static func dropped_images(data: Variant) -> PackedStringArray:
	var images := PackedStringArray()
	if data is Dictionary and data.get("type") == "files":
		for path: Variant in data.get("files", []):
			if str(path).get_extension().to_lower() in IMAGE_EXTENSIONS:
				images.append(str(path))
	return images


## The image files [param clip_text] names, one per line (paths or file:// URLs), or none when
## any line is something else.
static func image_paths(clip_text: String) -> PackedStringArray:
	var paths := PackedStringArray()
	for raw: String in clip_text.strip_edges().split("\n", false):
		var line := raw.strip_edges()
		if line.begins_with("file://"):
			line = line.trim_prefix("file://").uri_decode()
		if (
			not line.is_absolute_path()
			or not line.get_extension().to_lower() in IMAGE_EXTENSIONS
			or not FileAccess.file_exists(line)
		):
			return PackedStringArray()
		paths.append(line)
	return paths


func _restyle() -> void:
	if _restyling:
		return
	_restyling = true
	var inset := 2.0 * UI_THEME.unit(self)
	for state: String in ["normal", "focus", "read_only"]:
		var box := StyleBoxEmpty.new()
		box.content_margin_left = inset
		box.content_margin_right = inset
		add_theme_stylebox_override(state, box)
	_restyling = false
	_fit()


func _fit() -> void:
	if not is_inside_tree():
		return
	var lines := clampi(get_visible_line_count_in_range(0, maxi(0, get_line_count() - 1)), 1, 999)
	var shown := clampi(lines, MIN_LINES, MAX_LINES)
	var height := get_line_height() * shown
	if not is_equal_approx(custom_minimum_size.y, height):
		custom_minimum_size.y = height
