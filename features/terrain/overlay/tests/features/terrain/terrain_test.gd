extends SceneTree
## Terrain feature test: a seeded terrain builds, collides, carries the player,
## draws its LODs and scatters by mask in the game.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/terrain/terrain_test.gd
##        -- --autostart --settings-path=<file>
##
## This file is compiled before the autoloads exist, so the checks live in
## terrain_checks.gd, loaded once the game runs.

const TEST_NAME := "terrain"
const CHECKS := "res://tests/features/terrain/terrain_checks.gd"


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the terrain checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run()
	h.finish()
