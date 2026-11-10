# gdlint: disable=max-public-methods
extends Node
## Save slots for the whole game. A slot holds the state of every node in group
## `saveable` (through `Game.capture_state`), the running level, play time,
## the start options and any extra data game code registers. Slot 0 is the
## autosave; the others are the player's.
##
## File `<save/directory>/slot_<n>.json`:
##   {"format": 1, "version": <save/data_version>, "game": <config/name>,
##    "blueprint": <game/blueprint>, "saved_at": <unix seconds>,
##    "level": <scene path>, "playtime": <seconds>, "options": {...},
##    "nodes": {save_id: state}, "extra": {id: data}}
## Values under `nodes`, `extra` and `options` are encoded with
## `JSON.from_native`, so vectors, colours and packed arrays survive. Files are
## written to `.tmp` first and then renamed, so a crash never leaves half a save.
##
## Changing what a node saves: raise `save/data_version` and register a step
## with `add_migration(old_version, step)`; older slots are upgraded on load,
## one version at a time. A damaged file, a newer format or version, an older
## version with a missing upgrade step, or a save from another starting point
## is reported (`slot_info`, `load_failed`) and never loaded.
##
## Extra data that is not on a saveable node (unlocks, totals kept in an
## autoload): `register(id, capture, restore)`.
##
## A blueprint can ship `res://features/save-load/blueprint_adapter.gd`
## (see save_adapter.gd): a one-line summary for the slot list and its own
## autosave moments.
##
## Online, only the server saves and loads; the menu entries are disabled for
## clients. Test runs (`-- --settings-path=…`) keep their saves in a fresh
## folder that is removed on exit, unless `-- --saves-dir=<dir>` names one.

signal saved(slot: int)
signal loaded(slot: int)
signal load_failed(slot: int, reason: String)
signal slots_changed

const SLOTS := 3
const AUTOSAVE_SLOT := 0
const FORMAT := 1
const DIR_SETTING := "save/directory"
const VERSION_SETTING := "save/data_version"
const AUTOSAVE_SETTING := "save/autosave_on_checkpoint"
const INTERVAL_SETTING := "save/autosave_min_interval"
const DEFAULT_DIR := "user://saves"
const DIR_ARG := "--saves-dir="
const SETTINGS_ARG := "--settings-path="
const ADAPTER_PATH := "res://features/save-load/blueprint_adapter.gd"
const SlotsPage := preload("res://features/save-load/slots_page.gd")

## Why a slot cannot be loaded (English msgids; show them with `tr`).
const REASON_EMPTY := "This slot is empty."
const REASON_DAMAGED := "This save is damaged and cannot be loaded."
const REASON_NEWER := "This save was made by a newer version of the game."
const REASON_OTHER_GAME := "This save belongs to a different game."
const REASON_TOO_OLD := "This save is from an older version that cannot be upgraded."
const REASON_CLIENT := "Only the host can save or load."
const REASON_NO_LEVEL := "There is no game running to save."

## Where the slot files live (an absolute or user:// path).
var directory: String = DEFAULT_DIR
## The blueprint's adapter, or null.
var adapter: RefCounted = null

var _temporary_dir: bool = false
var _migrations: Dictionary = {}
var _providers: Dictionary = {}
var _kept_extra: Dictionary = {}
var _loading_slot: int = -1
var _playtime_base: float = 0.0
var _autosave_wait: float = -1.0
var _last_autosave_msec: int = -1
var _quiet_until_frame: int = 0


func _ready() -> void:
	directory = _pick_directory()
	_load_adapter()
	GameEvents.checkpoint_reached.connect(_on_checkpoint_reached)
	GameEvents.level_started.connect(_on_level_started)
	_add_menu_entries()
	refresh_menu()


func _process(delta: float) -> void:
	if _autosave_wait < 0.0:
		return
	_autosave_wait -= delta
	if _autosave_wait <= 0.0:
		_autosave_wait = -1.0
		if Game.current_level() != null and can_save():
			autosave()


func _exit_tree() -> void:
	if _temporary_dir:
		_remove_tree(directory)


# --- slots -------------------------------------------------------------------

