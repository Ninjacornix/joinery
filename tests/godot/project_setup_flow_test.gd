extends SceneTree
## Run with the fork's --project-manager --script and a temporary output directory.
## Walks the agent connect screen, the API form and the three-step new-project wizard
## (Name your game, Choose your apps, Look and sound), then creates a fixture project.

const STEP_TITLES := ["Name your game", "Choose your apps", "Look and sound"]

var _failed := false


func _initialize() -> void:
	# Joinery screens animate in a graphical run; the flow checks end states.
	Engine.set_meta("joinery_motion_enabled", false)
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


## The one visible accent-filled action in [param parent] (the wizard's Next).
func _primary(parent: Node) -> Button:
	for node: Node in parent.find_children("*", "Button", true, false):
		var button := node as Button
		if button.theme_type_variation == &"JoineryPrimaryButton" and button.is_visible_in_tree():
			return button
	return null


func _label_text(parent: Node, text: String) -> Label:
	for node: Node in parent.find_children("*", "Label", true, false):
		if (node as Label).text == text and (node as Label).is_visible_in_tree():
			return node as Label
	return null


func _capture(window: Window, filename: String) -> void:
	_check(DisplayServer.get_name() != "headless", "Startup captures require a graphical renderer")
	if DisplayServer.get_name() == "headless":
		return
	var directory := OS.get_environment("JOINERY_CAPTURE_DIR")
	_check(not directory.is_empty(), "Startup capture directory must be supplied")
	if directory.is_empty():
		return
	for _frame: int in 2:
		await process_frame
	RenderingServer.force_draw()
	var image := window.get_texture().get_image()
	_check(image != null and not image.is_empty(), "Startup framebuffer must contain pixels")
	if image == null or image.is_empty():
		return
	_check(
		image.save_png(directory.path_join(filename)) == OK, "Startup PNG must save: " + filename
	)


func _fail_now(message: String) -> void:
	_check(false, message)
	print("project setup flow: FAILED")
	quit(1)


