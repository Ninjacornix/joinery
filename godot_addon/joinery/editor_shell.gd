@tool
extends RefCounted
## Viewport controls have their own preference, independent of Focus mode. Also keeps the
## FileSystem tree scrolled to its first row when the Files panel opens, and explains the
## Output tab's status mark (the engine's error or warning icon) in the tab's tooltip.

const TOOLBAR_SETTING := "joinery/show_viewport_toolbar"
## Longest log line quoted in the Output tab's tooltip.
const OUTPUT_TIP_CHARS := 120

var _toolbars: Array[Control] = []
var _previous: Array[bool] = []
var _shown := false
var _file_tree: Tree
var _output: Control
var _output_bar: TabBar


func apply(base: Control, settings: EditorSettings) -> void:
	_shown = (
		bool(settings.get_setting(TOOLBAR_SETTING))
		if settings.has_setting(TOOLBAR_SETTING)
		else false
	)
	for node: Node in base.find_children("*", "MarginContainer", true, false):
		var control := node as Control
		if (
			control.get_theme_type_variation() == "MainToolBarMargin"
			and control.get_parent().is_class("Node3DEditor")
		):
			_toolbars.append(control)
			_previous.append(control.visible)
			control.visible = _shown
	for node: Node in base.find_children("*", "EditorSceneTabs", true, false):
		var control := node as Control
		_toolbars.append(control)
		_previous.append(control.visible)
		control.visible = _shown
	var logs := base.find_children("*", "EditorLog", true, false)
	if not logs.is_empty():
		_output = logs[0] as Control
		if not _output.tree_entered.is_connected(_watch_output_tab):
			_output.tree_entered.connect(_watch_output_tab)
		_watch_output_tab()


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


## True while the viewport toolbar and scene tabs are shown (the saved preference).
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
