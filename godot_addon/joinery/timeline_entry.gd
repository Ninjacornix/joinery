@tool
extends VBoxContainer
## One Timeline entry, opened from the list (timeline_tab.gd): what it was, who made it, when,
## and its files. Each file opens in place to show what changed: coloured lines for scenes and
## scripts, before and after pictures for images, sizes for models and other binaries. Under
## it, Open file and Restore this file as it was. Restoring stages an ordinary reviewed change
## (it waits in the Review tab); the timeline itself is never rewritten.
##
## Data arrives from `joinery timeline show` through [method show_entry] (the file list) and
## [method apply_file] (one file's detail, read when it is first opened).

## Back to the list.
signal back
## A file row opened for the first time: read its detail.
signal file_requested(commit: String, path: String)
## "Restore this file as it was".
signal restore_requested(commit: String, path: String)
## "Open file".
signal open_requested(path: String)
## "Review" after a restore: show the change waiting in the Review tab.
signal review_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const CHANGE_WORDS := {
	"created": "Created", "modified": "Changed", "deleted": "Deleted", "renamed": "Renamed"
}
## What a binary file is, by extension, for "Model changed · 1.2 MB → 1.4 MB".
const BINARY_NOUNS := {
	"Model": ["glb", "gltf", "blend", "fbx", "obj", "res", "scn"],
	"Sound": ["wav", "ogg", "mp3", "flac"],
	"Painting": ["kra", "aseprite", "ase", "psd", "exr", "hdr"],
	"Font": ["ttf", "otf", "woff", "woff2"],
}

## The entry on show (a `timeline show` result).
var entry: Dictionary = {}
var _title: Label
var _meta: Label
var _files: VBoxContainer
var _loading: HBoxContainer
var _rows: Dictionary = {}


func _init() -> void:
	name = "TimelineEntry"
	theme_type_variation = "JoineryTight"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var back_button := UI.quiet("Timeline")
	back_button.name = "Back"
	back_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	back_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back_button.tooltip_text = "Back to every entry"
	UI.bind_icon(back_button, "back")
	back_button.pressed.connect(func() -> void: back.emit())
	add_child(back_button)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	UI_MOTION.rubber_band(scroll)
	add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(column)
	# Inset like the rows, so the title lines up with the Back arrow and the file chevrons.
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	column.add_child(inset)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryFlush"
	inset.add_child(head)
	_title = UI.label("", "JoinerySection", true)
	_title.name = "Title"
	head.add_child(_title)
	_meta = UI.label("", "JoineryNumeric")
	_meta.name = "Meta"
	head.add_child(_meta)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	_loading.add_child(dots)
	_loading.add_child(UI.label("Reading this entry…", "JoineryCaption"))
	_loading.hide()
	column.add_child(_loading)
	_files = VBoxContainer.new()
	_files.name = "Files"
	_files.theme_type_variation = "JoineryTight"
	column.add_child(_files)


## Show the entry [param summary] ({commit, subject, author, time, files}) while it is read.
func set_loading(summary: Dictionary) -> void:
	entry = summary.duplicate()
	_title.text = str(summary.get("subject", ""))
	_meta.text = meta_text(summary)
	_clear()
	_loading.show()


## Show an entry as `joinery timeline show` prints it.
func show_entry(data: Dictionary) -> void:
	entry = data
	_loading.hide()
	_title.text = str(data.get("subject", "")).strip_edges()
	if _title.text.is_empty():
		_title.text = "Project changes"
	_meta.text = meta_text(data)
	_clear()
	var files: Array = data.get("files", [])
	for file: Variant in files:
		if file is Dictionary:
			_add_row(file)
	if files.is_empty():
		_files.add_child(UI.label("This entry changed no files.", "JoineryCaption"))
	UI_MOTION.stagger(_files.get_children())
	# One file: open it at once, there is nothing else to choose.
	if _rows.size() == 1:
		set_file_open(str(_rows.keys()[0]), true)


## Open or close the file row for [param path].
func set_file_open(path: String, open: bool) -> void:
	if not _rows.has(path):
		return
	var row: Dictionary = _rows[path]
	var toggle: Button = row["toggle"]
	var body: VBoxContainer = row["body"]
	toggle.set_pressed_no_signal(open)
	UI.bind_icon(toggle, "disclosure_open" if open else "disclosure_closed")
	if not open:
		body.hide()
		return
	UI_MOTION.reveal(body)
	if not bool(row.get("requested", false)):
		row["requested"] = true
		file_requested.emit(str(entry.get("commit", "")), path)


## One file's detail, as `joinery timeline show --path` prints it.
func apply_file(data: Dictionary) -> void:
	var path := str(data.get("path", ""))
	if not _rows.has(path):
		return
	var row: Dictionary = _rows[path]
	var view: VBoxContainer = row["view"]
	for child: Node in view.get_children():
		view.remove_child(child)
		child.queue_free()
	match str(data.get("kind", "")):
		"text":
			view.add_child(_diff(data))
		"image":
			view.add_child(_pictures(data))
		_:
			view.add_child(UI.label(binary_text(data), "JoineryCaption", true))
	UI_MOTION.fade_in(view)


