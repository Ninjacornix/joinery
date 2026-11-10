extends SceneTree
## Checks the game core on whatever starting point the project uses: menus by
## keyboard and mouse, pause, settings on disk, sessions, spawning, actions,
## and saving and restoring world state.
## Run: godot --headless --fixed-fps 60 --script res://tests/support/core_test.gd
##        -- --settings-path=<file>

const PROBE := "res://tests/support/probe.gd"
const NO_PAUSE_SESSION := """extends GameSession

func can_pause() -> bool:
	return false
"""

## Restarts through the session, as online play does: it either takes care
## of the restart (calling back into Game) or leaves it to Game.
const ROUTING_SESSION := """extends GameSession

var asked: int = 0
var handle: bool = true


func restart_level() -> bool:
	asked += 1
	if handle:
		Game.restart_level()
	return handle
"""

var play: PlayHarness


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	play = PlayHarness.new(self, "core")
	await play.boot(false)
	await _menus_by_keyboard()
	await _settings_persist()
	await _start_by_mouse()
	await _pause_and_sessions()
	await _spawn_actions_and_state()
	await _players_and_pending_state()
	await _restart_through_session()
	await _restart_and_leave()
	play.finish()


func _menus_by_keyboard() -> void:
	var menus: Node = play.menus
	var ids: Array[String] = _ids(menus.entries(&"main"))
	play.expect(ids == ["start", "settings", "quit"], "core main entries %s" % [ids])
	ids = _ids(menus.entries(&"pause"))
	play.expect(
		ids == ["resume", "restart", "settings", "main_menu"], "core pause entries %s" % [ids]
	)
	var calls: Array[int] = [0]
	menus.add_entry(&"main", &"extra", "Extra", func() -> void: calls[0] += 1, 50)
	menus.add_entry(
		&"main",
		&"locked",
		"Locked",
		func() -> void: calls[0] += 100,
		60,
		func() -> bool: return false
	)
	ids = _ids(menus.entries(&"main"))
	play.expect(
		ids == ["start", "extra", "locked", "settings", "quit"], "entries sort by order %s" % [ids]
	)
	menus.open_main()
	play.expect(play.focused_text() == "Play", "the first entry takes focus")
	var locked: Button = menus.current_page().find_child("locked", true, false) as Button
	play.expect(locked != null and locked.disabled, "an entry whose enabled() is false is disabled")
	play.expect(await play.focus_button("Extra"), "ui_down reaches a feature entry")
	await play.tap(&"ui_accept")
	play.expect(calls[0] == 1, "ui_accept runs the focused entry")
	await play.tap(&"ui_down")
	play.expect(play.focused_text() == "Settings", "focus skips the disabled entry")
	menus.remove_entry(&"main", &"extra")
	menus.remove_entry(&"main", &"locked")
	play.expect(_ids(menus.entries(&"main")).size() == 3, "remove_entry drops entries")

	menus.add_settings_page(&"probe", "Probe page", _probe_page, 40)
	menus.open_main()
	play.expect_min_text(menus.current_page(), "main menu")
	play.expect(await play.focus_button("Settings"), "Settings is reachable")
	await play.tap(&"ui_accept")
	play.expect(menus.current_menu() == &"main", "settings stacks over the main menu")
	play.expect_min_text(menus.current_page(), "settings list")
	var pages: Array[String] = []
	for page: Dictionary in menus.settings_pages():
		pages.append(str(page["label"]))
	play.expect(
		pages == ["Video", "Audio", "Accessibility", "Probe page"], "settings pages %s" % [pages]
	)
	for label: String in pages:
		play.expect(await play.focus_button(label), "%s is reachable" % label)
		var opened: Dictionary = play.watch(play.events.menu_opened)
		await play.tap(&"ui_accept")
		play.expect(play.fire_count(opened) == 1, "%s opens a page" % label)
		await play.wait_watched(opened, 1)
		play.expect(play.focused() != null, "%s page has a focused control" % label)
		play.expect_min_text(menus.current_page(), "%s page" % label)
		await play.tap(&"ui_cancel")
		play.expect(play.focused_text() == label, "Back returns focus to %s" % label)
	await play.tap(&"ui_cancel")
	play.expect(menus.current_menu() == &"main", "ui_cancel returns to the main menu")
	play.expect(play.focused_text() == "Settings", "focus returns to Settings")
	await play.tap(&"ui_cancel")
	play.expect(menus.is_open(), "ui_cancel never closes the main menu")
	menus.remove_settings_page(&"probe")


