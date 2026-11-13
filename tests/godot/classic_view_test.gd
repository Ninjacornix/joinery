@tool
extends EditorPlugin
## Checks the editor in Joinery's layout or Godot's classic one, and captures it.
##
## Installed by run_classic_view_test.py into a disposable project beside a copy of
## ui_checks.gd. JOINERY_EXPECT_LAYOUT names the layout the seeded editor settings ask for
## (joinery or classic), JOINERY_VISUAL_THEME the theme (dark or light). JOINERY_LAYOUT_STEP
## is "first" for the first Joinery run, which opens the History dock (closed in Joinery's
## default) before it closes, and "restore" for the Joinery run after the classic one, which
## checks History is still open: switching back restored Joinery's own docks, not its
## default. Prints "classic view: ok" when every check passed, then closes the editor the way
## a user would, so it saves the layout the next run starts from.

const LAYOUT_SETTING := "interface/joinery/editor_layout"

var _checks: GDScript
var _layout := "joinery"
var _step := ""
var _theme := "dark"
var _failed := false
var _plugin: EditorPlugin


func _enter_tree() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	load("res://addons/joinery/ui_motion.gd").set_enabled(false)
	_step = OS.get_environment("JOINERY_LAYOUT_STEP")
	for key: String in ["JOINERY_EXPECT_LAYOUT", "JOINERY_VISUAL_THEME"]:
		var value := OS.get_environment(key)
		if not value.is_empty():
			if key == "JOINERY_EXPECT_LAYOUT":
				_layout = value
			else:
				_theme = value
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	print("CHECK ", "ok   " if condition else "FAIL ", message)
	if not condition:
		push_error(message)
		_failed = true


func _frames(count: int = 4) -> void:
	for index: int in count:
		await get_tree().process_frame


func _shot(name: String, roots: Array, window: Window = null) -> void:
	await _frames()
	var screen := "classic-view-%s-%s-%s" % [_layout, name, _theme]
	# A dialog in its own native window is captured from that window.
	var target: Viewport = (
		window if window != null and not window.is_embedded() else get_tree().root
	)
	_check(_checks.snapshot(screen, target, roots), "Capture " + screen)


## Grows the window to the size the editor asks for. The classic layout is
## stock Godot's: its 3D toolbar and side docks need about 1400 points across,
## more than a hosted macOS runner's 1024-point screen, so the window would
## otherwise cut off everything right of the toolbar. The checks judge the
## layout, not the size of the machine's display.
func _fit_window(base: Control) -> void:
	var window := get_tree().root
	# Growing the window can widen the layout again (more toolbar shows), so
	# repeat until nothing reaches past it.
	for _attempt: int in 5:
		await _frames()
		var past := Vector2.ZERO
		for child: Node in base.get_children():
			if child is Control and (child as Control).visible:
				var end := (child as Control).get_global_rect().end
				past = past.max(end - Vector2(window.size))
		if past.x <= 0.0 and past.y <= 0.0:
			return
		window.mode = Window.MODE_WINDOWED
		window.size += Vector2i(past.max(Vector2.ZERO).ceil()) + Vector2i(2, 2)


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			_plugin = node as EditorPlugin
	_check(_plugin != null, "Joinery plugin loads")
	if _plugin == null:
		get_tree().quit(1)
		return
	var classic := _layout == "classic"
	var base := EditorInterface.get_base_control()
	var settings := EditorInterface.get_editor_settings()
	_check(settings.has_setting(LAYOUT_SETTING), "The fork has the editor layout setting")
	_check(
		int(settings.get_setting(LAYOUT_SETTING)) == (1 if classic else 0),
		"The seeded layout setting reads " + _layout
	)
	_check(bool(_plugin.get("_joinery_layout")) != classic, "The addon sees the " + _layout)
	_check_docks(base, classic)
	_check_chrome(base, classic)
	_check_sidebar(classic)
	if not classic and _step == "restore":
		_check(_history_open(base), "Switching back restores Joinery's own docks")
	EditorInterface.set_main_screen_editor("3D")
	EditorInterface.get_selection().clear()
	await _fit_window(base)
	await _shot("editor", [base])
	await _settings_page(classic)
	await _switch_dialog(base, classic)
	if not classic and _step == "first":
		# A change to Joinery's default that only a restored arrangement keeps.
		_check(not _history_open(base), "History starts closed in Joinery's layout")
		var history := base.find_children("*", "HistoryDock", true, false)
		if not history.is_empty():
			(history[0] as Node).call("open")
		await _frames()
		_check(_history_open(base), "History opens")
	print("classic view: ", "FAILED" if _failed else "ok")
	# Close as a user would, so the editor saves this layout for the next run: the close
	# request goes to the editor, as Editor > Quit or the window's close button sends it.
	# Never through root.propagate_notification: that marks every node busy while it walks
	# the tree, so the editor's quit cannot detach the plugins it frees, and the freed
	# plugins stay listed as children until the engine crashes on them at exit. Deferred, so
	# nothing of this script is running while the editor takes the plugins down.
	var tree := get_tree()
	tree.create_timer(15.0).timeout.connect(tree.quit.bind(1))
	var editor := tree.root.find_children("*", "EditorNode", false, false)
	_check(editor.size() == 1, "The editor node is found to close")
	if editor.is_empty():
		tree.quit(1)
		return
	(editor[0] as Node).notification.call_deferred(NOTIFICATION_WM_CLOSE_REQUEST)