## Reading the file's detail failed.
func file_failed(path: String, message: String) -> void:
	if not _rows.has(path):
		return
	var row: Dictionary = _rows[path]
	row["requested"] = false
	var view: VBoxContainer = row["view"]
	for child: Node in view.get_children():
		view.remove_child(child)
		child.queue_free()
	view.add_child(UI.label(message, "JoineryError", true))


## The restore of [param path] is running ("busy"), waiting in Review ("done") or failed.
func set_restore(path: String, outcome: String, message := "") -> void:
	if not _rows.has(path):
		return
	var row: Dictionary = _rows[path]
	var button: Button = row["restore"]
	var status: HBoxContainer = row["status"]
	for child: Node in status.get_children():
		status.remove_child(child)
		child.queue_free()
	button.disabled = outcome == "busy"
	status.visible = true
	match outcome:
		"busy":
			var dots := INDICATOR.new()
			dots.running = true
			status.add_child(dots)
			status.add_child(UI.label("Preparing…", "JoineryCaption"))
		"done":
			var words := UI.label("Waiting for you in Review", "JoineryCaption")
			words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			status.add_child(words)
			var link := UI.link("Review")
			link.name = "Review"
			link.accessibility_name = "Show the restore waiting in the Review tab"
			link.pressed.connect(func() -> void: review_requested.emit())
			status.add_child(link)
			UI_MOTION.pulse(status)
		_:
			var error := UI.label(message, "JoineryError", true)
			status.add_child(error)


## "Ada Lovelace · 3 files · 12 Oct 2026, 14:05".
static func meta_text(data: Dictionary) -> String:
	var parts: Array[String] = []
	var author := str(data.get("author", ""))
	if not author.is_empty():
		parts.append(author)
	var files: Variant = data.get("files", 0)
	var count := (files as Array).size() if files is Array else int(files)
	parts.append("%d file%s" % [count, "" if count == 1 else "s"])
	var stamp := int(data.get("time", 0))
	if stamp > 0:
		var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
		var when := Time.get_datetime_dict_from_unix_time(stamp + bias)
		var months := [
			"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
		]
		(
			parts
			. append(
				(
					"%d %s %d, %02d:%02d"
					% [
						int(when["day"]),
						months[int(when["month"]) - 1],
						int(when["year"]),
						int(when["hour"]),
						int(when["minute"]),
					]
				)
			)
		)
	return " · ".join(parts)


## "Model changed · 1.2 MB → 1.4 MB", "Sound added · 340 KB".
static func binary_text(data: Dictionary) -> String:
	var path := str(data.get("path", ""))
	var noun := "File"
	for candidate: String in BINARY_NOUNS:
		if path.get_extension().to_lower() in BINARY_NOUNS[candidate]:
			noun = candidate
	var before := int(data.get("before_size", -1))
	var after := int(data.get("after_size", -1))
	match str(data.get("change", "")):
		"created":
			return "%s added · %s" % [noun, size_text(after)]
		"deleted":
			return "%s removed · was %s" % [noun, size_text(before)]
	return "%s changed · %s → %s" % [noun, size_text(before), size_text(after)]


## Bytes for people: "340 KB", "1.2 MB".
static func size_text(count: int) -> String:
	if count < 0:
		return "unknown size"
	var value := float(count)
	var units := ["B", "KB", "MB", "GB"]
	var index := 0
	while value >= 1000.0 and index < units.size() - 1:
		value /= 1000.0
		index += 1
	return ("%.1f %s" if index >= 2 and value < 10.0 else "%d %s") % [value, units[index]]


func _clear() -> void:
	_rows.clear()
	for child: Node in _files.get_children():
		_files.remove_child(child)
		child.queue_free()


