@tool
extends EditorPlugin
## Joinery's editor plugin: the Journeyman sidebar, the project questionnaire,
## and Focus mode.
##
## Plugin first, per CLAUDE.md: everything here uses stock Godot extension
## points, so the addon still loads on an unpatched editor. The parts that the
## plugin API genuinely cannot reach are listed in `_unreachable_from_a_plugin`
## and need the fork (ADR 0004, ADR 0005).

const DOCK_SCRIPT := preload("res://addons/joinery/journeyman_dock.gd")
const SETUP_SCRIPT := preload("res://addons/joinery/project_setup.gd")
const FOCUS_SCRIPT := preload("res://addons/joinery/focus_mode.gd")
const WORKSPACE_SCRIPT := preload("res://addons/joinery/tool_workspace.gd")
const SHELL_SCRIPT := preload("res://addons/joinery/editor_shell.gd")
const RAIL_SCRIPT := preload("res://addons/joinery/tool_rail.gd")
const EDITS_SCRIPT := preload("res://addons/joinery/scene_edits.gd")
const CHAT_SCRIPT := preload("res://addons/joinery/chat_bridge.gd")

## Focus mode is remembered, so a user who wants the stripped-back layout gets
## it on every launch instead of re-toggling it each session.
const FOCUS_SETTING := "joinery/focus_mode"

## Written by the Project Manager's Next button, in the fork. Tells the addon
## that this project was just created and still needs its questionnaire.
const SETUP_PENDING := "joinery/setup_pending"

var _dock: Control
var _setup: AcceptDialog
var _focus: RefCounted
var _workspace: Control
var _shell: RefCounted
var _rail: Control
var _sidebar_tabs: TabContainer
var _sidebar_tabs_were_visible := true
var _chat: Node
var _edits: RefCounted
var _scene_revision := 0
var _adopting_preview := false
var _file_review: Dictionary = {}
var _file_accept_live_id := ""
var _review_panel: Control
var _review_button: Button


func _enter_tree() -> void:
	_edits = EDITS_SCRIPT.new()
	scene_changed.connect(_on_edit_scene_changed)
	_workspace = WORKSPACE_SCRIPT.new()
	get_editor_interface().get_editor_main_screen().add_child(_workspace)
	_workspace.hide()
	get_editor_interface().get_inspector().resource_selected.connect(_on_resource_selected)
	get_editor_interface().get_file_system_dock().selection_changed.connect(_on_file_selected)
	get_editor_interface().get_selection().selection_changed.connect(_on_workspace_selection)
	get_editor_interface().get_resource_filesystem().filesystem_changed.connect(
		_workspace.invalidate
	)
	get_editor_interface().get_resource_filesystem().resources_reimported.connect(
		_workspace.resources_reimported
	)
	_dock = DOCK_SCRIPT.new()
	_dock.name = "Journeyman"
	# Right column, lower slot. The upper slot already holds Inspector, Signals
	# and Groups, and adding a fourth tab there scrolls it out of sight behind
	# overflow arrows, which is no use for the surface you work from.
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, _dock)
	_review_panel = _dock.take_review_panel()
	_review_button = add_control_to_bottom_panel(_review_panel, "Review")
	_review_button.hide()
	_dock.review_requested.connect(_show_edit_review_panel)
	_dock.review_closed.connect(_hide_edit_review_panel)
	_workspace.asset_selected.connect(_on_asset_selected)
	_raise_dock.call_deferred()

	_setup = SETUP_SCRIPT.new()
	get_editor_interface().get_base_control().add_child(_setup)

	_focus = FOCUS_SCRIPT.new()
	_shell = SHELL_SCRIPT.new()

	# Menu entries, so the features are reachable without the dock.
	# Also reachable as /setup and /focus in the sidebar. These stay for
	# discoverability: a slash command you have not been told about is hidden.
	add_tool_menu_item("Joinery: New project…", _on_new_project)
	add_tool_menu_item("Joinery: Project setup…", _on_project_setup)
	add_tool_menu_item("Joinery: Toggle Focus mode", _on_toggle_focus)
	add_tool_menu_item("Joinery: Toggle viewport toolbar", _on_toggle_toolbar)

	_dock.setup_requested.connect(_on_project_setup)
	_dock.focus_toggle_requested.connect(_on_toggle_focus)
	_dock.toolbar_toggle_requested.connect(_on_toggle_toolbar)

	get_editor_interface().get_selection().selection_changed.connect(_dock.on_selection_changed)
	_dock.on_selection_changed()
	_restore_focus.call_deferred()


