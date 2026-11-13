@tool
extends EditorPlugin
## Joinery's editor plugin: the Journeyman sidebar, the Review panel, the app rail, the
## project questionnaire and Focus mode.
##
## Plugin first, per CLAUDE.md: everything here uses stock Godot extension
## points, so the addon still loads on an unpatched editor. The new-project screen and the
## editor layout changes live in the fork (ADR 0004, ADR 0005).

const DOCK_SCRIPT := preload("res://addons/joinery/journeyman_dock.gd")
const SETUP_SCRIPT := preload("res://addons/joinery/project_setup.gd")
const FOCUS_SCRIPT := preload("res://addons/joinery/focus_mode.gd")
const WORKSPACE_SCRIPT := preload("res://addons/joinery/tool_workspace.gd")
const SHELL_SCRIPT := preload("res://addons/joinery/editor_shell.gd")
const RAIL_SCRIPT := preload("res://addons/joinery/tool_rail.gd")
const EDITS_SCRIPT := preload("res://addons/joinery/scene_edits.gd")
const CHAT_SCRIPT := preload("res://addons/joinery/chat_bridge.gd")
const OPTIONS_SCRIPT := preload("res://addons/joinery/composer_options.gd")
const CONNECT_SCRIPT := preload("res://addons/joinery/agent_connect.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_COMPONENTS := preload("res://addons/joinery/ui_components.gd")
const REVIEW_TAB_SCRIPT := preload("res://addons/joinery/review_tab.gd")
const REVIEW_CHECKS := preload("res://addons/joinery/review_checks.gd")
const FLOW_SCRIPT := preload("res://addons/joinery/review_flow.gd")
const EDITOR_CONTEXT := preload("res://addons/joinery/editor_context.gd")

## Focus mode is remembered, so a user who wants the stripped-back layout gets
## it on every launch instead of re-toggling it each session.
const FOCUS_SETTING := "joinery/focus_mode"
## The main screen this plugin adds: the asset workspace and the rail pages.
const SCREEN := "Tools"

## Written by the Project Manager's Next button, in the fork. Tells the addon
## that this project was just created and still needs its questionnaire.
const SETUP_PENDING := "joinery/setup_pending"

## One wording per outcome, shared with the sidebar notices.
const ACCEPTED := "Change accepted"
const REJECTED := "Discarded. Your files were not touched."

const ART_DIALOG := preload("res://addons/joinery/art_review_dialog.gd")
const PICK_SCRIPT := preload("res://addons/joinery/part_pick.gd")
const CHIP_SCRIPT := preload("res://addons/joinery/target_chip.gd")
## Shown in the Review panel when a combined file and scene review went stale.
const STALE_REVIEW := "The scene changed while you were reviewing. Nothing was applied."

var _dock: Control
var _setup: AcceptDialog
var _focus: RefCounted
var _workspace: Control
var _shell: RefCounted
var _rail: Control
var _sidebar_tabs: TabContainer
var _sidebar_tabs_were_visible := true
var _chat: Node
## Opens Journeyman's change for review when it passes Joinery's checks (review_flow.gd).
var _flow: Node
var _edits: RefCounted
var _scene_revision := 0
var _adopting_preview := false
var _file_review: Dictionary = {}
var _file_accept_live_id := ""
## Changed files of each change accepted this session, by transaction id, so a Revert
## reloads exactly the scenes it restored.
var _accepted_changes: Dictionary = {}
var _review_panel: Control
var _review_tab: Control
var _review_button: Button
var _review_open := false
## True while a file decision is on its way to the gateway; an error message arriving
## meanwhile is the decision failing, and is shown in the Review panel.
var _deciding := false
## The 2D or 3D view the user had last, for the Godot rail button.
var _last_view := "3D"
var _connect_dialog: AcceptDialog
## Joinery's own pages opened from the rail (Timeline, Share, Mixer), by rail id, each
## {"title", "control", "icon"}. Shown in the main screen in place of the asset workspace.
## Empty in Godot's classic layout, where they are bottom panel tabs.
var _pages: Dictionary = {}
## The rail page the main screen shows, or "" for the asset workspace.
var _page := ""
## False in Godot's classic layout (the fork's interface/joinery/editor_layout) and on stock Godot.
var _joinery_layout := false


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
	_joinery_layout = SHELL_SCRIPT.is_joinery_layout(get_editor_interface().get_base_control())
	_dock = DOCK_SCRIPT.new()
	_dock.name = "Journeyman"
	_dock.min_em = DOCK_SCRIPT.MIN_EM if _joinery_layout else DOCK_SCRIPT.SHARED_MIN_EM
	# Right column, lower slot: alone in Joinery's layout, below the Inspector in the classic one.
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, _dock)
	_review_panel = _dock.take_review_panel()
	_review_tab = REVIEW_TAB_SCRIPT.new()
	_review_tab.set_detail(_review_panel)
	_review_tab.changes_applied.connect(_on_review_row_applied)
	_review_tab.art_requested.connect(func() -> void: _dock.art_review_requested.emit())
	_review_button = add_control_to_bottom_panel(_review_tab, "Review")
	for tab: String in ["timeline_tab", "share_tab", "mixer_tab", "tour", "update_dialog"]:
		load("res://addons/joinery/%s.gd" % tab).install(self)
	# Revert runs the sidebar's own, so the chat's change card follows.
	_review_tab.attach(_dock.get("_slash"), _review_button)
	# Accessibility and performance (ADR 0049, 0050): last scores, and their cards.
	REVIEW_CHECKS.attach(_review_tab.get("_bible_decided"), _dock.get("_slash"))
	_dock.review_requested.connect(_show_edit_review_panel)
	_dock.review_closed.connect(_hide_edit_review_panel)
	if _review_panel.has_signal("show_requested"):
		_review_panel.connect("show_requested", _show_edit_review_panel)
	_workspace.asset_selected.connect(_on_asset_selected)
	if _workspace.has_signal("part_selected"):
		_workspace.connect("part_selected", set_part_target)
	# Alt+click on a model in the 3D view picks one of its parts. Forwarding is always on
	# rather than through _handles: this plugin has a main screen, and a main-screen plugin
	# that handles an object takes the editor over to that screen.
	set_input_event_forwarding_always_enabled()
	_workspace.setup_requested.connect(_on_project_setup)
	_link(_workspace.ask_requested, "fill_composer")
	_dock.target_opened.connect(_on_target_opened)
	_raise_dock.call_deferred()

	_setup = SETUP_SCRIPT.new()
	get_editor_interface().get_base_control().add_child(_setup)
	_setup.history_changed.connect(_dock.refresh_revertible)

	_focus = FOCUS_SCRIPT.new()
	_shell = SHELL_SCRIPT.new()

	# Menu entries, so the features are reachable without the dock (also /setup and /focus in
	# the sidebar): a slash command you have not been told about is hidden.
	add_tool_menu_item("Joinery: New project…", _on_new_project)
	add_tool_menu_item("Joinery: Project setup…", _on_project_setup)
	add_tool_menu_item("Joinery: Hide or show panels", _on_toggle_focus)
	if _joinery_layout:
		add_tool_menu_item("Joinery: Toggle viewport toolbar", _on_toggle_toolbar)

	_dock.setup_requested.connect(_on_project_setup)
	_dock.focus_toggle_requested.connect(_on_toggle_focus)
	_dock.toolbar_toggle_requested.connect(_on_toggle_toolbar)
	if _dock.has_signal("connect_requested"):
		_dock.connect("connect_requested", _on_connect_requested)
	if _dock.has_signal("change_reverted"):
		_dock.connect("change_reverted", _on_change_reverted)
	if _dock.has_signal("files_changed"):
		_dock.connect("files_changed", _on_files_changed)

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
	_shell.apply(get_editor_interface().get_base_control(), settings, _joinery_layout)
	_shell.watch_file_tree(get_editor_interface().get_file_system_dock())
	# The saved layout has been applied by now; put the Review tab back in front.
	_review_tab.pin_first()
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
		_rail.set_toolbar_shown(_shell.is_shown())
		for id: String in _pages:
			_rail.add_page(id, str(_pages[id]["title"]), _pages[id]["icon"])
		_workspace.refresh()
	main_screen_changed.connect(_on_main_screen_changed)
	# Godot's classic layout opens as Godot arranges it; Focus mode stays one command away.
	var focus := settings.has_setting(FOCUS_SETTING) and bool(settings.get_setting(FOCUS_SETTING))
	if _joinery_layout and focus:
		if _focus.apply(get_editor_interface().get_base_control(), _review_tab):
			_focus.show_review(_review_open)
	_offer_setup_if_pending()
	if not startup_prompt.is_empty() and _dock.has_method("_on_submit"):
		_dock.call_deferred("_on_submit", startup_prompt)