func _settings_persist() -> void:
	var settings: Node = play.settings
	var path: String = str(settings.path)
	var expected_path: String = ""
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--settings-path="):
			expected_path = argument.substr("--settings-path=".length())
	if not expected_path.is_empty():
		play.expect(path == expected_path, "Settings uses --settings-path (%s)" % path)
	var changes: Dictionary = play.watch(settings.changed)
	settings.set_value("audio", "music", 0.5)
	play.expect(play.fire_count(changes) == 1, "set_value emits changed")
	await play.wait_watched(changes, 1)
	var music: int = AudioServer.get_bus_index(&"Music")
	play.expect(music > 0, "the Music bus exists")
	play.expect_near(
		AudioServer.get_bus_volume_db(music), linear_to_db(0.5), 0.01, "Music bus volume"
	)
	settings.set_value("audio", "sfx", 0.0)
	play.expect(AudioServer.is_bus_mute(AudioServer.get_bus_index(&"SFX")), "0 mutes SFX")
	settings.set_value("access", "reduced_motion", true)
	settings.set_value("custom", "number", 7)
	var disk: ConfigFile = ConfigFile.new()
	play.expect(disk.load(path) == OK, "the settings file is written")
	play.expect_near(float(disk.get_value("audio", "music", -1.0)), 0.5, 0.001, "music saved")
	play.expect(disk.get_value("access", "reduced_motion", false) == true, "toggle saved")
	settings.reload()
	play.expect(int(settings.get_value("custom", "number")) == 7, "values survive reload")
	play.expect(settings.reduced_motion(), "reduced_motion() reads the setting")
	play.expect(settings.get_value("nothing", "here", 3) == 3, "get_value falls back")
	if not settings.has_value("audio", "master"):
		play.expect_near(float(settings.get_value("audio", "master")), 1.0, 0.001, "default master")
	settings.set_value("audio", "sfx", 1.0)
	settings.set_value("access", "reduced_motion", false)

	# The Video page toggles write straight through.
	play.menus.open_settings()
	play.expect(await play.focus_button("Video"), "Video is reachable")
	await play.tap(&"ui_accept")
	var focus: Control = play.focused()
	play.expect(focus is CheckButton, "the Video page focuses a toggle")
	var was: bool = bool(settings.get_value("video", "fullscreen"))
	await play.tap(&"ui_accept")
	play.expect(
		bool(settings.get_value("video", "fullscreen")) == (not was), "ui_accept flips full screen"
	)
	await play.tap(&"ui_accept")
	play.menus.close_all()
	play.expect(not play.menus.is_open(), "close_all closes every page")


func _start_by_mouse() -> void:
	play.menus.open_main()
	await play.process_frames(2)
	var start: Button = play.menus.current_page().find_child("start", true, false) as Button
	play.expect(start != null, "the Play button exists")
	var started: Dictionary = play.watch(play.events.level_started)
	if start != null:
		await play.click_control(start)
	await play.wait_watched(started, 120)
	play.expect(play.game.current_level() != null, "clicking Play starts the level")
	play.expect(not play.menus.is_open(), "starting closes the menu")


func _pause_and_sessions() -> void:
	var game: Node = play.game
	await play.physics_frames(30)
	var t0: float = float(game.level_time())
	play.expect(t0 > 0.3, "level time advances (%s)" % t0)
	var changes: Dictionary = play.watch(play.events.paused_changed)
	await play.tap(&"pause")
	play.expect(paused, "pause pauses the tree")
	play.expect(play.fire_count(changes) == 1, "paused_changed fires")
	var t1: float = float(game.level_time())
	await play.physics_frames(20)
	play.expect_near(float(game.level_time()), t1, 0.0001, "level time stops while paused")
	play.expect_min_text(play.menus.current_page(), "pause menu")
	await play.tap(&"pause")
	play.expect(not paused and not play.menus.is_open(), "pause again resumes")
	await play.tap(&"pause")
	await play.tap(&"ui_cancel")
	play.expect(not paused and not play.menus.is_open(), "ui_cancel resumes")
	await play.wait_watched(changes, 1)

	var script: GDScript = GDScript.new()
	script.source_code = NO_PAUSE_SESSION
	play.expect(script.reload() == OK, "a custom session compiles")
	var session: GameSession = script.new() as GameSession
	game.set_session(session)
	play.expect(game.session() == session, "set_session swaps the session")
	await play.tap(&"pause")
	play.expect(play.menus.current_menu() == &"pause", "the pause menu opens online too")
	play.expect(not paused, "a session that cannot pause keeps the game running")
	await play.tap(&"ui_cancel")
	play.expect(not play.menus.is_open(), "the menu closes")
	game.set_session(GameSession.new())
	play.expect(not game.session().is_online(), "the local session is offline")
	play.expect(game.peer_ids() == PackedInt32Array([1]), "offline peer ids are [1]")
	play.expect(game.actor_id() == 1, "the offline actor is peer 1")