func _restore_focus() -> void:
	## Docks exist only after the editor has finished building its layout, so
	## this waits a frame before walking the tree.
	var startup_prompt := OS.get_environment("JOINERY_STARTUP_PROMPT")
	OS.unset_environment("JOINERY_STARTUP_PROMPT")
	var settings := get_editor_interface().get_editor_settings()
	_connect_chat(settings)
	_shell.apply(get_editor_interface().get_base_control(), settings)
	var navigation := get_editor_interface().get_base_control().find_child(
		"JoineryNavigation", true, false
	)
	if navigation != null:
		_workspace.use_rail_navigation()
		_rail = RAIL_SCRIPT.new()
		navigation.add_child(_rail)
		_workspace.catalog_changed.connect(_rail.update_tools)
		_workspace.creator_changed.connect(_on_creator_changed)
		_rail.tool_selected.connect(_on_tool_selected)
		_rail.toolbar_requested.connect(_on_toggle_toolbar)
		main_screen_changed.connect(_on_main_screen_changed)
		_workspace.refresh()
	if settings.has_setting(FOCUS_SETTING) and bool(settings.get_setting(FOCUS_SETTING)):
		_focus.apply(get_editor_interface().get_base_control())
		_dock.log_system("Focus mode restored from last session.")
	_offer_setup_if_pending()
	if not startup_prompt.is_empty():
		_dock._on_submit.call_deferred(startup_prompt)


func _connect_chat(settings: EditorSettings) -> void:
	var agent := OS.get_environment("JOINERY_AGENT")
	var profile := OS.get_environment("JOINERY_MODEL_PROFILE")
	if agent.is_empty() and settings.has_setting("joinery/agent"):
		agent = str(settings.get_setting("joinery/agent"))
	if profile.is_empty() and settings.has_setting("joinery/connection"):
		profile = str(settings.get_setting("joinery/connection"))
	_chat = CHAT_SCRIPT.new()
	_chat.context_provider = _editor_context
	_chat.editor_action.connect(_on_editor_action)
	_chat.file_preview.connect(_on_file_preview)
	_chat.file_decision.connect(_on_file_decision)
	_chat.art_review.connect(_on_art_review)
	_dock.add_child(_chat)
	_dock.set_chat_agent(agent)
	_chat.status_changed.connect(_dock.set_chat_state)
	_chat.busy_changed.connect(_dock.set_chat_busy)
	_chat.message_delta.connect(_dock.append_agent_text)
	_chat.message_received.connect(_dock.show_agent_message)
	_chat.completed.connect(_dock.finish_agent_response)
	_chat.models_received.connect(_dock.set_model_options)
	_chat.execution_changed.connect(_dock.show_execution)
	_chat.timing_received.connect(_dock.show_timing)
	var selection: Dictionary = settings.get_project_metadata(
		"joinery", "assistant", {"mode": "auto", "model": "", "effort": ""}
	)
	_dock.restore_selection_options(selection)
	_chat.set_selection(selection)
	_dock.selection_options_changed.connect(
		func(options: Dictionary) -> void:
			_chat.set_selection(options)
			settings.set_project_metadata("joinery", "assistant", options)
	)
	_dock.prompt_submitted.connect(_chat.submit)
	_dock.cancel_requested.connect(_chat.cancel_turn)
	_dock.cancel_requested.connect(_on_cancel_scene_edit)
	_dock.accept_edit_requested.connect(_accept_scene_edit)
	_dock.reject_edit_requested.connect(_reject_scene_edit)
	_dock.reconnect_requested.connect(_chat.reconnect)
	_dock.art_review_requested.connect(_chat.review_art)
	_dock.models_refresh_requested.connect(_chat.refresh_models)
	_chat.start(agent, profile, _dock._engine_dir(), ProjectSettings.globalize_path("res://"))


func _offer_setup_if_pending() -> void:
	## A project created through Joinery's Project Manager screen is marked
	## `joinery/setup_pending`, because that screen's Next button hands the rest
	## of setup here: the questionnaire lives in GDScript so it can change
	## without rebuilding the engine. Clear the flag first, so a project cannot
	## get stuck reopening the questionnaire if something below fails.
	if not ProjectSettings.has_setting(SETUP_PENDING):
		return
	if not bool(ProjectSettings.get_setting(SETUP_PENDING)):
		return
	ProjectSettings.set_setting(SETUP_PENDING, null)
	ProjectSettings.save()
	_dock.log_system("New project: choosing the tools that will make it.")
	_setup.open_for_new_project()