func _connect_chat(settings: EditorSettings) -> void:
	var agent := OS.get_environment("JOINERY_AGENT")
	var profile := OS.get_environment("JOINERY_MODEL_PROFILE")
	if agent.is_empty() and settings.has_setting("joinery/agent"):
		agent = str(settings.get_setting("joinery/agent"))
	if profile.is_empty() and settings.has_setting("joinery/connection"):
		profile = str(settings.get_setting("joinery/connection"))
	_chat = CHAT_SCRIPT.new()
	_chat.context_provider = _editor_context
	_review_tab.follow(_chat)
	_chat.task_review.connect(
		func(_id: String) -> void: make_bottom_panel_item_visible(_review_tab)
	)
	_chat.editor_action.connect(_on_editor_action)
	_flow = FLOW_SCRIPT.new()
	add_child(_flow)
	_flow.setup(self, _chat)
	_chat.file_decision.connect(_on_file_decision)
	_chat.art_review.connect(_on_art_review)
	_dock.add_child(_chat)
	_call_dock("set_chat_agent", [agent])
	_link(_chat.status_changed, "set_chat_state")
	_chat.status_changed.connect(_on_chat_state)
	_link(_chat.busy_changed, "set_chat_busy")
	_link(_chat.message_delta, "append_agent_text")
	_chat.message_received.connect(_on_chat_message)
	_link(_chat.completed, "finish_agent_response")
	_link(_chat.models_received, "set_model_options")
	_link(_chat.execution_changed, "show_execution")
	_link(_chat.timing_received, "show_timing")
	OPTIONS_SCRIPT.restore_saved(settings, agent, _dock, _chat)
	_dock.selection_options_changed.connect(
		func(options: Dictionary) -> void:
			_chat.set_selection(options)
			OPTIONS_SCRIPT.save(settings, str(_chat.get("_agent")), options)
	)
	_dock.prompt_submitted.connect(_chat.submit)
	_dock.cancel_requested.connect(_chat.cancel_turn)
	_dock.cancel_requested.connect(_on_cancel_scene_edit)
	_dock.accept_edit_requested.connect(_accept_scene_edit)
	_dock.reject_edit_requested.connect(_reject_scene_edit)
	_dock.reconnect_requested.connect(_chat.reconnect)
	_dock.art_review_requested.connect(_chat.review_art)
	_dock.models_refresh_requested.connect(_chat.refresh_models)
	_chat.start(agent, profile, _engine_dir(), ProjectSettings.globalize_path("res://"))


