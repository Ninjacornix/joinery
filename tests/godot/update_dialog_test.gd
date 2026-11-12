extends SceneTree
## The Joinery updates dialog (update_dialog.gd), headless, against a stand-in `joinery update`
## that answers in JSON as the real one does: a quiet daily check that finds nothing stays
## hidden; a quiet check that finds a signed version opens the dialog with its release notes;
## Download and install streams progress and ends at Restart now; an unsigned release can't be
## installed from here; a source checkout says git pull; offline offers Try again; the link
## text a Project Manager shows follows along.
## With JOINERY_ASSET_CAPTURE (a path prefix) and JOINERY_PARTS_THEME it also saves
## <prefix>-available.png, <prefix>-ready.png and <prefix>-current.png.

const DIALOG := preload("res://addons/joinery/update_dialog.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
## Answers `update --check`, `--install --progress` (two progress lines, then ready) and the
## small commands, like the real CLI's --json output. NONE makes the check find nothing.
const FAKE := """
import json, os, sys
a = sys.argv[1:]
release = {"version": "0.6.0", "prerelease": False, "signed": os.environ.get("UNSIGNED") != "1",
    "size": 104857600, "url": "https://github.com/irchiinnuss/joinery/releases/tag/v0.6.0",
    "notes": "## Joinery 0.6.0\\n\\n- Review shows sound and video side by side.\\n- Faster kits."}
base = {"current": "0.5.0", "prereleases": False, "can_install": os.environ.get("SOURCE") != "1",
    "previous": "0.4.2", "pending": "", "dismissed": ""}
if "--check" in a:
    if os.environ.get("NONE") == "1":
        print(json.dumps({**base, "available": False, "offline": False}))
    else:
        print(json.dumps({**base, "available": True, "offline": False, "release": release}))
elif "--install" in a:
    print(json.dumps({"progress": 0.5, "stage": "download"}), flush=True)
    print(json.dumps({"progress": 1.0, "stage": "verify"}), flush=True)
    print(json.dumps({"state": "ready", "version": "0.6.0"}))
else:
    print(json.dumps({"ok": True}))
"""

var _failed := false
var _labels: Array[String] = []


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


func _run() -> void:
	root.size = Vector2i(900, 900)
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("update-dialog-test-ui.cfg")
		var file := ConfigFile.new()
		file.set_value("appearance", "theme", theme)
		file.save(config)
		Engine.set_meta("joinery_ui_config", config)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var box := StyleBoxFlat.new()
	box.bg_color = UI_THEME.color(backdrop, "surface_sunken")
	backdrop.add_theme_stylebox_override("panel", box)
	var dialog: DIALOG = DIALOG.new()
	dialog.cli_prefix = PackedStringArray([OS.get_environment("JOINERY_TEST_PYTHON"), "-c", FAKE])
	dialog.label_changed.connect(func(text: String) -> void: _labels.append(text))
	root.add_child(dialog)
	await process_frame
	await _check_quiet(dialog)
	await _check_install(dialog)
	_check_states(dialog)
	dialog.hide()
	dialog.apply(_answer({"available": false}))
	dialog.call("_popup")
	await _capture("current")
	dialog.queue_free()
	await process_frame
	print("update dialog: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _answer(extra: Dictionary) -> Dictionary:
	var answer := {
		"current": "0.5.0",
		"prereleases": false,
		"can_install": true,
		"previous": "0.4.2",
		"pending": "",
		"available": true,
		"offline": false,
		"release": {"version": "0.6.0", "signed": true, "notes": "- Faster kits."},
	}
	answer.merge(extra, true)
	return answer


## The daily check: nothing new stays hidden; a new signed version opens the dialog.
func _check_quiet(dialog: DIALOG) -> void:
	OS.set_environment("NONE", "1")
	dialog.check_daily()
	_check(await _until(func() -> bool: return dialog.state == "current"), "quiet check answers")
	_check(not dialog.visible, "Nothing new: the daily check stays quiet")
	_check(_labels.back() == "Check for updates", "link text: %s" % [_labels])
	OS.set_environment("NONE", "0")
	dialog.check_daily()
	_check(await _until(func() -> bool: return dialog.visible), "A new version opens the dialog")
	_check(dialog.state == "available", "state: %s" % dialog.state)
	_check(dialog.title_label.text == "Joinery 0.6.0 is available", dialog.title_label.text)
	_check(dialog.caption.text == "You have Joinery 0.5.0.", dialog.caption.text)
	_check(dialog.notes_scroll.visible, "The release notes show")
	_check(not dialog.status_row.visible, "No leftover status line")
	var notes := dialog.notes.get_parsed_text()
	_check("Faster kits" in notes and not "Joinery 0.6.0" in notes, "notes: %s" % notes)
	_check(dialog.get_ok_button().text == "Download and install", "primary action")
	_check(dialog.later_button.visible and dialog.later_button.text == "Not now", "Not now")
	_check(dialog.back_button.visible and dialog.back_button.text == "Go back to 0.4.2", "back")
	_check(dialog.prereleases.visible and not dialog.prereleases.is_on(), "pre-releases off")
	_check(_labels.back() == "Joinery 0.6.0 is available", "link text: %s" % [_labels])
	var body := float(UI_THEME.font_size(dialog, "body"))
	if DisplayServer.get_name() != "headless":  # A headless window has no size.
		var want := roundi(DIALOG.SIZE_EM.x * body)
		_check(absi(dialog.size.x - want) <= 1, "sized in ems: %s, not %d" % [dialog.size, want])
		_check(dialog.page.size.y < dialog.size.y, "The page fits: %s" % dialog.page.size)
	await _capture("available")


## Download and install: progress lines, then Restart now.
func _check_install(dialog: DIALOG) -> void:
	dialog.get_ok_button().pressed.emit()
	_check(dialog.state == "working", "Installing: %s" % dialog.state)
	_check(dialog.get_ok_button().disabled, "No second install while one runs")
	_check(dialog.later_button.text == "Cancel", "Cancel while installing")
	_check(await _until(func() -> bool: return dialog.state == "ready"), "install finishes")
	_check(dialog.visible, "Still open at the end")
	_check(dialog.title_label.text == "Joinery 0.6.0 is ready", dialog.title_label.text)
	_check(dialog.get_ok_button().text == "Restart now", dialog.get_ok_button().text)
	_check(dialog.later_button.text == "Later", "Later")
	_check(not dialog.status_row.visible, "No leftover progress line")
	_check("0.5.0 stays available" in dialog.caption.text, dialog.caption.text)
	_check(_labels.back() == "Restart to finish updating", "link text: %s" % [_labels])
	await _capture("ready")


## Answers that can't be installed from here, and offline.
func _check_states(dialog: DIALOG) -> void:
	dialog.apply(_answer({"release": {"version": "0.6.0", "signed": false, "notes": "x"}}))
	_check(dialog.state == "unsigned", "unsigned: %s" % dialog.state)
	_check(dialog.get_ok_button().text == "Open the release page", "unsigned action")
	_check("no signature" in dialog.status_label.text, dialog.status_label.text)
	dialog.apply(_answer({"can_install": false}))
	_check(dialog.state == "source", "source: %s" % dialog.state)
	_check("git pull" in dialog.status_label.text, dialog.status_label.text)
	_check(not dialog.later_button.visible, "Only Close for a checkout")
	dialog.apply(_answer({"available": false, "offline": true, "message": "You're offline."}))
	_check(dialog.state == "offline", "offline: %s" % dialog.state)
	_check(dialog.get_ok_button().text == "Try again", "offline action")
	_check(dialog.status_label.text == "You're offline.", dialog.status_label.text)
	dialog.apply(_answer({"available": false, "prereleases": true}))
	_check(dialog.state == "current" and dialog.prereleases.is_on(), "pre-releases on")
	_check(dialog.title_label.text == "Joinery 0.5.0 is up to date", dialog.title_label.text)
	_check(dialog.back_button.visible, "Go back stays offered when up to date")
	dialog.apply({"error": "The archive's SHA-256 doesn't match."})
	_check(dialog.state == "failed", "failed: %s" % dialog.state)
	_check("SHA-256" in dialog.status_label.text, dialog.status_label.text)


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png when asked (a windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.5).timeout
	await RenderingServer.frame_post_draw
	root.get_texture().get_image().save_png("%s-%s.png" % [prefix, state])
