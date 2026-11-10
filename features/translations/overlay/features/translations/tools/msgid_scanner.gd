extends RefCounted
## Finds the text a game shows, for the translation template (POT).
##
## In GDScript it reads:
## - translation calls: `tr("…")`, `atr`, `tr_n("…", "…", n)`, `atr_n`,
##   `TranslationServer.translate` and `translate_plural`, with their context;
## - calls whose text argument is an English message id: the core's
##   `Menus.add_entry`, `add_settings_page`, `toast`, the `UiStyle` builders
##   (`button`, `title`, `hint`, `row`, `page`), any `*_row(…)` helper,
##   `add_item`, `add_tab`, `set_text`, `set_tooltip_text`. The first plain
##   string argument is taken (`&"ids"` and `^"paths"` never are);
## - assignments of a string to `text`, `tooltip_text`, `placeholder_text`,
##   `dialog_text`, `heading` or `title`, unless the string is then formatted
##   or joined (`"…" % n` is a sign it should be `tr("…") % n`).
## In scenes it reads `text`, `tooltip_text`, `placeholder_text`,
## `dialog_text` and `title` of nodes whose auto-translate is not disabled.
##
## Text that none of these reach can always be wrapped in `tr()`.

const TRANSLATE_CALLS := {
	"tr": [0, -1, 1],
	"atr": [0, -1, 1],
	"translate": [0, -1, 1],
	"tr_n": [0, 1, 3],
	"atr_n": [0, 1, 3],
	"translate_plural": [0, 1, 3],
}
const TEXT_CALLS: Array[String] = [
	"add_entry",
	"add_settings_page",
	"toast",
	"button",
	"title",
	"hint",
	"row",
	"page",
	"_menu_page",
	"add_item",
	"add_check_item",
	"add_radio_check_item",
	"add_tab",
	"set_text",
	"set_tooltip_text",
]
const TEXT_CALL_SUFFIX := "_row"
const TEXT_TARGETS: Array[String] = [
	"text", "tooltip_text", "placeholder_text", "dialog_text", "heading", "title"
]
const SCENE_PROPERTIES: Array[String] = [
	"text", "tooltip_text", "placeholder_text", "dialog_text", "title"
]
const SCRIPT_EXTENSIONS: Array[String] = ["gd"]
const SCENE_EXTENSIONS: Array[String] = ["tscn"]
## Context separator, as in gettext's compiled catalogues.
const EOT := "\u0004"

## key (context + EOT + msgid) -> {"msgid", "plural", "context", "refs"}.
var entries: Dictionary = {}
## Files read, in order.
var files: PackedStringArray = PackedStringArray()
var _letter: RegEx = RegEx.create_from_string("\\p{L}")


static func key_of(context: String, msgid: String) -> String:
	return context + EOT + msgid


## Every entry in the order found (files in path order, text in file order).
func sorted_entries() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for entry: Dictionary in entries.values():
		found.append(entry)
	return found


## Files that gave at least one message id.
func files_with_text() -> PackedStringArray:
	var found: PackedStringArray = PackedStringArray()
	for entry: Dictionary in entries.values():
		for ref: String in entry["refs"]:
			if not found.has(ref):
				found.append(ref)
	found.sort()
	return found


## Whether `text` is worth translating: it has a letter and is not a path.
func is_text(text: String) -> bool:
	if text.strip_edges().is_empty():
		return false
	for prefix: String in ["res://", "user://", "uid://"]:
		if text.begins_with(prefix):
			return false
	return _letter.search(text) != null


func add(msgid: String, ref: String, plural: String = "", context: String = "") -> void:
	if not is_text(msgid):
		return
	var key: String = key_of(context, msgid)
	if not entries.has(key):
		entries[key] = {
			"msgid": msgid, "plural": plural, "context": context, "refs": PackedStringArray()
		}
	var entry: Dictionary = entries[key]
	if not plural.is_empty() and str(entry["plural"]).is_empty():
		entry["plural"] = plural
	var refs: PackedStringArray = entry["refs"]
	if not refs.has(ref):
		refs.append(ref)
		entry["refs"] = refs


## Reads every script and scene under each folder (sorted, recursive), except
## folders listed in `skip` and hidden folders.
func scan_dirs(dirs: PackedStringArray, skip: PackedStringArray) -> void:
	var paths: PackedStringArray = PackedStringArray()
	for dir: String in dirs:
		_collect(dir.trim_suffix("/"), skip, paths)
	paths.sort()
	for path: String in paths:
		scan_file(path)