## Calls [param method] on the sidebar when this version of it has the method, passing only
## as many arguments as it takes. The addon also runs beside older sidebars (degraded), so a
## missing method is skipped. Returns false when it was skipped.
func _call_dock(method: String, args: Array = []) -> bool:
	if _dock == null or not _dock.has_method(method):
		return false
	_dock.callv(method, args.slice(0, _dock.get_method_argument_count(method)))
	return true


## Connect [param source] to the sidebar's [param method] when the sidebar has it.
func _link(source: Signal, method: String) -> void:
	if _dock.has_method(method):
		source.connect(Callable(_dock, method))


## A transient line in the sidebar footer (Focus mode, viewport tools).
func _status(text: String) -> void:
	_call_dock("show_status", [text])


## A transient footer note with a link ("Panels hidden · Show panels"). A sidebar whose
## show_status takes no action shows the text alone; _call_dock drops the extra arguments.
func _status_action(text: String, action: String, callback: Callable) -> void:
	_call_dock("show_status", [text, 4.0, action, callback])


func _engine_dir() -> String:
	var from_env := OS.get_environment("JOINERY_HOME")
	if not from_env.is_empty():
		return from_env
	return ProjectSettings.globalize_path("res://").path_join("..").simplify_path()


func _offer_setup_if_pending() -> void:
	## A project created through Joinery's Project Manager screen is marked
	## `joinery/setup_pending`: that screen hands the rest of setup here, in GDScript so it
	## can change without rebuilding the engine. Clear the flag first, so a project cannot
	## get stuck reopening the questionnaire if something below fails.
	if not ProjectSettings.has_setting(SETUP_PENDING):
		return
	if not bool(ProjectSettings.get_setting(SETUP_PENDING)):
		return
	ProjectSettings.set_setting(SETUP_PENDING, null)
	ProjectSettings.save()
	_setup.open_for_new_project()