func _exit_tree() -> void:
	if _review_panel != null:
		remove_control_from_bottom_panel(_review_panel)
		_review_panel.queue_free()
	if _edits != null:
		_edits.reject()
	if _workspace != null:
		_workspace.queue_free()
	remove_tool_menu_item("Joinery: New project…")
	remove_tool_menu_item("Joinery: Project setup…")
	remove_tool_menu_item("Joinery: Toggle Focus mode")
	remove_tool_menu_item("Joinery: Toggle viewport toolbar")
	if _shell != null:
		_shell.restore()
	if _rail != null:
		_rail.queue_free()
	if _focus != null:
		_focus.restore()
	if is_instance_valid(_sidebar_tabs):
		_sidebar_tabs.tabs_visible = _sidebar_tabs_were_visible
	if _dock != null:
		remove_control_from_docks(_dock)
		_dock.queue_free()
	if _setup != null:
		_setup.queue_free()


func _show_edit_review_panel() -> void:
	_review_button.show()
	make_bottom_panel_item_visible(_review_panel)


func _hide_edit_review_panel() -> void:
	_review_button.hide()
	hide_bottom_panel()


func _has_main_screen() -> bool:
	return true


func _get_plugin_name() -> String:
	return "Tools"


func _get_plugin_icon() -> Texture2D:
	return get_editor_interface().get_base_control().get_theme_icon("FileList", "EditorIcons")


func _make_visible(visible: bool) -> void:
	if _workspace != null:
		_workspace.visible = visible
		if visible:
			_workspace.select_tool("")
			_workspace.refresh()


func _on_resource_selected(resource: Resource, _path: String) -> void:
	if _workspace != null and resource != null:
		_workspace.select_resource(resource.resource_path)
		var path := resource.resource_path
		if path.is_empty():
			path = "current-scene::%s@%s" % [_path, resource.get_instance_id()]
		_dock.set_resource_target(path, resource.get_class())


func _on_file_selected() -> void:
	if _workspace != null:
		for path: String in get_editor_interface().get_selected_paths():
			_workspace.select_resource(path)
			_dock.set_resource_target(path)
			return


func _on_asset_selected(asset: Dictionary) -> void:
	var source := "res://" + str(asset["path"])
	var sources: Array = asset.get("sources", [])
	if not sources.is_empty():
		source = "res://" + str(sources[0])
	_dock.set_resource_target("res://" + str(asset["path"]), "Resource", source)


func _on_workspace_selection() -> void:
	if _workspace == null:
		return
	for selected: Node in get_editor_interface().get_selection().get_selected_nodes():
		var node: Node = selected
		while node != null:
			if not node.scene_file_path.is_empty():
				_workspace.select_resource(node.scene_file_path)
				return
			node = node.get_parent()


func _on_project_setup() -> void:
	_setup.open_for_current_project()


func _on_new_project() -> void:
	# Creating a project from inside an open project is a Project Manager job,
	# which a plugin cannot replace. Say so, and point at the path that works.
	_setup.open_new_project_notice()


func _on_toggle_focus() -> void:
	var editor := get_editor_interface()
	if _focus.is_active():
		_focus.restore()
		_dock.log_system("Focus mode off: the full editor layout is back.")
	else:
		_focus.apply(editor.get_base_control())
		_dock.log_system(
			(
				"Focus mode on: viewport and sidebar only. The rest is hidden, not "
				+ "removed, so nothing about the project changed."
			)
		)
	editor.get_editor_settings().set_setting(FOCUS_SETTING, _focus.is_active())


func _on_toggle_toolbar() -> void:
	var shown: bool = _shell.toggle(get_editor_interface().get_editor_settings())
	_dock.log_system("Viewport toolbar %s." % ("shown" if shown else "hidden"))


func _on_tool_selected(tool: String) -> void:
	if tool == "godot":
		get_editor_interface().set_main_screen_editor("3D")
		if _rail != null:
			_rail.select_tool("godot")
		return
	get_editor_interface().set_main_screen_editor("Tools")
	_workspace.select_tool(tool)


func _on_main_screen_changed(screen: String) -> void:
	if _rail != null and screen != "Tools":
		_rail.select_tool("godot")


