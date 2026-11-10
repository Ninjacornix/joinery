extends SceneTree
## The bottom panel's Timeline tab, headless. First the screens from fixture listings: loading,
## the offer to start one (and the plain no-git and nested messages, without a button), rows
## with author, file count and time, "Show older entries" for a full page, the empty filter
## result, the switched-off note and an entry with text, image and model files. Then the real
## CLI end to end in this disposable project: Start keeping a timeline, a scene edit accepted
## live joining it on save, its diff, and Restore this file as it was waiting in Review.

const TAB := preload("res://addons/joinery/timeline_tab.gd")
const ENTRY := preload("res://addons/joinery/timeline_entry.gd")
const SETTING := preload("res://addons/joinery/timeline_setting.gd")
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


func _until(condition: Callable, seconds := 20.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while not condition.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return condition.call()


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-timeline-test")
	DirAccess.make_dir_recursive_absolute(cache)
	Engine.set_meta("joinery_ui_config", cache.path_join("journeyman.cfg"))
	Engine.remove_meta("joinery_ui_prefs")
	UI_MOTION.set_enabled(false)
	root.size = Vector2i(900, 520)
	_host = Control.new()
	_host.size = Vector2(900, 520)
	root.add_child(_host)
	_check_wording()
	var tab: TAB = TAB.new()
	tab.cli_enabled = false
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	await _frames()
	_check(tab._loading.visible, "The first read shows that it is loading")
	_check(not tab._offer.visible, "No offer before the project has answered")
	await _check_offers(tab)
	await _check_rows(tab)
	await _check_entry(tab)
	tab.queue_free()
	await _frames()
	await _check_setting()
	await _check_real_cli()
	print("timeline: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_wording() -> void:
	var now := 1_800_000_000.0
	var entry := {"author": "Ada Lovelace", "files": 3, "time": now - 7200}
	_check(
		TAB.meta_text(entry, now) == "Ada Lovelace · 3 files · 2 h ago", "Row meta reads plainly"
	)
	_check(TAB.meta_text({"files": 1}, now) == "1 file", "One file is singular")
	_check(ENTRY.size_text(340_000) == "340 KB", "Kilobytes read plainly")
	_check(ENTRY.size_text(1_240_000) == "1.2 MB", "Megabytes keep one decimal")
	var model := {
		"path": "art/models/door.glb",
		"change": "modified",
		"before_size": 1_200_000,
		"after_size": 1_400_000
	}
	_check(ENTRY.binary_text(model) == "Model changed · 1.2 MB → 1.4 MB", "A model says so")
	var sound := {"path": "audio/creak.wav", "change": "created", "after_size": 52_000}
	_check(ENTRY.binary_text(sound) == "Sound added · 52 KB", "A new sound says so")


func _check_offers(tab: TAB) -> void:
	tab.apply_listing({"state": "none", "message": "", "auto_commit": true, "entries": []})
	await _frames()
	_check(tab._offer.visible and tab._offer_button.visible, "No repository: offer to start one")
	_check(not tab._filter.get_parent().visible, "No filter without a timeline")
	_check(not tab._loading.visible, "Loaded")
	var no_git := "Git isn't installed, so this project has no timeline."
	tab.apply_listing({"state": "no-git", "message": no_git, "entries": []})
	await _frames()
	_check(tab._offer.visible and not tab._offer_button.visible, "No git: say so, no button")
	_check(tab._offer_text.text == no_git, "No git: the CLI's own sentence")
	tab.apply_listing({"state": "nested", "message": "Part of /work.", "entries": []})
	await _frames()
	_check(not tab._offer_button.visible, "Inside another repository: never offer to start")
	tab.apply_listing({"error": "Git: not a git repository"})
	await _frames()
	_check(tab._load_error.visible, "A failed read says so")
	_check(tab._load_error.text == "Git: not a git repository", "With the reason")


func _check_rows(tab: TAB) -> void:
	var now := Time.get_unix_time_from_system()
	var page: Array = []
	for index: int in 50:
		(
			page
			. append(
				{
					"commit": "%040d" % index,
					"short": "%07d" % index,
					"subject": "Change %d" % index,
					"author": "Ada Lovelace",
					"time": now - index * 600,
					"files": index % 3 + 1,
				}
			)
		)
	tab.apply_listing({"state": "ready", "auto_commit": true, "entries": page, "more": true})
	await _frames()
	_check(not tab._load_error.visible, "A good read clears the error")
	_check(not tab._offer.visible, "A timeline hides the offer")
	_check(tab._filter.get_parent().visible, "A timeline can be filtered")
	_check(tab._rows.get_child_count() == 50, "Fifty rows on the first page")
	var first: Control = tab._rows.get_child(0)
	var open: Button = first.get_node("Open")
	_check(open.text == "Change 0", "Newest first, by its message")
	_check((first.get_node("Meta") as Label).text.begins_with("Ada Lovelace · 1 file"), "Meta")
	_check(tab._more.visible, "A full page offers older entries")
	tab.apply_listing({"state": "ready", "entries": [page[0]], "more": false}, true)
	await _frames()
	_check(tab._rows.get_child_count() == 51 and not tab._more.visible, "Older entries append")
	tab._filter.text = "lamp"
	tab.apply_listing({"state": "ready", "auto_commit": false, "entries": [], "more": false})
	await _frames()
	_check(tab._empty.visible and tab._empty.text == "Nothing matches “lamp”.", "Empty filter")
	_check(tab._note.visible and tab._note.text.contains("aren't added"), "Switched off: a note")
	tab._filter.text = ""
	tab.live_edit_accepted("res://door.tscn", "Widen the door")
	tab.apply_listing({"state": "ready", "auto_commit": true, "entries": page, "more": false})
	await _frames()
	_check(tab._note.text == TAB.PENDING_SCENES, "An unsaved live edit says when it joins")
	tab.scene_saved("res://door.tscn")
	await _frames()
	_check(not tab._note.visible, "Saving the scene clears the note")


func _check_entry(tab: TAB) -> void:
	var first: Control = tab._rows.get_child(0)
	(first.get_node("Open") as Button).pressed.emit()
	await _frames()
	var view := tab.entry_view()
	_check(view.visible and not tab._list_view.visible, "An entry opens in place of the list")
	_check(view._loading.visible, "While it is read, it says so")
	var cache := OS.get_cache_dir().path_join("joinery-timeline-test")
	var before := cache.path_join("before.png")
	var after := cache.path_join("after.png")
	var image := Image.create(32, 32, false, Image.FORMAT_RGBA8)
	image.fill(Color("b07a45"))
	image.save_png(before)
	image.fill(Color("6b4423"))
	image.save_png(after)
	var data := {
		"commit": "%040d" % 0,
		"subject": "Darken the door",
		"author": "Ada Lovelace",
		"time": Time.get_unix_time_from_system(),
		"files":
		[
			{"path": "door.gd", "change": "modified", "kind": "text", "added": 1, "removed": 1},
			{"path": "wood.png", "change": "modified", "kind": "image", "added": 0, "removed": 0},
			{"path": "door.glb", "change": "modified", "kind": "binary", "added": 0, "removed": 0},
		],
	}
	var requested: Array = []
	view.file_requested.connect(func(commit: String, path: String) -> void: requested.append(path))
	view.show_entry(data)
	await _frames()
	_check(view._title.text == "Darken the door", "The entry's message is its title")
	_check(view._meta.text.begins_with("Ada Lovelace · 3 files · "), "Who, how many, when")
	_check(view._rows.size() == 3, "One row per file")
	view.set_file_open("door.gd", true)
	view.set_file_open("door.gd", true)
	_check(requested == ["door.gd"], "A file's detail is read once, when first opened")
	view.apply_file(
		{
			"path": "door.gd",
			"kind": "text",
			"diff": ["@@ -1 +1 @@", "-var w := 1", "+var w := 2"],
			"truncated": false
		}
	)
	view.set_file_open("wood.png", true)
	view.apply_file(
		{
			"path": "wood.png",
			"kind": "image",
			"before": before,
			"after": after,
			"before_size": 120,
			"after_size": 118
		}
	)
	view.set_file_open("door.glb", true)
	view.apply_file(
		{
			"path": "door.glb",
			"kind": "binary",
			"change": "modified",
			"before_size": 1_200_000,
			"after_size": 1_400_000
		}
	)
	await _frames()
	var diff: RichTextLabel = view.find_child("Diff", true, false)
	_check(diff != null and diff.get_parsed_text().contains("+var w := 2"), "Text shows its lines")
	var pictures: Node = view.find_child("Pictures", true, false)
	_check(pictures != null, "An image shows before and after")
	if pictures != null:
		var shown: TextureRect = pictures.find_child("Before", true, false)
		_check(shown != null and shown.texture != null, "The before picture is loaded")
	var rows: Dictionary = view._rows
	var model_view: VBoxContainer = rows["door.glb"]["view"]
	var words: Label = model_view.get_child(0) if model_view.get_child_count() > 0 else null
	_check(words != null and words.text == "Model changed · 1.2 MB → 1.4 MB", "A model's sizes")
	view.set_restore("door.gd", "busy")
	_check((rows["door.gd"]["restore"] as Button).disabled, "Restoring: the button waits")
	view.set_restore("door.gd", "done")
	var status: HBoxContainer = rows["door.gd"]["status"]
	_check(status.find_child("Review", true, false) != null, "Done: it waits in Review")
	_check(not (rows["door.gd"]["restore"] as Button).disabled, "And can be restored again")
	view.file_failed("wood.png", "Git: bad object")
	view.back.emit()
	await _frames()
	_check(tab._list_view.visible and not view.visible, "Back returns to the list")


func _check_setting() -> void:
	var setting: SETTING = SETTING.new()
	setting.cli_prefix = PackedStringArray(["false"])
	_host.add_child(setting)
	setting.apply_status({"state": "ready", "auto_commit": false})
	_check(not setting.row.is_on() and not setting.row.switch_button.disabled, "Off, changeable")
	setting.apply_status({"state": "none", "auto_commit": true})
	_check(setting.row.switch_button.disabled, "No timeline: the switch waits")
	_check(setting.row.reason.text == SETTING.NO_TIMELINE, "And says why")
	setting.queue_free()
	await _frames()


## The real CLI in this disposable project: start, a live scene edit saved, its diff, restore.
func _check_real_cli() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var prefix := PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	var tab: TAB = TAB.new()
	tab.cli_prefix = prefix
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.add_child(tab)
	tab.refresh()
	var offered := func() -> bool: return tab._offer.visible or tab._rows.get_child_count() > 0
	_check(await _until(offered), "The real list answers")
	if not tab._offer_button.visible:
		push_error("A project without git must offer to start a timeline")
		_failed = true
		return
	tab._offer_button.pressed.emit()
	var started := func() -> bool: return tab._rows.get_child_count() == 1
	_check(await _until(started, 60.0), "Start keeping a timeline makes the first entry")
	var scene := "res://timeline_door.tscn"
	var file := FileAccess.open(scene, FileAccess.WRITE)
	file.store_string('[gd_scene format=3]\n\n[node name="Door" type="Node3D"]\n')
	file.close()
	tab.live_edit_accepted(scene, "Widen the door")
	tab.scene_saved(scene)
	var saved := func() -> bool: return tab._rows.get_child_count() == 2
	_check(await _until(saved, 30.0), "A saved scene edit joins the timeline")
	var newest: Control = tab._rows.get_child(0)
	_check((newest.get_node("Open") as Button).text == "Widen the door", "By its summary")
	var meta := (newest.get_node("Meta") as Label).text
	_check(meta.begins_with("Joinery user · 1 file"), "The local author, one file: " + meta)
	file = FileAccess.open(scene, FileAccess.WRITE)
	file.store_string('[gd_scene format=3]\n\n[node name="Door" type="Node3D"]\n')
	file.store_string('[node name="Hinge" type="Node3D" parent="."]\n')
	file.close()
	tab.live_edit_accepted(scene, "Add a hinge")
	tab.scene_saved(scene)
	var hinged := func() -> bool: return tab._rows.get_child_count() == 3
	_check(await _until(hinged, 30.0), "A second save is a second entry")
	var widened: Control = tab._rows.get_child(1)
	(widened.get_node("Open") as Button).pressed.emit()
	var view := tab.entry_view()
	var shown := func() -> bool: return view._rows.has("timeline_door.tscn")
	_check(await _until(shown, 30.0), "The entry lists its scene")
	var diff_ready := func() -> bool: return view.find_child("Diff", true, false) != null
	_check(await _until(diff_ready, 30.0), "Its one file opens with its lines")
	var diff: RichTextLabel = view.find_child("Diff", true, false)
	if diff != null:
		_check(diff.get_parsed_text().contains('+[node name="Door"'), "The added lines show")
	var rows: Dictionary = view._rows
	(rows["timeline_door.tscn"]["restore"] as Button).pressed.emit()
	var status: HBoxContainer = rows["timeline_door.tscn"]["status"]
	var answered := func() -> bool:
		return status.find_child("Review", true, false) != null or status.get_child_count() == 1
	_check(await _until(answered, 30.0), "The restore answers")
	_check(status.find_child("Review", true, false) != null, "The restore waits in Review")
	# Nothing on disk changed: the restore is a reviewed change, not a reset.
	_check(FileAccess.get_file_as_string(scene).contains("Hinge"), "The file is untouched")
	var waiting := 0
	var folder := "res://.joinery/transactions"
	for name: String in DirAccess.get_directories_at(folder):
		var journal := FileAccess.get_file_as_string(
			folder.path_join(name).path_join("journal.json")
		)
		if journal.contains('"state": "review"') and journal.contains("Restore timeline_door.tscn"):
			waiting += 1
	_check(waiting == 1, "One reviewed change waits to restore the scene")
	tab.queue_free()
	await _frames()