## Saves the running game into `slot`. Errors: ERR_PARAMETER_RANGE_ERROR (no
## such slot), ERR_UNAUTHORIZED (an online client), ERR_UNCONFIGURED (no level
## running), or the file error.
func save_slot(slot: int) -> Error:
	if not _valid(slot):
		return ERR_PARAMETER_RANGE_ERROR
	if not can_save():
		return ERR_UNAUTHORIZED
	var level: Node = Game.current_level()
	if level == null:
		return ERR_UNCONFIGURED
	var data: Dictionary = {
		"format": FORMAT,
		"version": data_version(),
		"game": str(ProjectSettings.get_setting("application/config/name", "")),
		"blueprint": str(ProjectSettings.get_setting("game/blueprint", "")),
		"saved_at": Time.get_unix_time_from_system(),
		"level": level.scene_file_path,
		"playtime": playtime(),
		"options": JSON.from_native(Game.options),
		"nodes": _encode_map(Game.capture_state()),
		"extra": _encode_map(_capture_extra()),
	}
	var error: Error = _write(slot_path(slot), JSON.stringify(data, "\t") + "\n")
	if error != OK:
		return error
	if slot == AUTOSAVE_SLOT:
		_last_autosave_msec = Time.get_ticks_msec()
	refresh_menu()
	saved.emit(slot)
	slots_changed.emit()
	return OK


## Loads `slot`: starts its level and restores every saved node once the level
## is ready (`loaded` fires then). A slot that cannot be loaded fires
## `load_failed` with the reason and changes nothing.
func load_slot(slot: int) -> Error:
	if not _valid(slot):
		return ERR_PARAMETER_RANGE_ERROR
	if not can_save():
		load_failed.emit(slot, REASON_CLIENT)
		return ERR_UNAUTHORIZED
	var read: Dictionary = _read(slot)
	if not bool(read["ok"]):
		load_failed.emit(slot, str(read["reason"]))
		return int(read["error"]) as Error
	var save: Dictionary = _migrate(_decode(read["data"]))
	if save.is_empty():
		load_failed.emit(slot, REASON_TOO_OLD)
		return ERR_FILE_UNRECOGNIZED
	var nodes: Dictionary = save.get("nodes", {})
	var options: Dictionary = save.get("options", {})
	Game.pending_state = nodes
	_kept_extra = save.get("extra", {})
	_loading_slot = slot
	_autosave_wait = -1.0
	var level_path: String = str(save.get("level", ""))
	var hook: String = str(ProjectSettings.get_setting("game/hooks/level_scene", ""))
	# Only the game's own scenes: a save file is data, and a scene from
	# anywhere else (user://, a shared save) could carry its own scripts.
	if (
		not level_path.begins_with("res://")
		or level_path == hook
		or not ResourceLoader.exists(level_path)
	):
		_playtime_base = float(save.get("playtime", 0.0))
		Game.start_game(options)
	else:
		# A level other than the starting one: change to it the way
		# `Game.start_game` does, keeping the play time counting on.
		_playtime_base = float(save.get("playtime", 0.0)) - Game.play_time()
		Game.options = options
		Menus.close_all()
		get_tree().paused = false
		get_tree().change_scene_to_file(level_path)
	return OK


## Loads the most recent loadable slot (main menu "Continue").
func continue_game() -> Error:
	var slot: int = latest_slot()
	if slot < 0:
		return ERR_FILE_NOT_FOUND
	return load_slot(slot)


## True when the slot has a file, loadable or not.
func has_slot(slot: int) -> bool:
	return _valid(slot) and FileAccess.file_exists(slot_path(slot))


## What the slot list shows: `exists`, `damaged` (true for anything that
## cannot be loaded), `reason` (English msgid, "" when loadable), `saved_at`
## (unix seconds), `level`, `playtime` (seconds), `version` and `summary` (the
## blueprint adapter's one line, already translated).
func slot_info(slot: int) -> Dictionary:
	var info: Dictionary = {
		"slot": slot,
		"exists": has_slot(slot),
		"damaged": false,
		"reason": "",
		"saved_at": 0.0,
		"level": "",
		"playtime": 0.0,
		"version": 0,
		"summary": "",
	}
	if not bool(info["exists"]):
		info["reason"] = REASON_EMPTY
		return info
	var read: Dictionary = _read(slot)
	var data: Dictionary = read["data"]
	info["saved_at"] = _number(data.get("saved_at"), 0.0)
	info["level"] = str(data.get("level", ""))
	info["playtime"] = _number(data.get("playtime"), 0.0)
	info["version"] = int(_number(data.get("version"), 1.0))
	if not bool(read["ok"]):
		info["damaged"] = true
		info["reason"] = str(read["reason"])
		return info
	if adapter != null and adapter.has_method(&"summary"):
		var nodes: Dictionary = _decode_map(data.get("nodes", {}))
		info["summary"] = str(adapter.call(&"summary", nodes))
	return info


