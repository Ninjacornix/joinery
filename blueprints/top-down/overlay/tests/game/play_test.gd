extends SceneTree
## Play test for the top-down arena.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##      -- --autostart --settings-path=user://test_settings.cfg
##
## This file is compiled before the autoloads exist, so the checks live in
## `play_checks.gd`, loaded here once they do.

const CHECKS_PATH := "res://tests/game/play_checks.gd"

var _checks: RefCounted


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var script: GDScript = load(CHECKS_PATH) as GDScript
	if script == null:
		print("FAIL: %s could not be loaded" % CHECKS_PATH)
		quit(1)
		return
	_checks = script.new() as RefCounted
	_checks.call(&"run", self)