func _exit_tree() -> void:
	if _review_tab != null:
		remove_control_from_bottom_panel(_review_tab)
		_review_tab.queue_free()
	if _edits != null:
		_edits.reject()
	if _workspace != null:
		_workspace.queue_free()
	remove_tool_menu_item("Joinery: New project…")
	remove_tool_menu_item("Joinery: Project setup…")
	remove_tool_menu_item("Joinery: Hide or show panels")
	if _joinery_layout:
		remove_tool_menu_item("Joinery: Toggle viewport toolbar")
	if _shell != null:
		_shell.restore()
	if _rail != null:
		_rail.queue_free()
	if _focus != null:
		_focus.restore()
	if is_instance_valid(_sidebar_tabs):
		_sidebar_tabs.tabs_visible = _sidebar_tabs_were_visible
	if is_instance_valid(_connect_dialog):
		_connect_dialog.queue_free()
	if _dock != null:
		remove_control_from_docks(_dock)
		_dock.queue_free()
	if _setup != null:
		_setup.queue_free()


# --- Review panel ----------------------------------------------------------------------------


## Show the Review panel: reveal its tab, open the bottom panel on it, and in Focus mode bring
## the bottom panel back with only this tab.
func _show_edit_review_panel() -> void:
	_review_open = true
	_review_tab.show_detail()
	if _focus != null and _focus.is_active():
		_focus.show_review(true)
	var dock := _review_tab.get_parent() as Control
	if dock != null and dock.get_parent() is Control:
		(dock.get_parent() as Control).show()
	make_bottom_panel_item_visible(_review_tab)


## The review was decided or dropped: a bottom panel that opened by itself for it closes.
func _hide_edit_review_panel() -> void:
	if not _review_open:
		return
	_review_open = false
	if _review_tab.review_closed():
		hide_bottom_panel()
	if _focus != null and _focus.is_active():
		_focus.show_review(false)


func _open_review(summary: String) -> void:
	_call_dock("show_edit_review", [summary, _edits.changes.size()])
	if _file_review.is_empty():
		_review_tab.set_live({"id": "scene-edit", "summary": summary, "tools": ["godot"]})
	_show_edit_review_panel()


## Close the review with one notice in the transcript ([param kind]: success, rejected, info,
## error).
func _close_review(message: String, kind: String) -> void:
	_deciding = false
	_call_dock("hide_edit_review", [message, kind])
	_hide_edit_review_panel()


func _review_applying(on: bool) -> void:
	_call_dock("set_review_applying", [on])


## A decision could not be applied: re-enable the buttons and say why, in the Review panel.
func _review_failed(message: String) -> void:
	_deciding = false
	_review_applying(false)
	_review_tab.live_failed(message)
	if not _call_dock("show_review_error", [message]):
		_call_dock("show_agent_message", [message])


func _on_chat_message(text: String) -> void:
	if _deciding:
		_review_failed(text)
		return
	_call_dock("show_agent_message", [text])


func _on_chat_state(state: String, detail: String) -> void:
	if _deciding and state == "error":
		_review_failed(detail)


## A change accepted from the Review tab wrote files: rescan them, like any accepted change.
func _on_review_row_applied(id: String, paths: PackedStringArray, changes: Array) -> void:
	_accepted_changes[id] = changes
	_on_files_changed(id, paths)


# --- Main screen and selection ---------------------------------------------------------------


func _has_main_screen() -> bool:
	return true


func _get_plugin_name() -> String:
	return SCREEN


func _get_plugin_icon() -> Texture2D:
	return get_editor_interface().get_base_control().get_theme_icon("FileList", "EditorIcons")