## Where Scene, FileSystem and Inspector sit: in Joinery's layout FileSystem is in the bottom
## panel and Scene above Inspector in the left column; in the classic one all three are in the
## side columns, as stock Godot puts them.
func _check_docks(base: Control, classic: bool) -> void:
	for type: String in ["SceneTreeDock", "FileSystemDock", "InspectorDock"]:
		var docks := base.find_children("*", type, true, false)
		_check(docks.size() == 1, type + " exists")
		if docks.is_empty():
			continue
		var holder := (docks[0] as Node).get_parent()
		var bottom := holder != null and holder.is_class("EditorBottomPanel")
		var expect_bottom := not classic and type == "FileSystemDock"
		_check(
			bottom == expect_bottom,
			"%s is %s" % [type, "in the bottom panel" if expect_bottom else "in a side column"]
		)
		if not classic and type != "FileSystemDock":
			var slot := "DockSlotLeftUR" if type == "SceneTreeDock" else "DockSlotLeftBR"
			_check(holder != null and holder.name == slot, "%s is in %s" % [type, slot])
		var control := docks[0] as Control
		if classic and type != "InspectorDock":
			# Stock Godot opens Scene and FileSystem; Inspector shares its slot with Node.
			_check(control.is_visible_in_tree(), type + " is open")
	var files := base.find_children("*", "FileSystemDock", true, false)
	if not files.is_empty():
		# An empty title shows the dock's name, FileSystem, as stock Godot does.
		var title := str((files[0] as Node).get("title"))
		_check((title != "Files") == classic, "The files dock is titled " + title)


## True while the History dock sits open in the bottom panel; a closed dock is parked outside
## every dock slot.
func _history_open(base: Control) -> bool:
	var docks := base.find_children("*", "HistoryDock", true, false)
	if docks.is_empty():
		return false
	var holder := (docks[0] as Node).get_parent()
	return holder != null and holder.is_class("EditorBottomPanel")


func _check_chrome(base: Control, classic: bool) -> void:
	var navigation := base.find_child("JoineryNavigation", true, false)
	_check((navigation == null) == classic, "The Joinery tool rail is shown only in Joinery's")
	var buttons := base.find_child("EditorMainScreenButtons", true, false) as Control
	_check(buttons != null, "The 2D, 3D and Script buttons exist")
	if buttons != null:
		_check(
			buttons.is_visible_in_tree() == classic,
			"The 2D, 3D and Script buttons show only in the classic view"
		)
		if classic:
			_check(buttons is HBoxContainer, "The 2D, 3D and Script buttons sit in a row")
			var texts: Array[String] = []
			for child: Node in buttons.get_children():
				if child is Button:
					texts.append((child as Button).text)
			_check("3D" in texts and "Script" in texts, "The buttons read " + ", ".join(texts))
	var toolbars := 0
	var shown := 0
	for node: Node in base.find_children("*", "MarginContainer", true, false):
		var control := node as Control
		if (
			control.get_theme_type_variation() == "MainToolBarMargin"
			and control.get_parent().is_class("Node3DEditor")
		):
			toolbars += 1
			shown += 1 if control.visible else 0
	_check(toolbars > 0, "The 3D viewport toolbar exists")
	if classic:
		_check(shown == toolbars, "The 3D viewport toolbar shows in the classic view")
	var menu := _menu_with(
		base, "Switch to Joinery view" if classic else "Switch to classic Godot view"
	)
	_check(menu != null, "The Editor menu offers the switch")
	var bars := base.find_children("*", "EditorRunBar", true, false)
	_check(not bars.is_empty(), "The run bar exists")
	if not bars.is_empty():
		var labels: Array[String] = []
		for node: Node in (bars[0] as Node).find_children("*", "Button", true, false):
			var button := node as Button
			if button.is_visible_in_tree() and not button.text.is_empty():
				labels.append(button.text)
		_check(("Play" in labels) != classic, "The run bar reads: " + ", ".join(labels))
		# The classic run bar keeps Play current scene, Play custom scene and Movie Maker beside
		# Play; Joinery's puts them in one ▾ menu beside Play.
		var tips: Array[String] = []
		for node: Node in (bars[0] as Node).find_children("*", "BaseButton", true, false):
			var control := node as Control
			if control.is_visible_in_tree():
				tips.append(control.tooltip_text)
		var expected: Array[String] = ["More ways to play"]
		if classic:
			expected = [
				"Play the currently edited scene.",
				"Play a custom scene.",
				"Enable Movie Maker mode."
			]
		for tip: String in expected:
			_check(
				tips.any(func(text: String) -> bool: return text.begins_with(tip)),
				"The run bar shows: " + tip
			)
		if not classic:
			var play := (bars[0] as Node).find_children("*", "Button", true, false).filter(
				func(node: Node) -> bool: return (node as Button).text == "Play"
			)
			_check(
				not play.is_empty() and (play[0] as Button).theme_type_variation == "RunBarButton",
				"Play is neutral, so Accept is the one filled accent button"
			)


