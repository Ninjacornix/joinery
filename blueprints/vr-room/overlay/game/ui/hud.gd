extends CanvasLayer
## The desktop HUD, built in code: a crosshair that brightens over something
## to pick up or press, a hint naming what interact does, and the goal. The
## win page opens through `Menus`, so it also shows on the headset's menu
## panel when the VR feature is on.

const CROSSHAIR_SIZE := 4.0
const CROSSHAIR_LENGTH := 10.0
const IDLE_COLOUR := Color(0.93, 0.95, 0.96, 0.7)
const ACTIVE_COLOUR := Color(1.0, 0.85, 0.3, 1.0)

var crosshair: Control
var hint: Label
var goal: Label
## The win page while it is open, or null.
var win_page: Control = null
var _bars: Array[ColorRect] = []
var _target: Node = null
var _holding: bool = false
var _win_result: Dictionary = {}


## "m:ss.cc" for a time in seconds.
static func format_time(seconds: float) -> String:
	var minutes: int = floori(seconds / 60.0)
	return "%d:%05.2f" % [minutes, seconds - float(minutes) * 60.0]


func _ready() -> void:
	layer = 5
	var root: Control = Control.new()
	root.name = "HudRoot"
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	crosshair = Control.new()
	crosshair.name = "Crosshair"
	crosshair.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	crosshair.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(crosshair)
	_add_bar(Vector2(CROSSHAIR_LENGTH * 2.0, CROSSHAIR_SIZE))
	_add_bar(Vector2(CROSSHAIR_SIZE, CROSSHAIR_LENGTH * 2.0))

	hint = _make_label("Hint", HORIZONTAL_ALIGNMENT_CENTER)
	hint.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	hint.offset_left = -360.0
	hint.offset_right = 360.0
	hint.offset_top = 32.0
	hint.offset_bottom = 72.0
	root.add_child(hint)

	goal = _make_label("Goal", HORIZONTAL_ALIGNMENT_LEFT)
	goal.set_anchors_and_offsets_preset(Control.PRESET_TOP_LEFT)
	goal.offset_left = 24.0
	goal.offset_top = 16.0
	goal.offset_right = 824.0
	goal.offset_bottom = 56.0
	root.add_child(goal)

	var player: Node = get_parent()
	if player.has_signal(&"target_changed"):
		player.connect(&"target_changed", _on_target_changed)
	if player.has_signal(&"held_changed"):
		player.connect(&"held_changed", _on_held_changed)
	GameEvents.level_completed.connect(show_win)
	GameEvents.world_changed.connect(_on_world_changed)
	var controller: Node = get_node_or_null(^"/root/Controller")
	if controller != null and controller.has_signal(&"device_changed"):
		controller.connect(&"device_changed", _on_device_changed)
	_on_target_changed(null)
	_render_goal()


func _notification(what: int) -> void:
	if what == NOTIFICATION_TRANSLATION_CHANGED and goal != null:
		_on_target_changed(_target)
		_render_goal()


## Opens the win page for a `level_completed` result and focuses Play again.
func show_win(result: Dictionary) -> void:
	hint.visible = false
	crosshair.visible = false
	_win_result = result
	var column: VBoxContainer = VBoxContainer.new()
	var time_label: Label = _make_label("WinTime", HORIZONTAL_ALIGNMENT_CENTER)
	time_label.text = tr("Time: %s") % format_time(float(result.get("time", 0.0)))
	column.add_child(time_label)
	var again: Button = UiStyle.button("Play again")
	again.name = "PlayAgain"
	again.pressed.connect(Game.restart_level)
	column.add_child(again)
	var leave: Button = UiStyle.button("Main menu")
	leave.name = "MainMenu"
	leave.pressed.connect(Game.quit_to_menu)
	column.add_child(leave)
	win_page = UiStyle.page("The cube is on the table!", column)
	win_page.name = "WinScreen"
	win_page.set_meta(&"menu", &"win")
	Menus.open_page(win_page)
	_render_goal()


func hide_win() -> void:
	if is_instance_valid(win_page) and Menus.current_page() == win_page:
		Menus.close_all()
	win_page = null
	crosshair.visible = true
	_render_goal()


func _exit_tree() -> void:
	if GameEvents.level_completed.is_connected(show_win):
		GameEvents.level_completed.disconnect(show_win)
	if GameEvents.world_changed.is_connected(_on_world_changed):
		GameEvents.world_changed.disconnect(_on_world_changed)


func _add_bar(size: Vector2) -> void:
	var bar: ColorRect = ColorRect.new()
	bar.color = IDLE_COLOUR
	bar.size = size
	bar.position = -size * 0.5
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	crosshair.add_child(bar)
	_bars.append(bar)


func _make_label(node_name: String, align: HorizontalAlignment) -> Label:
	var label: Label = Label.new()
	label.name = node_name
	label.horizontal_alignment = align
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.add_theme_font_size_override("font_size", UiStyle.px(UiStyle.TEXT_BODY))
	label.add_theme_color_override("font_color", Color(0.93, 0.95, 0.96))
	label.add_theme_color_override("font_outline_color", Color(0.05, 0.06, 0.08))
	label.add_theme_constant_override("outline_size", 6)
	return label


func _render_goal() -> void:
	var level: Node = Game.current_level()
	var open: bool = level != null and bool(level.get(&"case_open"))
	if is_instance_valid(win_page):
		goal.text = tr("Done: the cube is on the table.")
	elif open:
		goal.text = tr("Goal: put the cube on the table.")
	else:
		goal.text = tr("Goal: press the button to open the glass case.")


func _on_world_changed(_node: Node) -> void:
	_render_goal()


func _on_device_changed(_using_pad: bool) -> void:
	_on_target_changed(_target)


func _on_held_changed(item: Node3D) -> void:
	_holding = item != null
	_on_target_changed(_target)


func _on_target_changed(target: Node) -> void:
	_target = target if is_instance_valid(target) else null
	for bar: ColorRect in _bars:
		bar.color = IDLE_COLOUR if _target == null and not _holding else ACTIVE_COLOUR
	if is_instance_valid(win_page):
		hint.visible = false
		return
	var verb: String = ""
	if _holding:
		verb = tr("Put it down")
	elif _target != null:
		verb = String(_target.call(&"prompt")) if _target.has_method(&"prompt") else tr("Use")
	if verb.is_empty():
		hint.text = ""
		hint.visible = false
		return
	var key: String = _interact_key()
	hint.text = tr("%s: %s") % [key, verb] if key != "" else verb
	hint.visible = true


func _interact_key() -> String:
	var controller: Node = get_tree().root.get_node_or_null(^"Controller")
	if controller != null and controller.has_method(&"prompt"):
		return String(controller.call(&"prompt", &"interact"))
	for event: InputEvent in InputMap.action_get_events(&"interact"):
		if event is InputEventKey:
			var key_event: InputEventKey = event
			var code: Key = key_event.keycode
			if code == KEY_NONE:
				code = key_event.physical_keycode
			return OS.get_keycode_string(code)
	return ""