func _make_visible(visible: bool) -> void:
	if not visible:
		_page = ""
	_show_page(visible)
	if visible and _page.is_empty() and _workspace != null:
		_workspace.select_tool("")
		_workspace.refresh()


## Show the asset workspace, or the rail page in [member _page], in the main screen.
func _show_page(visible := true) -> void:
	if _workspace != null:
		_workspace.visible = visible and _page.is_empty()
	for id: String in _pages:
		var page: Control = _pages[id]["control"]
		if is_instance_valid(page):
			page.visible = visible and id == _page


# --- Rail pages ------------------------------------------------------------------------------


## Make [param page] a rail page titled [param title] in Joinery's layout (Timeline, Share,
## Mixer: tab scripts call this from their install). Returns false in Godot's classic layout
## and on stock Godot, where the caller adds it to the bottom panel instead.
func add_rail_page(id: String, title: String, page: Control, icon: Dictionary) -> bool:
	if not _joinery_layout:
		return false
	page.hide()
	get_editor_interface().get_editor_main_screen().add_child(page)
	_pages[id] = {"title": title, "control": page, "icon": icon}
	if _rail != null:
		_rail.add_page(id, title, icon)
	return true


## Take [param page] out of the rail pages and the main screen.
func remove_rail_page(page: Control) -> void:
	for id: String in _pages.keys():
		if _pages[id]["control"] == page:
			_pages.erase(id)
			if _page == id:
				_page = ""
	if is_instance_valid(page) and page.get_parent() != null:
		page.get_parent().remove_child(page)


## Show [param page] wherever it lives: its rail page, or its bottom panel tab.
func open_page(page: Control) -> void:
	for id: String in _pages:
		if _pages[id]["control"] == page:
			_open_rail_page(id)
			return
	make_bottom_panel_item_visible(page)


## The rail button of [param id] (an app or a page), or null without the rail.
func rail_button(id: String) -> Control:
	if _rail == null:
		return null
	return (_rail.get("_buttons") as Dictionary).get(id) as Control


func _open_rail_page(id: String) -> void:
	_page = id
	get_editor_interface().set_main_screen_editor(SCREEN)
	_show_page()
	if _rail != null:
		_rail.select_tool(id)


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


## A chip in the chat opens its asset in the Tools view, under the tool that made it.
func _on_target_opened(path: String) -> void:
	if _workspace == null or path.is_empty():
		return
	_open_workspace()
	_workspace.select_resource(path)


## Open the asset workspace in the main screen, in place of any rail page.
func _open_workspace() -> void:
	_page = ""
	get_editor_interface().set_main_screen_editor(SCREEN)
	_show_page()


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
		# The panels coming back say it themselves; an empty note clears "Panels hidden", and a
		# pending review gets its own footer line again.
		_status("" if _review_open else "Panels shown")
	elif _focus.apply(editor.get_base_control(), _review_tab):
		_focus.show_review(_review_open)
		# A pending review keeps its footer line: review status wins over this note.
		if _review_open:
			_status("")
		else:
			_status_action("Panels hidden", "Show panels", _on_toggle_focus)
	else:
		_status("This editor version can't hide the panels")
	editor.get_editor_settings().set_setting(FOCUS_SETTING, _focus.is_active())


func _on_toggle_toolbar() -> void:
	if not _joinery_layout:
		_status("The classic Godot view always shows the viewport tools")
		return
	var shown: bool = _shell.toggle(get_editor_interface().get_editor_settings())
	if _rail != null:
		_rail.set_toolbar_shown(shown)
	_status("Viewport tools shown" if shown else "Viewport tools hidden")


func _on_tool_selected(tool: String) -> void:
	if _pages.has(tool):
		_open_rail_page(tool)
		return
	if tool == "godot":
		get_editor_interface().set_main_screen_editor(_godot_view())
		if _rail != null:
			_rail.select_tool("godot")
		return
	_open_workspace()
	_workspace.select_tool(tool)


## The view the Godot rail button opens: 2D for a Node2D or Control scene, 3D for a Node3D
## scene, otherwise the last of 2D and 3D the user had.
func _godot_view() -> String:
	var root := EditorInterface.get_edited_scene_root()
	if root is Node3D:
		return "3D"
	if root is Node2D or root is Control:
		return "2D"
	return _last_view


