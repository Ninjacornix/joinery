extends CanvasLayer
## Coins, time and falls while playing; the win screen when the flag is
## reached. Built in code with the core menu style, so it stays readable at
## any window size.

signal play_again_requested
signal main_menu_requested

var root: Control
var stats: PanelContainer
var coins_label: Label
var time_label: Label
var falls_label: Label
var win_panel: Control
var win_coins_label: Label
var win_time_label: Label
var win_falls_label: Label
var again_button: Button
var menu_button: Button

## What the labels show, kept so a language change can draw them again.
var _coins: Vector2i = Vector2i.ZERO
var _seconds: float = 0.0
var _falls: int = 0
var _win_result: Dictionary = {}


func _ready() -> void:
	root = Control.new()
	root.name = "Root"
	root.theme = UiStyle.build_theme()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	stats = PanelContainer.new()
	stats.name = "Stats"
	stats.position = Vector2(UiStyle.px(32), UiStyle.px(32))
	stats.mouse_filter = Control.MOUSE_FILTER_IGNORE
	stats.add_theme_stylebox_override(
		&"panel", UiStyle.box(Color(UiStyle.PANEL, 0.82), UiStyle.px(10), UiStyle.px(18))
	)
	var column: VBoxContainer = VBoxContainer.new()
	column.add_theme_constant_override(&"separation", UiStyle.px(6))
	coins_label = _value_label("Coins", 36, UiStyle.ACCENT)
	time_label = _value_label("Time", 28, UiStyle.TEXT)
	falls_label = _value_label("Falls", 28, UiStyle.TEXT_DIM)
	for label: Label in [coins_label, time_label, falls_label]:
		column.add_child(label)
	stats.add_child(column)
	root.add_child(stats)

	_build_win_panel()
	set_coins(0, 0)
	set_time(0.0)
	set_falls(0)
	set_timer_visible(bool(Settings.get_value("platformer", "show_timer", true)))
	Settings.changed.connect(_on_setting_changed)


func _notification(what: int) -> void:
	if what == NOTIFICATION_TRANSLATION_CHANGED and coins_label != null:
		set_coins(_coins.x, _coins.y)
		set_time(_seconds)
		set_falls(_falls)
		if win_panel.visible:
			_render_win()


func set_coins(count: int, total: int) -> void:
	_coins = Vector2i(count, total)
	coins_label.text = tr("Coins: %d / %d") % [count, total]


func set_time(seconds: float) -> void:
	_seconds = seconds
	time_label.text = tr("Time: %s") % format_time(seconds)


func set_falls(count: int) -> void:
	_falls = count
	falls_label.text = tr("Falls: %d") % count


func set_timer_visible(on: bool) -> void:
	time_label.visible = on


func show_win(result: Dictionary) -> void:
	_win_result = result
	_render_win()
	win_panel.visible = true
	again_button.grab_focus.call_deferred()


func _render_win() -> void:
	var result: Dictionary = _win_result
	win_coins_label.text = tr("Coins: %d / %d") % [
		int(result.get("coins", 0)), int(result.get("total", 0))
	]
	win_time_label.text = tr("Time: %s") % format_time(float(result.get("time", 0.0)))
	win_falls_label.text = tr("Falls: %d") % int(result.get("falls", 0))


func hide_win() -> void:
	win_panel.visible = false


## "m:ss.cc" for a time in seconds.
static func format_time(seconds: float) -> String:
	var minutes: int = floori(seconds / 60.0)
	return "%d:%05.2f" % [minutes, seconds - float(minutes) * 60.0]


func _build_win_panel() -> void:
	var column: VBoxContainer = VBoxContainer.new()
	column.add_theme_constant_override(&"separation", UiStyle.px(16))
	win_coins_label = _value_label("WinCoins", 32, UiStyle.ACCENT)
	win_time_label = _value_label("WinTime", 32, UiStyle.TEXT)
	win_falls_label = _value_label("WinFalls", 32, UiStyle.TEXT_DIM)
	again_button = UiStyle.button("Play again")
	again_button.name = "PlayAgain"
	menu_button = UiStyle.button("Main menu")
	menu_button.name = "MainMenu"
	again_button.pressed.connect(play_again_requested.emit)
	menu_button.pressed.connect(main_menu_requested.emit)
	for node: Control in [win_coins_label, win_time_label, win_falls_label, again_button, menu_button]:
		if node is Label:
			(node as Label).horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		column.add_child(node)
	win_panel = UiStyle.page("Level complete!", column)
	win_panel.name = "Win"
	win_panel.visible = false
	root.add_child(win_panel)


## A label whose text is filled in by code (already translated), so the
## label does not translate it a second time.
func _value_label(node_name: String, size_1080: int, colour: Color) -> Label:
	var label: Label = Label.new()
	label.name = node_name
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.add_theme_font_size_override(&"font_size", UiStyle.px(size_1080))
	label.add_theme_color_override(&"font_color", colour)
	label.add_theme_color_override(&"font_outline_color", Color(0.0, 0.0, 0.0, 0.8))
	label.add_theme_constant_override(&"outline_size", UiStyle.px(6))
	return label


func _on_setting_changed(section: String, key: String) -> void:
	if section == "platformer" and key == "show_timer":
		set_timer_visible(bool(Settings.get_value("platformer", "show_timer", true)))
