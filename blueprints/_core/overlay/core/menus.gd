extends CanvasLayer
## Main, pause and settings menus, built in code so features can add entries
## and settings pages without editing core files. Menus stack: opening a page
## hides the one below it, Back (or ui_cancel) returns to it. Everything works
## with the ui_* actions alone; the first control of each page takes focus.

const MAIN := &"main"
const PAUSE := &"pause"
const SettingsPages := preload("res://core/ui/settings_pages.gd")

var _entries: Dictionary = {MAIN: {}, PAUSE: {}}
var _settings_pages: Dictionary = {}
var _stack: Array[Control] = []
var _focus_memory: Dictionary = {}
var _paused_by_menu: bool = false
var _root: Control
var _toasts: VBoxContainer


func _ready() -> void:
	layer = 64
	process_mode = Node.PROCESS_MODE_ALWAYS
	_root = Control.new()
	_root.name = "Root"
	_root.theme = UiStyle.build_theme()
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_root)
	_toasts = VBoxContainer.new()
	_toasts.name = "Toasts"
	_toasts.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_toasts.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	_toasts.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_toasts.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_toasts.offset_bottom = -UiStyle.px(48)
	_root.add_child(_toasts)
	_add_core_entries()


func _unhandled_input(event: InputEvent) -> void:
	var is_pause: bool = InputMap.has_action(&"pause") and event.is_action_pressed(&"pause")
	var is_cancel: bool = event.is_action_pressed(&"ui_cancel")
	if not (is_pause or is_cancel):
		return
	if is_open():
		get_viewport().set_input_as_handled()
		if _stack.size() > 1:
			back()
		elif _stack[0].get_meta(&"menu", &"") == PAUSE:
			close_all()
	elif is_pause and Game.current_level() != null:
		get_viewport().set_input_as_handled()
		open_pause()


## Adds or replaces an entry. `menu` is `&"main"` or `&"pause"`; lower `order`
## comes first; `enabled` (optional) returns whether the entry can be used;
## `reason` (optional) returns why it cannot (an English msgid, "" for none),
## shown under the disabled entry.
func add_entry(
	menu: StringName,
	id: StringName,
	label: String,
	action: Callable,
	order: int = 100,
	enabled: Callable = Callable(),
	reason: Callable = Callable()
) -> void:
	if not _entries.has(menu):
		_entries[menu] = {}
	var entries: Dictionary = _entries[menu]
	entries[id] = {
		"id": id,
		"label": label,
		"action": action,
		"order": order,
		"enabled": enabled,
		"reason": reason,
	}


func remove_entry(menu: StringName, id: StringName) -> void:
	if _entries.has(menu):
		(_entries[menu] as Dictionary).erase(id)


## Entries of `menu`, ordered by `order`, then id.
func entries(menu: StringName) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for entry: Dictionary in (_entries.get(menu, {}) as Dictionary).values():
		found.append(entry)
	found.sort_custom(_entry_before)
	return found


## Adds a page to Settings. `factory` returns the page content (a Control);
## the menu adds the title and the Back button.
func add_settings_page(id: StringName, label: String, factory: Callable, order: int = 100) -> void:
	_settings_pages[id] = {"id": id, "label": label, "action": factory, "order": order}


func remove_settings_page(id: StringName) -> void:
	_settings_pages.erase(id)


func settings_pages() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for page: Dictionary in _settings_pages.values():
		found.append(page)
	found.sort_custom(_entry_before)
	return found


func open_main() -> void:
	close_all()
	var name_text: String = str(ProjectSettings.get_setting("application/config/name", ""))
	var page: Control = _menu_page(MAIN, name_text)
	var heading: Label = page.find_child("Title", true, false) as Label
	if heading != null:
		heading.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	_push(page)


## Opens the pause menu over the running level. The tree pauses only when the
## session allows it (online sessions keep running).
func open_pause() -> void:
	if is_open() or Game.current_level() == null:
		return
	_push(_menu_page(PAUSE, "Paused"))
	if Game.session().can_pause() and not get_tree().paused:
		get_tree().paused = true
		_paused_by_menu = true
		GameEvents.paused_changed.emit(true)


func open_settings() -> void:
	var column: VBoxContainer = VBoxContainer.new()
	for page: Dictionary in settings_pages():
		var item: Button = UiStyle.button(str(page["label"]))
		item.name = str(page["id"])
		item.pressed.connect(_open_settings_page.bind(page["id"]))
		column.add_child(item)
	var screen: Control = UiStyle.page("Settings", column, back)
	screen.set_meta(&"menu", &"settings")
	_push(screen)


## Shows any Control as the top page (for example a feature's lobby).
func open_page(control: Control) -> void:
	_push(control)


## Closes the top page and shows the one below it. Closing the last page
## resumes the game when the menu paused it.
func back() -> void:
	if _stack.is_empty():
		return
	var top: Control = _stack.pop_back()
	_discard(top)
	if _stack.is_empty():
		_resume()
		return
	var below: Control = _stack.back()
	below.visible = true
	var remembered: Variant = _focus_memory.get(below.get_instance_id())
	_focus_memory.erase(below.get_instance_id())
	if remembered is Control and is_instance_valid(remembered):
		(remembered as Control).grab_focus()
	else:
		_focus_first(below)