func scan_file(path: String) -> void:
	if files.has(path):
		return
	var extension: String = path.get_extension().to_lower()
	if not (extension in SCRIPT_EXTENSIONS or extension in SCENE_EXTENSIONS):
		return
	var text: String = FileAccess.get_file_as_string(path)
	if text.is_empty() and FileAccess.get_open_error() != OK:
		return
	files.append(path)
	if extension in SCRIPT_EXTENSIONS:
		scan_script_text(text, path)
	else:
		scan_scene_text(text, path)


func _collect(dir: String, skip: PackedStringArray, into: PackedStringArray) -> void:
	if skip.has(dir) or not DirAccess.dir_exists_absolute(dir):
		return
	for file: String in DirAccess.get_files_at(dir):
		var extension: String = file.get_extension().to_lower()
		if extension in SCRIPT_EXTENSIONS or extension in SCENE_EXTENSIONS:
			into.append(dir.path_join(file))
	for sub: String in DirAccess.get_directories_at(dir):
		if not sub.begins_with("."):
			_collect(dir.path_join(sub), skip, into)


# --- GDScript ----------------------------------------------------------------


## Reads one GDScript source. A small lexer: it skips comments, reads every
## kind of string literal and follows brackets, call names, argument indexes
## and assignment targets.
func scan_script_text(source: String, ref: String) -> void:
	var state: Dictionary = {
		"frames": [] as Array[Dictionary],
		"prev": "",
		"before_prev": "",
		"declared": "",
		"expect_name": false,
		"target": "",
		"target_depth": -1,
	}
	var i: int = 0
	var n: int = source.length()
	while i < n:
		var c: String = source[i]
		if c == "#":
			while i < n and source[i] != "\n":
				i += 1
		elif c == "\n":
			_end_line(state)
			i += 1
		elif c == " " or c == "\t" or c == "\r":
			i += 1
		elif c == "\\" and i + 1 < n and source[i + 1] == "\n":
			i += 2
		elif c == '"' or c == "'":
			i = _string_at(source, i, "plain", ref, state)
		elif (
			(c == "&" or c == "^") and i + 1 < n and (source[i + 1] == '"' or source[i + 1] == "'")
		):
			i = _string_at(source, i + 1, "name", ref, state)
		elif _is_word_start(c):
			var start: int = i
			while i < n and _is_word_char(source[i]):
				i += 1
			var word: String = source.substr(start, i - start)
			if word == "r" and i < n and (source[i] == '"' or source[i] == "'"):
				i = _string_at(source, i, "raw", ref, state)
			else:
				_word(word, state)
		elif c.is_valid_int():
			while i < n and (_is_word_char(source[i]) or source[i] == "."):
				i += 1
			_token("0", state)
		else:
			i = _punct(source, i, ref, state)


func _end_line(state: Dictionary) -> void:
	var frames: Array[Dictionary] = state["frames"]
	if frames.size() <= int(state["target_depth"]):
		state["target"] = ""
		state["target_depth"] = -1
	if frames.is_empty():
		state["declared"] = ""
		state["expect_name"] = false


func _word(word: String, state: Dictionary) -> void:
	if word == "var" or word == "const":
		state["expect_name"] = true
	elif bool(state["expect_name"]):
		state["declared"] = word
		state["expect_name"] = false
	_token(word, state)


func _token(token: String, state: Dictionary) -> void:
	state["before_prev"] = state["prev"]
	state["prev"] = token


func _punct(source: String, i: int, ref: String, state: Dictionary) -> int:
	var c: String = source[i]
	var next: String = source[i + 1] if i + 1 < source.length() else ""
	var frames: Array[Dictionary] = state["frames"]
	var token: String = c
	if c == "(":
		var callee: String = str(state["prev"])
		if not callee.is_valid_identifier() or str(state["before_prev"]) == "func":
			callee = ""
		frames.append({"callee": callee, "arg": 0, "taken": false, "found": {}})
	elif c == "[" or c == "{":
		frames.append({"callee": "", "arg": 0, "taken": false, "found": {}})
	elif c == ")" or c == "]" or c == "}":
		if not frames.is_empty():
			_close_frame(frames.pop_back(), ref)
	elif c == ",":
		if not frames.is_empty():
			frames.back()["arg"] = int(frames.back()["arg"]) + 1
	elif c == "=" and next != "=":
		var target: String = str(state["declared"])
		if target.is_empty():
			target = str(state["prev"])
		state["target"] = target
		state["target_depth"] = frames.size()
		state["declared"] = ""
	elif (
		next == "=" and c in ["=", "!", "<", ">", ":", "+", "-", "*", "/", "%", "&", "|", "^", "~"]
	):
		token = c + next
	_token(token, state)
	return i + token.length()


