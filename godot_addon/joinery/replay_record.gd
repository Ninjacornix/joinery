@tool
extends RefCounted
## "/record": Record this play. Runs the main scene with `--fixed-fps <physics rate>` and
## `-- --joinery-record --joinery-pace`, so the game core writes the session to
## `user://joinery_replays/` as it goes (one physics step a frame, held to real time) and
## it replays frame for frame. Godot's run arguments (`editor/run/main_run_args`) carry
## them for this one run and are put back on the next frame; nothing is saved to the
## project. `joinery replay add <file>` then keeps the recording as a test (ADR 0059).

const SETTING := "editor/run/main_run_args"
const TICKS := "physics/common/physics_ticks_per_second"
const RECORD_ARGS: Array[String] = ["--joinery-record", "--joinery-pace"]


## [param original] run arguments with a fixed frame rate before `--` and the recording
## arguments after it (arguments the user set are kept).
static func run_args(original: String, ticks: int) -> String:
	var engine: PackedStringArray = []
	var user: PackedStringArray = []
	var after := false
	for word: String in original.strip_edges().split(" ", false):
		if after:
			user.append(word)
		elif word == "--":
			after = true
		else:
			engine.append(word)
	if not "--fixed-fps" in engine:
		engine.append_array(["--fixed-fps", str(maxi(ticks, 1))])
	for word: String in RECORD_ARGS:
		if not word in user:
			user.append(word)
	return " ".join(engine + PackedStringArray(["--"]) + user)


## Plays the main scene once with recording on.
static func record_play(tree: SceneTree) -> void:
	var original := str(ProjectSettings.get_setting(SETTING, ""))
	var ticks := int(ProjectSettings.get_setting(TICKS, 60))
	ProjectSettings.set_setting(SETTING, run_args(original, ticks))
	# The run dialog reads the setting when it hears it changed, a frame later.
	await tree.process_frame
	EditorInterface.play_main_scene()
	await tree.process_frame
	ProjectSettings.set_setting(SETTING, original)