func close_all() -> void:
	while not _stack.is_empty():
		_discard(_stack.pop_back())
	_focus_memory.clear()
	_resume()


func is_open() -> bool:
	return not _stack.is_empty()


## The page on top, or null.
func current_page() -> Control:
	return null if _stack.is_empty() else _stack.back()


## The kind of the bottom page: &"main", &"pause", &"settings" or &"".
func current_menu() -> StringName:
	if _stack.is_empty():
		return &""
	return _stack[0].get_meta(&"menu", &"")


## A short message at the bottom of the screen. `text` is an English msgid.
func toast(text: String, seconds: float = 2.5) -> void:
	var holder: PanelContainer = PanelContainer.new()
	holder.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var label: Label = Label.new()
	label.text = text
	holder.add_child(label)
	_toasts.add_child(holder)
	get_tree().create_timer(seconds, true, false, true).timeout.connect(holder.queue_free)


func _add_core_entries() -> void:
	add_entry(MAIN, &"start", "Play", func() -> void: Game.start_game(), 10)
	add_entry(MAIN, &"settings", "Settings", open_settings, 80)
	add_entry(MAIN, &"quit", "Quit", func() -> void: get_tree().quit(), 90)
	add_entry(PAUSE, &"resume", "Resume", close_all, 10)
	add_entry(PAUSE, &"restart", "Restart", func() -> void: Game.restart_level(), 20)
	add_entry(PAUSE, &"settings", "Settings", open_settings, 80)
	add_entry(PAUSE, &"main_menu", "Main menu", func() -> void: Game.quit_to_menu(), 90)
	add_settings_page(&"video", "Video", SettingsPages.video, 10)
	add_settings_page(&"audio", "Audio", SettingsPages.audio, 20)
	add_settings_page(&"accessibility", "Accessibility", SettingsPages.accessibility, 30)


func _menu_page(menu: StringName, heading: String) -> Control:
	var column: VBoxContainer = VBoxContainer.new()
	for entry: Dictionary in entries(menu):
		var item: Button = UiStyle.button(str(entry["label"]))
		item.name = str(entry["id"])
		var enabled: Callable = entry["enabled"]
		var why: String = ""
		if enabled.is_valid() and not bool(enabled.call()):
			item.disabled = true
			item.focus_mode = Control.FOCUS_NONE
			var reason: Callable = entry.get("reason", Callable())
			if reason.is_valid():
				why = str(reason.call())
		item.pressed.connect(entry["action"])
		column.add_child(item)
		if not why.is_empty():
			item.tooltip_text = why
			var note: Label = UiStyle.hint(why)
			note.name = "%s_reason" % entry["id"]
			column.add_child(note)
	var screen: Control = UiStyle.page(heading, column)
	var heading_label: Node = screen.find_child("Column", true, false).get_child(0)
	if heading_label is Label:
		heading_label.name = "Title"
	screen.set_meta(&"menu", menu)
	screen.name = "%sMenu" % String(menu).capitalize()
	return screen


func _open_settings_page(id: StringName) -> void:
	if not _settings_pages.has(id):
		return
	var page: Dictionary = _settings_pages[id]
	var factory: Callable = page["action"]
	var content: Control = factory.call() as Control
	var screen: Control = UiStyle.page(str(page["label"]), content, back)
	screen.set_meta(&"menu", &"settings")
	screen.name = "Settings_%s" % String(id)
	_push(screen)


func _push(page: Control) -> void:
	if not _stack.is_empty():
		var top: Control = _stack.back()
		var owner_control: Control = get_viewport().gui_get_focus_owner()
		if owner_control != null and top.is_ancestor_of(owner_control):
			_focus_memory[top.get_instance_id()] = owner_control
		top.visible = false
	_root.add_child(page)
	_root.move_child(_toasts, -1)
	_stack.append(page)
	_focus_first(page)
	GameEvents.menu_opened.emit(page)


func _discard(page: Control) -> void:
	if is_instance_valid(page):
		page.visible = false
		page.queue_free()


func _resume() -> void:
	if _paused_by_menu:
		_paused_by_menu = false
		get_tree().paused = false
		GameEvents.paused_changed.emit(false)


func _focus_first(page: Control) -> void:
	var target: Control = _first_focusable(page)
	if target != null:
		target.grab_focus()


func _first_focusable(node: Node) -> Control:
	for child: Node in node.get_children():
		if child is Control and not (child as Control).visible:
			continue
		if child is Control and (child as Control).focus_mode == Control.FOCUS_ALL:
			if not (child is BaseButton and (child as BaseButton).disabled):
				return child as Control
		var found: Control = _first_focusable(child)
		if found != null:
			return found
	return null


static func _entry_before(a: Dictionary, b: Dictionary) -> bool:
	if int(a["order"]) != int(b["order"]):
		return int(a["order"]) < int(b["order"])
	return String(a["id"]) < String(b["id"])
