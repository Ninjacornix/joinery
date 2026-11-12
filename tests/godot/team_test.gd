extends SceneTree
## Working together (ADR 0071), headless. First from fixtures: the Timeline's Pull and Push
## (hidden until the project is shared, counts in their words, the next step as the primary
## button, the result under the bar), a pulled entry's dot, the Review tab's teammate version
## ("Use Ana's" and "Keep mine", both pictures when opened), the brief a teammate changed with
## Approve, the art bible row naming who changed it, and Settings → Project's sharing rows. Then
## the real CLI in this disposable project (already under git from timeline_test.gd): connect a
## local bare remote, Push, a teammate's clone commits and pushes, and Pull brings it in with
## the teammate's name on the Timeline.

const TIMELINE := preload("res://addons/joinery/timeline_tab.gd")
const REVIEW := preload("res://addons/joinery/review_tab.gd")
const SETTING := preload("res://addons/joinery/team_setting.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while not condition.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return condition.call()


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-team-test")
	DirAccess.make_dir_recursive_absolute(cache)
	Engine.set_meta("joinery_ui_config", cache.path_join("journeyman.cfg"))
	Engine.remove_meta("joinery_ui_prefs")
	UI_MOTION.set_enabled(false)
	root.size = Vector2i(900, 560)
	_host = Control.new()
	_host.size = Vector2(900, 560)
	root.add_child(_host)
	await _check_timeline()
	await _check_review(cache)
	await _check_setting()
	await _check_real_cli(cache)
	print("team: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _entry(index: int, author: String, pulled: bool) -> Dictionary:
	return {
		"commit": "%040d" % index,
		"subject": "Change %d" % index,
		"author": author,
		"time": Time.get_unix_time_from_system() - index * 60,
		"files": 1,
		"teammate": author != "Ben Okafor",
		"pulled": pulled,
	}


func _check_timeline() -> void:
	var tab: TIMELINE = TIMELINE.new()
	tab.cli_enabled = false
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	var entries := [_entry(0, "Ana Lima", true), _entry(1, "Ben Okafor", false)]
	tab.apply_listing({"state": "ready", "entries": entries, "team": {"connected": false}})
	await _frames()
	_check(not tab._sync.visible, "Not shared: no Pull or Push")
	var team := {"connected": true, "ahead": 0, "behind": 2, "conflicts": 0}
	tab.apply_listing({"state": "ready", "entries": entries, "team": team})
	await _frames()
	_check(tab._sync.visible, "Shared: Pull and Push beside the filter")
	_check(tab._pull.text == "Pull 2" and tab._push.text == "Push", "Counts in the words")
	_check(tab._pull.theme_type_variation == "JoineryPrimaryButton", "Pull is the next step")
	_check(tab._push.theme_type_variation == "JoinerySecondaryButton", "Push waits")
	tab.apply_team({"connected": true, "ahead": 3, "behind": 0, "conflicts": 0})
	_check(tab._push.text == "Push 3", "Three to push")
	_check(tab._push.theme_type_variation == "JoineryPrimaryButton", "Push is the next step")
	tab.apply_team({"connected": true, "ahead": 3, "behind": 0, "conflicts": 1})
	_check(tab._push.theme_type_variation == "JoinerySecondaryButton", "A choice waits first")
	_check(tab._push.tooltip_text.contains("in Review first"), "And says so")
	var first: Control = tab._rows.get_child(0)
	_check(first.get_node_or_null("Pulled") != null, "The last pull's entry has its dot")
	_check(tab._rows.get_child(1).get_node_or_null("Pulled") == null, "Others don't")
	var meta := (first.get_node("Meta") as Label).text
	_check(meta.begins_with("Ana Lima · 1 file"), "A teammate's entry names them: " + meta)
	tab.show_sync_result("Pulled 2 changes from Ana.", false, true)
	await _frames()
	_check(tab._sync_note.visible and tab._sync_review.visible, "The result, with Review")
	tab.show_sync_result("Couldn't reach the shared remote.", true)
	_check(tab._sync_text.theme_type_variation == "JoineryError", "A failure reads as one")
	_check(not tab._sync_review.visible, "No Review without a choice")
	_check(TIMELINE.sync_text("Pull", 0) == "Pull", "Nothing to pull: just the word")
	tab.queue_free()
	await _frames()


func _check_review(cache: String) -> void:
	var mine := cache.path_join("mine.png")
	var theirs := cache.path_join("theirs.png")
	var image := Image.create(16, 16, false, Image.FORMAT_RGBA8)
	image.fill(Color("e6c8a0"))
	image.save_png(mine)
	image.fill(Color("6b4423"))
	image.save_png(theirs)
	var tab: REVIEW = REVIEW.new()
	tab.cli_enabled = false
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	var conflict := {
		"id": "a".repeat(32),
		"state": "review",
		"summary": "Ana's door.png or yours",
		"created": Time.get_unix_time_from_system(),
		"tools": ["joinery"],
		"files": [{"path": "art/door.png", "change": "modified"}],
		"file_count": 1,
		"team":
		{
			"kind": "conflict",
			"theirs": "Ana Lima",
			"files":
			[
				{
					"path": "art/door.png",
					"kind": "binary",
					"mine_picture": mine,
					"theirs_picture": theirs,
					"mine_size": 48200,
					"theirs_size": 47900,
				},
				{
					"path": "audio/creak.wav",
					"kind": "binary",
					"mine_size": 52000,
					"theirs_size": -1
				},
			],
		},
	}
	var listing := {
		"transactions": [conflict],
		"art_bible": {"state": "needs_approval", "by_teammate": "Ana Lima"},
		"team_documents":
		[
			{"path": "art/bible.md", "label": "art bible", "author": "Ana Lima"},
			{"path": "design/brief.md", "label": "game brief", "author": "Ana Lima"},
		],
	}
	tab.apply_listing(listing)
	await _frames()
	var row: Control = tab._waiting_body.get_child(0)
	var accept: Button = row.find_child("Accept", true, false)
	var discard: Button = row.find_child("Discard", true, false)
	_check(accept.text == "Use Ana's" and discard.text == "Keep mine", "The choice in words")
	(row.find_child("Open", true, false) as Button).pressed.emit()
	await _frames()
	row = tab._waiting_body.get_child(0)
	var versions: Node = row.find_child("Versions", true, false)
	_check(versions != null, "Opening it shows both versions")
	if versions != null:
		var pictures: Node = versions.find_child("Pictures", true, false)
		_check(
			pictures != null and pictures.get_child_count() == 2, "Yours and Ana's, side by side"
		)
		var shown: TextureRect = pictures.find_child("Picture", true, false) if pictures else null
		_check(shown != null and shown.texture != null, "Each with its picture")
		var labels := versions.find_children("*", "Label", true, false)
		var words := labels.map(func(label: Node) -> String: return (label as Label).text)
		_check(
			words.has("Yours 52 KB · Ana's deleted"), "A sound: sizes, or deleted: " + str(words)
		)
	var document: Node = tab._waiting_body.find_child("Document", true, false)
	_check(document != null, "The brief Ana changed waits here")
	if document != null:
		var summary := (document.get_node("Summary") as Label).text
		_check(summary == "Ana changed the game brief", "Who changed what: " + summary)
		var approve: Button = document.get_node("Approve")
		approve.pressed.emit()
		_check(approve.disabled and approve.text == "Approving…", "Approve answers at once")
	_check(tab._bible_status.text == "Ana changed it. Approve before new art or audio", "Bible row")
	_check(tab.waiting_count() == 3, "Badge: the choice, the bible and the brief")
	tab.queue_free()
	await _frames()


func _check_setting() -> void:
	var setting: SETTING = SETTING.new()
	setting.cli_enabled = false
	_host.add_child(setting)
	await _frames()
	setting.apply_status({"state": "ready", "connected": false, "teammates": []})
	_check(setting.connect_row.visible and not setting.remote_row.visible, "Not shared: Connect")
	(
		setting
		. apply_status(
			{
				"state": "ready",
				"connected": true,
				"remote": "git@example.com:team/harbour.git",
				"teammates": [{"name": "Ana Lima"}, {"name": "Cy Park"}],
			}
		)
	)
	_check(setting.remote_row.visible and not setting.connect_row.visible, "Shared: the remote")
	_check(setting.remote_label.text == "git@example.com:team/harbour.git", "Its address")
	_check(setting.people.text == "Teammates in the history: Ana Lima, Cy Park", "Teammates")
	setting.apply_status({"state": "no-git", "connected": false})
	_check(not setting.connect_row.visible, "No git: nothing to connect")
	setting.queue_free()
	await _frames()


func _git(args: Array) -> int:
	var output: Array = []
	var code := OS.execute("git", PackedStringArray(args), output, true)
	if code != 0:
		print("git ", " ".join(args), "\n", "\n".join(output))
	return code


func _cli(python: String, args: Array) -> Dictionary:
	var output: Array = []
	var argv := PackedStringArray(
		["-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	argv.append_array(PackedStringArray(args))
	OS.execute(python, argv, output, true)
	var lines := "\n".join(output).strip_edges().split("\n", false)
	lines.reverse()
	for line: String in lines:
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			return parsed
	return {}


## The real CLI: connect a bare remote, Push, a teammate pushes from a clone, Pull.
func _check_real_cli(cache: String) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var remote := cache.path_join("remote-%d.git" % Time.get_ticks_usec())
	var clone := cache.path_join("ana-%d" % Time.get_ticks_usec())
	_check(_git(["init", "-q", "--bare", "-b", "main", remote]) == 0, "A local bare remote")
	var connected := _cli(python, ["team", "connect", remote, "--project", project, "--json"])
	_check(bool(connected.get("connected", false)), "Connected: " + str(connected))
	var branch := str(connected.get("branch", "main"))
	# A hosted remote takes its default branch from the first push; a bare one is told.
	_git(["-C", remote, "symbolic-ref", "HEAD", "refs/heads/" + branch])
	var prefix := PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	var tab: TIMELINE = TIMELINE.new()
	tab.cli_prefix = prefix
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	tab.refresh()
	_check(await _until(func() -> bool: return tab._sync.visible), "Shared: Pull and Push show")
	tab._push.pressed.emit()
	var pushed := func() -> bool: return tab._sync_text.text.begins_with("Pushed")
	_check(await _until(pushed, 60.0), "Push shares the project: " + tab._sync_text.text)
	var cloned := _cli(python, ["team", "clone", remote, "--target", clone, "--json"])
	_check(bool(cloned.get("connected", false)), "A teammate's clone")
	_git(["-C", clone, "config", "user.name", "Ana Lima"])
	_git(["-C", clone, "config", "user.email", "ana@example.com"])
	var file := FileAccess.open(clone.path_join("lamp.gd"), FileAccess.WRITE)
	file.store_string("extends Node3D\n")
	file.close()
	_git(["-C", clone, "add", "lamp.gd"])
	_git(["-C", clone, "commit", "-q", "-m", "Add a lamp script"])
	_check(_git(["-C", clone, "push", "-q", "origin", "HEAD:" + branch]) == 0, "Ana pushes")
	tab._fetched_at = -1.0e9
	tab.refresh()
	var behind := func() -> bool: return tab._pull.text == "Pull 1"
	_check(await _until(behind, 60.0), "Her change waits to be pulled: " + tab._pull.text)
	_check(tab._pull.theme_type_variation == "JoineryPrimaryButton", "Pull is the next step")
	tab._pull.pressed.emit()
	var pulled := func() -> bool: return tab._sync_text.text.begins_with("Pulled")
	_check(await _until(pulled, 60.0), "Pull answers: " + tab._sync_text.text)
	_check(tab._sync_text.text == "Pulled 1 change from Ana.", "In words: " + tab._sync_text.text)
	var arrived := func() -> bool:
		if tab._rows.get_child_count() == 0:
			return false
		var open: Button = tab._rows.get_child(0).get_node("Open")
		return open.text == "Add a lamp script"
	_check(await _until(arrived, 30.0), "Her entry heads the Timeline")
	if tab._rows.get_child_count() > 0:
		var newest: Control = tab._rows.get_child(0)
		var meta := (newest.get_node("Meta") as Label).text
		_check(meta.begins_with("Ana Lima"), "With her name: " + meta)
		_check(newest.get_node_or_null("Pulled") != null, "And the pulled dot")
	_check(FileAccess.file_exists("res://lamp.gd"), "Her file is in the project")
	tab.queue_free()
	await _frames()
