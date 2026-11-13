@tool
extends RefCounted
## Viewport controls have their own preference, independent of Focus mode, in Joinery's layout
## (Godot's classic layout always shows them); they show unless the user hid them with /toolbar.
## The 3D view's "Perspective" menus wear the Joinery theme. Scene tabs always show. Also keeps the
## FileSystem tree scrolled to its first row when the Files panel opens, and explains the
## Output tab's status mark (the engine's error or warning icon) in the tab's tooltip.

const TOOLBAR_SETTING := "joinery/show_viewport_toolbar"
const UI_THEME := preload("ui_theme.gd")
const SWITCH_SCRIPT := preload("screen_switch.gd")
## Godot's main screens by the class of the editor each shows.
const SCREEN_CLASSES := {
	"CanvasItemEditor": "2D",
	"Node3DEditor": "3D",
	"ScriptEditor": "Script",
	"GameView": "Game",
}
## Longest log line quoted in the Output tab's tooltip.
const OUTPUT_TIP_CHARS := 120

var _toolbars: Array[Control] = []
var _styled_menus: Array[MenuButton] = []
## The 2D | 3D | Script switch in the scene tab row, in Joinery's layout (screen_switch.gd).
var _switch: Control
## The ⤢ Focus toggle that stands in for Godot's distraction-free button, and that button.
var _focus_button: Button
var _distraction_free: Button
## The fork's top-level Joinery menu, and the command each of its items runs by id.
var _menu: PopupMenu
var _commands: Array[Callable] = []
## Toolbar controls hidden to keep the 3D toolbar compact.
var _compacted: Array[Control] = []
var _previous: Array[bool] = []
var _shown := false
var _file_tree: Tree
var _output: Control
var _output_bar: TabBar


## [param joinery_layout] is false in Godot's classic layout, which always shows the viewport
## toolbar as Godot does; the saved preference applies to Joinery's layout only.
func apply(base: Control, settings: EditorSettings, joinery_layout := true) -> void:
	_shown = (
		bool(settings.get_setting(TOOLBAR_SETTING))
		if settings.has_setting(TOOLBAR_SETTING)
		else true
	)
	if not joinery_layout:
		_shown = true
	for node: Node in base.find_children("*", "MarginContainer", true, false):
		var control := node as Control
		if (
			control.get_theme_type_variation() == "MainToolBarMargin"
			and control.get_parent().is_class("Node3DEditor")
		):
			_toolbars.append(control)
			_previous.append(control.visible)
			control.visible = _shown
			if joinery_layout:
				_compact(control)
	if joinery_layout and _switch == null:
		_switch = SWITCH_SCRIPT.new()
		if _switch.place(base):
			_switch.select(current_screen())
		else:
			_switch.free()
			_switch = null
	if joinery_layout:
		for viewport: Node in base.find_children("*", "Node3DEditorViewport", true, false):
			for menu: Node in viewport.find_children("*", "MenuButton", true, false):
				if not (menu as MenuButton).flat:
					_style_view_menu(menu as MenuButton)
	# Scene tabs stay visible in both layouts: they switch between open scenes and their
	# "+" button makes a new one.
	for node: Node in base.find_children("*", "EditorSceneTabs", true, false):
		(node as Control).visible = true
	var logs := base.find_children("*", "EditorLog", true, false)
	if not logs.is_empty():
		_output = logs[0] as Control
		if not _output.tree_entered.is_connected(_watch_output_tab):
			_output.tree_entered.connect(_watch_output_tab)
		_watch_output_tab()


## Keep the 3D toolbar to one compact group: the mode tools (Transform, Move, Rotate, Scale,
## Select) and Use Snap. The full toolbar is wider than the viewport beside the Scene column
## and the sidebar; the classic view keeps all of it.
func _compact(toolbar: Control) -> void:
	var rows := toolbar.find_children("*", "HBoxContainer", true, false)
	if rows.is_empty():
		return
	var first_group := true
	for child: Node in (rows[0] as Node).get_children():
		var control := child as Control
		if control == null:
			continue
		if control is VSeparator:
			first_group = false
		var keep := first_group or control.accessibility_name == "Use Snap"
		if not keep and not _compacted.has(control):
			control.visible = false
			_compacted.append(control)
			# Godot shows Lock or Unlock, Group or Ungroup, as the selection changes.
			control.visibility_changed.connect(
				func() -> void:
					if control.visible and _compacted.has(control):
						control.hide.call_deferred()
			)