func _on_main_screen_changed(screen: String) -> void:
	if screen in ["2D", "3D"]:
		_last_view = screen
	if _rail != null and screen != SCREEN:
		_rail.select_tool("godot")


func _on_creator_changed(tool: String) -> void:
	if _rail != null and _page.is_empty():
		_rail.select_tool(tool if _workspace.visible else "godot")


func _raise_dock() -> void:
	## Select the Journeyman tab, so the sidebar is the panel you land on.
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
	# In Godot's classic layout the sidebar is an ordinary dock, with a tab to close or move it.
	tabs.tabs_visible = not _joinery_layout and _sidebar_tabs_were_visible
	for index: int in tabs.get_tab_count():
		if tabs.get_tab_control(index) == wrapper:
			tabs.current_tab = index
			return


# --- Connecting an assistant -----------------------------------------------------------------


## The sidebar's "Connect an assistant": the Project Manager's connection screen, in a dialog.
func _on_connect_requested() -> void:
	if is_instance_valid(_connect_dialog):
		_connect_dialog.popup_centered(UI_THEME.dialog_size(_dock, "connect"))
		return
	var dialog := AcceptDialog.new()
	dialog.title = "Connect Journeyman"
	dialog.ok_button_text = "Close"
	dialog.theme = UI_THEME.build(_dock)
	var view: Control = CONNECT_SCRIPT.new()
	view.set("engine_home", _engine_dir())
	view.set("initial_agent", str(_chat.get("_agent")) if _chat != null else "")
	view.set("confirm_choice", true)
	dialog.add_child(view)
	view.connect("connected", _on_agent_connected.bind(view))
	dialog.canceled.connect(dialog.queue_free)
	dialog.confirmed.connect(dialog.queue_free)
	_dock.add_child(dialog)
	_connect_dialog = dialog
	dialog.popup_centered(UI_THEME.dialog_size(_dock, "connect"))


## Signed in: remember the agent, close the dialog and restart the chat with it. The
## connection screen sends (agent, method) or (agent); both reach here.
func _on_agent_connected(agent: String, method_or_view: Variant, view: Variant = null) -> void:
	var screen: Variant = view if view is Object else method_or_view
	var profile: Variant = screen.get("selected_profile") if screen is Object else null
	var connection := str(profile) if profile is String else ""
	if is_instance_valid(_connect_dialog):
		_connect_dialog.hide()
		_connect_dialog.queue_free()
	# The agent already in use, still connected: nothing to restart.
	if _chat != null and _chat.is_current(agent, connection):
		return
	var settings := get_editor_interface().get_editor_settings()
	settings.set_setting("joinery/agent", agent)
	settings.set_setting("joinery/connection", connection)
	_call_dock("set_chat_agent", [agent])
	OPTIONS_SCRIPT.restore_saved(settings, agent, _dock, _chat)
	_chat.start(agent, connection, _engine_dir(), ProjectSettings.globalize_path("res://"))


## A change the user applied wrote files: show them, and when it edited project.godot offer to
## reload, so the editor's settings in memory never overwrite the new ones.
func _on_files_changed(_transaction_id: String, paths: PackedStringArray) -> void:
	EditorInterface.get_resource_filesystem().scan()
	if paths.has("project.godot"):
		_offer_reload()


func _offer_reload() -> void:
	UI_COMPONENTS.confirm(
		_dock,
		"Reload the project",
		"This change edited the project settings. Reload to use them. Scenes are saved first.",
		["Reload", "Later"],
		_reload_project
	)


func _reload_project() -> void:
	EditorInterface.save_all_scenes()
	EditorInterface.restart_editor(false)


# --- Scene edits and file reviews ------------------------------------------------------------


func _editor_context(targets: Array) -> Dictionary:
	var scene := EditorInterface.get_edited_scene_root()
	var context: Dictionary = _edits.context(scene)
	_scene_revision += 1
	context["scene_revision"] = str(_scene_revision)
	context["unsaved_scenes"] = JSON.stringify(EditorInterface.get_unsaved_scenes())
	return EDITOR_CONTEXT.describe(scene, targets, context)


