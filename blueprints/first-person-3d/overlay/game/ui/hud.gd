extends CanvasLayer
## Player HUD, built in code: a crosshair that brightens over something
## usable, a hint naming what the interact action will do, a switch counter,
## and the win screen once the player walks out through the exit.

const CROSSHAIR_SIZE := 4.0
const CROSSHAIR_LENGTH := 10.0
const IDLE_COLOUR := Color(0.93, 0.95, 0.96, 0.7)
const ACTIVE_COLOUR := Color(1.0, 0.85, 0.3, 1.0)

var crosshair: Control
var hint: Label
var counter: Label
var win_panel: Control
var win_time_label: Label
var win_switches_label: Label
var again_button: Button
var menu_button: Button
var _bars: Array[ColorRect] = []


## "m:ss.cc" for a time in seconds.
static func format_time(seconds: float) -> String:
	var minutes: int = floori(seconds / 60.0)
	return "%d:%05.2f" % [minutes, seconds - float(minutes) * 60.0]


func _ready() -> void:
	layer = 5
	var root := Control.new()
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
	hint.offset_left = -300.0
	hint.offset_right = 300.0
	hint.offset_top = 32.0
	hint.offset_bottom = 72.0
	root.add_child(hint)

	counter = _make_label("Counter", HORIZONTAL_ALIGNMENT_LEFT)
	counter.set_anchors_and_offsets_preset(Control.PRESET_TOP_LEFT)
	counter.offset_left = 24.0
	counter.offset_top = 16.0
	counter.offset_right = 424.0
	counter.offset_bottom = 56.0
	root.add_child(counter)
	_build_win_panel(root)

	var player: Node = get_parent()
	if player.has_signal(&"target_changed"):
		player.connect(&"target_changed", _on_target_changed)
	GameEvents.score_changed.connect(_on_score_changed)
	GameEvents.level_completed.connect(show_win)
	_on_target_changed(null)
	_on_score_changed(0)


## Shows the win screen for a `level_completed` result and focuses Play again.
func show_win(result: Dictionary) -> void:
	hint.visible = false
	crosshair.visible = false
	win_time_label.text = tr("Time: %s") % format_time(float(result.get("time", 0.0)))
	win_switches_label.text = tr("Switches on: %d of %d") % [
		int(result.get("switches", 0)), int(result.get("total", result.get("switches", 0)))
	]
	win_panel.visible = true
	again_button.grab_focus.call_deferred()


func hide_win() -> void:
	win_panel.visible = false
	crosshair.visible = true


func _build_win_panel(root: Control) -> void:
	var column: VBoxContainer = VBoxContainer.new()
	win_time_label = _make_label("WinTime", HORIZONTAL_ALIGNMENT_CENTER)
	win_switches_label = _make_label("WinSwitches", HORIZONTAL_ALIGNMENT_CENTER)
	again_button = UiStyle.button("Play again")
	again_button.name = "PlayAgain"
	again_button.pressed.connect(Game.restart_level)
	menu_button = UiStyle.button("Main menu")
	menu_button.name = "MainMenu"
	menu_button.pressed.connect(Game.quit_to_menu)
	for node: Control in [win_time_label, win_switches_label, again_button, menu_button]:
		column.add_child(node)
	win_panel = UiStyle.page("You made it out!", column)
	win_panel.name = "WinScreen"
	win_panel.theme = UiStyle.build_theme()
	win_panel.visible = false
	root.add_child(win_panel)


func _exit_tree() -> void:
	if GameEvents.level_completed.is_connected(show_win):
		GameEvents.level_completed.disconnect(show_win)
	if GameEvents.score_changed.is_connected(_on_score_changed):
		GameEvents.score_changed.disconnect(_on_score_changed)


func _add_bar(size: Vector2) -> void:
	var bar := ColorRect.new()
	bar.color = IDLE_COLOUR
	bar.size = size
	bar.position = -size * 0.5
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	crosshair.add_child(bar)
	_bars.append(bar)


func _make_label(node_name: String, align: HorizontalAlignment) -> Label:
	var label := Label.new()
	label.name = node_name
	label.horizontal_alignment = align
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.add_theme_font_size_override("font_size", _text_size())
	label.add_theme_color_override("font_color", Color(0.93, 0.95, 0.96))
	label.add_theme_color_override("font_outline_color", Color(0.05, 0.06, 0.08))
	label.add_theme_constant_override("outline_size", 6)
	return label


func _text_size() -> int:
	return UiStyle.px(UiStyle.TEXT_BODY)


func _on_target_changed(target: Node) -> void:
	for bar: ColorRect in _bars:
		bar.color = IDLE_COLOUR if target == null else ACTIVE_COLOUR
	if target == null or (win_panel != null and win_panel.visible):
		hint.text = ""
		hint.visible = false
		return
	var verb: String = tr("Use")
	if target.has_method(&"prompt"):
		verb = String(target.call(&"prompt"))
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


func _on_score_changed(value: int) -> void:
	var level: Node = Game.current_level()
	var total: int = 0
	if level != null and level.has_method(&"switch_total"):
		total = int(level.call(&"switch_total"))
	counter.visible = total > 0
	counter.text = tr("Switches on: %d of %d") % [value, total]