func delete_slot(slot: int) -> Error:
	if not _valid(slot):
		return ERR_PARAMETER_RANGE_ERROR
	var path: String = slot_path(slot)
	if FileAccess.file_exists(path + ".tmp"):
		DirAccess.remove_absolute(path + ".tmp")
	if not FileAccess.file_exists(path):
		return ERR_FILE_NOT_FOUND
	var error: Error = DirAccess.remove_absolute(path)
	if error == OK:
		refresh_menu()
		slots_changed.emit()
	return error


## The loadable slot saved last, or -1.
func latest_slot() -> int:
	var best: int = -1
	var best_time: float = -1.0
	for slot: int in SLOTS:
		if not has_slot(slot):
			continue
		var read: Dictionary = _read(slot)
		if not bool(read["ok"]):
			continue
		var when: float = _number((read["data"] as Dictionary).get("saved_at"), 0.0)
		if when > best_time:
			best = slot
			best_time = when
	return best


## Saves into the autosave slot now.
func autosave() -> Error:
	return save_slot(AUTOSAVE_SLOT)


## Asks for an autosave at the end of this frame. With `throttled`, at most
## one per `save/autosave_min_interval` seconds (later requests wait). Ignored
## for online clients, while a slot is loading and right after a level starts.
func request_autosave(throttled: bool = false) -> void:
	if not can_save() or _loading_slot >= 0 or Game.current_level() == null:
		return
	if Engine.get_process_frames() < _quiet_until_frame:
		return
	var wait: float = 0.0
	if throttled and _last_autosave_msec >= 0:
		var interval: float = float(ProjectSettings.get_setting(INTERVAL_SETTING, 10.0))
		var since: float = (Time.get_ticks_msec() - _last_autosave_msec) / 1000.0
		wait = maxf(0.0, interval - since)
	if _autosave_wait < 0.0 or wait < _autosave_wait:
		_autosave_wait = wait


## Drops an autosave that is still waiting (throttled or end of frame).
func cancel_autosave() -> void:
	_autosave_wait = -1.0


## True while an autosave is waiting to happen.
func autosave_pending() -> bool:
	return _autosave_wait >= 0.0


## Only the authority saves: true offline and on an online server.
func can_save() -> bool:
	var session: GameSession = Game.session()
	if session != null and not session.is_authority():
		return false
	if multiplayer.has_multiplayer_peer() and not multiplayer.is_server():
		return false
	return true


func slot_path(slot: int) -> String:
	return directory.path_join("slot_%d.json" % slot)


## "Autosave" or "Slot <n>", translated.
func slot_name(slot: int) -> String:
	if slot == AUTOSAVE_SLOT:
		return tr("Autosave")
	return tr("Slot %d") % slot


## Seconds played in this game, across loads.
func playtime() -> float:
	return maxf(0.0, _playtime_base + Game.play_time())


func data_version() -> int:
	return int(ProjectSettings.get_setting(VERSION_SETTING, 1))


# --- extension points ----------------------------------------------------------

## Registers a step that turns a save made at `from_version` into one at
## `from_version + 1`. `step` takes and returns the decoded save Dictionary
## (`nodes`, `extra`, `options`, `level`, …); return {} to refuse.
func add_migration(from_version: int, step: Callable) -> void:
	_migrations[from_version] = step
	slots_changed.emit()


func remove_migration(from_version: int) -> void:
	_migrations.erase(from_version)
	slots_changed.emit()


## Saves `capture.call()` under `extra[id]` and hands it back to
## `restore.call(data)` after a load. Registering after a load restores at once.
func register(id: String, capture: Callable, restore: Callable) -> void:
	_providers[id] = {"capture": capture, "restore": restore}
	if _kept_extra.has(id) and _loading_slot < 0:
		var data: Variant = _kept_extra[id]
		_kept_extra.erase(id)
		restore.call(data)


