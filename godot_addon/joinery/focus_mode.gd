@tool
extends RefCounted
## Focus mode: viewport, sidebar and the Review panel only.
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
## stops finding panels and [method apply] returns false rather than breaking
## the editor.
##
## The Review panel is where every change is decided, so Focus mode never hides
## it: the bottom panel that holds it stays, with every other tab hidden, and is
## shown only while a review is pending ([method show_review]).

## Docks whose titles we keep, by substring. Everything else gets hidden.
const KEEP := ["Journeyman", "Chatup", "JoineryNavigation", "Review"]

var _hidden: Array[Control] = []
var _active := false
## The tab container holding the Review panel, the tabs hidden in it, and whether it was
## visible before Focus mode took it over.
var _review_tabs: TabContainer
var _review_tabs_visible := true
var _hidden_tabs: Array[Control] = []


func is_active() -> bool:
	return _active


## Hide every dock column except the sidebar, the navigation rail and the Review panel's
## container. [param review] is the Review panel; its container keeps only the Review tab.
## Returns false, and changes nothing, when the editor layout has nothing to hide.
func apply(base: Control, review: Control = null) -> bool:
	if _active:
		return true
	_hidden.clear()
	var containers := _find_dock_containers(base)
	if review != null:
		for container: Control in containers:
			if container is TabContainer and container.is_ancestor_of(review):
				_isolate_review(container as TabContainer, review)
				break
	for container: Control in containers:
		# The Review panel's container (and anything inside it) is handled by its tabs.
		if (
			_review_tabs != null
			and (container == _review_tabs or _review_tabs.is_ancestor_of(container))
		):
			continue
		if _should_keep(container):
			continue
		if container.visible:
			container.visible = false
			_hidden.append(container)
	# The viewport toolbar has its own persisted /toolbar toggle. Workspace
	# navigation stays visible so Focus mode never strands the current view.
	_active = not _hidden.is_empty()
	if not _active:
		restore()
	return _active


## While Focus mode is on, show the Review panel's container only when a review is pending.
func show_review(pending: bool) -> void:
	if _active and is_instance_valid(_review_tabs):
		_review_tabs.visible = pending


func restore() -> void:
	for container: Control in _hidden:
		if is_instance_valid(container):
			container.visible = true
	_hidden.clear()
	if is_instance_valid(_review_tabs):
		for control: Control in _hidden_tabs:
			var index := (
				_review_tabs.get_tab_idx_from_control(control) if is_instance_valid(control) else -1
			)
			if index >= 0:
				_review_tabs.set_tab_hidden(index, false)
		_review_tabs.visible = _review_tabs_visible
	_hidden_tabs.clear()
	_review_tabs = null
	_active = false


func _isolate_review(tabs: TabContainer, review: Control) -> void:
	_review_tabs = tabs
	_review_tabs_visible = tabs.visible
	_hidden_tabs.clear()
	for index: int in tabs.get_tab_count():
		var control := tabs.get_tab_control(index)
		if control == review or control.is_ancestor_of(review) or tabs.is_tab_hidden(index):
			continue
		tabs.set_tab_hidden(index, true)
		_hidden_tabs.append(control)


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