func _spawn_actions_and_state() -> void:
	var game: Node = play.game
	var level: Node = game.current_level()
	var parent: Node = level.get_node_or_null(^"World")
	if parent == null:
		parent = level
	var packed: PackedScene = _probe_scene()
	var a: Node2D = game.spawn(packed, parent, {"save_id": "probe_a", "position": Vector2(10, 20)})
	var b: Node2D = game.spawn(packed, parent, {"save_id": "probe_b", "unknown": 5})
	play.expect(a.is_inside_tree() and a.get_parent() == parent, "spawn adds under the parent")
	play.expect(a.position == Vector2(10, 20), "spawn data sets properties")
	play.expect(str(a.get(&"save_id")) == "probe_a", "spawn data sets the save id")
	var changed: Dictionary = play.watch(play.events.world_changed)
	game.request_action(a, &"bump", [3])
	game.request_action(b, &"bump", [1])
	play.expect(int(a.get(&"count")) == 3, "request_action runs the action locally")
	play.expect(int(a.get(&"last_actor")) == 1, "actor_id is 1 during a local action")
	play.expect(play.fire_count(changed) == 2, "world_changed fires per action")
	await play.wait_watched(changed, 1)
	game.perform_action(5, b, &"bump", [1])
	play.expect(int(b.get(&"last_actor")) == 5, "perform_action reports its peer")
	play.expect(game.actor_id() == 1, "the actor resets after an action")

	var state: Dictionary = game.capture_state()
	play.expect(state.has("probe_a") and state.has("probe_b"), "capture_state finds saveables")
	a.set(&"count", 99)
	a.position = Vector2.ZERO
	b.set(&"count", 0)
	game.apply_state(state)
	play.expect(int(a.get(&"count")) == 3, "apply_state restores state")
	play.expect(a.position == Vector2(10, 20), "apply_state restores position")
	play.expect(int(b.get(&"count")) == 2, "apply_state restores every node")
	var round_trip: Variant = JSON.to_native(JSON.from_native(state))
	game.apply_state(round_trip)
	play.expect(a.position == Vector2(10, 20), "state survives JSON.from_native")
	a.queue_free()
	b.queue_free()

	play.menus.toast("Saved", 0.1)
	await play.process_frames(2)
	var toasts: Node = play.menus.find_child("Toasts", true, false)
	play.expect(toasts != null and toasts.get_child_count() == 1, "toast shows a message")
	await play.physics_frames(30)
	play.expect(toasts != null and toasts.get_child_count() == 0, "the toast goes away")


func _players_and_pending_state() -> void:
	var game: Node = play.game
	var packed: PackedScene = _probe_scene()
	var path: String = "user://core_test_player.tscn"
	play.expect(ResourceSaver.save(packed, path) == OK, "a probe player scene saves")
	var previous: Variant = ProjectSettings.get_setting("game/hooks/player_scene", "")
	ProjectSettings.set_setting("game/hooks/player_scene", path)
	game.pending_state = {"probe": {"count": 7, "position": Vector2(1, 2)}}
	var spawned: Dictionary = play.watch(play.events.player_spawned)
	var started: Dictionary = play.watch(play.events.level_started)
	game.restart_level()
	await play.wait_watched(started, 120)
	var args: Array = await play.wait_watched(spawned, 1)
	var players: Array = game.players()
	play.expect(players.size() == 1, "one local player spawns (%d)" % players.size())
	if players.size() == 1:
		var player: Node2D = players[0] as Node2D
		play.expect(args.size() == 2 and args[0] == player and args[1] == 1, "player_spawned")
		play.expect(player.name == &"1", "the local player is named after peer 1")
		play.expect(game.local_player() == player, "local_player finds it")
		play.expect(int(player.get(&"count")) == 7, "pending_state is applied on level start")
		play.expect(game.pending_state.is_empty(), "pending_state is used once")
	ProjectSettings.set_setting("game/hooks/player_scene", previous)
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))


func _restart_through_session() -> void:
	var game: Node = play.game
	var script: GDScript = GDScript.new()
	script.source_code = ROUTING_SESSION
	play.expect(script.reload() == OK, "a routing session compiles")
	var session: GameSession = script.new() as GameSession
	game.set_session(session)
	var started: Dictionary = play.watch(play.events.level_started)
	game.restart_level()
	await play.wait_watched(started, 120)
	play.expect(int(session.get(&"asked")) == 1, "restart asks the session once")
	session.set(&"handle", false)
	started = play.watch(play.events.level_started)
	game.restart_level()
	await play.wait_watched(started, 120)
	play.expect(int(session.get(&"asked")) == 2, "a session that declines is asked")
	game.set_session(null)


func _restart_and_leave() -> void:
	var started: Dictionary = play.watch(play.events.level_started)
	await play.tap(&"pause")
	play.expect(await play.focus_button("Restart"), "Restart is reachable")
	await play.tap(&"ui_accept")
	await play.wait_watched(started, 120)
	play.expect(not paused, "restart unpauses")
	play.expect(play.game.players().is_empty(), "restart rebuilds the level")
	var to_menu: Dictionary = play.watch(play.events.menu_opened)
	play.game.quit_to_menu()
	await play.wait_watched(to_menu, 120)
	play.expect(play.menus.current_menu() == &"main", "quit_to_menu shows the main menu")
	play.expect(play.game.current_level() == null, "no level after quitting to the menu")


func _probe_page() -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	column.add_child(UiStyle.row("Probe value", UiStyle.slider(0.5)))
	return column


func _probe_scene() -> PackedScene:
	var node: Node2D = Node2D.new()
	node.name = "Probe"
	node.set_script(load(PROBE))
	var packed: PackedScene = PackedScene.new()
	packed.pack(node)
	node.free()
	return packed


func _ids(entries: Array) -> Array[String]:
	var ids: Array[String] = []
	for entry: Dictionary in entries:
		ids.append(str(entry["id"]))
	return ids
