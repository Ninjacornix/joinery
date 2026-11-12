extends SceneTree
## Dialogue feature test: a .dlg file plays in the running game.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/dialogue/dialogue_test.gd
##        -- --autostart --settings-path=<file>
##
## This file is compiled before the autoloads exist, so the checks live in
## dialogue_checks.gd, loaded once the game runs.

const TEST_NAME := "dialogue"
const CHECKS := "res://tests/features/dialogue/dialogue_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the dialogue checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run()
	h.finish()
