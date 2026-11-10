@tool
extends VBoxContainer
## The Details part of the Review panel: one row per changed file with its kind ("Created",
## "Changed", "Deleted") and "+12 −3" line counts, expanding to the raw diff lines in the mono
## font. Also builds the counts caption under the review title ("1 model · 3 files").

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
## Asset kinds counted in the caption, in caption order, by file extension.
const KINDS := {
	"model": ["blend", "glb", "gltf", "fbx", "obj"],
	"image": ["png", "jpg", "jpeg", "webp", "kra", "aseprite", "ase", "svg", "exr", "tga", "psd"],
	"sound": ["wav", "ogg", "mp3", "flac", "scd", "mid", "midi"],
	"material": ["mmg", "material"],
	"scene": ["tscn", "scn"],
	"script": ["gd", "cs", "gdshader"],
}
## Joinery bookkeeping: counted as files, never as assets.
const BOOKKEEPING := ["recipes/", ".joinery/", "joinery.lock"]
const CHANGE_WORDS := {"created": "Created", "modified": "Changed", "deleted": "Deleted"}
## Title of the group that holds diff lines not tied to a file.
const LOOSE_GROUP := "Changes"

var _rows: Array[Control] = []


func _init() -> void:
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Changed files"


## Rebuild the rows from a unified [param diff] and the transaction's [param changes]
## ({path, change}). Files without text changes get a row without counts.
func show_changes(diff: String, changes: Array) -> void:
	clear_changes()
	var sections := split_diff(diff)
	var listed: Array[String] = []
	for change: Dictionary in changes:
		var path := str(change.get("path", ""))
		if path.is_empty() or path in listed:
			continue
		listed.append(path)
		var word: String = CHANGE_WORDS.get(str(change.get("change", "")), "Changed")
		_add_row(path, word, sections.get(path, []))
	for path: String in sections:
		if not path in listed:
			_add_row(path, "Changed", sections[path])
	var with_lines := _rows.filter(func(row: Control) -> bool: return row.has_meta("lines"))
	if with_lines.size() == 1:
		_set_row_open(with_lines[0], true, false)


## Remove every row.
func clear_changes() -> void:
	for row: Control in _rows:
		remove_child(row)
		row.queue_free()
	_rows.clear()


## True when there is at least one file row.
func has_content() -> bool:
	return not _rows.is_empty()


## The file rows, top to bottom (for staggering them in as the details settle).
func rows() -> Array[Control]:
	return _rows.duplicate()


## Open or close the diff of the row at [param index].
func set_row_open(index: int, open: bool) -> void:
	if index >= 0 and index < _rows.size():
		_set_row_open(_rows[index], open, true)


## The "+12 −3" text of the row at [param index] ("" when it has no text changes).
func row_counts(index: int) -> String:
	if index < 0 or index >= _rows.size():
		return ""
	var counts: Label = _rows[index].get_meta("counts")
	return counts.text if counts.visible else ""


## Split a unified diff into {path: Array[String] of lines without the ---/+++ headers}.
## Lines before any file header go under LOOSE_GROUP.
static func split_diff(diff: String) -> Dictionary:
	var sections := {}
	if diff.strip_edges().is_empty():
		return sections
	var lines := diff.split("\n")
	var current := LOOSE_GROUP
	var index := 0
	while index < lines.size():
		var line := lines[index]
		if (
			line.begins_with("--- ")
			and index + 1 < lines.size()
			and lines[index + 1].begins_with("+++ ")
		):
			current = _header_path(line.substr(4), lines[index + 1].substr(4))
			if not sections.has(current):
				sections[current] = []
			index += 2
			continue
		if not (line.is_empty() and index == lines.size() - 1):
			if not sections.has(current):
				sections[current] = []
			(sections[current] as Array).append(line)
		index += 1
	return sections


## Added and removed line counts of diff [param lines]: [added, removed].
static func line_counts(lines: Array) -> Array[int]:
	var added := 0
	var removed := 0
	for line: String in lines:
		if line.begins_with("+"):
			added += 1
		elif line.begins_with("-"):
			removed += 1
	return [added, removed]


## The counts caption for [param changes]: assets by kind (the source and its exports count
## once, so hammer.blend and hammer.glb are "1 model"), then "N files" when there are more
## files than assets. Examples: "1 model · 3 files", "2 images", "4 files".
static func counts_text(changes: Array) -> String:
	var assets := {}
	var files: Array[String] = []
	for change: Dictionary in changes:
		var path := str(change.get("path", ""))
		if path.is_empty() or path in files:
			continue
		files.append(path)
		var kind := kind_of(path)
		if kind.is_empty():
			continue
		var names: Array = assets.get_or_add(kind, [])
		var stem := path.get_file().get_basename()
		if not stem in names:
			names.append(stem)
	var parts := PackedStringArray()
	var counted := 0
	for kind: String in KINDS:
		if assets.has(kind):
			var count: int = (assets[kind] as Array).size()
			counted += count
			parts.append(plural(count, kind))
	if files.size() > counted or parts.is_empty():
		parts.append(plural(files.size(), "file"))
	return " · ".join(parts)


## The counts caption of a scene edit: "1 scene node", "4 scene nodes".
static func scene_text(count: int) -> String:
	return plural(count, "scene node")


## "1 model", "3 models".
static func plural(count: int, noun: String) -> String:
	return "%d %s%s" % [count, noun, "" if count == 1 else "s"]


