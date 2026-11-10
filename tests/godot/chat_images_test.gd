extends SceneTree
## Images in the chat: pasted (an image, or image files copied in the file manager), dropped
## (from the file manager or the FileSystem dock) or chosen with the paperclip. Each becomes a
## thumbnail, up to five per message, scaled to 2048 px; nothing else is attached, and a drop
## anywhere but the composer still goes to the editor.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const INPUT_PATH := "res://addons/joinery/message_input.gd"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _file(name: String, width := 64, height := 48) -> String:
	var folder := OS.get_cache_dir().path_join("joinery chat images")
	DirAccess.make_dir_recursive_absolute(folder)
	var path := folder.path_join(name)
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	image.fill(Color("6b4423"))
	match path.get_extension():
		"png":
			image.save_png(path)
		"jpg":
			image.save_jpg(path)
		"webp":
			image.save_webp(path)
		_:
			FileAccess.open(path, FileAccess.WRITE).store_string("not an image")
	return path


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var input_script: GDScript = load(INPUT_PATH)
	var png := _file("door sketch.png")
	var jpg := _file("palette.jpg")
	var webp := _file("mood.webp")
	var text := _file("notes.txt")

	# What a paste means: image files, then an image with no text; everything else is text.
	var url := "file://" + png.uri_encode().replace("%2F", "/")
	_check(input_script.image_paths(url) == PackedStringArray([png]), "A file URL names a file")
	_check(input_script.image_paths(png + "\n" + jpg).size() == 2, "Several copied files")
	_check(input_script.image_paths(png + "\nhello").is_empty(), "Text with a path is text")
	_check(input_script.image_paths(text).is_empty(), "Only image files")
	var files := {"type": "files", "files": ["res://icon.png", "res://notes.md"]}
	_check(input_script.dropped_images(files) == PackedStringArray(["res://icon.png"]), "Drops")

	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "")
	chat.prompt_submitted.connect(func(_text: String, _targets: Array) -> void: pass)
	var composer: Control = chat.get("_composer")
	var row: Control = composer.get("attachments")
	var prompt: TextEdit = composer.get("prompt")
	var caption: Label = chat._transcript.get("_caption_text")

	# Pasting: a screenshot on the clipboard, then copied files; plain text stays text.
	var big := Image.create(3000, 1500, false, Image.FORMAT_RGBA8)
	big.fill(Color("4b5d6e"))
	_check(prompt.call("paste_clipboard", "", big), "An image on the clipboard is attached")
	var pasted: Dictionary = row.call("entries")[0]
	_check(pasted["width"] == 2048 and pasted["height"] == 1024, "Pasted images are scaled down")
	_check(str(pasted["label"]) == "Pasted image", "A pasted image is named so")
	_check(prompt.call("paste_clipboard", png, null), "Copied image files are attached")
	_check(not prompt.call("paste_clipboard", "make it wider", big), "Text pastes as text")
	_check(row.call("count") == 2, "Two images so far")

	# The paperclip's files, drops from the FileSystem dock and the limit of five.
	composer.call("attach_files", PackedStringArray([jpg, text]))
	_check(row.call("count") == 3, "A JPEG is attached")
	_check(caption.text.contains("Only PNG, JPEG and WebP"), "Other files are refused with why")
	var dock_drag := {"type": "files", "files": [webp, text]}
	_check(composer.call("_can_drop_data", Vector2.ZERO, dock_drag), "The composer takes images")
	_check(prompt.call("_can_drop_data", Vector2.ZERO, dock_drag), "So does the message field")
	_check(not prompt.call("_can_drop_data", Vector2.ZERO, {"type": "nodes"}), "Not other drags")
	prompt.call("_drop_data", Vector2.ZERO, dock_drag)
	_check(row.call("count") == 4, "A WebP dropped on the field is attached")
	composer.call("attach_files", PackedStringArray([png, png]))
	_check(row.call("count") == 5, "At most five images")
	_check(caption.text.contains("Up to 5 images"), "The limit is explained")
	var dialog_button: Button = composer.get("attach")
	_check(
		dialog_button.tooltip_text.begins_with("Attach images"), "The paperclip says what it does"
	)
	row.call("clear")

	# Drops from the file manager: over the composer they attach; elsewhere the editor has them.
	var editor_drops: Array = []
	var editor_handler := func(dropped: PackedStringArray) -> void: editor_drops.append(dropped)
	root.files_dropped.connect(editor_handler)
	composer.call("_guard_drops", true)
	_check(not root.files_dropped.is_connected(editor_handler), "The composer sees drops first")
	composer.call("route_drop", PackedStringArray([png]), true)
	_check(row.call("count") == 1 and editor_drops.is_empty(), "A drop on the composer attaches")
	composer.call("route_drop", PackedStringArray([png]), false)
	_check(editor_drops.size() == 1 and row.call("count") == 1, "Other drops reach the editor")
	composer.call("_guard_drops", false)
	_check(root.files_dropped.is_connected(editor_handler), "The editor's handler comes back")
	root.files_dropped.disconnect(editor_handler)

	# The images go with the message and show on its card.
	prompt.text = "Match the door to this sketch"
	prompt.text_submitted.emit(prompt.text)
	await process_frame
	var card: Control = chat._transcript.call("current_card")
	_check(card.get("targets").size() == 1, "The image went with the message")
	_check(row.call("count") == 0, "The composer is empty again")

	chat.queue_free()
	await process_frame
	print("chat images: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
