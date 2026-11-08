@tool
extends RefCounted
## Viewport controls have their own preference, independent of Focus mode.

const TOOLBAR_SETTING := "joinery/show_viewport_toolbar"

var _toolbars: Array[Control] = []
var _previous: Array[bool] = []
var _shown := false


func apply(base: Control, settings: EditorSettings) -> void:
	_shown = (
		bool(settings.get_setting(TOOLBAR_SETTING))
		if settings.has_setting(TOOLBAR_SETTING) else false
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


func toggle(settings: EditorSettings) -> bool:
	_shown = not _shown
	for toolbar: Control in _toolbars:
		if is_instance_valid(toolbar):
			toolbar.visible = _shown
	settings.set_setting(TOOLBAR_SETTING, _shown)
	return _shown


func restore() -> void:
	for index: int in _toolbars.size():
		if is_instance_valid(_toolbars[index]):
			_toolbars[index].visible = _previous[index]
	_toolbars.clear()
	_previous.clear()
