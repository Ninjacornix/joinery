extends SceneTree
## Two flows, chosen by the runner:
## - with the fork's --project-manager --script (tests/godot/run_startup_test.py): the agent
##   connect screen, the API form and the new-project wizard, then a fixture project;
## - with JOINERY_SETUP_DIALOG_TEST=1 inside a project (tests/godot/run_setup_tests.py): the
##   editor's Project apps dialog and the app rail against a fixture Joinery CLI, checking
##   readiness rows, Check again as a job that never blocks, and features planned before
##   they apply.

const STEP_TITLES := ["What are you making?", "Features and apps", "Brief and art direction"]
const EXISTING_PROJECT := (
	"This folder already has a Godot project. Open it from the project list, or pick an "
	+ "empty folder."
)
const NOT_EMPTY := (
	"This folder isn't empty. Pick an empty folder for a new game, so nothing already in it "
	+ "gets overwritten."
)
## The dialog flow's addon scripts, loaded only in that flow: the Project Manager run has
## no project to load them from.
const ADDON := "res://addons/joinery/"

var _failed := false
var _calls_path := ""


func _initialize() -> void:
	# Joinery screens animate in a graphical run; the flow checks end states.
	Engine.set_meta("joinery_motion_enabled", false)
	if OS.get_environment("JOINERY_SETUP_DIALOG_TEST") == "1":
		_run_dialog.call_deferred()
	else:
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


## The visible button reading [param text], when several share it across steps.
func _shown_button(parent: Node, text: String) -> Button:
	for node: Node in parent.find_children("*", "Button", true, false):
		if node.text == text and (node as Button).is_visible_in_tree():
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
	_check(_label_text(dialog, STEP_TITLES[0]) != null, "Step 1 asks what you are making")
	_check(not dialog.get_ok_button().is_visible_in_tree(), "The wizard brings its own footer")
	var initial_rect := Rect2i(dialog.position, dialog.size)
	# Next waits for the starting points; the first card (blank) is chosen by default.
	var cards_ready := func() -> bool:
		for node: Node in dialog.find_children("*", "Button", true, false):
			if (node as Button).theme_type_variation == &"JoineryCard":
				if (node as Button).is_visible_in_tree():
					return true
		return false
	_check(await _until(cards_ready, 60.0), "Step 1 shows the starting points as cards")
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
	var path_edit: LineEdit
	for line: Node in dialog.find_children("*", "LineEdit", true, false):
		if line.get_accessibility_name() == "Project Name:":
			line.text = "Joinery Setup Check"
			line.text_changed.emit(line.text)
		elif line.get_accessibility_name() == "Project Path:":
			path_edit = line as LineEdit
	_check(path_edit != null, "Step 1 has a folder field")
	if path_edit != null:
		await _check_folder_refusals(dialog, path_edit)
		path_edit.text = OS.get_environment("JOINERY_SETUP_CHECK_DIR")
		path_edit.text_changed.emit(path_edit.text)
		await process_frame
		_check(_label_text(dialog, NOT_EMPTY) == null, "An empty folder is fine")
	await _capture(dialog, "startup-setup-details.png")
	var next := _primary(dialog)
	_check(next != null and next.text == "Next", "Step 1 offers Next as its one primary action")
	if next == null:
		return
	next.pressed.emit()
	await create_timer(0.2).timeout
	_check(_label_text(dialog, STEP_TITLES[1]) != null, "Step 2 is Features and apps")
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect, "Apps must keep the window rectangle"
	)
	# The apps sit behind one summary line once the list loads; its Change… opens the rows.
	var apps_change := func() -> Button:
		for node: Node in dialog.find_children("*", "Button", true, false):
			if node.get_accessibility_name() == "Change the apps" and node.is_visible_in_tree():
				return node
		return null
	await _until(func() -> bool: return apps_change.call() != null, 60.0)
	var change: Button = apps_change.call()
	_check(change != null and change.is_visible_in_tree(), "Step 2 sums up the apps with Change…")
	if change != null:
		change.button_pressed = true
		await process_frame
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
	_check(_label_text(dialog, STEP_TITLES[2]) != null, "Step 3 is Brief and art direction")
	_check(
		Rect2i(dialog.position, dialog.size) == initial_rect,
		"Step 3 must keep the window rectangle"
	)
	var first_field: Control
	for field: Node in dialog.find_children("*", "TextEdit", true, false):
		if field.is_visible_in_tree() and (field as TextEdit).editable:
			first_field = field
			(field as TextEdit).text = "Test answer retained when navigating back."
			break
	_check(first_field != null, "Step 3 asks at least one question")
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
	var failed := func() -> bool: return _shown_button(dialog, "Try again") != null
	# project.godot is written before the steps run: wait for the end of the run instead.
	var finished := func() -> bool:
		var open := _shown_button(dialog, "Open project")
		return not dialog.visible or failed.call() or open != null
	while not finished.call() and Time.get_ticks_msec() < deadline:
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


