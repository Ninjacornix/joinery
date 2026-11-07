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

## Focus mode is remembered, so a user who wants the stripped-back layout gets
## it on every launch instead of re-toggling it each session.
const FOCUS_SETTING := "joinery/focus_mode"

var _dock: Control
var _setup: AcceptDialog
var _focus: RefCounted


func _enter_tree() -> void:
	_dock = DOCK_SCRIPT.new()
	_dock.name = "Journeyman"
	# Right column, lower slot. The upper slot already holds Inspector, Signals
	# and Groups, and adding a fourth tab there scrolls it out of sight behind
	# overflow arrows, which is no use for the surface you work from.
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, _dock)
	_raise_dock.call_deferred()

	_setup = SETUP_SCRIPT.new()
	get_editor_interface().get_base_control().add_child(_setup)

	_focus = FOCUS_SCRIPT.new()

	# Menu entries, so the features are reachable without the dock.
	add_tool_menu_item("Joinery: New project…", _on_new_project)
	add_tool_menu_item("Joinery: Project setup…", _on_project_setup)
	add_tool_menu_item("Joinery: Toggle Focus mode", _on_toggle_focus)

	_dock.setup_requested.connect(_on_project_setup)
	_dock.focus_toggle_requested.connect(_on_toggle_focus)

	get_editor_interface().get_selection().selection_changed.connect(
		_dock.on_selection_changed
	)
	_dock.on_selection_changed()
	_restore_focus.call_deferred()


func _restore_focus() -> void:
	## Docks exist only after the editor has finished building its layout, so
	## this waits a frame before walking the tree.
	var settings := get_editor_interface().get_editor_settings()
	if settings.has_setting(FOCUS_SETTING) and bool(settings.get_setting(FOCUS_SETTING)):
		_focus.apply(get_editor_interface().get_base_control())
		_dock.log_system("Focus mode restored from last session.")


func _exit_tree() -> void:
	remove_tool_menu_item("Joinery: New project…")
	remove_tool_menu_item("Joinery: Project setup…")
	remove_tool_menu_item("Joinery: Toggle Focus mode")
	if _focus != null:
		_focus.restore()
	if _dock != null:
		remove_control_from_docks(_dock)
		_dock.queue_free()
	if _setup != null:
		_setup.queue_free()


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
			"Focus mode on: viewport and sidebar only. The rest is hidden, not "
			+ "removed, so nothing about the project changed."
		)
	editor.get_editor_settings().set_setting(FOCUS_SETTING, _focus.is_active())


func _raise_dock() -> void:
	## Select the Journeyman tab, so the sidebar is the panel you land on rather
	## than something you have to go hunting for behind other docks.
	var tabs := _dock.get_parent() as TabContainer
	if tabs == null:
		return
	for index: int in tabs.get_tab_count():
		if tabs.get_tab_control(index) == _dock:
			tabs.current_tab = index
			return


## What this plugin cannot do, and why. Kept in code so it stays honest.
func _unreachable_from_a_plugin() -> Array[String]:
	return [
		"Replacing the Project Manager's create-project screen: it runs before "
		+ "a project is open, so no plugin is loaded. Needs the fork patch in "
		+ "ADR 0004. `joinery new` is the working path today.",
		"Removing built-in docks outright: Focus mode hides them, which is "
		+ "reversible. Actually restructuring the layout is ADR 0005.",
		"A 3D viewport right-click slot: 2D has one, 3D does not. Fork patch 1.",
	]