func unregister(id: String) -> void:
	_providers.erase(id)


## Adds or removes the main menu's Continue entry to match the slots on disk.
func refresh_menu() -> void:
	if latest_slot() >= 0:
		Menus.add_entry(&"main", &"continue", "Continue", _on_continue, 5, can_save)
	else:
		Menus.remove_entry(&"main", &"continue")


## True when any slot can be loaded.
func any_loadable() -> bool:
	return latest_slot() >= 0


# --- internals -----------------------------------------------------------------

func _valid(slot: int) -> bool:
	return slot >= 0 and slot < SLOTS


func _pick_directory() -> String:
	var picked: String = str(ProjectSettings.get_setting(DIR_SETTING, DEFAULT_DIR))
	var testing: bool = false
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(DIR_ARG):
			return argument.substr(DIR_ARG.length())
		if argument.begins_with(SETTINGS_ARG):
			testing = true
	if testing:
		_temporary_dir = true
		picked = "user://test_saves/run_%d_%d" % [OS.get_process_id(), Time.get_ticks_usec()]
	return picked


func _load_adapter() -> void:
	if not ResourceLoader.exists(ADAPTER_PATH):
		return
	var script: GDScript = load(ADAPTER_PATH) as GDScript
	if script == null:
		return
	adapter = script.new() as RefCounted
	if adapter == null:
		return
	if adapter.has_method(&"migrations"):
		var steps: Dictionary = adapter.call(&"migrations")
		for from_version: Variant in steps:
			add_migration(int(from_version), steps[from_version])
	if adapter.has_method(&"watch"):
		adapter.call(&"watch", request_autosave.bind(true))


func _add_menu_entries() -> void:
	Menus.add_entry(&"main", &"load", "Load", _open_load_page, 15, _can_open_load)
	Menus.add_entry(&"pause", &"save", "Save", _open_save_page, 30, can_save)
	Menus.add_entry(&"pause", &"load", "Load", _open_load_page, 40, _can_open_load)


func _can_open_load() -> bool:
	return can_save() and any_loadable()


func _open_save_page() -> void:
	Menus.open_page(SlotsPage.build(self, SlotsPage.SAVE))


func _open_load_page() -> void:
	Menus.open_page(SlotsPage.build(self, SlotsPage.LOAD))


func _on_continue() -> void:
	var error: Error = continue_game()
	if error != OK:
		Menus.toast(tr("Could not load the save."))


func _on_checkpoint_reached(_id: String) -> void:
	if bool(ProjectSettings.get_setting(AUTOSAVE_SETTING, true)):
		request_autosave(false)


func _on_level_started(_level: Node) -> void:
	# A waiting autosave belonged to the level run that just ended.
	_autosave_wait = -1.0
	_quiet_until_frame = Engine.get_process_frames() + 2
	if _loading_slot < 0:
		if Game.play_time() < 0.1:
			# A new game from the menu: the clock and the kept data start over.
			_playtime_base = 0.0
			_kept_extra = {}
		return
	var slot: int = _loading_slot
	_loading_slot = -1
	for id: String in _providers.keys():
		if _kept_extra.has(id):
			var data: Variant = _kept_extra[id]
			_kept_extra.erase(id)
			(_providers[id]["restore"] as Callable).call(data)
	loaded.emit(slot)


func _capture_extra() -> Dictionary:
	var extra: Dictionary = _kept_extra.duplicate(true)
	for id: String in _providers:
		var capture: Callable = _providers[id]["capture"]
		if capture.is_valid():
			extra[id] = capture.call()
	return extra


## {"ok", "error", "reason", "data"} for a slot file; `data` is the parsed,
## still encoded envelope ({} when unreadable).
func _read(slot: int) -> Dictionary:
	var result: Dictionary = {
		"ok": false, "error": ERR_FILE_NOT_FOUND, "reason": REASON_EMPTY, "data": {}
	}
	var path: String = slot_path(slot)
	if not FileAccess.file_exists(path):
		return result
	var text: String = FileAccess.get_file_as_string(path)
	var json: JSON = JSON.new()
	if not text.is_empty() and json.parse(text) == OK and json.data is Dictionary:
		result["data"] = json.data
	var reason: String = _problem(result["data"])
	result["ok"] = reason.is_empty()
	result["reason"] = reason
	if reason.is_empty():
		result["error"] = OK
	elif reason == REASON_DAMAGED:
		result["error"] = ERR_FILE_CORRUPT
	else:
		result["error"] = ERR_FILE_UNRECOGNIZED
	return result


