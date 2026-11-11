extends SceneTree
## The bottom panel's Share tab, headless. From fixtures: targets and Build, the templates
## download offer (Build waits behind it), progress lines, one result row per target, the media
## shelf with screenshots and cover, the page preview, and the publish dialog naming the itch.io
## page and channels, with Publish off until the user is signed in and butler is there or its
## install is ticked. Then the real CLI in this disposable project: status answers, and Draft
## page text writes share/page.md and shows it.

const TAB := preload("res://addons/joinery/share_tab.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _until(condition: Callable, seconds := 20.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while not condition.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return condition.call()


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-share-test")
	DirAccess.make_dir_recursive_absolute(cache)
	Engine.set_meta("joinery_ui_config", cache.path_join("journeyman.cfg"))
	Engine.remove_meta("joinery_ui_prefs")
	UI_MOTION.set_enabled(false)
	root.size = Vector2i(1000, 640)
	_host = Control.new()
	_host.size = Vector2(1000, 640)
	root.add_child(_host)
	_check_wording()
	var tab: TAB = TAB.new()
	tab.cli_enabled = false
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	await _frames()
	_check(tab._loading.visible, "The first read shows that it is loading")
	await _check_builds(tab, cache)
	await _check_publish(tab)
	tab.queue_free()
	await _frames()
	await _check_real_cli()
	print("share tab: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_wording() -> void:
	_check(TAB.size_text(10_326_066.0) == "9.8 MB", "Sizes read in MB")
	_check(TAB.size_text(2048.0) == "2 KB", "Small sizes read in KB")
	var line := (
		TAB
		. result_line(
			{
				"ok": true,
				"size": 60_560_573,
				"boot": {"ran": true, "ok": true},
				"playtest": {"summary": "Played 0:30 (seed 4242): nothing to report."},
			}
		)
	)
	_check(line == "57.8 MB · starts · nothing to report.", "One line per build: " + line)
	_check(TAB.result_line({"ok": false, "error": "No templates."}) == "No templates.", "Errors")
	var plain := TAB._plain("<!-- note -->\n\n# Moss\n\n| A | B |\n|---|---|\n| Jump | Space |")
	_check(plain == "Moss\n\nA · B\nJump · Space", "The page preview drops markup: " + plain)


func _status(cache: String) -> Dictionary:
	var shot := cache.path_join("shot-01.png")
	var image := Image.create(320, 180, false, Image.FORMAT_RGB8)
	image.fill(Color("5b8a5a"))
	image.save_png(shot)
	var cover := cache.path_join("cover.png")
	Image.create(630, 500, false, Image.FORMAT_RGB8).save_png(cover)
	return {
		"name": "Moss Run",
		"host": "macos",
		"targets": [{"id": "web"}, {"id": "windows"}, {"id": "macos"}, {"id": "linux"}],
		"templates": {"version": "4.7.2.stable", "ready": false, "download_gb": 1.3},
		"build": null,
		"media":
		{
			"seed": 777,
			"screenshots": [shot, shot, shot, shot],
			"cover": cover,
			"trailer": {"preview": null, "seconds": 31.5, "sound": "Silent."}
		},
		"page": "<!-- d -->\n\n# Moss Run\n\n_Write this part._\n\n## About\n\nRoll.\n",
		"itch": {"butler": null, "version": "15.24.0", "pinned": true, "signed_in": null},
	}


func _check_builds(tab: TAB, cache: String) -> void:
	var status := _status(cache)
	tab.apply_status(status)
	await _frames()
	_check(not tab._loading.visible, "Loading ends with the answer")
	_check(tab._templates.visible, "Missing templates offer the download, with its size")
	var notice := tab._templates.find_child("Text", true, false) as Label
	_check("1.3 GB" in notice.text, "The offer says how big the download is: " + notice.text)
	_check(tab._build_button.disabled, "Build waits for the templates")
	_check(tab._gallery.get_child_count() == 4, "Four screenshots on the shelf")
	_check(tab._cover.visible and tab._cover.texture != null, "The cover shows")
	_check(not tab._trailer_box.visible, "No trailer preview without the file")
	_check(tab._media_button.text == "Remake", "Media can be remade")
	_check("Roll." in tab._page_text.text and not "<!--" in tab._page_text.text, "Page preview")
	_check(tab._page_note.text == "1 part still to write.", "Gaps are counted")
	_check(tab._publish_button.disabled, "Nothing to publish before a build")
	(tab._templates.find_child("Action", true, false) as Button).pressed.emit()
	var started: Array = tab.commands[-1]
	_check("--download-templates" in started, "Only the user's click downloads the templates")
	var expected := ["share", "build", "--targets", "web,windows,macos,linux"]
	_check(started.slice(0, 4) == expected, "Builds every checked target: " + str(started))
	tab.apply_progress("Exporting Web")
	await _frames()
	_check(tab._progress.visible and tab._progress_text.text == "Exporting Web…", "Progress")
	_check(tab._build_button.text == "Building…", "The button says it is building")
	status["templates"] = {"ready": true}
	status["build"] = {
		"changes": ["Added the Web export preset"],
		"targets":
		{
			"web":
			{
				"label": "Web",
				"ok": true,
				"size": 10_326_066,
				"boot": {"ran": false, "detail": "Plays in a browser."},
				"warnings": []
			},
			"macos":
			{
				"label": "macOS",
				"ok": true,
				"size": 60_560_573,
				"boot": {"ran": true, "ok": true, "detail": "Started."},
				"warnings": [],
				"playtest": {"status": "played", "summary": "Played 0:30: nothing to report."}
			},
			"linux": {"label": "Linux", "ok": false, "error": "Godot couldn't export it."},
		},
	}
	tab.finish_job("build", {})
	tab.finish_job("status", status)
	tab.apply_status(status)
	await _frames()
	_check(tab._results.get_child_count() == 3, "One row per target built")
	var mac := tab._results.get_node("macos").get_node("Line") as Label
	_check(mac.text == "57.8 MB · starts · nothing to report.", "macOS row: " + mac.text)
	var linux := tab._results.get_node("linux").get_node("Line") as Label
	_check(linux.text == "Godot couldn't export it.", "A failed build says why")
	_check(tab._changes.visible and "Web export preset" in tab._changes.text, "Changes shown")
	_check(not tab._templates.visible and not tab._build_button.disabled, "Build is ready again")
	(tab._checks["web"] as CheckBox).button_pressed = false
	(tab._checks["windows"] as CheckBox).button_pressed = false
	(tab._checks["macos"] as CheckBox).button_pressed = false
	(tab._checks["linux"] as CheckBox).button_pressed = false
	_check(tab._build_button.disabled, "No target, no Build")
	(tab._checks["web"] as CheckBox).button_pressed = true
	_check(not tab._publish_button.disabled, "Built games can be published")
	_check("Web and macOS" in tab._publish_note.text, "The note names what would be pushed")


func _check_publish(tab: TAB) -> void:
	tab._publish_button.pressed.emit()
	await _frames()
	var dialog := tab.publish_dialog()
	_check(dialog.visible, "Publish opens a confirmation")
	_check(dialog._checks.size() == 2, "Only built targets can be pushed")
	_check(dialog.get_ok_button().disabled, "Publish is off without a page name")
	dialog._target.text = "moss studio/run"
	dialog._target.text_changed.emit(dialog._target.text)
	_check(dialog.get_ok_button().disabled, "A page name must be user/game")
	dialog._target.text = "moss-studio/moss-run"
	dialog._target.text_changed.emit(dialog._target.text)
	_check(
		dialog.summary_text() == "Push Web and macOS to itch.io as moss-studio/moss-run.",
		"The confirmation names the page and channels: " + dialog.summary_text()
	)
	_check(dialog._note.visible and "BUTLER_API_KEY" in dialog._note.text, "Sign-in is explained")
	_check(dialog.get_ok_button().disabled, "Publish is off until the user is signed in")
	_check(dialog._install.visible and not dialog._install.button_pressed, "Install asks first")
	dialog._dry_timer.timeout.emit()
	var dry: Array = tab.commands[-1]
	_check("--dry-run" in dry and "moss-studio/moss-run" in dry, "The dry run shows the plan")
	tab.finish_job(
		"dry-run",
		{"steps": [{"channel": "web", "file": "share/builds/moss-run-web.zip", "size": 10_326_066}]}
	)
	_check(
		dialog._plan.text == "moss-run-web.zip → moss-studio/moss-run:web (9.8 MB)",
		"Each push is listed: " + dialog._plan.text
	)
	var itch := {"butler": null, "version": "15.24.0", "pinned": true, "signed_in": "butler login"}
	dialog.open(tab, ["web", "macos"], itch)
	dialog._target.text = "moss-studio/moss-run"
	dialog._target.text_changed.emit(dialog._target.text)
	_check(dialog.get_ok_button().disabled, "No butler and no consent: Publish stays off")
	dialog._install.button_pressed = true
	_check(not dialog.get_ok_button().disabled, "With consent to install, Publish is on")
	var before := tab.commands.size()
	dialog.get_ok_button().pressed.emit()
	await _frames()
	_check(tab.commands.size() == before + 1, "One publish command")
	var push: Array = tab.commands[-1]
	_check(
		"--install-butler" in push and "--yes" in push and "--dry-run" not in push,
		"The click installs butler and pushes: " + str(push)
	)
	tab.finish_job("itch", {"ok": false, "error": "butler: not authorized"})
	_check(dialog.visible and dialog._result.text == "butler: not authorized", "Failures stay")
	dialog.apply_result({"ok": true})
	_check(not dialog.visible, "Success closes the dialog")


func _check_real_cli() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var prefix := PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	var tab: TAB = TAB.new()
	tab.cli_prefix = prefix
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	tab.refresh()
	var answered := func() -> bool: return not tab._status.is_empty() or tab._load_error.visible
	_check(await _until(answered, 60.0), "The real status answers")
	_check(not tab._load_error.visible, "Status read: " + tab._load_error.text)
	_check(tab._page_button.text == "Draft page text", "No page yet: offer a draft")
	tab._page_button.pressed.emit()
	var drafted := func() -> bool: return tab._page_button.text == "Edit"
	_check(await _until(drafted, 60.0), "Draft page text writes the page")
	_check(FileAccess.file_exists("res://share/page.md"), "share/page.md exists")
	_check("Native widget checks" in tab._page_text.text, "The draft names the game")
	tab.queue_free()
	await _frames()
