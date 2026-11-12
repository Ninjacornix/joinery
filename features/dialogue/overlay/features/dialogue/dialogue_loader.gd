@tool
class_name DialogueFileLoader
extends ResourceFormatLoader
## Lets Godot load .dlg files as DialogueFile resources: `load()`, `preload()`,
## exported properties and the FileSystem dock. Godot registers a loader that has a
## class_name by itself, in the editor and in exported games.

const DialogueFileScript := preload("res://features/dialogue/dialogue_file.gd")


func _get_recognized_extensions() -> PackedStringArray:
	return PackedStringArray([DialogueFileScript.EXTENSION])


func _handles_type(type: StringName) -> bool:
	return type == &"Resource"


func _get_resource_type(path: String) -> String:
	return "Resource" if _is_dialogue(path) else ""


func _get_resource_script_class(path: String) -> String:
	return "DialogueFile" if _is_dialogue(path) else ""


func _load(path: String, _original: String, _threads: bool, _cache_mode: int) -> Variant:
	var file: Resource = DialogueFileScript.open(path)
	if file == null:
		return ERR_FILE_CANT_OPEN
	return file


static func _is_dialogue(path: String) -> bool:
	return path.get_extension().to_lower() == DialogueFileScript.EXTENSION