## The asset kind of [param path] ("model", "image", …), or "" for bookkeeping and other files.
static func kind_of(path: String) -> String:
	for prefix: String in BOOKKEEPING:
		if path.begins_with(prefix):
			return ""
	var extension := path.get_extension().to_lower()
	for kind: String in KINDS:
		if extension in KINDS[kind]:
			return kind
	return ""


## Changes inferred from a diff and media list, for callers that do not pass them.
static func changes_from(diff: String, media: Array) -> Array:
	var changes: Array = []
	var seen: Array[String] = []
	for item: Dictionary in media:
		var path := str(item.get("path", ""))
		if not path.is_empty() and not path in seen:
			seen.append(path)
			var word := "modified" if item.has("before") else "created"
			changes.append({"path": path, "change": word})
	for path: String in split_diff(diff):
		if path != LOOSE_GROUP and not path in seen:
			seen.append(path)
			changes.append({"path": path, "change": "modified"})
	return changes


static func _header_path(old: String, new: String) -> String:
	var before := old.split("\t")[0].strip_edges()
	var after := new.split("\t")[0].strip_edges()
	if after == "/dev/null":
		after = before
	if before.begins_with("a/") and after.begins_with("b/") and before.substr(2) == after.substr(2):
		after = after.substr(2)
	return after


func _add_row(path: String, word: String, lines: Array) -> void:
	var row := VBoxContainer.new()
	row.theme_type_variation = "JoineryTight"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	row.add_child(head)
	var toggle := Button.new()
	toggle.text = path.get_file() if path != LOOSE_GROUP else path
	toggle.theme_type_variation = "JoineryQuietButton"
	toggle.alignment = HORIZONTAL_ALIGNMENT_LEFT
	toggle.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	toggle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toggle.tooltip_text = "res://" + path if path != LOOSE_GROUP else ""
	toggle.accessibility_name = path
	UI.bind_height(toggle, "sm")
	UI.bind_icon(toggle, "disclosure_closed")
	head.add_child(toggle)
	head.add_child(_fixed(UI.label(word, "JoineryCaption")))
	var counts := _fixed(UI.label("", "JoineryNumeric"))
	head.add_child(counts)
	row.set_meta("toggle", toggle)
	row.set_meta("counts", counts)
	if lines.is_empty():
		# Keep the icon slot so every file name starts on the same line.
		toggle.add_theme_color_override("icon_normal_color", Color.TRANSPARENT)
		toggle.add_theme_color_override("icon_disabled_color", Color.TRANSPARENT)
		toggle.disabled = true
		toggle.focus_mode = Control.FOCUS_NONE
		toggle.tooltip_text = (
			"\n"
			. join(PackedStringArray([toggle.tooltip_text, "No text changes to show"]))
			. strip_edges()
		)
		counts.hide()
	else:
		row.set_meta("lines", lines)
		var numbers := line_counts(lines)
		counts.text = "+%d −%d" % numbers
		toggle.toggle_mode = true
		toggle.focus_mode = Control.FOCUS_ALL
		UI_MOTION.press_feedback(toggle)
		var inset := MarginContainer.new()
		inset.theme_type_variation = "JoineryInset"
		inset.hide()
		row.add_child(inset)
		var body := RichTextLabel.new()
		body.fit_content = true
		body.scroll_active = false
		body.selection_enabled = true
		body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		body.accessibility_name = "Changes in " + toggle.text
		inset.add_child(body)
		row.set_meta("body", body)
		body.theme_changed.connect(
			func() -> void:
				if row.has_meta("rendered"):
					_render(row)
		)
		toggle.toggled.connect(func(open: bool) -> void: _set_row_open(row, open, true))
	add_child(row)
	_rows.append(row)


func _set_row_open(row: Control, open: bool, animate: bool) -> void:
	if not row.has_meta("body"):
		return
	var toggle: Button = row.get_meta("toggle")
	var body: RichTextLabel = row.get_meta("body")
	toggle.set_pressed_no_signal(open)
	UI.bind_icon(toggle, "disclosure_open" if open else "disclosure_closed")
	var inset := body.get_parent() as Control
	if not open:
		inset.hide()
		return
	if not row.has_meta("rendered"):
		_render(row)
	if animate:
		UI_MOTION.reveal(inset)
	else:
		inset.show()


## Diff lines in the mono font, coloured by role. Re-run on every theme change because
## RichTextLabel bakes its colours.
func _render(row: Control) -> void:
	var body: RichTextLabel = row.get_meta("body")
	var lines: Array = row.get_meta("lines", [])
	row.set_meta("rendered", true)
	body.clear()
	body.push_mono()
	for index: int in lines.size():
		var line: String = lines[index]
		var role := "text"
		if line.begins_with("+"):
			role = "success"
		elif line.begins_with("-"):
			role = "danger"
		elif line.begins_with("@@"):
			role = "text_faint"
		body.push_color(_role(body, role))
		body.add_text(line)
		body.pop()
		if index < lines.size() - 1:
			body.newline()
	body.pop()


## A short label that keeps its full width in a row (no ellipsis collapsing it to "…").
static func _fixed(label: Label) -> Label:
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return label


static func _role(control: Control, role: String) -> Color:
	if control.has_theme_color(role, "Joinery"):
		return control.get_theme_color(role, "Joinery")
	return UI_THEME.color(control, role)
