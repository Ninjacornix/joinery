@tool
class_name DialogueFile
extends Resource
## One dialogue file (.dlg): its text and the program parsed from it.
## `load("res://game/dialogue/guard.dlg")` gives one (DialogueFileLoader reads the
## file); DialogueFile.open() reads one directly, and from_text() makes one from text.

const Parser := preload("res://features/dialogue/dialogue_parser.gd")
const EXTENSION := "dlg"

@export_multiline var text: String = ""
## Where it was read from (empty for text made in code).
@export var source: String = ""

var _program: Dictionary = {}


static func from_text(dialogue_text: String, path: String = "") -> DialogueFile:
	var file: DialogueFile = DialogueFile.new()
	file.text = dialogue_text
	file.source = path
	return file


## Reads a .dlg file, or returns null when it cannot be read.
static func open(path: String) -> DialogueFile:
	if not FileAccess.file_exists(path):
		return null
	var dialogue_text: String = FileAccess.get_file_as_string(path)
	if dialogue_text.is_empty() and FileAccess.get_open_error() != OK:
		return null
	return from_text(dialogue_text, path)


## The parsed program (see dialogue_parser.gd), parsed once.
func program() -> Dictionary:
	if _program.is_empty():
		_program = Parser.parse(text, source)
	return _program


## Problems in the file, as "source:line: problem" (empty when it is fine).
func errors() -> Array:
	return program()["errors"]


func node_names() -> Array:
	return program()["node_order"]