func _add_row(file: Dictionary) -> void:
	var path := str(file.get("path", ""))
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryFlush"
	box.set_meta("path", path)
	_files.add_child(box)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	UI.bind_height(head, "md", false)
	box.add_child(head)
	var toggle := UI.row_button(path, "disclosure_closed")
	toggle.name = "Toggle"
	toggle.icon_alignment = HORIZONTAL_ALIGNMENT_LEFT
	toggle.tooltip_text = "res://" + path
	toggle.accessibility_name = path + ", show what changed"
	toggle.toggle_mode = true
	UI_MOTION.press_feedback(toggle)
	head.add_child(toggle)
	var change := str(file.get("change", "modified"))
	var word := _fixed(UI.label(CHANGE_WORDS.get(change, "Changed"), "JoineryCaption"))
	word.name = "Change"
	head.add_child(word)
	var added := int(file.get("added", 0))
	var removed := int(file.get("removed", 0))
	if str(file.get("kind", "")) == "text" and added + removed > 0:
		var counts := _fixed(UI.label("+%d −%d" % [added, removed], "JoineryNumeric"))
		counts.name = "Counts"
		head.add_child(counts)
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	body.hide()
	box.add_child(body)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	body.add_child(inset)
	var inner := VBoxContainer.new()
	inner.theme_type_variation = "JoineryTight"
	inset.add_child(inner)
	var view := VBoxContainer.new()
	view.name = "View"
	view.theme_type_variation = "JoineryTight"
	inner.add_child(view)
	var reading := HBoxContainer.new()
	reading.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	reading.add_child(dots)
	reading.add_child(UI.label("Reading…", "JoineryCaption"))
	view.add_child(reading)
	var actions := HBoxContainer.new()
	actions.theme_type_variation = "JoineryRow"
	inner.add_child(actions)
	var restore := UI.secondary("Restore this file as it was")
	restore.name = "Restore"
	restore.tooltip_text = (
		"Bring back the version from just before this entry deleted it. You review it first."
		if change == "deleted"
		else "Put this version back. It waits in the Review tab for you to accept."
	)
	restore.accessibility_name = "Restore %s as it was" % path
	restore.pressed.connect(func() -> void: restore_requested.emit(str(entry.get("commit")), path))
	actions.add_child(restore)
	var open := UI.quiet("Open file")
	open.name = "Open"
	open.tooltip_text = "Open the file as it is now"
	open.accessibility_name = "Open " + path
	open.disabled = not FileAccess.file_exists("res://" + path)
	if open.disabled:
		open.tooltip_text = "This file isn't in the project now"
	open.pressed.connect(func() -> void: open_requested.emit(path))
	actions.add_child(open)
	var status := HBoxContainer.new()
	status.name = "Status"
	status.theme_type_variation = "JoineryRowTight"
	status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status.hide()
	actions.add_child(status)
	toggle.toggled.connect(func(open_now: bool) -> void: set_file_open(path, open_now))
	_rows[path] = {
		"toggle": toggle, "body": body, "view": view, "restore": restore, "status": status
	}


## Diff lines in the mono font, coloured by role, like the Review panel's Details.
func _diff(data: Dictionary) -> Control:
	var lines: Array = data.get("diff", [])
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryTight"
	if lines.is_empty():
		box.add_child(UI.label("No line changes to show.", "JoineryCaption"))
		return box
	var body := RichTextLabel.new()
	body.name = "Diff"
	body.fit_content = true
	body.scroll_active = false
	body.selection_enabled = true
	body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.accessibility_name = "Changed lines"
	body.set_meta("lines", lines)
	body.theme_changed.connect(_paint.bind(body))
	box.add_child(body)
	_paint(body)
	if bool(data.get("truncated", false)):
		box.add_child(UI.label("Showing the first part of a long change.", "JoineryFaint"))
	return box


## RichTextLabel bakes its colours, so this runs again on every theme change.
func _paint(body: RichTextLabel) -> void:
	var lines: Array = body.get_meta("lines", [])
	body.clear()
	body.push_mono()
	for index: int in lines.size():
		var line := str(lines[index])
		var role := "text"
		if line.begins_with("+"):
			role = "success"
		elif line.begins_with("-"):
			role = "danger"
		elif line.begins_with("@@"):
			role = "text_faint"
		body.push_color(UI_THEME.color(body, role))
		body.add_text(line)
		body.pop()
		if index < lines.size() - 1:
			body.newline()
	body.pop()


## Before and after side by side, each with its caption.
func _pictures(data: Dictionary) -> Control:
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryTight"
	var pair := HBoxContainer.new()
	pair.name = "Pictures"
	pair.theme_type_variation = "JoineryRow"
	box.add_child(pair)
	for side: String in ["before", "after"]:
		var column := VBoxContainer.new()
		column.theme_type_variation = "JoineryFlush"
		column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		pair.add_child(column)
		var picture := TextureRect.new()
		picture.name = side.capitalize()
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		picture.custom_minimum_size = Vector2(0, UI_THEME.control_h(self, "md") * 5)
		picture.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var file := str(data.get(side, ""))
		var image := Image.load_from_file(file) if not file.is_empty() else null
		if image != null and not image.is_empty():
			picture.texture = ImageTexture.create_from_image(image)
			column.add_child(picture)
		else:
			var none := UI.label("Not there yet" if side == "before" else "Removed", "JoineryFaint")
			none.custom_minimum_size = picture.custom_minimum_size
			none.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
			none.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
			column.add_child(none)
		var size := int(data.get(side + "_size", -1))
		var caption := side.capitalize() + (" · " + size_text(size) if size >= 0 else "")
		column.add_child(UI.label(caption, "JoineryNumeric"))
	if data.has("note"):
		box.add_child(UI.label(str(data["note"]), "JoineryCaption", true))
	return box


static func _fixed(label: Label) -> Label:
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return label
