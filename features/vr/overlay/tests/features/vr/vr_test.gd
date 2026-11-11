extends SceneTree
## Headless checks for the VR feature, on whichever 3D starting point it was
## added to. Without a headset (always, here) the game must start on the
## desktop; `--vr-simulate` carries the rig without one, so locomotion,
## pointer, grab and the menu panel run for real.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/features/vr/vr_test.gd
##        -- --autostart --settings-path=<file> [--vr-simulate]
##        [--vr-phase=write|read]   (settings kept between two processes)

var h: PlayHarness


func _initialize() -> void:
	h = PlayHarness.new(self, "vr")
	_run.call_deferred()


func _run() -> void:
	var checks: RefCounted = (load("res://tests/features/vr/vr_checks.gd") as GDScript).new()
	await checks.call(&"run", h, self)
	h.finish()
