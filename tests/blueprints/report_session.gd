extends SceneTree
## A play session that ends in a bug report (copied into a test project as
## res://tests/report_session.gd): walk a little, press F8 (`report_bug`), write a note
## and save. Prints `REPORT <folder>` for the Python side to read the files.

var play: PlayHarness


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	play = PlayHarness.new(self, "report")
	await play.boot(true)
	var menus: Node = play.menus
	var ids: Array = []
	for entry: Dictionary in menus.entries(&"pause"):
		ids.append(String(entry["id"]))
	play.expect("report_bug" in ids, "the pause menu offers Report a bug: %s" % [ids])
	for action: StringName in [&"move_right", &"move_up", &"move_forward"]:
		if InputMap.has_action(action):
			await play.hold(action, 30)
	push_warning("A warning the report's log must carry.")
	await play.physics_frames(10)
	var reports: Node = root.get_node(^"BugReports")
	var saved: Dictionary = play.watch(reports.report_saved)
	await play.tap(&"report_bug")
	var page: Control = menus.current_page()
	play.expect(page != null and page.name == "BugReport", "F8 opens the bug report page")
	play.expect(paused, "the game pauses while the player writes the report")
	var note: TextEdit = page.find_child("Note", true, false) as TextEdit
	play.expect(note != null and note.has_focus(), "the note box has focus")
	note.text = "The coin vanished when I jumped."
	(page.find_child("Save", true, false) as Button).pressed.emit()
	var args: Array = await play.wait_watched(saved, 120)
	if args.size() == 2:
		print("REPORT %s" % args[1])
	play.expect(menus.current_menu() == &"pause", "saving returns to the pause menu")
	play.finish()
