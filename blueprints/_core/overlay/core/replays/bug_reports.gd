extends Node
## "Report a bug": an entry in the pause menu and the `report_bug` key (F8) open a page
## where the player says what went wrong. Save writes one folder,
## `user://joinery_reports/<id>/`:
##
##   report.json    id, note, time, game, game version, Joinery version, engine build,
##                  seed, scene, frame, the seconds the report looks back on
##   replay.replay  the session up to the report (Replays.snapshot), with its last
##                  seconds marked; absent when this build does not record
##   log.txt        the game's last log lines, errors and warnings included
##
## Nothing is uploaded: the player sends the folder, and `joinery reports` reads it.
## `game/bug_reports/enabled` (true) turns the entry and the key off;
## `game/bug_reports/seconds` (60) is how far back a report looks.

signal report_saved(id: String, folder: String)

const FOLDER := "user://joinery_reports"
const ENABLED_SETTING := "game/bug_reports/enabled"
const SECONDS_SETTING := "game/bug_reports/seconds"
const ACTION := &"report_bug"
const MENU := &"bug_report"
## Reports kept on the player's machine; the oldest go first.
const KEEP := 20
const NOTE_LIMIT := 2000

## The report page while it is open.
var page: Control
var _note: TextEdit
## The replay frame the page opened on: the report's replay stops there.
var _opened_frame: int = -1


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	if enabled():
		Menus.add_entry(&"pause", &"report_bug", "Report a bug", open_page, 85)


func _unhandled_input(event: InputEvent) -> void:
	if not enabled() or not InputMap.has_action(ACTION):
		return
	if event.is_action_pressed(ACTION):
		get_viewport().set_input_as_handled()
		open_page()


static func enabled() -> bool:
	return bool(ProjectSettings.get_setting(ENABLED_SETTING, true))


static func seconds() -> float:
	return maxf(5.0, float(ProjectSettings.get_setting(SECONDS_SETTING, 60.0)))


## Opens the report page over the game (pausing it first when a level runs).
func open_page() -> void:
	if is_instance_valid(page) and page.is_inside_tree():
		return
	_opened_frame = Replays.frame
	if not Menus.is_open() and Game.current_level() != null:
		Menus.open_pause()
	var column: VBoxContainer = VBoxContainer.new()
	column.add_child(UiStyle.hint("What went wrong? The report stays on this computer."))
	_note = TextEdit.new()
	_note.name = "Note"
	_note.custom_minimum_size = Vector2(UiStyle.px(640), UiStyle.px(200))
	_note.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_note.focus_mode = Control.FOCUS_ALL
	column.add_child(_note)
	var save: Button = UiStyle.button("Save report")
	save.name = "Save"
	save.pressed.connect(_on_save)
	column.add_child(save)
	page = UiStyle.page("Report a bug", column, Menus.back)
	page.name = "BugReport"
	page.set_meta(&"menu", MENU)
	Menus.open_page(page)
	_note.grab_focus()


## Writes a report with `note` and returns its id ("" when it could not be written).
## Replaying a recording never writes one.
func save_report(note: String) -> String:
	if Replays.is_replaying():
		return ""
	var until: int = _opened_frame if _opened_frame >= 0 else Replays.frame
	_opened_frame = -1
	var id: String = Replays.new_id()
	var folder: String = FOLDER.path_join(id)
	var made: Error = DirAccess.make_dir_recursive_absolute(folder)
	if made != OK:
		push_warning("The bug report folder %s could not be made." % folder)
		return ""
	var scene: Node = get_tree().current_scene
	var report: Dictionary = {
		"format": "joinery-bug-report",
		"version": 1,
		"id": id,
		"note": note.strip_edges().left(NOTE_LIMIT),
		"created": Time.get_datetime_string_from_system(true),
		"game": str(ProjectSettings.get_setting("application/config/name", "")),
		"game_version": str(ProjectSettings.get_setting("application/config/version", "")),
		"joinery": str(ProjectSettings.get_setting(Replays.VERSION_SETTING, "")),
		"engine": str(Engine.get_version_info().get("string", "")),
		"build": Replays.build_id(),
		"os": OS.get_name(),
		"debug": OS.is_debug_build(),
		"seed": Replays.seed_value,
		"scene": scene.scene_file_path if scene != null else "",
		"frame": until,
		"seconds": seconds(),
		"errors": Replays.log_lines.errors,
		"replay": "",
		"log": "log.txt",
	}
	var lines: Array[Dictionary] = Replays.snapshot(seconds(), until)
	if lines.is_empty():
		report["replay_note"] = "This build does not record play, so there is no replay."
	elif Replays.write_lines(lines, folder.path_join("replay.replay")) == OK:
		report["replay"] = "replay.replay"
	var log_file: FileAccess = FileAccess.open(folder.path_join("log.txt"), FileAccess.WRITE)
	if log_file != null:
		log_file.store_string(Replays.log_lines.text() + "\n")
		log_file.close()
	var out: FileAccess = FileAccess.open(folder.path_join("report.json"), FileAccess.WRITE)
	if out == null:
		push_warning("The bug report could not be written to %s." % folder)
		return ""
	out.store_string(JSON.stringify(report, "  ") + "\n")
	out.close()
	_prune()
	report_saved.emit(id, ProjectSettings.globalize_path(folder))
	return id


func _on_save() -> void:
	var text: String = _note.text if is_instance_valid(_note) else ""
	var id: String = save_report(text)
	Menus.back()
	if not id.is_empty():
		Menus.toast("Bug report saved")
	elif not Replays.is_replaying():
		Menus.toast("The bug report could not be saved")


func _prune() -> void:
	var names: PackedStringArray = DirAccess.get_directories_at(FOLDER)
	var sorted: Array[String] = []
	for folder_name: String in names:
		sorted.append(folder_name)
	sorted.sort()
	while sorted.size() > KEEP:
		var oldest: String = FOLDER.path_join(sorted.pop_front())
		for file_name: String in DirAccess.get_files_at(oldest):
			DirAccess.remove_absolute(oldest.path_join(file_name))
		DirAccess.remove_absolute(oldest)
