extends SceneTree
## A scripted play session for the replay tests (copied into a test project as
## res://tests/replay_session.gd): it boots like every play test, then walks, jumps,
## looks around, pauses and resumes through the game's own input map. Run it with
## `-- --joinery-record=<file>` and the core records it; replaying the file must end
## in the same state.

var play: PlayHarness


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	play = PlayHarness.new(self, "session")
	await play.boot(true)
	var moves: Array[StringName] = []
	for action: StringName in [&"move_right", &"move_forward", &"move_up", &"move_left"]:
		if InputMap.has_action(action):
			moves.append(action)
	await play.physics_frames(10)
	for round_index: int in 3:
		for move: StringName in moves:
			play.press(move)
			await play.physics_frames(12)
			if InputMap.has_action(&"jump"):
				await play.tap(&"jump")
			await play.physics_frames(12)
			play.release(move)
			play.mouse_motion(Vector2(24.0 + round_index, 4.0))
			await play.physics_frames(3)
	await play.tap(&"pause")
	await play.physics_frames(20)
	await play.tap(&"pause")
	await play.physics_frames(40)
	play.finish()
