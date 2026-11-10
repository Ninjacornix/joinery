extends SceneTree
## Save-load feature test: changes the game the way playing does, saves,
## restarts, loads and checks the state came back; damaged, newer and foreign
## files are refused; old saves are upgraded by migration steps; checkpoints
## autosave; the Save and Load pages work with the ui_* actions alone; an
## online client may neither save nor load.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/save-load/save_load_test.gd
##        -- --autostart --settings-path=<file> [--save-phase=save|load --saves-dir=<dir>]
##
## Without a phase everything runs in one process. `save` changes the game and
## saves slot 1 into `--saves-dir`; `load`, in a new process with the same
## folder, continues from the main menu and checks the state.
## This file is compiled before the autoloads exist, so the checks live in
## save_load_checks.gd, loaded once the game runs.

const TEST_NAME := "save-load"
const CHECKS := "res://tests/features/save-load/save_load_checks.gd"
const PHASE_ARG := "--save-phase="


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var phase: String = "all"
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(PHASE_ARG):
			phase = argument.substr(PHASE_ARG.length())
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the save-load checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run(phase)
	h.finish()