func _close_frame(frame: Dictionary, ref: String) -> void:
	var found: Dictionary = frame["found"]
	if found.has("msgid"):
		add(str(found["msgid"]), ref, str(found.get("plural", "")), str(found.get("context", "")))


## Reads the string literal whose opening quote is at `i`; returns the index
## after it. `kind` is "plain", "raw" or "name" (StringName or NodePath).
func _string_at(source: String, i: int, kind: String, ref: String, state: Dictionary) -> int:
	var quote: String = source[i]
	var triple: bool = source.substr(i, 3) == quote.repeat(3)
	var closing: String = quote.repeat(3) if triple else quote
	var j: int = i + closing.length()
	var n: int = source.length()
	while j < n:
		if kind != "raw" and source[j] == "\\":
			j += 2
			continue
		if source.substr(j, closing.length()) == closing:
			break
		j += 1
	var body: String = source.substr(i + closing.length(), j - i - closing.length())
	var end: int = mini(j + closing.length(), n)
	if kind != "name":
		var value: String = body if kind == "raw" else body.c_unescape()
		_string(value, _next_char(source, end), ref, state)
	_token('"', state)
	return end


func _string(value: String, after: String, ref: String, state: Dictionary) -> void:
	var frames: Array[Dictionary] = state["frames"]
	if not frames.is_empty():
		var frame: Dictionary = frames.back()
		var callee: String = frame["callee"]
		var arg: int = frame["arg"]
		if TRANSLATE_CALLS.has(callee):
			var spec: Array = TRANSLATE_CALLS[callee]
			var found: Dictionary = frame["found"]
			if arg == int(spec[0]):
				found["msgid"] = value
			elif arg == int(spec[1]):
				found["plural"] = value
			elif arg == int(spec[2]):
				found["context"] = value
			return
		if _is_text_call(callee):
			if not bool(frame["taken"]):
				frame["taken"] = true
				add(value, ref)
			return
	if (
		str(state["target"]) in TEXT_TARGETS
		and frames.size() == int(state["target_depth"])
		and not after in ["%", "+", ".", "["]
	):
		add(value, ref)


func _is_text_call(callee: String) -> bool:
	return callee in TEXT_CALLS or (callee.ends_with(TEXT_CALL_SUFFIX) and callee.length() > 4)


func _next_char(source: String, from: int) -> String:
	var j: int = from
	while j < source.length() and source[j] in [" ", "\t"]:
		j += 1
	return source[j] if j < source.length() else ""


static func _is_word_start(c: String) -> bool:
	return c == "_" or c.to_lower() != c.to_upper()


static func _is_word_char(c: String) -> bool:
	return _is_word_start(c) or c.is_valid_int()


# --- Scenes ------------------------------------------------------------------


## Reads one text scene (.tscn): text properties of nodes that translate.
func scan_scene_text(source: String, ref: String) -> void:
	var lines: PackedStringArray = source.replace("\r\n", "\n").split("\n")
	var in_node: bool = false
	var disabled: bool = false
	var found: PackedStringArray = PackedStringArray()
	var i: int = 0
	while i < lines.size():
		var line: String = lines[i]
		i += 1
		if line.begins_with("["):
			_flush_node(found, disabled, ref)
			found = PackedStringArray()
			disabled = false
			in_node = line.begins_with("[node ")
			continue
		if not in_node:
			continue
		var split: int = line.find(" = ")
		if split <= 0:
			continue
		var property: String = line.substr(0, split)
		var value: String = line.substr(split + 3)
		if property == "auto_translate_mode":
			disabled = value.strip_edges() == str(Node.AUTO_TRANSLATE_MODE_DISABLED)
		elif property == "auto_translate":
			disabled = value.strip_edges() == "false"
		elif property in SCENE_PROPERTIES and value.begins_with('"'):
			while not _closed(value) and i < lines.size():
				value += "\n" + lines[i]
				i += 1
			found.append(value.substr(1, value.length() - 2).c_unescape())
	_flush_node(found, disabled, ref)


func _flush_node(found: PackedStringArray, disabled: bool, ref: String) -> void:
	if disabled:
		return
	for text: String in found:
		add(text, ref)


## Whether a scene value that starts with a quote ends with an unescaped one.
static func _closed(value: String) -> bool:
	if value.length() < 2 or not value.ends_with('"'):
		return false
	var backslashes: int = 0
	var j: int = value.length() - 2
	while j >= 1 and value[j] == "\\":
		backslashes += 1
		j -= 1
	return backslashes % 2 == 0
