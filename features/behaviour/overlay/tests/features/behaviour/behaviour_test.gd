extends SceneTree
## Behaviour feature test: behaviour trees run enemies in the game.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/behaviour/behaviour_test.gd
##        -- --autostart --settings-path=<file>
##
## This file is compiled before the autoloads exist, so the checks live in
## behaviour_checks.gd, loaded once the game runs.

const TEST_NAME := "behaviour"
const CHECKS := "res://tests/features/behaviour/behaviour_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the behaviour checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run()
	h.finish()
