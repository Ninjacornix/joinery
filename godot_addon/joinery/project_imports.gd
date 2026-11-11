@tool
extends VBoxContainer
## "References and design documents" in Project apps: the user's own pictures and design
## documents, added to a project that already exists.
##
## Add references… and Add design documents… pick files on disk. Each picture shows its
## thumbnail and a one-line "what to take from it"; each document its name and kind; a × takes
## a file off the list. `joinery references add --plan --json` says what the change would do
## before anything moves; Add to project runs it (`--json --yes`), one recorded change that the
## Timeline can revert. Only the picked files are copied. Every command is a command_job.gd job.

signal applied(message: String)

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR := preload("activity_indicator.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI_THEME := preload("ui_theme.gd")
const IMAGES := ["png", "jpg", "jpeg", "webp"]
const DOCUMENTS := ["md", "markdown", "txt", "pdf"]
const MAX_IMAGES := 20
const MAX_DOCUMENTS := 10
const CAPTION := (
	"Your own pictures go on the art bible's reference board, and design documents sit beside "
	+ "the game brief for Journeyman to read. Only the files you pick are copied."
)

## The Joinery CLI and its leading arguments (see project_setup.gd); empty: `uv run … joinery`.
var cli_prefix := PackedStringArray()
## The project folder the commands act on.
var project := ""

var card: PanelContainer
var rows: VBoxContainer
var notes: VBoxContainer
var problem: Label
var working: HBoxContainer
var add_button: Button
var cancel_button: Button
var picker: FileDialog
var _add_images: Button
var _add_documents: Button
var _picking := ""
var _images: Array[String] = []
var _documents: Array[String] = []
var _note_fields: Dictionary = {}
var _plan_job: RefCounted
var _apply_job: RefCounted


func _init() -> void:
	name = "References"
	theme_type_variation = "JoineryTight"
	add_child(UI_COMPONENTS.label("References and design documents", "JoinerySection"))
	add_child(UI_COMPONENTS.label(CAPTION, "JoineryCaption", true))
	var actions := HBoxContainer.new()
	actions.theme_type_variation = "JoineryRow"
	add_child(actions)
	_add_images = UI_COMPONENTS.secondary("Add references…")
	_add_images.pressed.connect(_pick.bind("images"))
	actions.add_child(_add_images)
	_add_documents = UI_COMPONENTS.secondary("Add design documents…")
	_add_documents.pressed.connect(_pick.bind("documents"))
	actions.add_child(_add_documents)
	_build_card()
	picker = FileDialog.new()
	picker.access = FileDialog.ACCESS_FILESYSTEM
	picker.file_mode = FileDialog.FILE_MODE_OPEN_FILES
	picker.files_selected.connect(func(paths: PackedStringArray) -> void: add_files(paths))
	add_child(picker)


## Set the CLI and project, call [param on_applied] with what changed; returns this section.
func setup(prefix: PackedStringArray, folder: String, on_applied: Callable) -> Node:
	cli_prefix = prefix
	project = folder
	applied.connect(on_applied)
	return self


func _build_card() -> void:
	card = UI_COMPONENTS.card()
	card.name = "ImportPlan"
	card.hide()
	add_child(card)
	var stack := VBoxContainer.new()
	stack.theme_type_variation = "JoineryTight"
	card.add_child(stack)
	rows = VBoxContainer.new()
	rows.theme_type_variation = "JoineryTight"
	stack.add_child(rows)
	working = HBoxContainer.new()
	working.theme_type_variation = "JoineryRow"
	var spinner := INDICATOR.new()
	spinner.set("running", true)
	working.add_child(spinner)
	var text := UI_COMPONENTS.label("Working out what changes…", "JoineryCaption")
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	working.add_child(text)
	stack.add_child(working)
	notes = VBoxContainer.new()
	notes.theme_type_variation = "JoineryFlush"
	stack.add_child(notes)
	problem = UI_COMPONENTS.label("", "JoineryError", true)
	problem.hide()
	stack.add_child(problem)
	var buttons := HBoxContainer.new()
	buttons.theme_type_variation = "JoineryRow"
	buttons.alignment = BoxContainer.ALIGNMENT_END
	stack.add_child(buttons)
	cancel_button = UI_COMPONENTS.quiet("Cancel")
	cancel_button.pressed.connect(clear)
	buttons.add_child(cancel_button)
	add_button = UI_COMPONENTS.secondary("Add to project")
	add_button.disabled = true
	add_button.pressed.connect(_apply)
	buttons.add_child(add_button)


func _pick(kind: String) -> void:
	_picking = kind
	picker.clear_filters()
	if kind == "images":
		picker.title = "Add reference images"
		picker.add_filter("*.png, *.jpg, *.jpeg, *.webp", "Pictures")
	else:
		picker.title = "Add design documents"
		picker.add_filter("*.md, *.markdown, *.txt, *.pdf", "Markdown, text or PDF")
	picker.popup_centered_ratio(0.6)


## Add picked or dropped files: pictures to the references, the rest to the documents, each
## checked by name first (the CLI checks the bytes). Says plainly why a file can't be added.
func add_files(paths: PackedStringArray) -> void:
	var why := ""
	for raw: String in paths:
		var path := raw.simplify_path()
		var file := path.get_file()
		var extension := path.get_extension().to_lower()
		if path in _images or path in _documents:
			continue
		if DirAccess.dir_exists_absolute(path):
			why = "%s is a folder. Pick the files inside it instead." % file
		elif extension in IMAGES and _picking != "documents":
			if _images.size() >= MAX_IMAGES:
				why = "Add up to %d references at a time." % MAX_IMAGES
				continue
			var image := Image.load_from_file(path)
			if image == null or image.is_empty():
				why = "%s doesn't hold a picture Joinery can read." % file
				continue
			_images.append(path)
			rows.add_child(_picture_row(path, image))
		elif extension in DOCUMENTS and _picking != "images":
			if _documents.size() >= MAX_DOCUMENTS:
				why = "Add up to %d design documents at a time." % MAX_DOCUMENTS
				continue
			_documents.append(path)
			rows.add_child(_document_row(path))
		elif _picking == "documents" or extension in IMAGES:
			why = "%s can't be a design document. Use Markdown (.md), text (.txt) or PDF." % file
		else:
			why = "%s isn't a picture. References can be PNG, JPEG or WebP images." % file
	_picking = ""
	if not card.visible and not (_images.is_empty() and _documents.is_empty()):
		UI_MOTION.reveal(card)
		card.show()
	_plan(why)


func _picture_row(path: String, image: Image) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.set_meta("path", path)
	var side := 48
	var scale := minf(1.0, float(side * 2) / maxi(1, maxi(image.get_width(), image.get_height())))
	if scale < 1.0:
		image.resize(
			maxi(1, int(image.get_width() * scale)), maxi(1, int(image.get_height() * scale))
		)
	var thumbnail := TextureRect.new()
	thumbnail.texture = ImageTexture.create_from_image(image)
	thumbnail.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	thumbnail.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	thumbnail.clip_contents = true
	thumbnail.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	thumbnail.theme_changed.connect(
		func() -> void:
			var px := float(UI_THEME.control_h(thumbnail, "lg"))
			thumbnail.custom_minimum_size = Vector2(px, px)
	)
	row.add_child(thumbnail)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(words)
	words.add_child(UI_COMPONENTS.label(path.get_file()))
	var note := LineEdit.new()
	note.placeholder_text = "What to take from it (optional)"
	note.accessibility_name = "What to take from " + path.get_file()
	note.max_length = 200
	note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI_COMPONENTS.bind_height(note, "md", false)
	words.add_child(note)
	_note_fields[path] = note
	row.add_child(_remove_button(path))
	return row


func _document_row(path: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.set_meta("path", path)
	var title := UI_COMPONENTS.label(path.get_file())
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	var extension := path.get_extension().to_lower()
	var kind := "PDF" if extension == "pdf" else ("Text" if extension == "txt" else "Markdown")
	row.add_child(UI_COMPONENTS.label(kind, "JoineryCaption"))
	row.add_child(_remove_button(path))
	return row


func _remove_button(path: String) -> Button:
	var remove := UI_COMPONENTS.quiet("×")
	remove.tooltip_text = "Remove " + path.get_file()
	remove.accessibility_name = remove.tooltip_text
	remove.pressed.connect(remove_file.bind(path))
	return remove


## Take one file off the list; with none left the card closes.
func remove_file(path: String) -> void:
	_images.erase(path)
	_documents.erase(path)
	_note_fields.erase(path)
	for row: Node in rows.get_children():
		if str(row.get_meta("path", "")) == path:
			rows.remove_child(row)
			row.queue_free()
	if _images.is_empty() and _documents.is_empty():
		clear()
		return
	_plan("")


## Close the card and forget the picked files; nothing in the project changed.
func clear() -> void:
	if _plan_job != null:
		_plan_job.call("cancel")
		_plan_job = null
	_images.clear()
	_documents.clear()
	_note_fields.clear()
	for row: Node in rows.get_children():
		rows.remove_child(row)
		row.queue_free()
	_clear_notes()
	problem.hide()
	card.hide()
	_update_buttons()


func _clear_notes() -> void:
	for child: Node in notes.get_children():
		notes.remove_child(child)
		child.queue_free()


func _update_buttons() -> void:
	_add_images.disabled = _apply_job != null or _images.size() >= MAX_IMAGES
	_add_documents.disabled = _apply_job != null or _documents.size() >= MAX_DOCUMENTS


## Ask what adding the listed files would change; [param why] is a refusal to keep showing.
func _plan(why: String) -> void:
	_update_buttons()
	problem.text = why
	problem.visible = not why.is_empty()
	_clear_notes()
	add_button.disabled = true
	if _images.is_empty() and _documents.is_empty():
		return
	if _plan_job != null:
		_plan_job.call("cancel")
	working.show()
	_plan_job = _start(_arguments(false) + ["--plan", "--json"])
	if _plan_job == null:
		_failed("Joinery's command line isn't available")


func _arguments(with_notes: bool) -> Array:
	var arguments: Array = ["references", "add", "--project", project]
	for path: String in _images:
		var note := (_note_fields[path] as LineEdit).text.strip_edges() if with_notes else ""
		arguments += ["--reference", path + ("=" + note if not note.is_empty() else "")]
	for path: String in _documents:
		arguments += ["--design-doc", path]
	return arguments


func _process(_delta: float) -> void:
	_plan_job = COMMAND_SCRIPT.finish(_plan_job, _plan_done)
	_apply_job = COMMAND_SCRIPT.finish(_apply_job, _apply_done)


func _exit_tree() -> void:
	if _plan_job != null:
		_plan_job.call("cancel")
		_plan_job = null


func _plan_done(result: Dictionary) -> void:
	working.hide()
	var data: Variant = _json(result)
	if not data is Dictionary or (data as Dictionary).has("error"):
		_failed(_reason(result, data))
		return
	for note: Variant in data.get("notes", []):
		notes.add_child(UI_COMPONENTS.label(str(note), "JoineryCaption", true))
	UI_MOTION.stagger(notes.get_children())
	add_button.disabled = false


func _failed(reason: String) -> void:
	working.hide()
	problem.text = reason if not reason.is_empty() else "Joinery couldn't check these files."
	problem.show()
	add_button.disabled = true


func _apply() -> void:
	if _apply_job != null or add_button.disabled:
		return
	_apply_job = _start(_arguments(true) + ["--json", "--yes"])
	if _apply_job == null:
		_failed("Joinery's command line isn't available")
		return
	add_button.disabled = true
	cancel_button.disabled = true
	add_button.text = "Adding…"
	_update_buttons()


func _apply_done(result: Dictionary) -> void:
	add_button.text = "Add to project"
	cancel_button.disabled = false
	var data: Variant = _json(result)
	if int(result.get("code", 1)) != 0 or not data is Dictionary or data.has("error"):
		var why := _reason(result, data)
		_update_buttons()
		_failed("Couldn't add them: " + (why if not why.is_empty() else "no reason given"))
		add_button.disabled = false
		return
	var said: Array[String] = []
	for step: Variant in data.get("steps", []):
		if step is Dictionary:
			said.append(str(step.get("message", "")))
	clear()
	applied.emit(" ".join(said) if not said.is_empty() else "Added.")


func _start(arguments: Array) -> RefCounted:
	return COMMAND_SCRIPT.joinery(cli_prefix, arguments)


static func _json(result: Dictionary) -> Variant:
	var json := JSON.new()
	return json.data if json.parse(str(result.get("output", ""))) == OK else null


## Why a command failed: its JSON error, else the first useful line it printed.
static func _reason(result: Dictionary, data: Variant) -> String:
	if data is Dictionary and not str((data as Dictionary).get("error", "")).is_empty():
		return str(data["error"])
	var text := str(result.get("error", "")) + "\n" + str(result.get("output", ""))
	for raw: String in text.split("\n", false):
		var line := raw.strip_edges()
		if not line.is_empty() and not line.begins_with("Traceback"):
			return line.trim_prefix("references:").strip_edges().left(160)
	return ""
