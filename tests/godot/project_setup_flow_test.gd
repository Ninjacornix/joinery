extends SceneTree
## Run with the fork's --project-manager --script and a temporary output directory.

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _button(parent: Node, text: String) -> Button:
	for node: Node in parent.find_children("*", "Button", true, false):
		if node.text == text:
			return node as Button
	return null


func _capture(dialog: Window, filename: String) -> void:
	_check(DisplayServer.get_name() != "headless", "Startup captures require a graphical renderer")
	if DisplayServer.get_name() == "headless":
		return
	var directory := OS.get_environment("JOINERY_CAPTURE_DIR")
	_check(not directory.is_empty(), "Startup capture directory must be supplied")
	if directory.is_empty():
		return
	RenderingServer.force_draw()
	var image := dialog.get_texture().get_image()
	_check(image != null and not image.is_empty(), "Startup framebuffer must contain pixels")
	if image == null or image.is_empty():
		return
	_check(image.save_png(directory.path_join(filename)) == OK, "Startup PNG must save: " + filename)


func _run() -> void:
	await create_timer(1.0).timeout
	var create := _button(root, "Create")
	_check(create != null, "Project Manager Create button must exist")
	if create == null:
		quit(1)
		return
	var connect := root.find_child("JoineryAgentConnect", true, false) as Control
	_check(connect != null, "Agent connection screen must exist")
	if connect == null:
		quit(1)
		return
	_button(root, "Change agent").pressed.emit()
	_check(not create.is_visible_in_tree(), "Projects must wait for connection")
	create.pressed.emit()
	for window: Node in root.find_children("*", "ConfirmationDialog", true, false):
		_check(not window.visible, "Create shortcut must wait for an agent connection")
	for id: String in ["claude", "codex", "gemini", "opencode"]:
		_check(connect._buttons.has(id), "Offer agent: " + id)
		var choice: Button = connect._buttons[id]
		_check(choice.get_parent() is VBoxContainer, "Agent choices must form one vertical list")
		_check(choice.icon != null, "Each agent choice must have an icon")
	_capture(root, "startup-agent-connect.png")
	# A local fixture tests UI gating without changing any account or user preference.
	if connect._job != null:
		connect._job.cancel()
		connect._job = null
	connect._selected = ""
	connect._update()
	_check(not connect._can_continue({}), "No selected agent means no continuation")
	connect._agents = {"codex": {
		"connected": false, "installed": true, "method": "", "api_key_configured": false,
	}}
	connect._select("codex")
	_check(not connect._can_continue(connect._agents["codex"]), "Sign-in is required")
	connect._finish()
	_check(connect.is_visible_in_tree(), "Enter or synthetic press cannot bypass sign-in")
	connect._agents["codex"]["connected"] = true
	connect._agents["codex"]["method"] = "Test account"
	connect._agents["codex"]["requires_key_confirmation"] = true
	connect._update()
	_check(not connect._can_continue(connect._agents["codex"]), "Activate an environment key")
	for signal_connection: Dictionary in connect.get_signal_connection_list("connected"):
		connect.disconnect("connected", signal_connection["callable"])
	connect.connected.connect(func(_agent: String, _method: String) -> void:
		connect.hide()
		var projects := root.find_child("LocalProjectsTab", true, false) as Control
		projects.get_parent().show()
	)
	connect._show_api()
	_check(connect._api_form.visible, "API settings must open as a submenu")
	_check(connect._api_form._key.secret, "The key input must be masked")
	connect._api_form._endpoint.text = OS.get_environment("JOINERY_TEST_MODEL_ENDPOINT")
	connect._api_form._model.text = "fixture-model"
	connect._api_form._key.text = "TEST_ONLY"
	await process_frame
	_capture(root, "startup-api-submenu.png")
	connect._api_form._submit()
	var api_deadline := Time.get_ticks_msec() + 15000
	while connect._job != null and Time.get_ticks_msec() < api_deadline:
		await process_frame
	await process_frame
	_check(connect.selected_profile.begins_with("api_"), "Connect a saved API profile")
	_check(create.is_visible_in_tree(), "Saving an API profile redirects to projects")
	_check(connect._api_form._key.text.is_empty(), "Successful save clears the key field")
	_check(not connect._message.text.contains("TEST_ONLY"), "Never echo the API key")
	_button(root, "Change agent").pressed.emit()
	connect._select("codex")
	connect._accepted_keys.append("codex")
	connect._auto_redirect = true
	connect._update()
	await process_frame
	_check(create.is_visible_in_tree(), "Authenticated selection must redirect automatically")
	_check(not connect._login.visible, "Do not offer sign-in again for a connected account")
	create.pressed.emit()
	await create_timer(0.3).timeout
	var dialog: ConfirmationDialog
	for node: Node in root.find_children("*", "ConfirmationDialog", true, false):
		if node is Window and node.visible and node.title.contains("Joinery"):
			dialog = node
			break
	_check(dialog != null, "Joinery project dialog must open")
	if dialog == null:
		quit(1)
		return
	var initial_rect := Rect2i(dialog.position, dialog.size)
	var renderers: Array[Button] = []
	for text: String in ["Forward+", "Mobile", "Compatibility"]:
		renderers.append(_button(dialog, text))
	_check(renderers[0].get_parent() is HBoxContainer, "Renderers must be horizontal")
	for renderer: Button in renderers:
		_check(renderer.get_parent() == renderers[0].get_parent(), "Renderers share one row")
	for line: Node in dialog.find_children("*", "LineEdit", true, false):
		if line.get_accessibility_name() == "Project Name:":
			line.text = "Joinery Setup Check"
			line.text_changed.emit(line.text)
		elif line.get_accessibility_name() == "Project Path:":
			line.text = OS.get_environment("JOINERY_SETUP_CHECK_DIR")
			line.text_changed.emit(line.text)
	_capture(dialog, "startup-setup-details.png")
	dialog.get_ok_button().pressed.emit()
	await create_timer(0.2).timeout
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect, "Tools must keep the window rectangle"
	)
	var picker: OptionButton
	for candidate: Node in dialog.find_children("*", "OptionButton", true, false):
		for index: int in candidate.item_count:
			if candidate.get_item_text(index).contains("hi-godot/godot-ai"):
				picker = candidate
				_check(not picker.is_item_disabled(index), "Non-preferred MCP must be selectable")
				_check(picker.get_item_text(index).contains("not preferred"), "Show preference honestly")
				picker.select(index)
				picker.item_selected.emit(index)
				break
	_check(picker != null, "Godot alternatives must be listed")
	dialog.get_ok_button().pressed.emit()
	await process_frame
	_check(dialog.get_ok_button().text == "Next", "An incomplete custom choice must not advance")
	_check(picker.is_visible_in_tree(), "An incomplete choice must stay on the tools page")
	if picker != null:
		for line: Node in picker.get_parent().get_children():
			if line is LineEdit and line.placeholder_text == "MCP executable":
				line.text = OS.get_environment("JOINERY_TEST_MCP_COMMAND")
			elif line is LineEdit:
				line.text = '["--stdio", "argument with spaces"]'
	_capture(dialog, "startup-setup-tools.png")
	dialog.get_ok_button().pressed.emit()
	await create_timer(0.2).timeout
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect, "Art page must keep the window rectangle"
	)
	var first_field: TextEdit
	for field: Node in dialog.find_children("*", "TextEdit", true, false):
		if field.is_visible_in_tree():
			first_field = field
			first_field.text = "Test answer retained when navigating back."
			break
	_capture(dialog, "startup-setup-art.png")
	var back := _button(dialog, "Back")
	back.pressed.emit()
	await process_frame
	_check(not first_field.is_visible_in_tree(), "Back must return to the previous page")
	if _failed:
		quit(1)
		return
	dialog.get_ok_button().pressed.emit()
	await process_frame
	_check(first_field.text.contains("retained"), "Back must retain answers")
	for _step: int in 2:
		dialog.get_ok_button().pressed.emit()
		await create_timer(0.1).timeout
		_check(Rect2i(dialog.position, dialog.size) == initial_rect, "Every page must stay in place")
	_check(dialog.get_ok_button().text == "Create", "Last page must offer Create")
	if _failed:
		quit(1)
		return
	# Create only the temporary fixture; do not register it or launch an editor.
	for connection: Dictionary in dialog.get_signal_connection_list("project_created"):
		dialog.disconnect("project_created", connection["callable"])
	dialog.get_ok_button().pressed.emit()
	await create_timer(0.2).timeout
	var project := OS.get_environment("JOINERY_SETUP_CHECK_DIR")
	_check(FileAccess.file_exists(project.path_join("project.godot")), "Create a plain Godot project")
	_check(FileAccess.file_exists(project.path_join("joinery.toml")), "Custom choices must save")
	var config := FileAccess.get_file_as_string(project.path_join("joinery.toml"))
	_check(config.contains('id = "hi-godot-godot-ai"'), "Save the selected alternative as custom")
	var mcp_path := project.path_join(".mcp.json")
	_check(FileAccess.file_exists(mcp_path), "Register the selected custom MCP")
	if FileAccess.file_exists(mcp_path):
		var mcp: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(mcp_path))
		var server: Dictionary = mcp["mcpServers"]["hi-godot-godot-ai"]
		_check(
			server["command"] == OS.get_environment("JOINERY_TEST_MCP_COMMAND"),
			"Preserve the chosen executable path including spaces"
		)
		_check(server["args"] == ["--stdio", "argument with spaces"], "Preserve argument boundaries")
	print("project setup flow: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
