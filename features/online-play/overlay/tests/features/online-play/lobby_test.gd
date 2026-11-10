extends SceneTree
## One game on its own: the "Play online" page and the switch between a local
## and an online session. Checks the main menu entry, the page (minimum text
## size, keyboard focus), hosting from the page, the host's own lobby line,
## the ready toggle and Start, the online session (no pause, "Leave game"),
## and that leaving returns to a local game. Also the address and argument
## parsing helpers.
##
## Run with --port=<n> --lan-port=<m> --lan-broadcast=127.0.0.1 so hosting
## stays on this machine.

var play: PlayHarness
var net: Node
var lobby: Node


func _initialize() -> void:
	Engine.max_fps = 120
	_run.call_deferred()


func _run() -> void:
	play = PlayHarness.new(self, "online_lobby")
	net = root.get_node(^"Net")
	lobby = root.get_node(^"Lobby")
	_check_parsing()
	await play.boot(false)
	play.expect(not bool(net.call(&"is_online")), "the game starts offline")
	var entry: Dictionary = _entry(&"main", &"online")
	play.expect(not entry.is_empty(), "the main menu offers Play online")
	if entry.is_empty():
		play.finish()
		return
	(entry["action"] as Callable).call()
	await play.process_frames(3)
	var page: Control = play.menus.call(&"current_page") as Control
	play.expect(page != null and page.name == "LobbyPage", "Play online opens the online page")
	if page == null:
		play.finish()
		return
	play.expect_min_text(page, "the online page")
	var host_button: Button = page.find_child("Host", true, false) as Button
	play.expect(play.focused() == host_button, "Host has keyboard focus")
	await play.tap(&"ui_accept")
	play.expect(bool(net.call(&"is_server")), "Host on the page hosts a game")
	await play.process_frames(2)
	var players: Dictionary = lobby.get(&"players")
	play.expect(players.size() == 1 and players.has(1), "the host is in the lobby: %s" % [players])
	var start: Button = page.find_child("Start", true, false) as Button
	var ready_toggle: CheckButton = page.find_child("Ready", true, false) as CheckButton
	play.expect(start.visible and start.disabled, "Start waits until everyone is ready")
	ready_toggle.button_pressed = true
	await play.process_frames(2)
	play.expect(bool(lobby.call(&"is_ready", 1)), "the ready toggle marks the host ready")
	play.expect(not start.disabled, "Start is available once everyone is ready")
	var started: Dictionary = play.watch(play.events.level_started)
	start.pressed.emit()
	await play.wait_watched(started, 600)
	var session: GameSession = play.game.session()
	play.expect(session.is_online() and not session.can_pause(), "the level runs an online session")
	play.expect(not bool(lobby.call(&"can_start")), "Start is gone once the level runs")
	var leave: Dictionary = _entry(&"pause", &"leave")
	play.expect(not leave.is_empty(), "the pause menu offers Leave game")
	if not leave.is_empty():
		var menu: Dictionary = play.watch(play.events.menu_opened)
		(leave["action"] as Callable).call()
		await play.wait_watched(menu, 600)
	play.expect(not bool(net.call(&"is_online")), "Leave game ends the online session")
	play.expect(not play.game.session().is_online(), "the menu runs a local session again")
	play.expect((lobby.get(&"players") as Dictionary).is_empty(), "the lobby is empty after leaving")
	play.expect(_entry(&"pause", &"leave").is_empty(), "Leave game is gone offline")
	play.finish()


func _check_parsing() -> void:
	var parsed: Dictionary = net.call(
		&"parse_args", PackedStringArray(["--host", "--port=7000", "--name=Ann Lee", "plain"])
	)
	play.expect(
		parsed == {"host": "", "port": "7000", "name": "Ann Lee"},
		"user arguments parse: %s" % [parsed]
	)
	var cases: Array = [
		["192.168.1.20:7000", ["192.168.1.20", 7000]],
		["192.168.1.20", ["192.168.1.20", 24565]],
		["[::1]:7001", ["::1", 7001]],
		["  example.lan  ", ["example.lan", 24565]],
	]
	for case: Array in cases:
		var got: Array = net.call(&"split_address", case[0], 24565)
		play.expect(got == case[1], "address %s splits as %s, got %s" % [case[0], case[1], got])


func _entry(menu: StringName, id: StringName) -> Dictionary:
	for entry: Dictionary in play.menus.call(&"entries", menu):
		if entry["id"] == id:
			return entry
	return {}
