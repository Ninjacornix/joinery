@tool
extends EditorPlugin
## Install only in a temporary copy of testbed for full editor verification.

var _failed := false
var _screens: Array[String] = []


func _enter_tree() -> void:
	main_screen_changed.connect(func(screen: String) -> void: _screens.append(screen))
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	var base := EditorInterface.get_base_control()
	print("layout check: editor loaded")
	var plugin: EditorPlugin
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			plugin = node as EditorPlugin
			break
	_check(plugin != null, "Joinery plugin must load")
	if plugin == null:
		get_tree().quit(1)
		return
	_check(plugin._dock.name == "Journeyman", "Restore the earlier sidebar layout")
	_check(
		plugin._dock.size.x >= plugin._dock.get_theme_default_font_size() * 24,
		"Saved narrow layouts must open with a readable chat width"
	)
	_check(plugin._chat != null, "Start the selected agent chat transport")
	_check(
		plugin._dock.prompt_submitted.is_connected(plugin._chat.submit),
		"Ordinary prompts must reach the chat transport"
	)
	if OS.get_environment("JOINERY_EXPECT_CODEX") == "1":
		var connection_deadline := Time.get_ticks_msec() + 15000
		while not plugin._chat._connected and Time.get_ticks_msec() < connection_deadline:
			await get_tree().process_frame
		_check(plugin._chat._connected, "The startup Codex account must connect in the editor")
	var chat_before: String = plugin._dock._transcript.get_parsed_text()
	var navigation := base.find_child("JoineryNavigation", true, false) as Control
	_check(navigation != null, "Icon navigation must exist")
	var switches := base.find_child("EditorMainScreenButtons", true, false)
	_check(switches is VBoxContainer, "Workspace selectors must be vertical")
	_check(not switches.visible, "Native workspace selectors must not appear in the app rail")
	for button: Node in switches.get_children():
		if button is Button:
			_check(button.text.is_empty(), "Native workspace selectors must be icon-only")
	# Scene above Inspector in the left column, so a node and its properties show together.
	for pair: Array in [["Scene", "DockSlotLeftUR"], ["Inspector", "DockSlotLeftBR"]]:
		var docked: Node
		for candidate: Node in base.find_children("*", "EditorDock", true, false):
			if str(candidate.name) == pair[0]:
				docked = candidate
				break
		_check(docked != null, "Missing dock: " + str(pair[0]))
		if docked != null:
			_check(docked.get_parent().name == pair[1], "%s must be in %s" % pair)
			_check((docked as Control).is_visible_in_tree(), str(pair[0]) + " must be open")
	var dock_titles: Array[String] = [
		"FileSystem",
		"Import",
		"History",
		"Signals",
		"Groups",
	]
	for title: String in dock_titles:
		var dock: Node
		for candidate: Node in base.find_children("*", "EditorDock", true, false):
			if str(candidate.name) == title:
				dock = candidate
				break
		_check(dock != null, "Missing dock: " + title)
		if dock != null:
			_check(dock.get_parent().is_class("EditorBottomPanel"), title + " must be at bottom")
	_check(plugin._shell._toolbars.size() >= 1, "The viewport toolbar must be discovered")
	for control: Control in plugin._shell._toolbars:
		_check(control.visible, "Viewport controls show by default")
	var scene_tabs := base.find_children("*", "EditorSceneTabs", true, false)
	_check(not scene_tabs.is_empty(), "Scene tabs must exist")
	for tabs: Node in scene_tabs:
		_check((tabs as Control).is_visible_in_tree(), "Scene tabs (with their + button) must show")
	var sidebar := base.find_child("DockSlotRightBL", true, false) as TabContainer
	_check(not sidebar.tabs_visible, "Chat sidebar must not have a dock tab bar")
	var bottom := base.find_child("*", true, false) as Control
	for candidate: Node in base.find_children("*", "EditorBottomPanel", true, false):
		bottom = candidate as Control
	var bottom_tabs := bottom as TabContainer
	for index: int in bottom_tabs.get_tab_count():
		if bottom_tabs.get_tab_title(index) == "Groups":
			bottom_tabs.current_tab = index
			break
	await get_tree().create_timer(0.2).timeout
	var center_split := base.find_child("DockVSplitCenter", true, false) as SplitContainer
	_check(bottom.size.y >= center_split.size.y * 0.4, "Bottom docks must open with useful height")
	await _capture("/tmp/joinery-editor-bottom-height.png")
	bottom_tabs.current_tab = -1
	_check(sidebar.get_popup() == null, "Journeyman must not show a docking menu")
	sidebar.get_tab_bar().tab_rmb_clicked.emit(0)
	for popup: Node in base.find_children("*", "DockContextPopup", true, false):
		_check(not popup.visible, "Right-click must not open the sidebar docking menu")
	for version: Node in base.find_children("*", "EditorVersionButton", true, false):
		_check(not version.is_visible_in_tree(), "Bottom version info must be hidden")
	plugin._on_toggle_toolbar()
	_check(
		plugin._dock._transcript.get_parsed_text() == chat_before,
		"Toolbar diagnostics must stay out of chat"
	)
	var scene := EditorInterface.get_edited_scene_root()
	var selection := EditorInterface.get_selection()
	selection.clear()
	selection.add_node(scene.get_node("Door"))
	await get_tree().create_timer(0.1).timeout
	print("layout check: object target ", plugin._dock.target_snapshot())
	_check(
		str(plugin._dock.target_snapshot()[0]["id"]).ends_with("::Door"),
		"Engine object selection must reach Chatup"
	)
	selection.clear()
	selection.add_node(scene.get_node("Music"))
	await get_tree().create_timer(0.1).timeout
	_check(plugin._dock.target_snapshot()[0]["kind"] == "Audio", "Identify engine audio selection")
	selection.clear()
	for control: Control in plugin._shell._toolbars:
		_check(not control.visible, "Viewport controls must be toggleable")
	plugin._on_toggle_toolbar()
	EditorInterface.set_main_screen_editor("3D")
	await get_tree().create_timer(0.2).timeout
	await _capture("/tmp/joinery-editor-layout-3d.png")
	print("layout check: 3D view captured")
	var deadline := Time.get_ticks_msec() + 15000
	while plugin._workspace._command_job != null and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	_check(plugin._rail._buttons.has("blender"), "Blender must be in the rail")
	_check(plugin._rail._buttons.has("godot"), "Godot must be in the app rail")
	_check(plugin._rail._buttons.has(""), "Assets must be in the app rail")
	await _rail_states(plugin)
	_check(
		plugin._rail.find_child("ViewportToolbarToggle", true, false) == null,
		"The viewport toolbar has no rail toggle; /toolbar hides it"
	)
	for id: String in plugin._rail._buttons:
		var app_button: Button = plugin._rail._buttons[id]
		_check(app_button.icon_alignment == HORIZONTAL_ALIGNMENT_CENTER, "Center app icons")
	if plugin._rail._buttons.has("blender"):
		plugin._rail._buttons["blender"].pressed.emit()
		await get_tree().process_frame
		print("layout check: Blender selected")
		_check(plugin._workspace._tool_id() == "blender", "Rail must select Blender view")
		for asset: Dictionary in plugin._workspace._rows:
			_check(asset["tool"] == "blender", "Blender view may only list Blender-owned assets")
		for index: int in plugin._workspace._assets.item_count:
			var text: String = plugin._workspace._assets.get_item_text(index)
			_check(not text.contains(".blend"), "Display names must omit extensions")
			_check(not text.contains("_"), "Display names must be readable")
		await get_tree().create_timer(0.3).timeout
		deadline = Time.get_ticks_msec() + 10000
		while plugin._workspace._command_job != null and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._workspace._command_job == null, "Initial workspace load must finish")
		var before: String = plugin._workspace._stamp
		var lock := FileAccess.open("res://joinery.lock", FileAccess.READ_WRITE)
		lock.seek_end()
		lock.store_string("\n# automatic refresh test\n")
		lock.close()
		deadline = Time.get_ticks_msec() + 10000
		while plugin._workspace._stamp == before and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._workspace._stamp != before, "Workspace must refresh automatically")
		print("layout check: automatic refresh checked")
		var viewer := plugin._workspace._preview.get_child(0) as SubViewportContainer
		_check(viewer != null, "Model viewer must be available")
		var camera := (viewer.get_child(0) as SubViewport).get_camera_3d()
		var zoom := InputEventMouseButton.new()
		zoom.position = viewer.get_global_rect().get_center()
		zoom.global_position = zoom.position
		zoom.pressed = true
		zoom.button_index = MOUSE_BUTTON_WHEEL_UP
		var distance := camera.position.z
		get_tree().root.push_input(zoom)
		_check(camera.position.z < distance, "Wheel must zoom in the real editor")
		zoom.button_index = MOUSE_BUTTON_WHEEL_DOWN
		get_tree().root.push_input(zoom)
		_check(camera.position.z > distance * 0.9, "Wheel must zoom out in the real editor")
		_check(plugin._workspace._preview.get_child_count() == 1, "Model has no extra controls")
		for button: Node in plugin._workspace.find_children("*", "Button", true, false):
			_check(button.text != "Refresh", "No manual Refresh button")
		await _capture("/tmp/joinery-editor-layout-tools.png")
		plugin._workspace.select_resource("res://art/sky/src/dusk_sky.blend")
		var selected: PackedInt32Array = plugin._workspace._assets.get_selected_items()
		plugin._workspace._assets.item_selected.emit(selected[0])
		_check(
			plugin._workspace._preview.get_child(0) is TextureRect,
			"Sky source must show its rendered counterpart"
		)
		_check(
			plugin._dock.target_snapshot()[0]["id"] == "res://art/sky/src/dusk_sky.blend",
			"Preview selection must target the native sky source"
		)
		plugin._dock._prompt.text = "/"
		plugin._dock._update_commands("/")
		await get_tree().process_frame
		_check(plugin._dock._command_popup.visible, "Slash command menu must open")
		_check(
			(
				plugin._dock._command_popup.get_global_rect().end.y
				<= plugin._dock._prompt.global_position.y
			),
			"Slash command menu must open upward"
		)
		await _capture("/tmp/joinery-editor-chatup.png")
		plugin._dock._prompt.text = "/hel"
		plugin._dock._update_commands("/hel")
		plugin._dock._prompt.grab_focus()
		var enter := InputEventKey.new()
		enter.keycode = KEY_ENTER
		enter.pressed = true
		get_tree().root.push_input(enter)
		_check(
			plugin._dock._transcript.get_parsed_text().contains("You  /hel"),
			"Enter must send a slash command in the real editor"
		)
		_check(not plugin._dock._command_popup.visible, "Enter must dismiss suggestions")
		await _capture("/tmp/joinery-editor-chat-sent.png")
	if plugin._rail._buttons.has("supercollider"):
		plugin._rail._buttons["supercollider"].pressed.emit()
		await get_tree().process_frame
		_check(
			plugin._workspace._audio.stream != null, "Audio app view must show playback controls"
		)
		await _capture("/tmp/joinery-editor-layout-audio.png")
	await _review_in_focus(plugin)
	await _single_accept_notice(plugin)
	print("editor layout: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


func _rail_states(plugin: EditorPlugin) -> void:
	# The scene is a Node3D, so the Godot item opens the 3D view.
	EditorInterface.set_main_screen_editor("Script")
	_screens.clear()
	plugin._rail._buttons["godot"].pressed.emit()
	await get_tree().process_frame
	_check(_screens.has("3D"), "Godot opens 3D for a 3D scene")
	var switch := EditorInterface.get_base_control().find_child("JoineryScreenSwitch", true, false)
	_check(switch != null and switch.get_node("3D").button_pressed, "The switch highlights 3D")
	_check(plugin._rail._buttons["godot"].button_pressed, "Godot is the selected rail item")
	var deadline := Time.get_ticks_msec() + 30000
	while plugin._rail._job != null and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	# Readiness is optional data: when the CLI reports it, the tooltip says it.
	var readiness: Dictionary = plugin._rail._readiness
	if readiness.has("blender") and plugin._rail._buttons.has("blender"):
		var status := str(readiness["blender"]["status"])
		var wording := str(plugin._rail.STATUS_TEXT.get(status, ""))
		var tip: String = plugin._rail._buttons["blender"].tooltip_text
		_check(wording.is_empty() or tip.contains(wording), "Blender's tooltip names its readiness")


func _review_in_focus(plugin: EditorPlugin) -> void:
	if plugin._focus.is_active():
		plugin._on_toggle_focus()
	plugin._on_toggle_focus()
	_check(plugin._focus.is_active(), "Focus mode must find the editor docks")
	var dock := (plugin._review_tab as Control).get_parent() as Control
	var tabs := dock.get_parent() as TabContainer
	_check(tabs.is_visible_in_tree(), "Focus mode keeps the bottom tab strip")
	_check(tabs.current_tab == -1, "Focus mode collapses the bottom panel to its strip")
	var focus_button := EditorInterface.get_base_control().find_child("JoineryFocus", true, false)
	_check(focus_button != null and focus_button.button_pressed, "⤢ shows Focus mode is on")
	plugin._open_review("Layout check")
	await get_tree().create_timer(0.3).timeout
	var review: Control = plugin._review_panel
	_check(tabs.get_tab_idx_from_control(dock) == 0, "Review is the first bottom tab")
	_check(review.is_visible_in_tree(), "A pending review opens on its full before and after")
	var accept := _button_starting(review, "Accept")
	_check(accept != null and accept.is_visible_in_tree(), "Accept stays visible in Focus mode")
	await _capture("/tmp/joinery-editor-focus-review.png")
	plugin._close_review("", "info")
	await get_tree().process_frame
	_check(tabs.is_visible_in_tree(), "The bottom tab strip stays once nothing waits")
	focus_button.pressed.emit()
	_check(not plugin._focus.is_active(), "⤢ turns Focus mode off")
	_check(not focus_button.button_pressed, "⤢ shows Focus mode is off")


## A file decision that also commits a live scene edit posts exactly one notice.
func _single_accept_notice(plugin: EditorPlugin) -> void:
	var scene := EditorInterface.get_edited_scene_root()
	var edit := {
		"summary": "Add a layout check box",
		"operations": [{"operation": "create_box", "name": "LayoutBox", "size": [1, 1, 1]}]
	}
	var event := {"payload": edit, "context": plugin._editor_context([]), "targets": []}
	_check(plugin._preview_scene_edit(event)["success"], "Live preview for the notice check")
	var before := _transcript(plugin._dock).count("Change accepted")
	plugin._file_review = {"transaction_id": "layout-notice", "changes": []}
	plugin._file_accept_live_id = plugin._edits.transaction_id
	await plugin._on_file_decision(
		{"transaction_id": "layout-notice", "accepted": true, "changes": []}
	)
	await get_tree().process_frame
	_check(not plugin._edits.pending(), "The live edit is committed with the files")
	_check(scene.has_node("LayoutBox"), "The accepted box stays")
	var after := _transcript(plugin._dock).count("Change accepted")
	_check(after == before + 1, "One accepted notice, not two (%d)" % (after - before))
	_check(not plugin._review_open, "The Review panel closes after the decision")
	var manager := plugin.get_undo_redo()
	manager.get_history_undo_redo(manager.get_object_history_id(scene)).undo()


func _transcript(dock: Control) -> String:
	var text := ""
	for node: Node in dock.find_children("*", "", true, false):
		if node is RichTextLabel:
			text += (node as RichTextLabel).get_parsed_text() + "\n"
		elif node is Label:
			text += (node as Label).text + "\n"
	return text


func _button_starting(root: Node, prefix: String) -> Button:
	for node: Node in root.find_children("*", "Button", true, false):
		if (node as Button).text.begins_with(prefix):
			return node as Button
	return null


func _capture(path: String) -> void:
	if DisplayServer.get_name() == "headless":
		return
	RenderingServer.force_draw()
	get_tree().root.get_texture().get_image().save_png(path)