## Step 1 refuses a folder that already holds a game, or any other files, and writes
## nothing there even when its primary action is pressed anyway.
func _check_folder_refusals(dialog: ConfirmationDialog, path_edit: LineEdit) -> void:
	var base := OS.get_environment("JOINERY_SETUP_CHECK_DIR")
	var game := base + "-existing-game"
	var settings := '[application]\nconfig/name="Their Game"\nrun/main_scene="res://main.tscn"\n'
	var files := {
		game.path_join("project.godot"): settings,
		game.path_join("scripts/Player.cs"): "public partial class Player {}\n",
		base + "-not-empty/notes.txt": "keep me\n",
		base + "-gitignore/.gitignore": "*.tmp\n",
	}
	for file_path: String in files:
		DirAccess.make_dir_recursive_absolute(file_path.get_base_dir())
		var file := FileAccess.open(file_path, FileAccess.WRITE)
		file.store_string(files[file_path])
		file.close()
	var cases := {
		game: EXISTING_PROJECT,
		base + "-not-empty": NOT_EMPTY,
		base + "-gitignore": NOT_EMPTY,
	}
	for folder: String in cases:
		path_edit.text = folder
		path_edit.text_changed.emit(path_edit.text)
		await process_frame
		_check(_label_text(dialog, cases[folder]) != null, "Say why %s can't be used" % folder)
		var next := _primary(dialog)
		_check(next != null and next.disabled, "A folder with files blocks Next: " + folder)
		if next != null:
			# Even a forced press must not move on or write anything.
			next.pressed.emit()
			await process_frame
			_check(_label_text(dialog, STEP_TITLES[0]) != null, "A forced press stays on step 1")
	_check(FileAccess.get_file_as_string(game.path_join("project.godot")) == settings, "Untouched")
	for name: String in ["joinery.toml", "icon.svg", "export_presets.cfg", ".gitignore"]:
		_check(not FileAccess.file_exists(game.path_join(name)), "Nothing written: " + name)


# --- Project apps dialog and app rail (JOINERY_SETUP_DIALOG_TEST) -----------------------------


