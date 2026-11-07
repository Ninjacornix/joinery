@tool
extends RefCounted
## Focus mode: viewport and sidebar only.
##
## Most control goes through the sidebar, so the rest of Godot's layout is more
## than the user needs (ADR 0005). The proper version of this is a fork patch
## that restructures the editor. A plugin has no API for hiding built-in docks,
## so this walks the editor's control tree and hides the tab containers it
## finds, remembering each one so it can put everything back.
##
## That makes it a presentation trick, not surgery: nothing is removed, nothing
## about the project changes, and `restore()` returns the editor exactly as it
## was. It is also the fragile part of the addon, since it depends on the
## editor's internal node shape; if a Godot release moves things, Focus mode
## stops finding panels and does nothing rather than breaking the editor.

## Docks whose titles we keep, by substring. Everything else gets hidden.
const KEEP := ["Journeyman"]

## Main-screen tab titles. Focus mode hides the switcher too: with the sidebar
## driving the work, a row of workspace tabs is chrome, and it comes straight
## back when Focus is turned off.
const MAIN_SCREEN_TABS := ["3D", "2D", "Script", "AssetLib", "Game", "Asset Store"]

var _hidden: Array[Control] = []
var _active := false


func is_active() -> bool:
	return _active


func apply(base: Control) -> void:
	if _active:
		return
	_hidden.clear()
	for container: Control in _find_dock_containers(base):
		if _should_keep(container):
			continue
		if container.visible:
			container.visible = false
			_hidden.append(container)
	_hide_viewport_chrome(base)
	_active = not _hidden.is_empty()
	if not _active:
		push_warning(
			"[Joinery] Focus mode found nothing to hide. The editor's layout is "
			+ "not the shape this expects, so nothing was changed."
		)


func _hide_viewport_chrome(base: Control) -> void:
	## The 3D toolbar and the workspace tab row. Both are things you reach for
	## when you drive the editor by hand; the point of Focus mode is that you
	## drive it from the sidebar, so they go until you turn Focus off.
	##
	## Found by shape rather than by name, since no plugin API exposes them: a
	## toolbar is an HBox of icon-only buttons sitting above the viewport, and
	## the tab row is an HBox whose buttons are the main screen names.
	for node: Node in _descendants(base):
		var row := node as HBoxContainer
		if row == null or not row.visible:
			continue
		if _is_main_screen_tabs(row) or _is_viewport_toolbar(row):
			row.visible = false
			_hidden.append(row)


func _is_main_screen_tabs(row: HBoxContainer) -> bool:
	var names: Array[String] = []
	for child: Node in row.get_children():
		var button := child as Button
		if button != null and not button.text.is_empty():
			names.append(button.text)
	if names.size() < 3:
		return false
	for name: String in names:
		if not MAIN_SCREEN_TABS.has(name):
			return false
	return true


func _is_viewport_toolbar(row: HBoxContainer) -> bool:
	## Mostly icon-only buttons: the transform mode row above the 3D viewport.
	var icon_only := 0
	var buttons := 0
	for child: Node in row.get_children():
		var button := child as Button
		if button == null:
			continue
		buttons += 1
		if button.text.is_empty() and button.icon != null:
			icon_only += 1
	return buttons >= 6 and icon_only >= buttons - 2


func restore() -> void:
	for container: Control in _hidden:
		if is_instance_valid(container):
			container.visible = true
	_hidden.clear()
	_active = false


func _should_keep(container: Control) -> bool:
	## Keep anything holding the sidebar, so Focus mode cannot hide itself.
	for node: Node in _descendants(container):
		for keep: String in KEEP:
			if node.name.contains(keep):
				return true
	return false


func _find_dock_containers(base: Control) -> Array[Control]:
	## The editor keeps its docks in TabContainers down the left and right.
	## Collect those, and leave the central viewport alone.
	var found: Array[Control] = []
	for node: Node in _descendants(base):
		if node is TabContainer and node.get_tab_count() > 0:
			var control := node as Control
			# A dock column, not the main screen tabs: the main screen tabs
			# hold the 2D/3D/Script views, which must stay.
			if not _holds_main_screen(control):
				found.append(control)
	return found


func _holds_main_screen(container: Control) -> bool:
	var tabs := container as TabContainer
	for index: int in tabs.get_tab_count():
		var title := tabs.get_tab_title(index)
		if title in ["3D", "2D", "Script", "AssetLib", "Game"]:
			return true
	return false


func _descendants(root: Node) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			out.append(child)
			stack.append(child)
	return out
