extends SceneTree
## Loads one kept save in the game as it is now and prints what came back (ADR 0066).
##
## Joinery runs it headless, outside the project (`--script /tmp/…/save_check.gd`):
##   godot --headless --path <game> --fixed-fps 60 --script save_check.gd --
##     --joinery-save=<slot file> --saves-dir=<empty dir> --settings-path=<file>
## It copies the save into slot 1 of the game's own save system (the save-load
## feature's `SaveGame`), loads it the way Load does, and once the level is ready
## prints one line:
##   JOINERY_SAVE {"status": "loaded"|"failed", "reason", "version", "data_version",
##                 "level", "saved": {level, playtime, nodes, extra, line},
##                 "loaded": {level, playtime, nodes, extra, line}}
## `saved` is the save after the game's migration steps, `loaded` what the game holds
## after loading it; values are plain JSON (engine types as their text, Vector2(1, 2)).
## Python (joinery.saves) compares the two, and both with the summary kept with the save.

const PREFIX := "JOINERY_SAVE "
const SAVE_ARG := "--joinery-save="
const SLOT := 1
## Frames to wait for the level to start before giving up.
const WAIT_FRAMES := 600

var _done := false


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	await process_frame
	var save_game: Node = root.get_node_or_null(^"SaveGame")
	if save_game == null or not save_game.has_method(&"load_slot"):
		_finish({"status": "failed", "reason": "The game has no save system (SaveGame)."})
		return
	var source := ""
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(SAVE_ARG):
			source = argument.substr(SAVE_ARG.length())
	var text := FileAccess.get_file_as_string(source)
	var parsed: Variant = JSON.parse_string(text)
	if not (parsed is Dictionary):
		_finish({"status": "failed", "reason": "The kept save isn't a save file."})
		return
	var envelope: Dictionary = parsed
	var result := {
		"status": "failed",
		"version": int(envelope.get("version", 1)),
		"data_version": int(save_game.call(&"data_version")),
	}
	var slot_path: String = save_game.call(&"slot_path", SLOT)
	DirAccess.make_dir_recursive_absolute(slot_path.get_base_dir())
	var file := FileAccess.open(slot_path, FileAccess.WRITE)
	if file == null:
		result["reason"] = "The save folder can't be written."
		_finish(result)
		return
	file.store_string(text)
	file.close()
	var migrated: Dictionary = {}
	if save_game.has_method(&"_decode") and save_game.has_method(&"_migrate"):
		var decoded: Dictionary = save_game.call(&"_decode", envelope)
		migrated = save_game.call(&"_migrate", decoded)
	if not migrated.is_empty():
		result["saved"] = _state(
			save_game,
			str(migrated.get("level", "")),
			float(migrated.get("playtime", 0.0)),
			migrated.get("nodes", {}),
			migrated.get("extra", {})
		)
	var failure: Array[String] = []
	save_game.connect(
		&"load_failed", func(_slot: int, reason: String) -> void: failure.append(reason)
	)
	save_game.connect(&"loaded", func(_slot: int) -> void: _on_loaded(save_game, result))
	var error: int = save_game.call(&"load_slot", SLOT)
	if error != OK or not failure.is_empty():
		result["reason"] = failure[0] if not failure.is_empty() else error_string(error)
		_finish(result)
		return
	for frame: int in WAIT_FRAMES:
		await process_frame
		if _done:
			return
	result["reason"] = "The saved level never started."
	_finish(result)


func _on_loaded(save_game: Node, result: Dictionary) -> void:
	if _done:
		return
	var game: Node = root.get_node_or_null(^"Game")
	var level: Node = game.call(&"current_level") if game != null else null
	var nodes: Dictionary = game.call(&"capture_state") if game != null else {}
	var extra: Dictionary = {}
	if save_game.has_method(&"_capture_extra"):
		extra = save_game.call(&"_capture_extra")
	result["status"] = "loaded"
	result["loaded"] = _state(
		save_game,
		level.scene_file_path if level != null else "",
		float(save_game.call(&"playtime")),
		nodes,
		extra
	)
	_finish(result)


func _state(
	save_game: Node, level: String, playtime: float, nodes: Dictionary, extra: Dictionary
) -> Dictionary:
	var line := ""
	var adapter: Variant = save_game.get(&"adapter")
	if adapter is Object and (adapter as Object).has_method(&"summary"):
		line = str((adapter as Object).call(&"summary", nodes))
	return {
		"level": level,
		"playtime": playtime,
		"nodes": _plain(nodes),
		"extra": _plain(extra),
		"line": line,
	}


## JSON-safe: dictionaries and arrays as they are, engine values as their text.
static func _plain(value: Variant) -> Variant:
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return value
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_DICTIONARY:
			var out := {}
			for key: Variant in value:
				out[str(key)] = _plain(value[key])
			return out
		TYPE_ARRAY:
			var items: Array = []
			for item: Variant in value:
				items.append(_plain(item))
			return items
	if value is Object:
		return "<%s>" % (value as Object).get_class()
	return var_to_str(value)


func _finish(result: Dictionary) -> void:
	if _done:
		return
	_done = true
	print(PREFIX + JSON.stringify(result))
	quit(0)