func _on_creator_changed(tool: String) -> void:
	if _rail != null:
		_rail.select_tool(tool if _workspace.visible else "godot")


func _raise_dock() -> void:
	## Select the Journeyman tab, so the sidebar is the panel you land on rather
	## than something you have to go hunting for behind other docks.
	var wrapper := _dock.get_parent() as Control
	if wrapper != null:
		if wrapper.is_class("EditorDock"):
			wrapper.set("layout_key", "Journeyman")
			wrapper.set("title", "Journeyman")
	var tabs := wrapper.get_parent() as TabContainer if wrapper != null else null
	if tabs == null:
		return
	_sidebar_tabs = tabs
	_sidebar_tabs_were_visible = tabs.tabs_visible
	tabs.tabs_visible = false
	for index: int in tabs.get_tab_count():
		if tabs.get_tab_control(index) == wrapper:
			tabs.current_tab = index
			return


## What this plugin cannot do, and why. Kept in code so it stays honest.
func _unreachable_from_a_plugin() -> Array[String]:
	return [
		(
			"Replacing the Project Manager's create-project screen: it runs before "
			+ "a project is open, so no plugin is loaded. Needs the fork patch in "
			+ "ADR 0004. `joinery new` is the working path today."
		),
		(
			"Removing built-in docks outright: Focus mode hides them, which is "
			+ "reversible. Actually restructuring the layout is ADR 0005."
		),
		"A 3D viewport right-click slot: 2D has one, 3D does not. Fork patch 1.",
	]


func _editor_context(targets: Array) -> Dictionary:
	var scene := EditorInterface.get_edited_scene_root()
	var context: Dictionary = _edits.context(scene)
	_scene_revision += 1
	context["scene_revision"] = str(_scene_revision)
	context["unsaved_scenes"] = JSON.stringify(EditorInterface.get_unsaved_scenes())
	var identities := {}
	if scene != null:
		for target: Dictionary in targets:
			var id := str(target.get("id", ""))
			var parts := id.split("::", true, 1)
			if parts.size() == 2 and parts[0] == scene.scene_file_path:
				var node := scene.get_node_or_null(NodePath(parts[1]))
				if node != null:
					identities[id] = str(node.get_instance_id())
	var manifest: Array[Dictionary] = []
	var context_bytes := 0
	if scene != null:
		var queue: Array[Node] = [scene]
		var cursor := 0
		while cursor < queue.size() and manifest.size() < 1000:
			var node := queue[cursor]
			cursor += 1
			var id := scene.scene_file_path + "::" + str(scene.get_path_to(node))
			identities[id] = str(node.get_instance_id())
			var row := {"id": id, "name": str(node.name), "type": node.get_class()}
			if node is Node3D:
				row["position"] = [node.position.x, node.position.y, node.position.z]
				row["rotation_degrees"] = [
					node.rotation_degrees.x, node.rotation_degrees.y, node.rotation_degrees.z
				]
				row["scale"] = [node.scale.x, node.scale.y, node.scale.z]
			var script: Script = node.get_script()
			if script != null:
				row["script"] = script.resource_path
			var properties := {}
			for property: Dictionary in node.get_property_list():
				if not int(property["usage"]) & PROPERTY_USAGE_STORAGE:
					continue
				var key := str(property["name"])
				var value: Variant = node.get(key)
				if properties.size() >= 32:
					break
				if value is String and value.length() > 4096:
					continue
				if value is bool or value is int or value is float or value is String:
					properties[key] = value
				elif value is Resource and not value is Script:
					properties[key] = _resource_context(value)
			row["properties"] = properties
			var source_node: Node = node
			while source_node != null and source_node != scene:
				if not source_node.scene_file_path.is_empty():
					row["source"] = source_node.scene_file_path
					break
				source_node = source_node.get_parent()
			context_bytes += JSON.stringify(row).length()
			if context_bytes > 500000:
				break
			manifest.append(row)
			for child: Node in node.get_children():
				queue.append(child)
	context["nodes"] = JSON.stringify(manifest)
	context["node_manifest_count"] = str(manifest.size())
	context["selection_instances"] = JSON.stringify(identities)
	return context