func _check_sidebar(classic: bool) -> void:
	var dock: Control = _plugin.get("_dock")
	_check(dock != null and dock.is_visible_in_tree(), "The Journeyman sidebar is open")
	var tabs := dock.get_parent().get_parent() as TabContainer if dock != null else null
	_check(tabs != null, "The sidebar sits in a dock slot")
	if tabs != null:
		_check(tabs.tabs_visible == classic, "The sidebar has a tab only in the classic view")


func _settings_page(classic: bool) -> void:
	var dock: Control = _plugin.get("_dock")
	var page: Node = dock.get("_settings") if dock != null else null
	_check(page != null and page.has_method("set_open"), "The settings page exists")
	if page == null:
		return
	page.call("set_open", true)
	var picker := page.get("_layout_picker") as OptionButton
	_check(picker != null, "Appearance has Layout")
	if picker == null:
		page.call("set_open", false)
		return
	_check(picker.selected == (1 if classic else 0), "Layout reads the running layout")
	var note := page.get("_layout_note") as Control
	_check(note != null and not note.visible, "No restart note while nothing changed")
	(page as ScrollContainer).ensure_control_visible(picker)
	await _shot("settings", [dock])
	# Choosing the other layout says it applies after a restart; choosing back clears it.
	var other := 0 if classic else 1
	picker.select(other)
	picker.item_selected.emit(other)
	await _frames()
	var settings := EditorInterface.get_editor_settings()
	_check(int(settings.get_setting(LAYOUT_SETTING)) == other, "Choosing saves the setting")
	_check(note != null and note.visible, "The page says it applies after a restart")
	await _shot("settings-pending", [dock])
	picker.select(1 - other)
	picker.item_selected.emit(1 - other)
	await _frames()
	_check(note != null and not note.visible, "Choosing back clears the restart note")
	_check(int(settings.get_setting(LAYOUT_SETTING)) == 1 - other, "Choosing back restores it")
	page.call("set_open", false)


## Editor > Switch…: the confirmation names the layout and offers Restart.
func _switch_dialog(base: Control, classic: bool) -> void:
	var title := "Switch to Joinery view" if classic else "Switch to classic Godot view"
	var menu := _menu_with(base, title)
	if menu == null:
		return
	for index: int in menu.item_count:
		if menu.get_item_text(index) == title:
			menu.id_pressed.emit(menu.get_item_id(index))
	await _frames()
	var dialog: ConfirmationDialog = null
	for node: Node in base.find_children("*", "ConfirmationDialog", true, false):
		var candidate := node as ConfirmationDialog
		if candidate.visible and candidate.title == title:
			dialog = candidate
	_check(dialog != null, "The switch asks first")
	if dialog == null:
		return
	_check(dialog.get_ok_button().text == "Restart", "The switch offers Restart")
	await _shot("switch", [dialog], dialog)
	dialog.hide()
	var settings := EditorInterface.get_editor_settings()
	_check(
		int(settings.get_setting(LAYOUT_SETTING)) == (1 if classic else 0),
		"Closing the question changes nothing"
	)


func _menu_with(base: Control, text: String) -> PopupMenu:
	for node: Node in base.get_tree().root.find_children("*", "PopupMenu", true, false):
		var menu := node as PopupMenu
		for index: int in menu.item_count:
			if menu.get_item_text(index) == text:
				return menu
	return null
