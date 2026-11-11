extends SceneTree
## Accessibility feature test: every option applies in the running game.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/accessibility/accessibility_test.gd
##        -- --autostart --settings-path=<file>
##
## This file is compiled before the autoloads exist, so the checks live in
## accessibility_checks.gd, loaded once the game runs.

const TEST_NAME := "accessibility"
const CHECKS := "res://tests/features/accessibility/accessibility_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the accessibility checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run()
	h.finish()