func _on_editor_action(event: Dictionary) -> void:
	var result := _preview_scene_edit(event)
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
		for field: String in ["target", "parent", "receiver", "replace"]:
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
		scene, payload, _edits.context(scene), event.get("targets", []), event.get("staged", {})
	)
	if not result["success"]:
		if empty:
			scene.get_parent().remove_child(scene)
			scene.free()
		return result
	_edits.owns_preview_root = empty
	_flow.live_ready(_edits.summary, str(event.get("owner", "")))
	EditorInterface.set_main_screen_editor("3D")
	if not empty:
		var selection := EditorInterface.get_selection()
		selection.clear()
		for change: Dictionary in _edits.changes:
			var node: Node = change["node"]
			if is_instance_valid(node) and node.is_inside_tree():
				selection.add_node(node)
	return result


## Accept the pending review. The Review panel shows "Applying…" at once; a failure re-enables
## it with the reason. [param quiet] commits a live scene edit as part of an accepted file
## review, whose own decision already posts the one "Change accepted" notice.
func _accept_scene_edit(quiet := false) -> void:
	if not _file_review.is_empty():
		_review_applying(true)
		_file_accept_live_id = ""
		if _edits.pending() and _flow.owns_live():
			var ready: Dictionary = _edits.prepare_accept()
			if not ready["success"]:
				_review_failed(str(ready["message"]))
				return
			_file_accept_live_id = _edits.transaction_id
		for change: Dictionary in _file_review.get("changes", []):
			if EditorInterface.get_unsaved_scenes().has("res://" + str(change["path"])):
				_review_failed("Save this scene's unsaved edits and ask again, or Discard.")
				return
		if not bool(_chat.get("_connected")):
			_review_failed("Journeyman isn't connected. Reconnect, then accept again.")
			return
		_deciding = true
		_chat.decide_files(str(_file_review["transaction_id"]), true)
		return
	if not _edits.pending():
		return
	if not quiet:
		_review_applying(true)
	var ready: Dictionary = _edits.prepare_accept()
	if not ready["success"]:
		_review_failed(str(ready["message"]))
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
	if not result["success"]:
		_review_failed(str(result["message"]))
		return
	if not quiet:
		_close_review(str(result["message"]), "success")
	_chat.finish_live_review(true)
	EditorInterface.mark_scene_as_unsaved()
	var selection := EditorInterface.get_selection()
	selection.clear()
	for node: Node in edited_nodes:
		if is_instance_valid(node) and node.is_inside_tree():
			selection.add_node(node)
	_dock.on_selection_changed()


## Reject the pending review. File changes are rejected through the gateway, whose decision
## posts the notice; a live scene edit alone is restored here and posts [param message].
func _reject_scene_edit(message: String = REJECTED, kind: String = "rejected") -> void:
	var files := not _file_review.is_empty()
	if files:
		if bool(_chat.get("_connected")):
			_review_applying(true)
			_deciding = true
			_chat.decide_files(str(_file_review["transaction_id"]), false)
		else:
			_review_failed("Journeyman isn't connected. Reconnect, then discard again.")
	# A task's live preview beside the main request's files stays for its own decision.
	if _edits != null and _edits.pending() and (not files or _flow.owns_live()):
		_edits.reject()
		_chat.finish_live_review(false)
		if not files:
			_close_review(message, kind)
		_dock.on_selection_changed()


func _on_cancel_scene_edit() -> void:
	_scene_revision += 1
	_reject_scene_edit("Edit cancelled. Original scene restored.", "info")


func _on_edit_scene_changed(_scene: Node) -> void:
	if _adopting_preview:
		return
	_scene_revision += 1
	_reject_scene_edit("Preview discarded because the open scene changed.", "info")


func _apply_changes() -> void:
	if not _adopting_preview:
		_reject_scene_edit(
			"Preview discarded before saving or running. Accept it first to keep it.", "info"
		)


func _on_art_review(event: Dictionary) -> void:
	ART_DIALOG.open(
		_dock,
		event,
		UI_THEME.dialog_size(_dock, "doc"),
		func() -> void:
			_chat.approve_art(str(event.get("sha256", "")))
			get_tree().create_timer(1.0).timeout.connect(_review_tab.refresh)
	)


