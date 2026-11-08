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
const KEEP := ["Journeyman", "Chatup", "JoineryNavigation"]

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
	# The viewport toolbar has its own persisted /toolbar toggle. Workspace
	# navigation stays visible so Focus mode never strands the current view.
	_active = not _hidden.is_empty()
	if not _active:
		push_warning(
			"[Joinery] Focus mode found nothing to hide. The editor's layout is "
			+ "not the shape this expects, so nothing was changed."
		)


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
		if title in ["3D", "2D", "Script", "AssetLib", "Game", "Tools"]:
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
