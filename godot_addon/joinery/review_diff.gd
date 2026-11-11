@tool
extends PanelContainer
## A text change at a glance: only the lines that changed, in the mono font, removed lines
## above the added ones, each file named once above its lines. No context lines and no hunk
## headers; the full diff stays in Details. Long changes stop after MAX_LINES with a count of
## the rest.

const UI_THEME := preload("ui_theme.gd")
const DETAILS := preload("review_details.gd")
## Changed lines shown at most; the rest are counted.
const MAX_LINES := 120

var _text: RichTextLabel
var _diff := ""


func _init() -> void:
	theme_type_variation = "JoineryMediaFrame"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	clip_contents = true
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	add_child(inset)
	_text = RichTextLabel.new()
	_text.scroll_active = true
	_text.selection_enabled = true
	_text.autowrap_mode = TextServer.AUTOWRAP_OFF
	_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_text.accessibility_name = "Changed lines"
	inset.add_child(_text)
	# Changed lines read as one block of code, not as paragraphs.
	_text.add_theme_constant_override("paragraph_separation", 0)
	_text.theme_changed.connect(_render)


## Show the changed lines of a unified [param diff].
func show_diff(diff: String) -> void:
	_diff = diff
	_render()


## The plain text on show (for tests).
func shown_text() -> String:
	return _text.get_parsed_text()


## The changed lines per file: [[path, [lines]]], files with no changed lines left out.
static func changed(diff: String) -> Array:
	var found: Array = []
	var sections := DETAILS.split_diff(diff)
	for path: String in sections:
		var lines: Array[String] = []
		for line: String in sections[path]:
			if (line.begins_with("+") or line.begins_with("-")) and line.length() > 0:
				lines.append(line)
		if not lines.is_empty():
			found.append([path, lines])
	return found


func _render() -> void:
	if _text == null:
		return
	_text.clear()
	var files := changed(_diff)
	var shown := 0
	var hidden := 0
	_text.push_mono()
	for index: int in files.size():
		var path: String = files[index][0]
		var lines: Array = files[index][1]
		if shown >= MAX_LINES:
			hidden += lines.size()
			continue
		if files.size() > 1:
			if index > 0:
				_text.newline()
			_text.push_color(_role("text_faint"))
			_text.add_text(path.get_file() if path != DETAILS.LOOSE_GROUP else path)
			_text.pop()
			_text.newline()
		for line: String in lines:
			if shown >= MAX_LINES:
				hidden += 1
				continue
			_text.push_color(_role("success" if line.begins_with("+") else "danger"))
			_text.add_text(line)
			_text.pop()
			_text.newline()
			shown += 1
	_text.pop()
	if hidden > 0:
		_text.push_color(_role("text_muted"))
		_text.add_text("%s more in Details" % DETAILS.plural(hidden, "line"))
		_text.pop()


func _role(role: String) -> Color:
	if _text.has_theme_color(role, "Joinery"):
		return _text.get_theme_color(role, "Joinery")
	return UI_THEME.color(_text, role)
