extends SceneTree
## Controller feature test: injects simulated gamepad, keyboard and mouse
## events through Godot's input pipeline and checks bindings, prompts, menu
## navigation, rumble, disconnect handling and the Controls page.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/controller/controller_test.gd
##        -- --autostart --settings-path=<file> [--controller-phase=verify]
##
## The default phase rebinds and saves; `verify` runs in a new process with the
## same settings file and checks the saved bindings came back, then resets.
## This file is compiled before the autoloads exist, so the checks live in
## controller_checks.gd, loaded once the game runs.

const TEST_NAME := "controller"
const CHECKS := "res://tests/features/controller/controller_checks.gd"
const PHASE_ARG := "--controller-phase="


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var phase: String = "play"
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(PHASE_ARG):
			phase = argument.substr(PHASE_ARG.length())
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the controller checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run(phase)
	h.finish()
