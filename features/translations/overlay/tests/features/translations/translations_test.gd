extends SceneTree
## Translations feature test: language list, switching language (menus update
## at once), the pseudo-locale, the Language settings page and that the choice
## is saved.
##
## Run: godot --headless --fixed-fps 60
##        --script res://tests/features/translations/translations_test.gd
##        -- --settings-path=<file> [--translations-phase=verify|pseudo-arg]
##           [--expect-locales=de,it]
##
## The default phase switches languages and leaves the last choice saved;
## `verify` runs in a new process with the same settings file and checks the
## choice came back, then resets it. `pseudo-arg` checks `--pseudo-locale`.
## The checks live in translations_checks.gd, loaded once the autoloads exist.

const TEST_NAME := "translations"
const CHECKS := "res://tests/features/translations/translations_checks.gd"
const PHASE_ARG := "--translations-phase="
const LOCALES_ARG := "--expect-locales="


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var h: PlayHarness = PlayHarness.new(self, TEST_NAME)
	var phase: String = "play"
	var expected: PackedStringArray = PackedStringArray()
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(PHASE_ARG):
			phase = argument.substr(PHASE_ARG.length())
		elif argument.begins_with(LOCALES_ARG):
			expected = argument.substr(LOCALES_ARG.length()).split(",", false)
	var script: GDScript = load(CHECKS) as GDScript
	h.expect(script != null, "the translations checks load")
	if script != null:
		var checks: Variant = script.new(self, h)
		await checks.run(phase, expected)
	h.finish()