func _resource_context(resource: Resource, depth: int = 0) -> Dictionary:
	var result := {"resource_type": resource.get_class(), "resource_path": resource.resource_path}
	var properties := {}
	if depth < 2:
		for property: Dictionary in resource.get_property_list():
			if properties.size() >= 32:
				break
			if not int(property["usage"]) & PROPERTY_USAGE_STORAGE:
				continue
			var key := str(property["name"])
			var value: Variant = resource.get(key)
			if value is String and value.length() > 4096:
				continue
			if value is bool or value is int or value is float or value is String:
				properties[key] = value
			elif value is Vector3:
				properties[key] = [value.x, value.y, value.z]
			elif value is Vector2:
				properties[key] = [value.x, value.y]
			elif value is Color:
				properties[key] = [value.r, value.g, value.b, value.a]
			elif value is Resource and not value is Script:
				properties[key] = _resource_context(value, depth + 1)
	result["properties"] = properties
	return result


func _on_editor_action(event: Dictionary) -> void:
	var result := _preview_scene_edit(event)
	if result.get("success", false):
		print("[Joinery] Scene preview ready: ", _edits.summary)
	_chat.send_editor_result(str(event.get("request_id", "")), result)


# gdlint: disable=max-returns
func _preview_scene_edit(event: Dictionary) -> Dictionary:
	var expected: Dictionary = event.get("context", {})
	var scene := EditorInterface.get_edited_scene_root()
	var current: Dictionary = _edits.context(scene)
	if str(expected.get("scene_revision")) != str(_scene_revision):
		return {"success": false, "message": "The request was cancelled or the scene changed."}
	for key: String in ["scene_instance", "scene_path"]:
		if str(expected.get(key)) != str(current[key]):
			return {"success": false, "message": "The open scene changed. Send the request again."}
	if _edits.pending():
		return {"success": false, "message": "Accept or reject the current preview first."}
	var payload: Dictionary = event.get("payload", {})
	var identities: Variant = JSON.parse_string(str(expected.get("selection_instances", "{}")))
	for op: Dictionary in payload.get("operations", []):
		for field: String in ["target", "parent", "receiver"]:
			var target := str(op.get(field, ""))
			if target.is_empty():
				continue
			var parts := target.split("::", true, 1)
			if scene == null or parts.size() != 2 or not identities is Dictionary:
				return {"success": false, "message": "Select an object in the current scene."}
			var node := scene.get_node_or_null(NodePath(parts[1]))
			if node == null or identities.get(target) != str(node.get_instance_id()):
				return {
					"success": false, "message": "The selected object changed. Select it again."
				}
	var empty := scene == null
	if empty:
		scene = Node3D.new()
		scene.name = "Scene"
		EditorInterface.get_editor_viewport_3d(0).add_child(scene)
	var result: Dictionary = _edits.preview(
		scene, payload, _edits.context(scene), event.get("targets", [])
	)
	if not result["success"]:
		if empty:
			scene.get_parent().remove_child(scene)
			scene.free()
		return result
	_edits.owns_preview_root = empty
	_dock.show_edit_review(_edits.summary)
	EditorInterface.set_main_screen_editor("3D")
	if not empty:
		var selection := EditorInterface.get_selection()
		selection.clear()
		for change: Dictionary in _edits.changes:
			var node: Node = change["node"]
			if is_instance_valid(node) and node.is_inside_tree():
				selection.add_node(node)
	return result


func _accept_scene_edit() -> void:
	if not _file_review.is_empty():
		_file_accept_live_id = ""
		if _edits.pending():
			var ready: Dictionary = _edits.prepare_accept()
			if not ready["success"]:
				_dock.show_agent_message(str(ready["message"]))
				return
			_file_accept_live_id = _edits.transaction_id
		for change: Dictionary in _file_review.get("changes", []):
			if EditorInterface.get_unsaved_scenes().has("res://" + str(change["path"])):
				_dock.show_agent_message(
					"This scene has unsaved work. Use the live editor tool or save it first."
				)
				return
		_chat.decide_files(str(_file_review["transaction_id"]), true)
		return
	if not _edits.pending():
		return
	var ready: Dictionary = _edits.prepare_accept()
	if not ready["success"]:
		_dock.show_agent_message(str(ready["message"]))
		return
	var edited_nodes: Array[Node] = []
	for change: Dictionary in _edits.changes:
		edited_nodes.append(change["node"])
	if _edits.owns_preview_root:
		if EditorInterface.get_edited_scene_root() != null:
			_reject_scene_edit()
			return
		var scene: Node3D = _edits.root
		scene.get_parent().remove_child(scene)
		_adopting_preview = true
		EditorInterface.add_root_node(scene)
		_adopting_preview = false
		_edits.owns_preview_root = false
	var result: Dictionary = _edits.accept(get_undo_redo())
	if result["success"]:
		_dock.hide_edit_review(str(result["message"]))
		_chat.finish_live_review()
		EditorInterface.mark_scene_as_unsaved()
		var selection := EditorInterface.get_selection()
		selection.clear()
		for node: Node in edited_nodes:
			if is_instance_valid(node) and node.is_inside_tree():
				selection.add_node(node)
		_dock.on_selection_changed()
	else:
		_dock.show_agent_message(str(result["message"]))


