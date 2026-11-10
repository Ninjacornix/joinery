extends SceneTree
## Play test for the 2D platformer: injects input through the core harness
## and checks what the player would see happen.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##      -- --autostart --settings-path=<file>
##
## This file is compiled before the autoloads exist, so it names no game
## script. The checks live in play_checks.gd, loaded once the game runs.

const TEST_NAME := "platformer-2d"
const CHECKS := "res://tests/game/play_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the play checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run()
	h.finish()
