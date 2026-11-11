extends SceneTree
## References and design documents in Project apps (/setup), against the fixture Joinery CLI
## of run_setup_tests.py: picked files are listed (a thumbnail and a note for each picture, the
## kind for each document), a file of neither kind is refused in plain words, the change is
## planned before anything moves, and Add to project applies it with the notes.

const ADDON := "res://addons/joinery/"

var _failed := false
var _calls_path := ""


func _initialize() -> void:
	Engine.set_meta("joinery_motion_enabled", false)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(ready: Callable, seconds := 20.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while not ready.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return ready.call()


func _all_calls() -> Array:
	var calls: Array = []
	for file_name: String in DirAccess.get_files_at(_calls_path):
		if file_name.ends_with(".json"):
			calls.append(
				JSON.parse_string(FileAccess.get_file_as_string(_calls_path.path_join(file_name)))
			)
	return calls


func _called(words: Array) -> bool:
	for file_name: String in DirAccess.get_files_at(_calls_path):
		if not file_name.ends_with(".json"):
			continue
		var call: Variant = JSON.parse_string(
			FileAccess.get_file_as_string(_calls_path.path_join(file_name))
		)
		var hit := call is Array
		for word: Variant in words:
			hit = hit and (call as Array).has(word)
		if hit:
			return true
	return false


func _texts(node: Node) -> Array[String]:
	var found: Array[String] = []
	for label: Node in node.find_children("*", "Label", true, false):
		if (label as Label).is_visible_in_tree():
			found.append((label as Label).text)
	return found


func _material(folder: String) -> PackedStringArray:
	DirAccess.make_dir_recursive_absolute(folder)
	var image := Image.create(48, 32, false, Image.FORMAT_RGB8)
	image.fill(Color(0.8, 0.5, 0.3))
	image.fill_rect(Rect2i(0, 20, 48, 12), Color(0.2, 0.18, 0.16))
	image.save_png(folder.path_join("dusk.png"))
	for name: String in ["gdd.md", "notes.docx"]:
		var file := FileAccess.open(folder.path_join(name), FileAccess.WRITE)
		file.store_string("## Core loop\nPatch a leak.\n")
		file.close()
	return PackedStringArray(
		[folder.path_join("dusk.png"), folder.path_join("gdd.md"), folder.path_join("notes.docx")]
	)


func _run() -> void:
	var state := OS.get_environment("JOINERY_SETUP_STATE")
	_calls_path = state.path_join("calls")
	Engine.set_meta("joinery_ui_config", state.path_join("journeyman.cfg"))
	var theme_script: GDScript = load(ADDON + "ui_theme.gd")
	theme_script.call("set_pref", "theme", "dark")
	var prefix := PackedStringArray(JSON.parse_string(OS.get_environment("JOINERY_FAKE_CLI")))
	var dialog: AcceptDialog = (load(ADDON + "project_setup.gd") as GDScript).new()
	dialog.set("cli_prefix", prefix)
	dialog.set("start_docker_argv", PackedStringArray())
	root.add_child(dialog)
	dialog.call("open_for_current_project")
	var found := func() -> bool:
		var shown := dialog.find_child("References", true, false) as Control
		return shown != null and shown.is_visible_in_tree()
	_check(await _until(found), "Project apps has References and design documents")
	var section := dialog.find_child("References", true, false) as Control
	_check(section != null, "The section is there")
	if section != null:
		await _check_section(dialog, section, _material(state.path_join("material")))
	theme_script.call("set_pref", "theme", "match")
	print("setup imports: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_section(dialog: AcceptDialog, section: Control, files: PackedStringArray) -> void:
	var texts := _texts(section)
	_check("References and design documents" in texts, "The section says what it is for")
	var card := section.get("card") as Control
	_check(not card.visible, "Nothing is listed before files are picked")
	section.call("add_files", files)
	await process_frame
	texts = _texts(section)
	_check("dusk.png" in texts and "gdd.md" in texts, "Picked files are listed by name")
	_check("Markdown" in texts, "A document says its kind")
	_check(
		"notes.docx isn't a picture. References can be PNG, JPEG or WebP images." in texts,
		"A file of neither kind is refused in plain words"
	)
	var add := section.get("add_button") as Button
	_check(add.disabled, "Add to project waits for the plan")
	var planned := func() -> bool: return not add.disabled
	_check(await _until(planned), "The change is planned first")
	_check(_called(["references", "add", "--plan"]), "The plan comes from --plan")
	_check(not _called(["references", "--yes"]), "Nothing is added before Add to project")
	_check(
		_texts(section).any(func(text: String) -> bool: return text.contains("approve it again")),
		"The plan says the art bible needs approving again"
	)
	var note: LineEdit = null
	for node: Node in section.find_children("*", "LineEdit", true, false):
		if node.get_accessibility_name() == "What to take from dusk.png":
			note = node
	_check(note != null, "A picture has a note")
	if note != null:
		note.text = "the warm low light"
	await _captures(dialog, section)
	# Captures switch themes, which can plan again: press only once the plan is ready.
	_check(await _until(planned), "The plan is ready again before adding")
	add.pressed.emit()
	var path := files[0] + "=the warm low light"
	var done := func() -> bool: return _called(["references", "--yes", path])
	var added := await _until(done)
	if not added:
		print("setup imports: calls seen: ", _all_calls())
	_check(added, "Add to project passes each picture with its note")
	_check(_called(["--design-doc", files[1]]), "And each document")
	var status := dialog.get("_status") as Label
	var said := func() -> bool: return status.visible and status.text.begins_with("Added 1")
	_check(await _until(said), "Adding says what changed")
	_check(not card.visible, "The list closes once added")


## Save setup-references-dark.png and -light.png with the section in view, when capturing.
func _captures(dialog: AcceptDialog, show: Control) -> void:
	var directory := OS.get_environment("JOINERY_CAPTURE_DIR")
	if DisplayServer.get_name() == "headless" or directory.is_empty():
		return
	var theme_script: GDScript = load(ADDON + "ui_theme.gd")
	var scroll := show.get_parent()
	while scroll != null and not scroll is ScrollContainer:
		scroll = scroll.get_parent()
	for mode: String in ["dark", "light"]:
		theme_script.call("set_pref", "theme", mode)
		RenderingServer.set_default_clear_color(theme_script.call("color", dialog, "surface"))
		dialog.remove_theme_stylebox_override("panel")
		var panel := dialog.get_theme_stylebox("panel").duplicate()
		if panel is StyleBoxFlat:
			(panel as StyleBoxFlat).bg_color = theme_script.call("color", dialog, "surface")
		dialog.add_theme_stylebox_override("panel", panel)
		for _frame: int in 6:
			await process_frame
		if scroll != null:
			var box := scroll as ScrollContainer
			box.scroll_vertical = int(
				(
					box.scroll_vertical
					+ show.get_global_rect().position.y
					- box.get_global_rect().position.y
				)
			)
		for _frame: int in 3:
			await process_frame
		RenderingServer.force_draw()
		var image := root.get_texture().get_image()
		_check(
			image.save_png(directory.path_join("setup-references-%s.png" % mode)) == OK,
			"The capture saves"
		)
	theme_script.call("set_pref", "theme", "dark")
