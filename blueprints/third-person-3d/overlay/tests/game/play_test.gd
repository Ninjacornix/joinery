extends SceneTree
## Play test for the third-person arena.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##      -- --autostart --settings-path=user://test_settings.cfg
##
## The checks live in play_checks.gd and are loaded at run time: a script
## given to --script is compiled before the autoloads are registered, so it
## must not preload anything that names them.

const CHECKS := "res://tests/game/play_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var script: GDScript = load(CHECKS) as GDScript
	if script == null:
		print("FAIL: could not load %s" % CHECKS)
		quit(1)
		return
	var checks: RefCounted = script.new(self)
	await checks.call(&"run")
