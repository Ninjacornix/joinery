@tool
extends RefCounted
## Focus mode: the viewport, the sidebar and the bottom tab strip.
##
## Most control goes through the sidebar, so the rest of Godot's layout is more than the user
## needs (ADR 0005). Focus mode hides the side dock columns (Scene, Inspector) and collapses
## the bottom panel to its tab strip, so Review, Files and Output stay one click away and a
## review still opens in place. The ⤢ button in the scene tab row is its one toggle.
##
## A plugin has no API for hiding built-in docks, so this walks the editor's control tree and
## hides the dock columns it finds, remembering each one so it can put everything back. It is
## a presentation trick, not surgery: nothing is removed, nothing about the project changes,
## and `restore()` returns the editor as it was. If a Godot release moves things, Focus mode
## stops finding panels and [method apply] returns false rather than breaking the editor.

## Docks whose titles we keep, by substring. Every other dock column gets hidden.
const KEEP := ["Journeyman", "Chatup", "JoineryNavigation", "Review"]

var _hidden: Array[Control] = []
var _active := false
## The bottom panel, and the tab it showed before Focus mode collapsed it (-1: none).
var _bottom: TabContainer
var _bottom_tab := -1


func is_active() -> bool:
	return _active


## Hide every dock column except the sidebar and the navigation rail, and collapse the bottom
## panel to its tab strip. [param review] is the Review panel, whose bottom panel stays.
## Returns false, and changes nothing, when the editor layout has nothing to hide.
func apply(base: Control, review: Control = null) -> bool:
	if _active:
		return true
	_hidden.clear()
	for container: Control in _find_dock_containers(base):
		if review != null and container.is_ancestor_of(review):
			_bottom = container as TabContainer
			continue
		if container.is_class("EditorBottomPanel"):
			_bottom = container as TabContainer
			continue
		if _should_keep(container) or not container.visible:
			continue
		container.visible = false
		_hidden.append(container)
	_active = not _hidden.is_empty()
	if not _active:
		restore()
		return false
	# A review on screen stays open; otherwise the bottom panel collapses to its strip.
	if is_instance_valid(_bottom) and not (review != null and review.is_visible_in_tree()):
		_bottom_tab = _bottom.current_tab
		_bottom.current_tab = -1
	return true


## A review opening in Focus mode shows in the bottom panel as usual; one closing collapses
## the panel back to its strip.
func show_review(pending: bool) -> void:
	if _active and not pending and is_instance_valid(_bottom):
		_bottom.current_tab = -1


func restore() -> void:
	for container: Control in _hidden:
		if is_instance_valid(container):
			container.visible = true
	_hidden.clear()
	if _active and is_instance_valid(_bottom) and _bottom.current_tab == -1:
		if _bottom_tab >= 0 and _bottom_tab < _bottom.get_tab_count():
			_bottom.current_tab = _bottom_tab
	_bottom = null
	_bottom_tab = -1
	_active = false


func _should_keep(container: Control) -> bool:
	## Keep anything holding the sidebar, so Focus mode cannot hide itself.
	for node: Node in _descendants(container):
		for keep: String in KEEP:
			if node.name.contains(keep):
				return true
	return false


func _find_dock_containers(base: Control) -> Array[Control]:
	## The editor keeps its docks in TabContainers down the left and right and along the
	## bottom. Collect those, and leave the central viewport alone.
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
		if title in ["3D", "2D", "Script", "AssetLib", "Game", "Tools", "Assets"]:
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
