@tool
extends RefCounted
## Images that go with a Journeyman message: a shared view of the editor, a pasted image, a
## dropped or chosen file. They are reference context for the model only, never assets: each one
## is saved under <project>/.joinery/attachments/ (kept out of git and out of Godot's import),
## never under art/, and the chat host accepts nothing from anywhere else.
##
## An attachment travels with the message as one more entry in its targets
## ({attachment: "image", id, path, label, width, height}), so queued, retried and edited
## messages keep their images; the chat bridge sends the paths separately.

## The folder under the project, the same as joinery/chat_attachments.py.
const FOLDER := ".joinery/attachments"
## Images per message; the chat host checks the same limit.
const MAX_IMAGES := 5
## The long side is scaled down to this many pixels.
const MAX_SIDE := 2048
## A PNG larger than this is saved as JPEG instead, so every provider takes it inline.
const PNG_BUDGET := 3_500_000
## Files larger than this are not opened at all.
const MAX_FILE_BYTES := 40_000_000
const EXTENSIONS: Array[String] = ["png", "jpg", "jpeg", "webp"]


## True when [param target] is an attachment rather than a scene or file target.
static func is_attachment(target: Variant) -> bool:
	return target is Dictionary and str(target.get("attachment", "")) == "image"


## The absolute folder attachments are saved in, made on first use and kept out of git.
static func folder() -> String:
	var path := ProjectSettings.globalize_path("res://" + FOLDER)
	if not DirAccess.dir_exists_absolute(path):
		DirAccess.make_dir_recursive_absolute(path)
	for marker: String in [".gitignore", ".gdignore"]:
		var file_path := path.path_join(marker)
		if not FileAccess.file_exists(file_path):
			var file := FileAccess.open(file_path, FileAccess.WRITE)
			if file != null:
				file.store_string("*\n" if marker == ".gitignore" else "")
	return path


## Save [param image] as an attachment named after [param label] and return its entry, or {}
## when it could not be saved. Images are scaled to [constant MAX_SIDE] on the long side; a large
## PNG becomes a JPEG. [param prefix] starts the file name ("view", "image").
static func save(image: Image, label: String, prefix := "image") -> Dictionary:
	if image == null or image.is_empty():
		return {}
	var copy := image.duplicate() as Image
	if copy.is_compressed() and copy.decompress() != OK:
		return {}
	if copy.get_format() != Image.FORMAT_RGBA8 and copy.get_format() != Image.FORMAT_RGB8:
		copy.convert(Image.FORMAT_RGBA8)
	var longest := maxi(copy.get_width(), copy.get_height())
	if longest > MAX_SIDE:
		var factor := float(MAX_SIDE) / longest
		copy.resize(
			maxi(1, roundi(copy.get_width() * factor)),
			maxi(1, roundi(copy.get_height() * factor)),
			Image.INTERPOLATE_LANCZOS
		)
	var stem := "%s-%d-%04d" % [prefix, int(Time.get_unix_time_from_system()), randi() % 10000]
	var path := folder().path_join(stem + ".png")
	var data := copy.save_png_to_buffer()
	if data.size() > PNG_BUDGET:
		if copy.get_format() == Image.FORMAT_RGBA8:
			copy.convert(Image.FORMAT_RGB8)
		data = copy.save_jpg_to_buffer(0.9)
		path = path.get_basename() + ".jpg"
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null or data.is_empty():
		return {}
	file.store_buffer(data)
	file.close()
	return {
		"attachment": "image",
		"id": "attachment:" + stem,
		"path": path,
		"label": label if not label.is_empty() else stem,
		"width": copy.get_width(),
		"height": copy.get_height(),
	}


## Attach the image file at [param path] (PNG, JPEG or WebP). Returns its entry, or
## {"error": message} when the file is not an image Joinery can attach.
static func from_file(path: String) -> Dictionary:
	if not path.get_extension().to_lower() in EXTENSIONS:
		return {"error": "Only PNG, JPEG and WebP images can be attached."}
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {"error": "Couldn't open %s." % path.get_file()}
	var size := file.get_length()
	file.close()
	if size > MAX_FILE_BYTES:
		return {"error": "%s is too large to attach." % path.get_file()}
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return {"error": "%s isn't an image Joinery can open." % path.get_file()}
	var entry := save(image, path.get_file())
	return entry if not entry.is_empty() else {"error": "Couldn't save the attachment."}


## A thumbnail texture for [param entry], or null when its file is gone.
static func thumbnail(entry: Dictionary, side: int) -> Texture2D:
	var image := Image.load_from_file(str(entry.get("path", "")))
	if image == null or image.is_empty():
		return null
	var longest := maxi(image.get_width(), image.get_height())
	if longest > side * 2:
		var factor := float(side * 2) / longest
		image.resize(
			maxi(1, roundi(image.get_width() * factor)),
			maxi(1, roundi(image.get_height() * factor)),
			Image.INTERPOLATE_BILINEAR
		)
	return ImageTexture.create_from_image(image)