func _run() -> void:
	await create_timer(1.0).timeout
	var create := _create_action()
	if create == null:
		_fail_now("Project Manager Create button must exist")
		return
	var connect := root.find_child("JoineryAgentConnect", true, false) as Control
	if connect == null:
		_fail_now("Agent connection screen must exist")
		return
	_check(connect.is_visible_in_tree(), "The connect screen opens first when no agent is set")
	_check(not create.is_visible_in_tree(), "Projects must wait for connection")
	create.pressed.emit()
	for window: Node in root.find_children("*", "ConfirmationDialog", true, false):
		_check(not window.visible, "Create shortcut must wait for an agent connection")
	for id: String in ["claude", "codex", "gemini", "opencode"]:
		_check(connect._buttons.has(id), "Offer agent: " + id)
		if not connect._buttons.has(id):
			continue
		var choice: Button = connect._buttons[id]
		_check(choice.get_parent() is VBoxContainer, "Agent choices must form one vertical list")
		var marks := choice.find_children("*", "TextureRect", true, false)
		var labelled := choice.find_children("*", "Label", true, false)
		_check(
			(
				(not marks.is_empty() and (marks[0] as TextureRect).texture != null)
				or not labelled.is_empty()
			),
			"Each agent choice must have an icon tile"
		)
	await _capture(root, "startup-agent-connect.png")
	# A local fixture tests UI gating without changing any account or user preference.
	if connect._job != null:
		connect._job.cancel()
		connect._job = null
	connect._selected = ""
	connect._update()
	_check(not connect._can_continue({}), "No selected agent means no continuation")
	connect._agents = {
		"codex":
		{
			"connected": false,
			"installed": true,
			"method": "",
			"api_key_configured": false,
		}
	}
	connect._select("codex")
	_check(not connect._can_continue(connect._agents["codex"]), "Sign-in is required")
	connect._finish()
	_check(connect.is_visible_in_tree(), "Enter or synthetic press cannot bypass sign-in")
	connect._agents["codex"]["connected"] = true
	connect._agents["codex"]["supported"] = false
	connect._agents["codex"]["reason"] = "Fixture connection is unavailable here."
	connect._update()
	_check(
		not connect._can_continue(connect._agents["codex"]),
		"An authenticated unsupported connection must not advance"
	)
	_check(
		connect._detail.text == "Fixture connection is unavailable here.",
		"Explain the platform restriction instead of asking for another sign-in"
	)
	_check(not connect._login.visible, "Do not offer sign-in for an unavailable connection")
	connect._agents["codex"].erase("supported")
	connect._agents["codex"].erase("reason")
	connect._agents["codex"]["method"] = "Test account"
	connect._agents["codex"]["requires_key_confirmation"] = true
	connect._update()
	_check(not connect._can_continue(connect._agents["codex"]), "Activate an environment key")
	# The Project Manager's own handler records the agent (in the isolated editor
	# settings) and shows the project list with the agent chip.
	connect._show_api()
	_check(connect._api_form.visible, "API settings must open as a submenu")
	_check(connect._api_form._key.secret, "The key input must be masked")
	connect._agents["opencode"] = {
		"connected": true,
		"installed": true,
		"supported": false,
		"reason": "Fixture API backend is unavailable here.",
	}
	connect._api_form.configure(connect._agents, "codex")
	_check(
		connect._api_form._driver.is_item_disabled(1),
		"API submenu must disable an installed unsupported backend"
	)
	connect._api_form._driver.select(1)
	connect._api_form._update_hint()
	_check(connect._api_form._save.disabled, "An unavailable backend cannot save and connect")
	_check(
		connect._api_form._hint.text == "Fixture API backend is unavailable here.",
		"Explain the API backend restriction"
	)
	connect._api_form._driver.select(0)
	connect._api_form._update_hint()
	connect._api_form._endpoint.text = OS.get_environment("JOINERY_TEST_MODEL_ENDPOINT")
	connect._api_form._model.text = "fixture-model"
	connect._api_form._key.text = "TEST_ONLY"
	await _capture(root, "startup-api-submenu.png")
	connect._api_form._submit()
	var api_deadline := Time.get_ticks_msec() + 15000
	while connect._job != null and Time.get_ticks_msec() < api_deadline:
		await process_frame
	await process_frame
	_check(connect.selected_profile.begins_with("api_"), "Connect a saved API profile")
	create = _create_action()
	_check(create.is_visible_in_tree(), "Saving an API profile redirects to projects")
	_check(connect._api_form._key.text.is_empty(), "Successful save clears the key field")
	_check(not connect._message.text.contains("TEST_ONLY"), "Never echo the API key")
	# The agent chip at the end of the project list's top bar reopens the connect screen.
	var chip: Button = null
	for node: Node in root.find_children("*", "Button", true, false):
		if (node as Button).text == "Change" and (node as Button).is_visible_in_tree():
			chip = node as Button
	_check(chip != null, "The project list shows the agent in use with a Change link")
	if chip != null:
		chip.pressed.emit()
		_check(connect.is_visible_in_tree(), "The agent chip reopens the connect screen")
	connect._select("codex")
	connect._accepted_keys.append("codex")
	connect._auto_redirect = true
	connect._update()
	await process_frame
	create = _create_action()
	_check(create.is_visible_in_tree(), "Authenticated selection must redirect automatically")
	_check(not connect._login.visible, "Do not offer sign-in again for a connected account")
	if _failed:
		print("project setup flow: FAILED")
		quit(1)
		return
	await _wizard(create)
	print("project setup flow: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _wizard(create: Button) -> void:
	create.pressed.emit()
	await create_timer(0.3).timeout
	var dialog: ConfirmationDialog
	for node: Node in root.find_children("*", "ConfirmationDialog", true, false):
		if node is Window and node.visible and node.get_class() == "ProjectDialog":
			dialog = node
			break
	_check(dialog != null, "The new-project wizard must open")
	if dialog == null:
		return
	_check(dialog.title == "New project", "The wizard window is titled in sentence case")
	_check(_label_text(dialog, "Step 1 of 3") != null, "Step 1 says where it is")
	_check(_label_text(dialog, STEP_TITLES[0]) != null, "Step 1 is Name your game")
	_check(not dialog.get_ok_button().is_visible_in_tree(), "The wizard brings its own footer")
	var initial_rect := Rect2i(dialog.position, dialog.size)
	# The renderer sits behind a collapsed Advanced disclosure.
	var forward := _button(dialog, "Forward+")
	var advanced := _button(dialog, "Advanced")
	_check(advanced != null and advanced.is_visible_in_tree(), "Advanced is offered on step 1")
	_check(forward == null or not forward.is_visible_in_tree(), "The renderer starts collapsed")
	if advanced != null:
		advanced.button_pressed = true
		await process_frame
		var choices: Array[Button] = []
		for text: String in ["Forward+", "Mobile", "Compatibility"]:
			var choice: Button = null
			for node: Node in dialog.find_children("*", "CheckBox", true, false):
				if (node as CheckBox).text == text and (node as CheckBox).is_visible_in_tree():
					choice = node as CheckBox
			_check(choice != null, "Advanced lists the renderer " + text)
			if choice != null:
				choices.append(choice)
		var chosen := choices.filter(func(button: Button) -> bool: return button.button_pressed)
		_check(chosen.size() == 1, "Exactly one renderer is chosen by default")
		advanced.button_pressed = false
		await process_frame
	for line: Node in dialog.find_children("*", "LineEdit", true, false):
		if line.get_accessibility_name() == "Project Name:":
			line.text = "Joinery Setup Check"
			line.text_changed.emit(line.text)
		elif line.get_accessibility_name() == "Project Path:":
			line.text = OS.get_environment("JOINERY_SETUP_CHECK_DIR")
			line.text_changed.emit(line.text)
	await _capture(dialog, "startup-setup-details.png")
	var next := _primary(dialog)
	_check(next != null and next.text == "Next", "Step 1 offers Next as its one primary action")
	if next == null:
		return
	next.pressed.emit()
	await create_timer(0.2).timeout
	_check(_label_text(dialog, STEP_TITLES[1]) != null, "Step 2 is Choose your apps")
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect, "Apps must keep the window rectangle"
	)
	# The app list loads on a worker thread; wait for its rows.
	var picker: OptionButton
	var alternative := -1
	var deadline := Time.get_ticks_msec() + 60000
	while picker == null and Time.get_ticks_msec() < deadline:
		await create_timer(0.2).timeout
		for candidate: Node in dialog.find_children("*", "OptionButton", true, false):
			for index: int in candidate.item_count:
				# The picker shows a name for people; the raw id leads its tooltip.
				if candidate.get_item_tooltip(index).get_slice("\n", 0) == "hi-godot/godot-ai":
					picker = candidate
					alternative = index
	_check(picker != null, "Godot connector alternatives must be listed")
	if picker == null:
		return
	_check(_label_text(dialog, "Engine") != null, "Apps are grouped under section labels")
	_check(not picker.is_visible_in_tree(), "The connector starts collapsed")
	_check(not picker.is_item_disabled(alternative), "Non-preferred connector must be selectable")
	_check(
		picker.get_item_text(picker.item_count - 1) == "Use my own…",
		"The last connector choice is the user's own"
	)
	var connector := picker.get_parent() as Control
	# Every app's connection sits under the step's one Advanced disclosure.
	var disclosure: Button
	for node: Node in dialog.find_children("*", "Button", true, false):
		if (node as Button).text == "Advanced" and (node as Button).is_visible_in_tree():
			disclosure = node
	_check(disclosure != null, "The apps step has an Advanced disclosure")
	if disclosure != null:
		disclosure.button_pressed = true
		await process_frame
	_check(picker.is_visible_in_tree(), "Advanced shows the connection pickers")
	picker.select(alternative)
	picker.item_selected.emit(alternative)
	await process_frame
	var notes: Array[String] = []
	for node: Node in connector.get_children():
		if node is Label and (node as Label).visible:
			notes.append((node as Label).text)
	_check(
		notes.has("Not recommended: sends telemetry."),
		"Say plainly why an alternative is not recommended"
	)
	next.pressed.emit()
	await process_frame
	_check(next.text == "Next", "An incomplete custom choice must not advance")
	_check(picker.is_visible_in_tree(), "An incomplete choice must stay on the apps page")
	_check(
		_label_text(dialog, "Enter the command that starts this connector.") != null,
		"The missing command is named under its field"
	)
	var fields: Array[LineEdit] = []
	for name: String in ["Command", "Arguments"]:
		for node: Node in connector.find_children("*", "LineEdit", true, false):
			if (node as LineEdit).get_accessibility_name() == name:
				fields.append(node as LineEdit)
	_check(fields.size() == 2, "A custom connector asks for Command and Arguments")
	if fields.size() == 2:
		fields[0].text = OS.get_environment("JOINERY_TEST_MCP_COMMAND")
		fields[1].text = '--stdio "argument with spaces"'
	await _capture(dialog, "startup-setup-tools.png")
	next.pressed.emit()
	await create_timer(0.2).timeout
	_check(_label_text(dialog, STEP_TITLES[2]) != null, "Step 3 is Look and sound")
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect,
		"Look and sound must keep the window rectangle"
	)
	var first_field: Control
	for field: Node in dialog.find_children("*", "TextEdit", true, false):
		if field.is_visible_in_tree() and (field as TextEdit).editable:
			first_field = field
			(field as TextEdit).text = "Test answer retained when navigating back."
			break
	_check(first_field != null, "Look and sound asks at least one question")
	_check(next.text == "Create project", "The last step offers Create project")
	await _capture(dialog, "startup-setup-art.png")
	var back := _button(dialog, "Back")
	_check(back != null and back.is_visible_in_tree(), "Back is offered after step 1")
	if back == null or first_field == null:
		return
	back.pressed.emit()
	await process_frame
	_check(not first_field.is_visible_in_tree(), "Back must return to the previous page")
	_check(next.text == "Next", "Back returns to a Next step")
	next.pressed.emit()
	await process_frame
	_check((first_field as TextEdit).text.contains("retained"), "Back must retain answers")
	if _failed:
		return
	# Create only the temporary fixture; do not register it or launch an editor.
	for connection: Dictionary in dialog.get_signal_connection_list("project_created"):
		dialog.disconnect("project_created", connection["callable"])
	next.pressed.emit()
	await process_frame
	_check(_label_text(dialog, "Setting up your project…") != null, "Creating shows its steps")
	var project := OS.get_environment("JOINERY_SETUP_CHECK_DIR")
	deadline = Time.get_ticks_msec() + 120000
	var failed := func() -> bool: return next.text == "Retry" and next.is_visible_in_tree()
	while (
		not FileAccess.file_exists(project.path_join("project.godot"))
		and not failed.call()
		and Time.get_ticks_msec() < deadline
	):
		await create_timer(0.2).timeout
	if failed.call():
		var details := ""
		for node: Node in dialog.find_children("*", "TextEdit", true, false):
			if not (node as TextEdit).editable:
				details = (node as TextEdit).text
		_check(false, "Creating the project failed: " + details.left(600))
		return
	_check(FileAccess.file_exists(project.path_join("project.godot")), "Create a Godot project")
	_check(FileAccess.file_exists(project.path_join("joinery.toml")), "Custom choices must save")
	var config := FileAccess.get_file_as_string(project.path_join("joinery.toml"))
	_check(config.contains('id = "hi-godot-godot-ai"'), "Save the selected alternative as custom")
	_check(
		FileAccess.file_exists(project.path_join("addons/joinery/plugin.cfg")),
		"Copy the Joinery addon into the project"
	)
	var mcp_path := project.path_join(".mcp.json")
	_check(FileAccess.file_exists(mcp_path), "Register the selected custom connector")
	if FileAccess.file_exists(mcp_path):
		var mcp: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(mcp_path))
		var server: Dictionary = mcp["mcpServers"].get("hi-godot-godot-ai", {})
		_check(
			server.get("command", "") == OS.get_environment("JOINERY_TEST_MCP_COMMAND"),
			"Preserve the chosen executable path including spaces"
		)
		_check(
			server.get("args", []) == ["--stdio", "argument with spaces"],
			"Preserve argument boundaries"
		)
