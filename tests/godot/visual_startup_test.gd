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
	_check(_checks.snapshot("pm-%s-%s" % [name, _theme], window, [window]), "Capture " + name)


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
	# The app list cannot load (uv is not on PATH): the banner offers Retry and a plain project.
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
	_check(_button_visible(dialog, "Create plain project") != null, "A failed app list says so")
	await _shot("setup-catalog-failed", dialog)
	dialog.hide()
	await _frames()

	# The three steps, with the list loaded.
	dialog = await _open_wizard(create)
	if dialog == null:
		return
	var folder := OS.get_environment("JOINERY_SETUP_CHECK_DIR")
	await _fill_name(dialog, folder)
	var names := ["details", "tools", "art"]
	var next: Button
	for page: int in names.size():
		if page == 1:
			deadline = Time.get_ticks_msec() + 60000
			# The app list loads on a worker thread; wait for its first section.
			while not _label_visible(dialog, "Engine") and Time.get_ticks_msec() < deadline:
				await create_timer(0.2).timeout
			_check(_label_visible(dialog, "Engine"), "The app list loads")
		await _shot("setup-%d-%s" % [page + 1, names[page]], dialog)
		next = _primary(dialog)
		if next == null or next.text == "Create project":
			break
		next.pressed.emit()
		await create_timer(0.25).timeout
		if not dialog.visible:
			_check(false, "Setup closed before its last page")
			return
	_check(next != null and next.text == "Create project", "Wizard reaches its last page")
	if next == null:
		return
	# Never register or open the project: the suite only looks at the pages.
	for connection: Dictionary in dialog.get_signal_connection_list("project_created"):
		dialog.disconnect("project_created", connection["callable"])
	# A folder Joinery can't write to: creating stops at its first step and says why.
	DirAccess.make_dir_recursive_absolute(folder)
	var locked := OS.execute("chmod", ["555", folder]) == 0
	next.pressed.emit()
	deadline = Time.get_ticks_msec() + 60000
	while not (next.text == "Retry" and next.is_visible_in_tree()):
		if Time.get_ticks_msec() > deadline or not dialog.visible:
			break
		await create_timer(0.1).timeout
	if locked:
		_check(next.text == "Retry", "A failed step offers Retry")
		await _shot("setup-failed", dialog)
		OS.execute("chmod", ["755", folder])
		# Retry from the failed step: capture it while the steps are running.
		next.pressed.emit()
		await _frames(2)
		await _shot("setup-creating", dialog)
		deadline = Time.get_ticks_msec() + 90000
		while dialog.visible and Time.get_ticks_msec() < deadline:
			if next.text == "Retry" and next.is_visible_in_tree():
				break
			if FileAccess.file_exists(folder.path_join("project.godot")):
				break
			await create_timer(0.2).timeout
	dialog.hide()
	await _frames()


func _button_visible(parent: Node, text: String) -> Button:
	# Several buttons can share a label (a hidden banner action and the footer primary).
	for node: Node in parent.find_children("*", "Button", true, false):
		var button := node as Button
		if button.text == text and button.is_visible_in_tree():
			return button
	return null


func _label_visible(parent: Node, text: String) -> bool:
	for node: Node in parent.find_children("*", "Label", true, false):
		if (node as Label).text == text and (node as Label).is_visible_in_tree():
			return true
	return false