func _reject_scene_edit(message: String = "Preview rejected. Original scene restored.") -> void:
	if not _file_review.is_empty():
		_chat.decide_files(str(_file_review["transaction_id"]), false)
	if _edits != null and _edits.pending():
		_edits.reject()
		_chat.finish_live_review()
		_dock.hide_edit_review(message)
		_dock.on_selection_changed()


func _on_cancel_scene_edit() -> void:
	_scene_revision += 1
	_reject_scene_edit("Edit cancelled. Original scene restored.")


func _on_edit_scene_changed(_scene: Node) -> void:
	if _adopting_preview:
		return
	_scene_revision += 1
	_reject_scene_edit("Preview discarded because the open scene changed.")


func _apply_changes() -> void:
	if not _adopting_preview:
		_reject_scene_edit(
			"Preview discarded before saving or running. Accept it first to keep it."
		)


func _on_file_preview(event: Dictionary) -> void:
	_file_review = event.duplicate(true)
	_dock.show_file_review(
		str(event.get("summary", "File changes")),
		str(event.get("diff", "")),
		event.get("media", [])
	)


func _on_art_review(event: Dictionary) -> void:
	var dialog := ConfirmationDialog.new()
	dialog.title = "Project art direction"
	dialog.ok_button_text = "Approve this version"
	dialog.cancel_button_text = "Close"
	var content := TextEdit.new()
	content.text = str(event.get("text", ""))
	content.editable = false
	content.custom_minimum_size = Vector2(640, 480) * EditorInterface.get_editor_scale()
	dialog.add_child(content)
	_dock.add_child(dialog)
	dialog.confirmed.connect(func() -> void: _chat.approve_art(str(event.get("sha256", ""))))
	dialog.confirmed.connect(dialog.queue_free)
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered()


func _on_file_decision(event: Dictionary) -> void:
	if _file_review.get("transaction_id") != event.get("transaction_id"):
		return
	var accepted := bool(event.get("accepted", false))
	if accepted and not _file_accept_live_id.is_empty():
		var valid: bool = _edits.pending() and _edits.transaction_id == _file_accept_live_id
		if valid:
			valid = bool(_edits.prepare_accept()["success"])
		if not valid:
			_file_accept_live_id = ""
			_chat.rollback_files(str(event["transaction_id"]))
			return
	_file_review.clear()
	_file_accept_live_id = ""
	_dock.hide_edit_review(
		str(
			event.get(
				"message",
				"Changes accepted." if accepted else "Changes rejected. Original files preserved."
			)
		)
	)
	if not accepted:
		if _edits.pending():
			_dock.show_edit_review(_edits.summary)
		return
	if _edits.pending():
		_accept_scene_edit()
	var files := EditorInterface.get_resource_filesystem()
	files.scan()
	await get_tree().process_frame
	while files.is_scanning() or files.is_importing():
		await get_tree().process_frame
	for change: Dictionary in event.get("changes", []):
		var path := "res://" + str(change["path"])
		if path.get_extension() != "tscn" or not FileAccess.file_exists(path):
			continue
		if EditorInterface.get_open_scenes().has(path):
			EditorInterface.reload_scene_from_path(path)
		elif EditorInterface.get_edited_scene_root() == null or change.get("change") == "created":
			EditorInterface.open_scene_from_path(path)
	# Exporting an asset alone does not change the scene. Show the actual imported
	# model immediately instead of leaving the user looking at an empty viewport.
	for change: Dictionary in event.get("changes", []):
		var path := "res://" + str(change["path"])
		if path.get_extension() in ["glb", "gltf"] and FileAccess.file_exists(path):
			EditorInterface.set_main_screen_editor("Tools")
			_workspace.select_resource(path)
			_workspace.invalidate()
			_workspace.refresh()
			_dock.set_resource_target(path, "PackedScene")
			break