## The 3D view's menu ("⋮ Perspective"): a raised surface with the theme's corner radius over
## the viewport, instead of the stock grey button.
func _style_view_menu(menu: MenuButton) -> void:
	for state: String in ["normal", "hover", "pressed", "focus"]:
		var box := StyleBoxFlat.new()
		box.bg_color = UI_THEME.color(menu, "surface" if state == "normal" else "surface_raised")
		box.set_corner_radius_all(UI_THEME.radius(menu, "md"))
		box.set_content_margin_all(UI_THEME.space(menu, 1))
		box.content_margin_left = UI_THEME.space(menu, 2)
		box.content_margin_right = UI_THEME.space(menu, 3)
		if state == "focus":
			box.draw_center = false
		menu.add_theme_stylebox_override(state, box)
	menu.add_theme_color_override("font_color", UI_THEME.color(menu, "text"))
	menu.add_theme_color_override("font_hover_color", UI_THEME.color(menu, "text"))
	_styled_menus.append(menu)


## The Godot main screen showing now ("2D", "3D", "Script", "Game"), or "" when another one
## (the asset workspace) is.
static func current_screen() -> String:
	for child: Node in EditorInterface.get_editor_main_screen().get_children():
		if child is Control and (child as Control).visible:
			for type: String in SCREEN_CLASSES:
				if child.is_class(type):
					return SCREEN_CLASSES[type]
	return ""


## Make the ⤢ button in the scene tab row the one Focus mode toggle: Godot's distraction-free
## button would hide the sidebar too, so it steps aside for a button that calls
## [param toggle]. Does nothing when the row has no such button.
func add_focus_toggle(base: Control, toggle: Callable) -> void:
	for node: Node in base.find_children("*", "Button", true, false):
		var button := node as Button
		if button.tooltip_text.begins_with("Toggle distraction-free mode"):
			_distraction_free = button
			break
	if _distraction_free == null or _focus_button != null:
		return
	_focus_button = Button.new()
	_focus_button.name = "JoineryFocus"
	_focus_button.flat = true
	_focus_button.toggle_mode = true
	_focus_button.focus_mode = Control.FOCUS_ACCESSIBILITY
	_focus_button.theme_type_variation = _distraction_free.theme_type_variation
	_focus_button.icon = _distraction_free.get_theme_icon("DistractionFree", "EditorIcons")
	_focus_button.tooltip_text = "Focus: hide the side panels and collapse the bottom panel"
	_focus_button.accessibility_name = "Focus"
	_focus_button.pressed.connect(toggle)
	_distraction_free.add_sibling(_focus_button)
	_distraction_free.hide()


## Add [param label] to the fork's top-level Joinery menu, running [param action]. Returns
## false when this editor has no such menu (the classic view, stock Godot): the caller puts the
## command under Project > Tools instead.
func add_command(base: Control, label: String, action: Callable) -> bool:
	if not is_instance_valid(_menu):
		var menus := base.find_children("Joinery", "PopupMenu", true, false)
		if menus.is_empty():
			return false
		_menu = menus[0] as PopupMenu
		_menu.id_pressed.connect(_run_command)
	_menu.add_item(label, _commands.size())
	_commands.append(action)
	return true


func _run_command(id: int) -> void:
	if id >= 0 and id < _commands.size() and _commands[id].is_valid():
		_commands[id].call()


## Show Focus mode as on or off on the ⤢ button.
func set_focus_shown(on: bool) -> void:
	if is_instance_valid(_focus_button):
		_focus_button.set_pressed_no_signal(on)


## The main screen changed to [param screen]: highlight it on the switch.
func screen_changed(screen: String) -> void:
	if is_instance_valid(_switch):
		_switch.select(screen)


## True when [param base] (the editor's base control) runs Joinery's layout. Godot's classic
## layout and stock Godot have no navigation column.
static func is_joinery_layout(base: Control) -> bool:
	return base != null and base.find_child("JoineryNavigation", true, false) != null


## Show the file tree from its first row ("Favorites") whenever [param dock] (the FileSystem
## dock) opens. The dock centres its selection in the tree, which leaves the first row cut in
## half under the path bar when the panel is short.
func watch_file_tree(dock: Control) -> void:
	var trees := dock.find_children("*", "Tree", true, false)
	if trees.is_empty():
		return
	_file_tree = trees[0] as Tree
	if not _file_tree.visibility_changed.is_connected(_on_file_tree_shown):
		_file_tree.visibility_changed.connect(_on_file_tree_shown)
	_on_file_tree_shown()


func _on_file_tree_shown() -> void:
	if is_instance_valid(_file_tree) and _file_tree.is_visible_in_tree():
		_scroll_file_tree_top.call_deferred()


