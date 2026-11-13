extends SceneTree
## The setup notice under the sidebar header (setup_notice.gd), headless, against a stand-in
## `joinery` that answers in JSON as the real one does: an out-of-date add-on is offered as
## Update and restart, which schedules the update after this editor exits and asks the editor to
## close; the connection banner holds the notice back (one notice at a time). Refresh tools
## runs in place with its step, a bar, Cancel and Show log, and a failure says why plainly.

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
elif a[0] == "bootstrap" and "--cancel" in a:
    print(json.dumps({"state": "idle"}))
elif a[0] == "bootstrap":
    import time
    log = os.environ["CALLS"] + ".log"
    for step in (1, 2):
        print(json.dumps({"state": "running", "progress": (step - 1) / 2, "step": step,
            "steps": 2, "text": "Refreshing Blender (step %d of 2)…" % step, "log": log}),
            flush=True)
        time.sleep(0.4)
    if os.environ.get("FAIL") == "1":
        print(json.dumps({"error": "Docker ran out of disk space.", "log": log}))
        sys.exit(1)
    print(json.dumps({"state": "done", "installed": [], "log": log}))
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
	await _check_refresh()
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


## Refresh tools runs right here: step and bar while it runs, then a fresh check; a failure says
## why in plain words and keeps Show log.
func _check_refresh() -> void:
	var notice := _notice()
	root.add_child(notice)
	var asked: Array[bool] = []
	notice.check_requested.connect(func() -> void: asked.append(true))
	var needs := {
		"state": "needs_image",
		"text": "Blender and Krita need a refresh after the Joinery update.",
		"action": {"id": "rebuild", "label": "Refresh tools"},
	}
	notice.show_readiness({"notice": needs})
	_check(notice.text() == needs["text"], "The apps' sentence shows: " + notice.text())
	_check(notice.action_text() == "Refresh tools", "With one button")
	OS.set_environment("FAIL", "0")
	notice.press()
	_check(notice.refreshing(), "Refresh tools starts at once")
	_check(
		await _until(func() -> bool: return notice.text().contains("step 2 of 2")),
		"The step shows as it goes: " + notice.text()
	)
	var bar: ProgressBar = notice.get("_bar")
	_check(bar.visible and is_equal_approx(bar.value, 0.5), "The bar follows: %s" % bar.value)
	_check((notice.get("_cancel") as Button).visible, "Cancel is there while it runs")
	_check((notice.get("_show_log") as Button).visible, "So is Show log")
	_check(await _until(func() -> bool: return not asked.is_empty()), "Done: ask again")
	_check(not bar.visible, "The bar leaves when it is done")
	_check(_logged()[-1].has("--progress"), "It asks for progress lines")
	OS.set_environment("FAIL", "1")
	notice.press()
	_check(
		await _until(func() -> bool: return notice.text() == "Docker ran out of disk space."),
		"A failure says why in plain words: " + notice.text()
	)
	_check(notice.action_text() == "Try again", "And offers to try again")
	_check((notice.get("_show_log") as Button).visible, "Show log stays for the failure")
	notice.queue_free()
