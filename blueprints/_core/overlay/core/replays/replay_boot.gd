extends SceneTree
## Starts a replay the way a test script or the playtest bot started the recorded
## session (the replay header's `boot` is "script"): it loads the start scene itself,
## with the same autostart, so frames line up with the recording.
##
## Run: godot --headless --fixed-fps 60 --script res://core/replays/replay_boot.gd
##        -- --joinery-replay=<file>
## A session the engine started (a normal run) is replayed without `--script`.


func _initialize() -> void:
	_boot.call_deferred()


func _boot() -> void:
	var replays: Node = root.get_node_or_null(^"Replays")
	if replays == null:
		print("The game has no Replays autoload; it can't be replayed.")
		quit(2)
		return
	var header: Dictionary = replays.call(&"header")
	var start: Dictionary = header.get("start", {})
	var scene: String = str(start.get("scene", ""))
	if scene.is_empty():
		scene = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	var game: Node = root.get_node_or_null(^"Game")
	if game != null:
		game.set(&"autostart", bool(start.get("autostart", false)))
	if change_scene_to_file(scene) != OK:
		print("The start scene %s could not be loaded." % scene)
		quit(2)