func _on_file_decision(event: Dictionary) -> void:
	if _file_review.get("transaction_id") != event.get("transaction_id"):
		return
	_deciding = false
	var accepted := bool(event.get("accepted", false))
	if accepted and not _file_accept_live_id.is_empty():
		var valid: bool = _edits.pending() and _edits.transaction_id == _file_accept_live_id
		if valid:
			valid = bool(_edits.prepare_accept()["success"])
		if not valid:
			# The gateway restores the files and answers with a rejected decision.
			_file_accept_live_id = ""
			_review_failed(STALE_REVIEW)
			_chat.rollback_files(str(event["transaction_id"]))
			return
	_file_review.clear()
	_file_accept_live_id = ""
	var message := str(event.get("message", ""))
	if not accepted:
		_close_review(REJECTED if message.is_empty() else message, "rejected")
		if _edits.pending():
			_open_review(_edits.summary)
		return
	_accepted_changes[str(event.get("transaction_id", ""))] = event.get("changes", [])
	if _edits.pending() and _flow.owns_live():
		_accept_scene_edit(true)
	_close_review(ACCEPTED if message.is_empty() else message, "success")
	_flow.reopen_live()
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
	# Only a newly created model takes the user to the Tools view, where it is selected and
	# flashed; an edit to an existing model stays in the view the user is working in.
	for change: Dictionary in event.get("changes", []):
		var path := "res://" + str(change["path"])
		if (
			change.get("change") == "created"
			and path.get_extension() in ["glb", "gltf"]
			and FileAccess.file_exists(path)
		):
			_open_workspace()
			_workspace.select_resource(path)
			_workspace.invalidate()
			_workspace.refresh()
			_dock.set_resource_target(path, "PackedScene")
			if _workspace.has_method("flash_selected"):
				_workspace.call("flash_selected")
			else:
				UI_THEME.flash(_workspace)
			break


## A Revert restored files on disk: rescan them and reload the open scenes it rewrote, so the
## editor shows the reverted state. A scene with unsaved edits is never reloaded (that would
## throw the edits away); the footer says so instead.
func _on_change_reverted(transaction_id: String) -> void:
	# Restored files keep their earlier timestamps, so the accepted change's own file list says
	# which scenes to reload. A change accepted in an earlier session: every open scene.
	var known: bool = _accepted_changes.has(transaction_id)
	var restored: Array[String] = []
	for change: Dictionary in _accepted_changes.get(transaction_id, []):
		restored.append("res://" + str(change.get("path", "")))
	_accepted_changes.erase(transaction_id)
	var files := EditorInterface.get_resource_filesystem()
	files.scan()
	await get_tree().process_frame
	while files.is_scanning() or files.is_importing():
		await get_tree().process_frame
	var unsaved := EditorInterface.get_unsaved_scenes()
	var kept: Array[String] = []
	for path: String in EditorInterface.get_open_scenes():
		if known and not restored.has(path) or not FileAccess.file_exists(path):
			continue
		if unsaved.has(path):
			kept.append(path.get_file())
		else:
			EditorInterface.reload_scene_from_path(path)
	if not kept.is_empty():
		_status("Reverted. Not reloaded, it has unsaved edits: " + ", ".join(kept))
	_workspace.invalidate()
	_workspace.refresh()


# --- Picking a part of a model -------------------------------------------------------------


## Never claims an object: a main-screen plugin that handles a model switches the editor to
## its screen. Alt+click picking uses always-on input forwarding instead.
func _handles(_object: Object) -> bool:
	return false


func _forward_3d_gui_input(camera: Camera3D, event: InputEvent) -> int:
	var part := PICK_SCRIPT.editor_pick(camera, event)
	if part.is_empty():
		return AFTER_GUI_INPUT_PASS
	set_part_target(CHIP_SCRIPT.for_part(part))
	return AFTER_GUI_INPUT_STOP


## Make [param target] (a target_chip.for_part target) the message target. A sidebar without
## part targets gets the whole model instead.
func set_part_target(target: Dictionary) -> void:
	if not _call_dock("set_part_target", [target]):
		var model := str(target.get("resource", "")).split("#")[0]
		_call_dock("set_resource_target", [model, "PackedScene"])
