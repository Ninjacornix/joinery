extends SceneTree
## Project Manager screens for the visual suite: agent connection, sign-in, API
## form, project list and every page of the new-project wizard.
## Run with the fork's --project-manager --script. JOINERY_VISUAL_THEME names the
## theme the runner seeded (light or dark); captures go to JOINERY_CAPTURE_DIR.

var _checks: GDScript
var _theme := "dark"
var _failed := false


func _initialize() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	var theme := OS.get_environment("JOINERY_VISUAL_THEME")
	if not theme.is_empty():
		_theme = theme
	# Joinery screens animate in a graphical run; captures must show end states.
	Engine.set_meta("joinery_motion_enabled", false)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _button(parent: Node, text: String) -> Button:
	for node: Node in parent.find_children("*", "Button", true, false):
		if (node as Button).text == text:
			return node as Button
	return null


## The project list's create action. The fork hides the bar's Create while the list is
## empty; then the empty state's "Create project" is the one to press.
func _create_action() -> Button:
	var fallback: Button = null
	for node: Node in root.find_children("*", "Button", true, false):
		var button := node as Button
		if button.get_window() != root or not button.text in ["Create", "Create project"]:
			continue
		if button.is_visible_in_tree():
			return button
		if fallback == null:
			fallback = button
	return fallback


func _frames(count: int = 3) -> void:
	for index: int in count:
		await process_frame


func _shot(name: String, window: Window) -> void:
	await _frames()
	var screen := "pm-%s-%s" % [name, _theme]
	_check(_checks.snapshot(screen, window, [window]), "Capture " + name)
	# One accent fill per view: the step's Next or Create, nothing else.
	var fills := 0
	for node: Node in window.find_children("*", "Button", true, false):
		var button := node as Button
		if button.theme_type_variation == &"JoineryPrimaryButton" and button.is_visible_in_tree():
			fills += 1
	if fills > 1:
		print("UI-CHECK %s primary %s %d accent fills, at most one" % [screen, window.name, fills])
		_check(false, "%s shows %d primary buttons" % [screen, fills])


