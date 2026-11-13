extends SceneTree
## The setup notice under the sidebar header (setup_notice.gd), headless, against a stand-in
## `joinery` that answers in JSON as the real one does: an out-of-date add-on is offered as
## Update and restart, which schedules the update after this editor exits and asks the editor to
## close; the connection banner holds the notice back (one notice at a time).

const NOTICE := preload("res://addons/joinery/setup_notice.gd")
const HEADER := preload("res://addons/joinery/sidebar_header.gd")
const SLASH := preload("res://addons/joinery/slash_commands.gd")
## Answers like the CLI; every call is appended to CALLS (one JSON line of arguments).
## It runs from a file, not `python -c` (see update_dialog_test.gd).
const FAKE := """
import json, os, sys
a = sys.argv[1:]
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(a) + "\\n")
if a[0] == "update-addon" and "--check" in a:
    print(json.dumps({"state": os.environ.get("ADDON", "stale")}))
elif a[0] == "update-addon":
    print(json.dumps({"state": "scheduled", "helper": 7, "project": "p"}))
else:
    print(json.dumps({"error": "unexpected"}))
    sys.exit(1)
"""

var _failed := false
var _calls := ""


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000)
	while Time.get_ticks_msec() < deadline:
		if condition.call():
			return true
		await process_frame
	return false


func _notice() -> NOTICE:
	var notice: NOTICE = NOTICE.new()
	var fake := OS.get_user_data_dir().path_join("fake_joinery_setup.py")
	var script := FileAccess.open(fake, FileAccess.WRITE)
	script.store_string(FAKE)
	script.close()
	notice.cli_prefix = PackedStringArray([OS.get_environment("JOINERY_TEST_PYTHON"), fake])
	notice.project_path = "/tmp/a project"
	notice.editor_path = "/opt/joinery/godot"
	return notice


func _logged() -> Array:
	var calls: Array = []
	for line: String in FileAccess.get_file_as_string(_calls).split("\n", false):
		calls.append(JSON.parse_string(line))
	return calls


func _run() -> void:
	root.size = Vector2i(500, 700)
	_calls = OS.get_user_data_dir().path_join("setup-notice-calls.jsonl")
	if FileAccess.file_exists(_calls):
		DirAccess.remove_absolute(_calls)
	OS.set_environment("CALLS", _calls)
	await _check_addon()
	await _check_banner_wins()
	_check_doctor()
	print("setup notice: ok" if not _failed else "setup notice: FAILED")
	quit(1 if _failed else 0)


func _check_addon() -> void:
	OS.set_environment("ADDON", "current")
	var notice := _notice()
	root.add_child(notice)
	notice.check_addon()
	await _until(func() -> bool: return not notice.is_processing())
	_check(notice.text().is_empty(), "A current add-on says nothing")
	OS.set_environment("ADDON", "stale")
	notice.check_addon()
	_check(
		await _until(func() -> bool: return notice.text() == NOTICE.ADDON_STALE),
		"An old add-on is named: " + notice.text()
	)
	_check(notice.action_text() == "Update and restart", "One button fixes it")
	var restarted: Array[bool] = []
	notice.restart_requested.connect(func() -> void: restarted.append(true))
	notice.press()
	_check(
		await _until(func() -> bool: return not restarted.is_empty()),
		"Update and restart asks the editor to close once the helper waits"
	)
	var last: Array = _logged()[-1]
	_check(last.slice(0, 3) == ["update-addon", "--project", "/tmp/a project"], str(last))
	_check(last[last.find("--after-exit") + 1] == str(OS.get_process_id()), "It waits for us")
	_check(last[last.find("--editor") + 1] == "/opt/joinery/godot", "It reopens this editor")
	notice.queue_free()


func _check_banner_wins() -> void:
	var header: HEADER = HEADER.new()
	root.add_child(header)
	header.notice.mark_addon_stale()
	await process_frame
	_check(header.notice.text() == NOTICE.ADDON_STALE, "The notice shows under a quiet header")
	header.show_state("none", "")
	await _until(func() -> bool: return not header.notice.visible, 2.0)
	_check(header.notice.text().is_empty(), "Connect wins: the notice waits")
	header.show_state("connected", "codex")
	await _until(func() -> bool: return header.notice.visible, 2.0)
	_check(header.notice.text() == NOTICE.ADDON_STALE, "Connected, the notice is back")
	header.queue_free()
	# The chat refusing an older add-on: Update and restart is the fix, not Reconnect.
	var refused: HEADER = HEADER.new()
	root.add_child(refused)
	refused.show_state("error", "codex", NOTICE.ADDON_STALE + " Choose Update and restart.")
	await _until(func() -> bool: return refused.notice.visible, 2.0)
	_check(refused.notice.action_text() == "Update and restart", "The notice offers the update")
	_check(not refused.get("_banner").visible, "No Reconnect banner for an old add-on")
	refused.queue_free()


## /doctor exits 1 when an app needs work: that is its answer, shown app by app.
func _check_doctor() -> void:
	var report := {
		"problems": 1,
		"tools":
		[
			{"name": "Godot", "status": "ready", "summary": "Godot 4.7 ready"},
			{
				"name": "Krita",
				"status": "unavailable",
				"summary": "Krita needs a refresh",
				"action": {"id": "rebuild", "label": "Refresh tools"},
			},
		],
	}
	var text := SLASH.reply("/doctor", 1, JSON.stringify(report))
	_check(text.contains("Godot 4.7 ready."), "Each app has its line: " + text)
	_check(text.contains("Krita needs a refresh."), "The app that needs work too: " + text)
	_check(not text.contains("couldn't finish"), "A found problem is not a crash: " + text)