## Why an envelope cannot be loaded (a REASON_* msgid), or "" when it can.
func _problem(data: Dictionary) -> String:
	var format: Variant = data.get("format")
	if not _is_number(format):
		return REASON_DAMAGED
	var version: int = int(_number(data.get("version"), 1.0))
	if int(format) > FORMAT or version > data_version():
		return REASON_NEWER
	if (
		int(format) < 1
		or not (data.get("nodes") is Dictionary)
		or not (data.get("level") is String)
		or not _is_number(data.get("saved_at"))
		or not _is_number(data.get("playtime"))
	):
		return REASON_DAMAGED
	var blueprint: String = str(ProjectSettings.get_setting("game/blueprint", ""))
	var saved_blueprint: String = str(data.get("blueprint", ""))
	if not blueprint.is_empty() and not saved_blueprint.is_empty() and blueprint != saved_blueprint:
		return REASON_OTHER_GAME
	if not _can_migrate(version):
		return REASON_TOO_OLD
	return ""


## The envelope with `nodes`, `extra` and `options` decoded to engine values.
func _decode(data: Dictionary) -> Dictionary:
	var save: Dictionary = data.duplicate()
	save["version"] = int(_number(data.get("version"), 1.0))
	var nodes: Dictionary = {}
	var decoded: Dictionary = _decode_map(data.get("nodes", {}))
	for id: Variant in decoded:
		if decoded[id] is Dictionary:
			nodes[str(id)] = decoded[id]
	save["nodes"] = nodes
	save["extra"] = _decode_map(data.get("extra", {}))
	var options: Variant = JSON.to_native(data.get("options", {}))
	save["options"] = options if options is Dictionary else {}
	return save


## True when a step is registered for every version from `version` up to
## `save/data_version`.
func _can_migrate(version: int) -> bool:
	for step: int in range(version, data_version()):
		if not _migrations.has(step):
			return false
	return true


## Runs the registered steps up to `save/data_version`; {} when one is missing.
func _migrate(save: Dictionary) -> Dictionary:
	var version: int = int(save.get("version", 1))
	var target: int = data_version()
	while version < target:
		if not _migrations.has(version):
			return {}
		var step: Callable = _migrations[version]
		var next: Variant = step.call(save.duplicate(true))
		if not (next is Dictionary) or (next as Dictionary).is_empty():
			return {}
		save = next
		version += 1
		save["version"] = version
	for key: String in ["nodes", "extra", "options"]:
		if not (save.get(key) is Dictionary):
			save[key] = {}
	return save


func _encode_map(map: Dictionary) -> Dictionary:
	var encoded: Dictionary = {}
	for key: Variant in map:
		encoded[str(key)] = JSON.from_native(map[key])
	return encoded


func _decode_map(map: Variant) -> Dictionary:
	var decoded: Dictionary = {}
	if not (map is Dictionary):
		return decoded
	for key: Variant in map:
		decoded[str(key)] = JSON.to_native(map[key])
	return decoded


func _write(path: String, text: String) -> Error:
	var folder: String = path.get_base_dir()
	if not DirAccess.dir_exists_absolute(folder):
		var made: Error = DirAccess.make_dir_recursive_absolute(folder)
		if made != OK:
			return made
	var temporary: String = path + ".tmp"
	var file: FileAccess = FileAccess.open(temporary, FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	file.store_string(text)
	file.flush()
	var error: Error = file.get_error()
	file.close()
	if error != OK:
		DirAccess.remove_absolute(temporary)
		return error
	error = DirAccess.rename_absolute(temporary, path)
	if error != OK:
		DirAccess.remove_absolute(temporary)
	return error


func _remove_tree(path: String) -> void:
	var folder: DirAccess = DirAccess.open(path)
	if folder == null:
		return
	for file_name: String in folder.get_files():
		folder.remove(file_name)
	for child: String in folder.get_directories():
		_remove_tree(path.path_join(child))
	DirAccess.remove_absolute(path)


static func _is_number(value: Variant) -> bool:
	return value is float or value is int


static func _number(value: Variant, fallback: float) -> float:
	return float(value) if _is_number(value) else fallback