func _finish() -> void:
	print("visual startup: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _run() -> void:
	await create_timer(1.0).timeout
	var connect := root.find_child("JoineryAgentConnect", true, false) as Control
	_check(connect != null, "Agent connection screen must exist")
	if connect == null:
		_finish()
		return
	# Let the first real status check finish so the screen shows what a new user sees.
	var deadline := Time.get_ticks_msec() + 20000
	while connect.get("_job") != null and Time.get_ticks_msec() < deadline:
		await process_frame
	await _shot("agent-connect", root)
	# From here, fixtures drive the screen; stop background status refreshes.
	if connect.get("_job") != null:
		connect.get("_job").call("cancel")
		connect.set("_job", null)
	connect.set("_elapsed", -1.0e9)
	var agents: Dictionary = connect.get("_agents")
	agents["codex"] = {
		"id": "codex", "installed": true, "connected": false, "method": "", "supported": true
	}
	connect.call("_select", "codex")
	await _shot("agent-signin", root)
	connect.call("_show_api")
	var form: Control = connect.get("_api_form")
	if form != null:
		(form.get("_endpoint") as LineEdit).text = "http://127.0.0.1:8080/v1"
		(form.get("_model") as LineEdit).text = "fixture-model"
		(form.get("_key") as LineEdit).text = "TEST_ONLY"
		form.call("_update_hint")
	await _shot("api-form", root)
	connect.call("_close_api")
	await _frames()
	agents["codex"]["connected"] = true
	agents["codex"]["method"] = "Test account"
	connect.call("_select", "codex")
	await create_timer(0.3).timeout
	var create := _create_action()
	_check(create != null and create.is_visible_in_tree(), "Connected agent reaches projects")
	await _shot("projects", root)
	if create == null:
		_finish()
		return
	await _wizard(create)
	_finish()


func _primary(parent: Node) -> Button:
	for node: Node in parent.find_children("*", "Button", true, false):
		var button := node as Button
		if button.theme_type_variation == &"JoineryPrimaryButton" and button.is_visible_in_tree():
			return button
	return null


func _open_wizard(create: Button) -> ConfirmationDialog:
	create.pressed.emit()
	await create_timer(0.3).timeout
	for node: Node in root.find_children("*", "ConfirmationDialog", true, false):
		if node is Window and (node as Window).visible and node.get_class() == "ProjectDialog":
			return node as ConfirmationDialog
	_check(false, "The new-project wizard must open")
	return null


func _fill_name(dialog: Window, folder: String) -> void:
	for line: Node in dialog.find_children("*", "LineEdit", true, false):
		var edit := line as LineEdit
		if edit.get_accessibility_name() == "Project Name:":
			edit.text = "Lighthouse Keeper"
			edit.text_changed.emit(edit.text)
		elif edit.get_accessibility_name() == "Project Path:":
			edit.text = folder
			edit.text_changed.emit(edit.text)
	await create_timer(0.2).timeout


func _wizard(create: Button) -> void:
	# The lists cannot load (uv is not on PATH): the banner offers Try again and a plain project.
	var path := OS.get_environment("PATH")
	OS.set_environment("PATH", "/nonexistent-joinery-visual")
	var dialog := await _open_wizard(create)
	if dialog == null:
		OS.set_environment("PATH", path)
		return
	var deadline := Time.get_ticks_msec() + 20000
	while _button_visible(dialog, "Create plain project") == null:
		if Time.get_ticks_msec() > deadline:
			break
		await create_timer(0.1).timeout
	OS.set_environment("PATH", path)
	_check(_button_visible(dialog, "Create plain project") != null, "A failed load says so")
	_check(_button_visible(dialog, "Try again") != null, "A failed load offers Try again")
	await _shot("setup-catalog-failed", dialog)
	dialog.hide()
	await _frames()

	dialog = await _open_wizard(create)
	if dialog == null:
		return
	var folder := OS.get_environment("JOINERY_SETUP_CHECK_DIR")
	await _fill_name(dialog, folder)
	if not await _starting_points(dialog):
		return
	await _shot("setup-1-details", dialog)
	var next := _primary(dialog)
	_check(next != null and next.text == "Next", "Step 1 offers Next")
	if next == null:
		return
	next.pressed.emit()
	await create_timer(0.25).timeout
	if not await _features(dialog, next):
		return
	next.pressed.emit()
	await create_timer(0.25).timeout
	_check(_label_visible(dialog, "Brief and art direction"), "Step 3 is Brief and art direction")
	var references := _field(dialog, "References")
	_check(references != null, "References is its own field")
	_check(_label_visible(dialog, "Game brief"), "The brief has its own section")
	var pitch := _field(dialog, "Pitch") as LineEdit
	_check(
		pitch != null and pitch.text == "A lighthouse keeper fixes the harbour before a storm.",
		"The brief's pitch starts from step 1's sentence"
	)
	var preset := _picker(dialog, "Starting rules")
	_check(
		preset != null and preset.get_item_metadata(preset.selected) == "stylised-pbr-3d",
		"The starting point's preset is chosen"
	)
	if references != null:
		(references as TextEdit).text = "art/reference/harbour_dusk.kra: the warm low light."
		await _scroll_to(dialog, references.get_parent().get_child(0) as Control)
	_check(next.text == "Create project", "The last step offers Create project")
	await _shot("setup-3-art", dialog)
	await _references(dialog, folder + "-material")

	# Never register or open the project: the suite only looks at the pages.
	for connection: Dictionary in dialog.get_signal_connection_list("project_created"):
		dialog.disconnect("project_created", connection["callable"])
	# Without uv the first two steps run and the starting point stops, with no download.
	OS.set_environment("PATH", "/nonexistent-joinery-visual")
	next.pressed.emit()
	await _frames(2)
	_check(_label_visible(dialog, "Copying the First-person 3D starting point"), "Name the start")
	# The note shows while the step waits or runs. Without uv the step can
	# already have failed (and hidden its note) by now, so check what the
	# step list was told the step will do.
	_check(_planned_note(dialog, "Downloading GodotSteam"), "Say GodotSteam downloads")
	await _shot("setup-creating", dialog)
	deadline = Time.get_ticks_msec() + 60000
	while not (next.text == "Try again" and next.is_visible_in_tree()):
		if Time.get_ticks_msec() > deadline or not dialog.visible:
			break
		await create_timer(0.1).timeout
	OS.set_environment("PATH", path)
	_check(next.text == "Try again", "A failed step offers Try again")
	_check(FileAccess.file_exists(folder.path_join("addons/joinery/LICENSE")), "The MIT licence")
	await _shot("setup-failed", dialog)
	dialog.hide()
	await _frames()


## Step 1: the cards load, one is chosen, and they take keyboard focus.
func _starting_points(dialog: Window) -> bool:
	_check(_label_visible(dialog, "What are you making?"), "Step 1 is What are you making?")
	var deadline := Time.get_ticks_msec() + 60000
	var cards := _cards(dialog)
	while cards.size() < 2 and Time.get_ticks_msec() < deadline:
		await create_timer(0.2).timeout
		cards = _cards(dialog)
	_check(cards.size() >= 2, "The starting points load as cards")
	if cards.size() < 2:
		return false
	var chosen := cards.filter(func(card: Button) -> bool: return card.button_pressed)
	_check(chosen.size() == 1, "Exactly one starting point is chosen")
	var target: Button = null
	for card: Button in cards:
		if card.get_meta("blueprint", "") == "first-person-3d":
			target = card
	_check(target != null, "First-person 3D is offered")
	if target == null:
		return false
	var room: Button = null
	for card: Button in cards:
		if card.get_meta("blueprint", "") == "vr-room":
			room = card
	_check(room != null, "The VR room is offered with its card")
	if room != null:
		await _scroll_to(dialog, room)
		await _shot("setup-1-vr-room", dialog)
	target.grab_focus()
	await _frames(1)
	_check(target.has_focus(), "A card takes keyboard focus")
	var neighbour := target.find_valid_focus_neighbor(SIDE_RIGHT)
	_check(neighbour == null or cards.has(neighbour), "Arrow keys move between cards")
	target.button_pressed = true
	target.pressed.emit()
	var sentence := _field(dialog, "Your game in one sentence") as LineEdit
	_check(sentence != null, "Step 1 asks for the game in one sentence")
	if sentence != null:
		sentence.text = "A lighthouse keeper fixes the harbour before a storm."
	_check(_label_visible(dialog, "Journeyman reads this before every request."), "Say why")
	var scroll := dialog.find_children("*", "ScrollContainer", true, false)[0] as ScrollContainer
	scroll.scroll_vertical = 0
	return true


## Step 2: switches from the starting point, Steam's inline fields, apps summary.
func _features(dialog: Window, next: Button) -> bool:
	_check(_label_visible(dialog, "Features and apps"), "Step 2 is Features and apps")
	var deadline := Time.get_ticks_msec() + 60000
	while _switch(dialog, "Steam") == null and Time.get_ticks_msec() < deadline:
		await create_timer(0.2).timeout
	var online := _switch(dialog, "Online play")
	var steam := _switch(dialog, "Steam")
	var controller := _switch(dialog, "Controller support")
	_check(online != null and steam != null, "Online play and Steam are offered")
	if online == null or steam == null:
		return false
	_check(
		controller != null and controller.button_pressed, "Defaults come from the starting point"
	)
	online.button_pressed = true
	steam.button_pressed = true
	await _frames(1)
	var app_id := _field(dialog, "Steam app ID") as LineEdit
	_check(app_id != null, "Steam asks for its app ID inline")
	if app_id == null:
		return false
	_check(
		_label_visible(dialog, "Connect players through:"), "Online and Steam ask how to connect"
	)
	var through: CheckBox = null
	for node: Node in dialog.find_children("*", "CheckBox", true, false):
		if (node as CheckBox).text == "Steam" and (node as CheckBox).is_visible_in_tree():
			through = node as CheckBox
	_check(through != null and through.button_pressed, "Players connect through Steam by default")
	# The app list loads on the worker after the starting points; Next waits for it.
	deadline = Time.get_ticks_msec() + 60000
	while _button_visible(dialog, "Change…") == null and Time.get_ticks_msec() < deadline:
		await create_timer(0.2).timeout
	_check(_button_visible(dialog, "Change…") != null, "The apps sit behind Change…")
	app_id.text = "48O"
	app_id.text_changed.emit(app_id.text)
	await _frames(1)
	_check(_label_visible(dialog, "Use digits only, like 480."), "Letters in the app ID are named")
	_check(next.disabled, "A bad app ID blocks Next")
	await _scroll_to(dialog, steam)
	await _shot("setup-2-steam-error", dialog)
	app_id.text = "4294967296"
	app_id.text_changed.emit(app_id.text)
	await _frames(1)
	_check(_label_visible(dialog, "Too big for a Steam app ID."), "An app ID past 32 bits is named")
	_check(next.disabled, "A too-big app ID blocks Next")
	await _scroll_to(dialog, steam)
	await _shot("setup-2-steam-too-big", dialog)
	app_id.text = "4294967295"
	app_id.text_changed.emit(app_id.text)
	await _frames(1)
	_check(not next.disabled, "The largest Steam app ID is fine")
	app_id.text = ""
	app_id.text_changed.emit(app_id.text)
	await _frames(1)
	_check(not next.disabled, "An empty app ID is fine")
	app_id.text = "480"
	app_id.text_changed.emit(app_id.text)
	await _frames(1)
	_check(not next.disabled and not _label_visible(dialog, "Use digits only, like 480."), "480")
	await _scroll_to(dialog, steam)
	await _shot("setup-2-tools", dialog)
	# The step ends with how many older versions the project keeps.
	var history: OptionButton = null
	for node: Node in dialog.find_children("*", "OptionButton", true, false):
		if node.get_accessibility_name() == "Keep older versions" and node.is_visible_in_tree():
			history = node
	_check(history != null, "Step 2 asks how many older versions to keep")
	if history != null:
		await _scroll_to(dialog, history)
		await _shot("setup-2-history", dialog)
	return true


func _cards(dialog: Window) -> Array[Button]:
	var out: Array[Button] = []
	for node: Node in dialog.find_children("*", "Button", true, false):
		var button := node as Button
		if button.theme_type_variation == &"JoineryCard" and button.is_visible_in_tree():
			out.append(button)
	return out


func _switch(dialog: Window, text: String) -> CheckButton:
	for node: Node in dialog.find_children("*", "CheckButton", true, false):
		if (node as CheckButton).text == text and (node as CheckButton).is_visible_in_tree():
			return node as CheckButton
	return null


func _field(dialog: Window, accessible: String) -> Control:
	for node: Node in dialog.find_children("*", "", true, false):
		if node is LineEdit or node is TextEdit:
			var control := node as Control
			if control.get_accessibility_name() == accessible and control.is_visible_in_tree():
				return control
	return null


func _picker(dialog: Window, accessible: String) -> OptionButton:
	for node: Node in dialog.find_children("*", "OptionButton", true, false):
		var picker := node as OptionButton
		if picker.get_accessibility_name() == accessible and picker.is_visible_in_tree():
			return picker
	return null


## Scrolls the page so [param control] sits near its top, so the capture shows what follows.
func _scroll_to(dialog: Window, control: Control) -> void:
	await _frames(1)
	var scroll := dialog.find_children("*", "ScrollContainer", true, false)[0] as ScrollContainer
	var offset := control.global_position.y - scroll.global_position.y
	scroll.scroll_vertical = maxi(0, scroll.scroll_vertical + int(offset) - 8)
	await _frames(2)


func _button_visible(parent: Node, text: String) -> Button:
	# Several buttons can share a label (a hidden banner action and the footer primary).
	for node: Node in parent.find_children("*", "Button", true, false):
		var button := node as Button
		if button.text == text and button.is_visible_in_tree():
			return button
	return null


func _planned_note(parent: Node, text: String) -> bool:
	for node: Node in parent.find_children("*", "Label", true, false):
		if str(node.get_meta("planned", "")) == text:
			return true
	return false


func _label_visible(parent: Node, text: String) -> bool:
	for node: Node in parent.find_children("*", "Label", true, false):
		if (node as Label).text == text and (node as Label).is_visible_in_tree():
			return true
	return false


## Step 3's reference images and design documents, dropped on the window: thumbnails with a
## note each, the documents by name and kind, and a plain refusal for a file of neither kind.
func _references(dialog: Window, folder: String) -> void:
	DirAccess.make_dir_recursive_absolute(folder)
	var files := PackedStringArray()
	for index: int in 2:
		var image := Image.create(96, 64, false, Image.FORMAT_RGB8)
		image.fill(Color(0.85, 0.55, 0.3) if index == 0 else Color(0.25, 0.4, 0.6))
		image.fill_rect(Rect2i(0, 40, 96, 24), Color(0.2, 0.18, 0.16))
		var path := folder.path_join(["harbour dusk.png", "quarry.png"][index])
		image.save_png(path)
		files.append(path)
	for name: String in ["gdd.md", "pitch.pdf", "notes.docx"]:
		var file := FileAccess.open(folder.path_join(name), FileAccess.WRITE)
		file.store_string("%PDF-1.4\n" if name.ends_with(".pdf") else "## Core loop\nFix a leak.\n")
		file.close()
		files.append(folder.path_join(name))
	dialog.files_dropped.emit(files)
	await _frames()
	_check(_label_visible(dialog, "harbour dusk.png"), "A dropped picture is listed")
	_check(_label_visible(dialog, "gdd.md"), "A dropped document is listed")
	_check(
		_label_visible(
			dialog,
			(
				"notes.docx isn't a picture or a design document. Drop PNG, JPEG or WebP "
				+ "pictures, or Markdown, text or PDF documents."
			)
		),
		"A file of neither kind is refused in plain words"
	)
	var note := _field(dialog, "What to take from harbour dusk.png") as LineEdit
	_check(note != null, "Each picture has a note")
	if note != null:
		note.text = "the warm low light"
	var heading: Label = null
	for node: Node in dialog.find_children("*", "Label", true, false):
		if (node as Label).text == "Reference images":
			heading = node
	if heading != null:
		await _scroll_to(dialog, heading)
	await _shot("setup-3-references", dialog)
	var documents: Label = null
	for node: Node in dialog.find_children("*", "Label", true, false):
		if (node as Label).text == "Design documents":
			documents = node
	if documents != null:
		await _scroll_to(dialog, documents)
	await _shot("setup-3-documents", dialog)