func _scroll_file_tree_top() -> void:
	if not is_instance_valid(_file_tree) or not _file_tree.is_inside_tree():
		return
	# One frame later than the dock's own scroll to its selection, so this wins.
	await _file_tree.get_tree().process_frame
	if not is_instance_valid(_file_tree) or _file_tree.get_root() == null:
		return
	var first := _file_tree.get_root()
	if _file_tree.hide_root and first.get_first_child() != null:
		first = first.get_first_child()
	_file_tree.scroll_to_item(first)


## True while the viewport toolbar is shown (the saved preference).
func is_shown() -> bool:
	return _shown


func toggle(settings: EditorSettings) -> bool:
	_shown = not _shown
	for toolbar: Control in _toolbars:
		if is_instance_valid(toolbar):
			toolbar.visible = _shown
	settings.set_setting(TOOLBAR_SETTING, _shown)
	return _shown


## Follow the Output dock to whichever tab bar holds it, so hovering its tab explains the mark.
func _watch_output_tab() -> void:
	_unwatch_output_tab()
	if not is_instance_valid(_output):
		return
	var tabs := _output.get_parent() as TabContainer
	if tabs != null:
		_output_bar = tabs.get_tab_bar()
		_output_bar.tab_hovered.connect(_on_tab_hovered)


func _unwatch_output_tab() -> void:
	if is_instance_valid(_output_bar) and _output_bar.tab_hovered.is_connected(_on_tab_hovered):
		_output_bar.tab_hovered.disconnect(_on_tab_hovered)
	_output_bar = null


func _on_tab_hovered(tab: int) -> void:
	if not is_instance_valid(_output_bar):
		return
	var tabs := _output_bar.get_parent() as TabContainer
	if tabs != null and is_instance_valid(_output) and tabs.get_tab_control(tab) == _output:
		tabs.set_tab_tooltip(tab, output_tip())


## What the Output tab's mark means: the latest error or warning, or "" with no mark.
func output_tip() -> String:
	if not is_instance_valid(_output):
		return ""
	var icon := _output.get("dock_icon") as Texture2D
	if icon == null:
		return ""
	var error := icon == _output.get_theme_icon("Error", "EditorIcons")
	var marker := "ERROR: " if error else "WARNING: "
	var text := ""
	for label: Node in _output.find_children("*", "RichTextLabel", true, false):
		text = (label as RichTextLabel).get_parsed_text()
		break
	var at := text.rfind(marker)
	var detail := "" if at < 0 else text.substr(at + marker.length()).get_slice("\n", 0)
	detail = detail.strip_edges()
	if detail.length() > OUTPUT_TIP_CHARS:
		detail = detail.left(OUTPUT_TIP_CHARS - 1) + "…"
	var tip := "Godot reported an error" if error else "Godot reported a warning"
	tip += "." if detail.is_empty() else ": " + detail
	return tip + "\nOpen Output to read the details."


func restore() -> void:
	_unwatch_output_tab()
	if is_instance_valid(_output) and _output.tree_entered.is_connected(_watch_output_tab):
		_output.tree_entered.disconnect(_watch_output_tab)
	_output = null
	if (
		is_instance_valid(_file_tree)
		and _file_tree.visibility_changed.is_connected(_on_file_tree_shown)
	):
		_file_tree.visibility_changed.disconnect(_on_file_tree_shown)
	_file_tree = null
	for index: int in _toolbars.size():
		if is_instance_valid(_toolbars[index]):
			_toolbars[index].visible = _previous[index]
	_toolbars.clear()
	_previous.clear()
	for menu: MenuButton in _styled_menus:
		if is_instance_valid(menu):
			for state: String in ["normal", "hover", "pressed", "focus"]:
				menu.remove_theme_stylebox_override(state)
			menu.remove_theme_color_override("font_color")
			menu.remove_theme_color_override("font_hover_color")
	_styled_menus.clear()
	if is_instance_valid(_switch):
		_switch.queue_free()
	_switch = null
	if is_instance_valid(_focus_button):
		_focus_button.queue_free()
	_focus_button = null
	if is_instance_valid(_distraction_free):
		_distraction_free.show()
	_distraction_free = null
	if is_instance_valid(_menu):
		_menu.clear()
		_menu.id_pressed.disconnect(_run_command)
	_menu = null
	_commands.clear()
	var compacted := _compacted.duplicate()
	_compacted.clear()
	for control: Control in compacted:
		if is_instance_valid(control):
			control.visible = true
