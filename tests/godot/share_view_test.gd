extends SceneTree
## "Share my view" and the agent's view_editor tool: screenshots saved as attachments (scaled,
## out of git), shown as thumbnails, sent with the next message and then cleared; the chat
## bridge sends their paths separately and answers the agent's view requests.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const ATTACHMENTS_PATH := "res://addons/joinery/chat_attachments.gd"

var _failed := false
var _prompts: Array = []


class FakeJob:
	extends RefCounted
	var lines: Array[String] = []

	func send_line(line: String) -> void:
		lines.append(line)

	func take_lines() -> Array[String]:
		return []

	func poll() -> Dictionary:
		return {}

	func close_pipes() -> void:
		pass


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _picture(width: int, height: int) -> Image:
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	image.fill(Color("3a3d42"))
	image.fill_rect(Rect2i(width / 4, height / 4, width / 2, height / 2), Color("c9a14a"))
	return image


func _view() -> Dictionary:
	var attachments: GDScript = load(ATTACHMENTS_PATH)
	var entry: Dictionary = attachments.save(_picture(640, 360), "3D view", "view")
	return {"success": true, "entry": entry, "path": entry["path"], "view": "3D", "camera": {}}


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var attachments: GDScript = load(ATTACHMENTS_PATH)

	# The store: scaled to 2048 px, under .joinery/attachments, out of git.
	var wide: Dictionary = attachments.save(_picture(4096, 1024), "wide.png")
	_check(wide.get("width") == 2048 and wide.get("height") == 512, "Scale to 2048 px")
	var saved := str(wide.get("path", ""))
	_check(saved.begins_with(ProjectSettings.globalize_path("res://.joinery/attachments")), saved)
	_check(FileAccess.file_exists(saved), "The attachment is on disk")
	_check(not saved.contains("/art/"), "Attachments are never saved under art/")
	var ignore := ProjectSettings.globalize_path("res://.joinery/attachments/.gitignore")
	_check(FileAccess.get_file_as_string(ignore) == "*\n", "Attachments stay out of git")
	_check(attachments.is_attachment(wide), "A saved image is an attachment")
	_check(not attachments.is_attachment({"id": "res://door.glb"}), "A target is not")

	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "")
	chat.prompt_submitted.connect(
		func(text: String, targets: Array) -> void: _prompts.append([text, targets])
	)
	var composer: Control = chat.get("_composer")
	var row: Control = composer.get("attachments")
	composer.set("view_provider", _view)

	# Share my view: a thumbnail joins the next message; a new selection keeps it.
	composer.call("share_view")
	await process_frame
	_check(row.call("count") == 1 and row.visible, "Share my view adds a thumbnail")
	_check(composer.get("view").tooltip_text.begins_with("Share my view"), "Its tooltip says so")
	chat.set_resource_target("res://art/props/door.glb", "PackedScene")
	var snapshot: Array = chat.target_snapshot()
	_check(snapshot.size() == 2, "A new selection keeps the shared view")
	_check(attachments.is_attachment(snapshot[1]), "The view goes after the targets")

	chat._prompt.text = "Does the door look too dark?"
	chat._prompt.text_submitted.emit(chat._prompt.text)
	await process_frame
	_check(_prompts.size() == 1, "The message is sent")
	var sent: Array = _prompts[0][1]
	_check(sent.size() == 2 and attachments.is_attachment(sent[1]), "The view goes with it")
	_check(row.call("count") == 0, "The composer lets go of the view once it is sent")
	var card: Control = chat._transcript.call("current_card")
	var strip := card.find_children("", "TextureRect", true, false)
	_check(
		strip.any(func(node: Node) -> bool: return (node as TextureRect).texture != null),
		"The request shows the shared view"
	)

	# A failed capture says why; the composer holds at most five images.
	composer.set("view_provider", func() -> Dictionary: return {"success": false, "message": "No"})
	composer.call("share_view")
	var caption: Label = chat._transcript.get("_caption_text")
	_check(caption.text == "No", "A failed capture says why")
	composer.set("view_provider", _view)
	for index: int in 6:
		composer.call("share_view")
	_check(row.call("count") == 5, "At most five images go with a message")
	row.call("remove", str(row.call("entries")[0]["id"]))
	_check(row.call("count") == 4, "× removes an image at once")
	row.call("clear")

	# The bridge sends the images' paths apart from the targets.
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	root.add_child(backend)
	var job := FakeJob.new()
	backend.set("_job", job)
	backend.set("_connected", true)
	backend.call("submit", "Look", [{"id": "res://door.glb"}, wide])
	var line: Dictionary = JSON.parse_string(job.lines[-1])
	_check(line["targets"] == [{"id": "res://door.glb"}], "Targets go without the images")
	_check(line["attachments"] == [saved], "The image paths go as attachments")
	backend.call("submit", "Plain", [])
	_check(not JSON.parse_string(job.lines[-1]).has("attachments"), "No images, no attachments")

	# The agent asks to look (view_editor): the bridge captures and answers.
	var steps: Array[String] = []
	backend.status_changed.connect(func(_s: String, detail: String) -> void: steps.append(detail))
	backend.set("view_provider", _view)
	backend.call("_handle", {"kind": "editor_view", "request_id": "r1"})
	var answer: Dictionary = JSON.parse_string(job.lines[-1])
	_check(answer["kind"] == "editor_view_result" and answer["request_id"] == "r1", "Answered")
	_check(answer["result"]["success"] and answer["result"]["view"] == "3D", "With the view")
	_check(not answer["result"].has("entry"), "Only the result goes back")
	_check(steps.has("Looking at your view"), "The request shows that Journeyman looked")

	backend.queue_free()
	chat.queue_free()
	await process_frame
	print("share view: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
