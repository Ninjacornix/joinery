extends RefCounted
## The visual suite's teamwork screens (ADR 0071): the Timeline shared with teammates (Pull and
## Push, a pull's result, teammates' entries), a teammate's version waiting in Review beside
## yours with the brief they changed, and Settings → Project's sharing rows. Copied beside
## visual_editor_test.gd by run_visual_suite.py; [param suite] is that test, whose `_shot`,
## `_check`, `_frames` and `_plugin` these use.


static func screens(suite: Variant) -> void:
	await _timeline(suite)
	await _review(suite)
	await _settings(suite)


static func _taller(tab: Control, by: int) -> Array:
	var split: SplitContainer
	var walker: Node = tab
	while walker != null and split == null:
		walker = walker.get_parent()
		split = walker as SplitContainer
	var offset := split.split_offset if split != null else 0
	if split != null:
		split.split_offset = offset - by
	return [split, offset]


static func _bottom(tab: Control) -> Control:
	var dock := tab.get_parent() as Control
	return dock.get_parent() as Control if dock != null else tab


static func _timeline(suite: Variant) -> void:
	var tab: Control
	for node: Node in EditorInterface.get_base_control().find_children("Timeline", "", true, false):
		if node.get_script() == load("res://addons/joinery/timeline_tab.gd"):
			tab = node as Control
	suite._check(tab != null, "The Timeline tab exists")
	if tab == null:
		return
	var plugin: EditorPlugin = suite._plugin
	tab.set("cli_enabled", false)
	tab.call("show_list")
	plugin.call("open_page", tab)
	var now := Time.get_unix_time_from_system()
	var rows := [
		["Bring in Ana's changes", "Ben Okafor", 60, 4, false, false],
		["Darken the door wood", "Ana Lima", 900, 2, true, true],
		["Add fog to the harbour", "Ana Lima", 1800, 1, true, true],
		["Model a brass harbour lamp", "Ben Okafor", 5000, 3, false, false],
		["Start Harbour Watch", "Ben Okafor", 400000, 48, false, false],
	]
	var entries: Array = []
	for index: int in rows.size():
		var row: Array = rows[index]
		(
			entries
			. append(
				{
					"commit": "%040x" % (index + 1),
					"subject": row[0],
					"author": row[1],
					"time": now - float(row[2]),
					"files": row[3],
					"teammate": row[4],
					"pulled": row[5],
				}
			)
		)
	var team := {"connected": true, "ahead": 1, "behind": 0, "conflicts": 1}
	tab.call(
		"apply_listing",
		{"state": "ready", "auto_commit": true, "entries": entries, "more": false, "team": team}
	)
	var result := "Pulled 2 changes from Ana. You both changed door_wood.png: yours stays, "
	tab.call("show_sync_result", result + "theirs waits in Review.", false, true)
	var taller := _taller(tab, 300)
	await suite.get_tree().create_timer(0.3).timeout
	await suite._shot("timeline-team", [_bottom(tab)])
	if taller[0] != null:
		(taller[0] as SplitContainer).split_offset = int(taller[1])
	tab.call("show_sync_result", "")
	plugin.hide_bottom_panel()
	await suite._frames()


static func _review(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var tab: Control = plugin.get("_review_tab")
	suite._check(tab != null, "The Review tab exists")
	if tab == null:
		return
	tab.set("cli_enabled", false)
	tab.call("show_list")
	plugin.make_bottom_panel_item_visible(tab)
	var cache := OS.get_cache_dir().path_join("joinery-visual")
	DirAccess.make_dir_recursive_absolute(cache)
	var mine := cache.path_join("team-door-mine.png")
	var theirs := cache.path_join("team-door-theirs.png")
	_planks(Color("c49a6c")).save_png(mine)
	_planks(Color("5e3b1f")).save_png(theirs)
	var id := "c".repeat(32)
	var conflict := {
		"id": id,
		"state": "review",
		"summary": "Ana's door_wood.png or yours",
		"created": Time.get_unix_time_from_system() - 40,
		"tools": ["joinery"],
		"file_count": 1,
		"files": [{"path": "art/textures/door_wood.png", "change": "modified"}],
		"team":
		{
			"kind": "conflict",
			"theirs": "Ana Lima",
			"files":
			[
				{
					"path": "art/textures/door_wood.png",
					"kind": "binary",
					"mine_picture": mine,
					"theirs_picture": theirs,
					"mine_size": 48200,
					"theirs_size": 47900,
				}
			],
		},
	}
	var listing := {
		"transactions": [conflict],
		"art_bible": {"state": "approved", "by": "you"},
		"team_documents":
		[{"path": "design/brief.md", "label": "game brief", "author": "Ana Lima"}],
	}
	tab.call("apply_listing", listing)
	tab.call("_on_open", id, false)
	var taller := _taller(tab, 360)
	await suite.get_tree().create_timer(0.3).timeout
	await suite._shot("review-team", [_bottom(tab)])
	if taller[0] != null:
		(taller[0] as SplitContainer).split_offset = int(taller[1])
	tab.call("_on_open", id, false)
	tab.call("apply_listing", {"transactions": [], "art_bible": {"state": "missing"}})
	plugin.hide_bottom_panel()
	await suite._frames()


static func _settings(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var dock: Control = plugin.get("_dock")
	var settings: Node = dock.get("_settings")
	if settings == null or not settings.has_method("set_open"):
		return
	var found := settings.find_children("TeamSetting", "", true, false)
	suite._check(not found.is_empty(), "Settings show the project's sharing")
	if found.is_empty():
		return
	var team: Control = found[0]
	team.set("cli_enabled", false)
	settings.call("set_open", true)
	await suite._frames()
	(
		team
		. call(
			"apply_status",
			{
				"state": "ready",
				"connected": true,
				"remote": "git@example.com:harbour-team/harbour-watch.git",
				"teammates": [{"name": "Ana Lima"}, {"name": "Cy Park"}],
			}
		)
	)
	var scroller: Node = team.get_parent()
	while scroller != null and not scroller is ScrollContainer:
		scroller = scroller.get_parent()
	if scroller != null:
		(scroller as ScrollContainer).scroll_vertical = 100000
	await suite.get_tree().create_timer(0.3).timeout
	await suite._shot("settings-team", [dock])
	settings.call("set_open", false)
	await suite._frames()


## A small plank texture in [param wood], so the two versions read as pictures.
static func _planks(wood: Color) -> Image:
	var image := Image.create(96, 96, false, Image.FORMAT_RGBA8)
	image.fill(wood)
	for row: int in range(0, 96, 24):
		image.fill_rect(Rect2i(0, row, 96, 2), wood.darkened(0.35))
	for x: int in [30, 70]:
		image.fill_rect(Rect2i(x, 0, 2, 24), wood.darkened(0.25))
		image.fill_rect(Rect2i(x - 20, 48, 2, 24), wood.darkened(0.25))
	return image
