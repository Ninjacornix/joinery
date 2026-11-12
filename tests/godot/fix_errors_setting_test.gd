extends SceneTree
## The "Fix console errors automatically" switch in Journeyman settings: unknown until the
## project answers, off by default (Joinery asks first), and a flip saved through the real
## `joinery fix-errors` command into this disposable project's joinery.toml.

const SETTING := preload("res://addons/joinery/fix_errors_setting.gd")

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		if bool(condition.call()):
			return true
		await process_frame
	return bool(condition.call())


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_host = Control.new()
	root.add_child(_host)
	_check_states()
	await _check_real_cli()
	_host.queue_free()
	await process_frame
	print("fix errors setting: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_states() -> void:
	var setting: SETTING = SETTING.new()
	_check(setting.row.switch_button.disabled, "Unknown until the project answers")
	_check(setting.row.title.text == "Fix console errors automatically", "Its name")
	_check(setting.row.description.text == SETTING.CAPTION, "Its caption says what off does")
	setting.apply_status({"fix_errors": "auto", "auto": true})
	_check(setting.row.is_on() and not setting.row.switch_button.disabled, "On, changeable")
	setting.apply_status({"fix_errors": "ask", "auto": false})
	_check(not setting.row.is_on() and not setting.row.reason.visible, "Ask: off")
	setting.apply_status({"fix_errors": "off", "auto": false})
	_check(setting.row.reason.visible and setting.row.reason.text == SETTING.OFF, "Off says so")
	setting.apply_status({"error": "joinery.toml can't be read"})
	_check(setting.row.reason.visible, "A failure says why")
	setting.free()


func _toml() -> String:
	return FileAccess.get_file_as_string("res://joinery.toml")


func _check_real_cli() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var setting: SETTING = SETTING.new()
	setting.cli_prefix = PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	_host.add_child(setting)
	var answered := func() -> bool: return not setting.row.switch_button.disabled
	_check(await _until(answered), "The real command answers")
	_check(not setting.row.is_on(), "Off (ask first) by default")
	setting.row.switch_button.button_pressed = true
	var saved := func() -> bool: return _toml().contains('fix_errors = "auto"')
	_check(await _until(saved), "On saves auto in joinery.toml")
	_check(await _until(answered), "And the switch is free again")
	setting.row.switch_button.button_pressed = false
	var back := func() -> bool: return _toml().contains('fix_errors = "ask"')
	_check(await _until(back), "Off saves ask")
	_check(_toml().contains("[tools]"), "The rest of joinery.toml stays")
	setting.queue_free()
	await process_frame