func _run_dialog() -> void:
	var state := OS.get_environment("JOINERY_SETUP_STATE")
	_calls_path = state.path_join("calls")
	Engine.set_meta("joinery_ui_config", state.path_join("journeyman.cfg"))
	Engine.remove_meta("joinery_ui_prefs")
	var theme_script: GDScript = load(ADDON + "ui_theme.gd")
	theme_script.call("set_pref", "theme", "dark")
	var prefix := PackedStringArray(JSON.parse_string(OS.get_environment("JOINERY_FAKE_CLI")))
	var dialog: AcceptDialog = (load(ADDON + "project_setup.gd") as GDScript).new()
	dialog.set("cli_prefix", prefix)
	dialog.set("start_docker_argv", PackedStringArray())
	root.add_child(dialog)
	# The window settles to its requested size over the first frames; open the dialog after.
	var settled := func() -> bool:
		return DisplayServer.get_name() == "headless" or root.get_visible_rect().size.y <= 900
	await _until(settled, 3.0)
	dialog.call("open_for_current_project")
	await _check_rows(dialog)
	if not _failed:
		await _captures(dialog, "setup-apps")
		await _check_actions(dialog)
		await _check_features(dialog)
		await _captures(dialog, "setup-features", dialog.find_child("Features", true, false))
		await _check_history(dialog)
	dialog.hide()
	if not _failed:
		await _check_rail(prefix)
	theme_script.call("set_pref", "theme", "match")
	print("project setup dialog: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## Wait up to [param seconds] for [param ready] to return true, drawing frames meanwhile.
func _until(ready: Callable, seconds := 20.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while not ready.call() and Time.get_ticks_msec() < deadline:
		await process_frame
	return ready.call()


## Every CLI call the fixture saw, as argument arrays.
func _calls() -> Array:
	var calls: Array = []
	var names := Array(DirAccess.get_files_at(_calls_path))
	names.sort()
	for file_name: String in names:
		if file_name.ends_with(".json"):
			var call: Variant = JSON.parse_string(
				FileAccess.get_file_as_string(_calls_path.path_join(file_name))
			)
			if call is Array:
				calls.append(call)
	return calls


func _called(words: Array) -> bool:
	for call: Variant in _calls():
		var hit := true
		for word: Variant in words:
			hit = hit and (call as Array).has(word)
		if hit:
			return true
	return false


func _row(dialog: Node, tool_id: String) -> Control:
	return dialog.find_child("App_" + tool_id.replace("-", "_"), true, false) as Control


## Readiness on a row: [wording, dot state, action label or "" when hidden, reason].
func _readiness(row: Control) -> Array:
	var badge: Control = row.get("state")
	var action: Button = badge.get("action")
	var reason: Label = row.get("reason")
	return [
		(badge.get("wording") as Label).text,
		str((badge.get("dot") as Control).get("state")) if badge.visible else "",
		action.text if action.visible else "",
		reason.text if reason.is_visible_in_tree() else "",
	]


func _check_rows(dialog: AcceptDialog) -> void:
	var loaded := func() -> bool: return _row(dialog, "krita") != null
	_check(await _until(loaded), "The app rows load from joinery tools")
	if _failed:
		return
	var asked_twice := func() -> bool:
		var count := 0
		for call: Variant in _calls():
			count += int((call as Array).has("--background"))
		return count >= 2 and _readiness(_row(dialog, "surge-xt"))[0] == "Ready"
	_check(await _until(asked_twice), "Readiness is asked again while it is still checking")
	var expected := {
		"godot": ["Downloading…", "working", ""],
		"blender": ["Ready", "ready", ""],
		"krita": ["Needs Docker", "attention", "Start Docker"],
		"inkscape": ["Not available yet", "off", "Rebuild"],
		"gimp": ["Not available yet", "off", "Check again"],
		"surge-xt": ["Ready", "ready", ""],
	}
	for id: String in expected:
		var shown := _readiness(_row(dialog, id))
		_check(shown.slice(0, 3) == expected[id], "%s shows %s, not %s" % [id, expected[id], shown])
	_check(
		"Docker" in str(_readiness(_row(dialog, "krita"))[3]), "Needs Docker says why under the row"
	)
	_check(
		"MIDI" in str(_readiness(_row(dialog, "surge-xt"))[3]),
		"A ready Surge XT still says which of its features is missing"
	)
	for id: String in ["aseprite", "reaper", "musescore", "audacity"]:
		_check(_row(dialog, id) == null, "No row for the scrapped app " + id)


func _check_actions(dialog: AcceptDialog) -> void:
	# Check again runs as a job: the editor keeps drawing frames while the CLI works.
	var gimp := _row(dialog, "gimp")
	var action: Button = (gimp.get("state") as Control).get("action")
	action.pressed.emit()
	_check(_readiness(gimp)[0] == "Checking…", "Check again says it is checking at once")
	var frames := 0
	var longest := 0
	var last := Time.get_ticks_msec()
	while _readiness(gimp)[0] != "Ready" and frames < 100000:
		await process_frame
		frames += 1
		var now := Time.get_ticks_msec()
		longest = maxi(longest, now - last)
		last = now
	_check(_called(["status", "--tool", "gimp", "--refresh"]), "Check again asks joinery status")
	_check(frames > 10, "The editor draws frames while Check again runs (%d)" % frames)
	_check(longest < 250, "No frame waits on the CLI (longest %d ms)" % longest)
	_check(_readiness(gimp).slice(0, 3) == ["Ready", "ready", ""], "Checked again: GIMP is ready")
	# Start Docker shows how, with Check again beside it.
	var krita := _row(dialog, "krita")
	((krita.get("state") as Control).get("action") as Button).pressed.emit()
	var howto: Control = krita.get("howto")
	_check(howto.is_visible_in_tree(), "Start Docker shows how to start it")
	var again := howto.find_child("Action", true, false) as Button
	_check(again != null and again.text == "Check again", "The how-to offers Check again")
	# Rebuild runs joinery bootstrap as a job.
	var inkscape := _row(dialog, "inkscape")
	((inkscape.get("state") as Control).get("action") as Button).pressed.emit()
	_check(await _until(func() -> bool: return _called(["bootstrap"])), "Rebuild runs bootstrap")
	var settled := func() -> bool: return dialog.get("_rebuild_job") == null
	_check(await _until(settled), "The rebuild job finishes")


func _check_features(dialog: AcceptDialog) -> void:
	var section := dialog.find_child("Features", true, false) as Control
	var shown := func() -> bool: return section != null and section.is_visible_in_tree()
	_check(await _until(shown), "Game features are listed")
	if _failed:
		return
	var online := section.find_child("Feature_online_play", true, false) as Control
	var steam := section.find_child("Feature_steam", true, false) as Control
	var saves := section.find_child("Feature_save_load", true, false) as Control
	_check(online != null and steam != null and saves != null, "One switch per feature")
	if _failed:
		return
	_check((saves.get("switch_button") as CheckButton).button_pressed, "Applied features are on")
	var steam_switch := steam.get("switch_button") as CheckButton
	_check(steam_switch.disabled, "Steam can't be added before online play")
	_check(
		(steam.get("reason") as Label).text == "Needs online play first.",
		"A disabled switch says why"
	)
	var switch := online.get("switch_button") as CheckButton
	switch.button_pressed = true
	var plan := func() -> Control: return section.find_child("FeaturePlan", true, false) as Control
	var planned := func() -> bool:
		var panel: Control = plan.call()
		return panel != null and not (panel.get("apply") as Button).disabled
	_check(await _until(planned), "Turning a feature on shows its plan first")
	_check(_called(["feature", "add", "online-play", "--plan"]), "The plan comes from --plan")
	_check(not _called(["--yes"]), "Nothing is applied before Apply")
	var texts := _label_texts(plan.call())
	_check("Changes 10 files:" in texts, "The plan says how many files change")
	_check("and 2 more" in texts, "A long plan ends with how many more")
	await _captures(dialog, "setup-plan", plan.call())
	((plan.call() as Control).get("apply") as Button).pressed.emit()
	var saves_switch := saves.get("switch_button") as CheckButton
	saves_switch.button_pressed = false
	_check(saves_switch.button_pressed, "Another switch waits while a change applies")
	_check(plan.call() != null, "The running change keeps its plan")
	_check(await _until(func() -> bool: return _called(["--yes"])), "Apply applies the change")
	var status := dialog.get("_status") as Label
	var done := func() -> bool: return status.visible and status.text == "Added online play."
	_check(await _until(done), "Applying says what changed")
	var refreshed := func() -> bool:
		var row := section.find_child("Feature_online_play", true, false) as Control
		return row != null and (row.get("switch_button") as CheckButton).button_pressed
	_check(await _until(refreshed), "The applied feature stays on")


## "Older versions": the saved choice, what a smaller one would remove (a dry run, before
## Save), Custom's checked numbers, and Save passing the change to `joinery setup`.
func _check_history(dialog: AcceptDialog) -> void:
	var section := dialog.find_child("OlderVersions", true, false) as Control
	var shown := func() -> bool: return section != null and section.is_visible_in_tree()
	_check(await _until(shown), "Older versions shows what the project keeps")
	if _failed:
		return
	_check(_called(["history", "policy", "--json"]), "It reads joinery history policy")
	var picker := section.get("picker") as OptionButton
	_check(picker.get_item_text(picker.selected) == "Last 50 changes", "The saved choice shows")
	var items: Array[String] = []
	for index: int in picker.item_count:
		items.append(picker.get_item_text(index))
	_check(
		items == ["Last 50 changes", "Last 10 changes", "Only the newest", "Custom…"],
		"Three plain choices and Custom: %s" % [items]
	)
	_check(picker.get_item_tooltip(1) == "Last 10 changes, up to 1 GB", "Tooltips give the space")
	picker.select(2)
	picker.item_selected.emit(2)
	var note := section.get("note") as Label
	var warned := func() -> bool: return note.is_visible_in_tree()
	_check(await _until(warned), "A smaller choice says what Save removes")
	_check(
		note.text == "Saving frees 120 MB. 3 older changes can no longer be reverted.",
		"The note says how much it frees: " + note.text
	)
	_check(_called(["history", "policy", "--dry-run", "--keep", "1"]), "The note is a dry run")
	_check(not _called(["--history-keep"]), "Nothing is saved before Save")
	picker.select(3)
	picker.item_selected.emit(3)
	var panel := section.get("panel") as Control
	_check(panel.is_visible_in_tree(), "Custom shows its two fields")
	var keep := (panel.get("keep") as Control).get("control") as LineEdit
	var space := (panel.get("space") as Control).get("control") as LineEdit
	_check(keep.text == "50" and space.text == "2", "Custom starts from the saved numbers")
	keep.text = "none"
	dialog.get_ok_button().pressed.emit()
	var error := (panel.get("keep") as Control).get("error") as Label
	_check(error.visible and error.text == "Enter a whole number, 1 or more", "Bad numbers say why")
	_check(dialog.get("_save_job") == null, "Save waits for good numbers")
	keep.text = "10"
	keep.text_changed.emit("10")
	space.text = "1"
	space.text_changed.emit("1")
	_check(await _until(warned), "Custom numbers are checked too")
	await _captures(dialog, "setup-history", section)
	var saved := [false]
	dialog.connect("history_changed", func() -> void: saved[0] = true)
	dialog.get_ok_button().pressed.emit()
	var called := func() -> bool:
		return _called(["setup", "--history-keep", "10", "--history-max-mb", "1024"])
	_check(await _until(called), "Save passes the new choice to joinery setup")
	_check(await _until(func() -> bool: return saved[0]), "Saving says history changed")


func _label_texts(node: Node) -> Array[String]:
	var texts: Array[String] = []
	if node == null:
		return texts
	for label: Node in node.find_children("*", "Label", true, false):
		texts.append((label as Label).text)
	return texts


func _check_rail(prefix: PackedStringArray) -> void:
	var host := PanelContainer.new()
	host.position = Vector2(16, 16)
	host.size = Vector2(64, 560)
	root.add_child(host)
	var rail: VBoxContainer = (load(ADDON + "tool_rail.gd") as GDScript).new()
	rail.set("cli_prefix", prefix)
	rail.set("start_docker_argv", PackedStringArray())
	host.add_child(rail)
	var tools: Array = []
	for id: String in ["godot", "blender", "krita", "surge-xt"]:
		tools.append({"id": id, "name": id.capitalize(), "domain": "3d", "enabled": true})
	rail.call("update_tools", tools, [])
	var known := func() -> bool: return (rail.get("_readiness") as Dictionary).has("krita")
	_check(await _until(known), "The rail reads joinery status")
	_check(_called(["status", "--catalog", "--background"]), "The rail asks status, not tools")
	var item := (rail.get("_items") as Dictionary).get("krita") as Control
	var dot: Control = item.get("dot")
	_check(dot.visible, "An app that needs something shows a mark on the rail")
	var krita_button: Button = (rail.get("_buttons") as Dictionary)["krita"]
	_check(krita_button.tooltip_text.contains("Needs Docker"), "The tooltip names its readiness")
	rail.call("_show_card", "krita")
	_check(krita_button.tooltip_text.is_empty(), "No tooltip over an open card")
	var card := rail.find_child("ReadinessCard", true, false) as Control
	_check(card != null and card.visible, "Hover or focus shows the readiness card")
	if card == null:
		return
	var sentence := card.find_child("Sentence", true, false) as Label
	_check("Docker" in sentence.text, "The card says what the app needs")
	var action := card.find_child("Action", true, false) as Button
	_check(action.visible and action.text == "Start Docker", "The card offers the one action")
	var again := card.find_child("CheckAgain", true, false) as Button
	_check(again != null and again.visible, "The card offers Check again")
	var stamp := card.find_child("LastChecked", true, false) as Label
	_check(stamp.text.begins_with("Last checked "), "The card says when it was last checked")
	await _captures(null, "rail-card")
	rail.call("_show_card", "blender")
	await process_frame
	_check(
		not again.visible and not stamp.visible,
		"A ready app's card has no Check again and no check time"
	)
	host.queue_free()


## Save "<name>-dark.png" and "<name>-light.png" of the whole window when capturing, with
## [param show] scrolled into view.
func _captures(dialog: AcceptDialog, name: String, show: Control = null) -> void:
	if DisplayServer.get_name() == "headless" or OS.get_environment("JOINERY_CAPTURE_DIR") == "":
		return
	var theme_script: GDScript = load(ADDON + "ui_theme.gd")
	if show != null:
		var scroll := show.get_parent()
		while scroll != null and not scroll is ScrollContainer:
			scroll = scroll.get_parent()
		for _frame: int in 3:
			await process_frame
		if scroll != null:
			# Bring the bottom of [param show] to the bottom of the list.
			var box := scroll as ScrollContainer
			var bottom := show.get_global_rect().end.y - box.get_global_rect().end.y
			box.scroll_vertical = int(box.scroll_vertical + maxf(bottom, 0.0))
	for mode: String in ["dark", "light"]:
		theme_script.call("set_pref", "theme", mode)
		# The editor paints windows in its own theme; stand in for it with Joinery's surfaces.
		var context: Node = dialog if dialog != null else root.get_child(0)
		RenderingServer.set_default_clear_color(theme_script.call("color", context, "surface"))
		if dialog != null:
			dialog.remove_theme_stylebox_override("panel")
			var panel := dialog.get_theme_stylebox("panel").duplicate()
			if panel is StyleBoxFlat:
				(panel as StyleBoxFlat).bg_color = theme_script.call("color", context, "surface")
			dialog.add_theme_stylebox_override("panel", panel)
		for _frame: int in 6:
			await process_frame
		await _capture(root, "%s-%s.png" % [name, mode])
	theme_script.call("set_pref", "theme", "dark")
	for _frame: int in 3:
		await process_frame
