extends SceneTree
## Plays the blank starting point with injected input only: menu to level,
## pause and resume, a settings change that persists, back to the menu.
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##        -- --settings-path=<file>


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var play: PlayHarness = PlayHarness.new(self, "blank")
	await play.boot(false)

	# Main menu: Play has focus and starts the level with ui_accept.
	play.expect(play.menus.current_menu() == &"main", "the main menu opens first")
	play.expect(play.focused_text() == "Play", "Play has focus in the main menu")
	play.expect_min_text(play.menus.current_page(), "main menu")
	var started: Dictionary = play.watch(play.events.level_started)
	await play.tap(&"ui_accept")
	await play.wait_watched(started, 120)
	await play.physics_frames(2)
	var level: Node = play.game.current_level()
	play.expect(level != null, "the level is running")
	play.expect(not play.menus.is_open(), "no menu covers the level")
	if level != null:
		var welcome: Label = level.get_node_or_null(^"Welcome") as Label
		play.expect(
			welcome != null and welcome.text == "Your game starts here",
			"the level greets the player"
		)

	# Pause pauses the tree; Resume (focused) carries on.
	await play.tap(&"pause")
	play.expect(paused, "pause pauses the game")
	play.expect(play.menus.current_menu() == &"pause", "the pause menu is open")
	play.expect(play.focused_text() == "Resume", "Resume has focus in the pause menu")
	play.expect_min_text(play.menus.current_page(), "pause menu")
	await play.tap(&"ui_accept")
	play.expect(not paused, "Resume unpauses")
	play.expect(not play.menus.is_open(), "Resume closes the pause menu")

	# Restart builds a fresh level and leaves the game running.
	await play.physics_frames(3)
	await play.tap(&"pause")
	play.expect(await play.focus_button("Restart"), "Restart is reachable with ui_down")
	var restarted: Dictionary = play.watch(play.events.level_started)
	await play.tap(&"ui_accept")
	await play.wait_watched(restarted, 120)
	play.expect(not is_instance_valid(level), "Restart frees the old level")
	play.expect(play.game.current_level() != null, "Restart loads a new level")
	play.expect(not paused and not play.menus.is_open(), "Restart closes the menu and runs")
	play.expect(play.game.capture_state().is_empty(), "the empty level has no saved state")

	# Settings > Audio: lowering the master volume is saved straight away.
	await play.physics_frames(3)
	await play.tap(&"pause")
	play.expect(await play.focus_button("Settings"), "Settings is reachable with ui_down")
	await play.tap(&"ui_accept")
	play.expect(play.focused_text() == "Video", "the settings page opens on its first entry")
	play.expect_min_text(play.menus.current_page(), "settings")
	play.expect(await play.focus_button("Audio"), "Audio is reachable with ui_down")
	await play.tap(&"ui_accept")
	var focus: Control = play.focused()
	play.expect(
		focus != null and focus.name == &"audio_master", "the audio page focuses the master volume"
	)
	play.expect_min_text(play.menus.current_page(), "audio settings")
	var before: float = float(play.settings.get_value("audio", "master"))
	await play.tap(&"ui_left")
	var after: float = float(play.settings.get_value("audio", "master"))
	play.expect_near(after, before - 0.05, 0.001, "ui_left lowers the master volume")
	var saved: ConfigFile = ConfigFile.new()
	play.expect(saved.load(play.settings.path) == OK, "settings are written to disk")
	play.expect_near(
		float(saved.get_value("audio", "master", -1.0)),
		after,
		0.001,
		"the saved master volume matches"
	)
	var bus: int = AudioServer.get_bus_index(&"Master")
	play.expect_near(
		AudioServer.get_bus_volume_db(bus),
		linear_to_db(after),
		0.01,
		"the Master bus follows the setting"
	)

	# Back out with ui_cancel, then leave through Main menu.
	await play.tap(&"ui_cancel")
	play.expect(play.focused_text() == "Audio", "Back returns focus to Audio")
	await play.tap(&"ui_cancel")
	play.expect(play.menus.current_menu() == &"pause", "Back again shows the pause menu")
	play.expect(play.focused_text() == "Settings", "focus returns to Settings")
	play.expect(await play.focus_button("Main menu"), "Main menu is reachable with ui_down")
	var to_menu: Dictionary = play.watch(play.events.menu_opened)
	await play.tap(&"ui_accept")
	await play.wait_watched(to_menu, 120)
	play.expect(play.menus.current_menu() == &"main", "Main menu returns to the main menu")
	play.expect(not paused, "the main menu is not paused")
	play.expect(play.game.current_level() == null, "the level is gone")
	play.expect(play.focused_text() == "Play", "Play has focus again")

	# Put the volume back so a second run starts from the same place.
	play.settings.set_value("audio", "master", before)
	play.finish()
