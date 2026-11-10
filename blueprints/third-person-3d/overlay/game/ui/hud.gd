extends CanvasLayer
## Coins, time and the interact prompt while playing; the win screen when the
## last coin is collected. Styled with the core `UiStyle` theme.

signal play_again_requested
signal main_menu_requested

var coins_label: Label
var time_label: Label
var prompt_label: Label
var win_panel: Control
var win_time_label: Label
var win_coins_label: Label
var again_button: Button
var menu_button: Button

var _player: Node = null


## "m:ss.cc" for a time in seconds.
static func format_time(seconds: float) -> String:
	var minutes: int = floori(seconds / 60.0)
	return "%d:%05.2f" % [minutes, seconds - float(minutes) * 60.0]


func _ready() -> void:
	var root: Control = Control.new()
	root.name = "Root"
	root.theme = UiStyle.build_theme()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	var stats: PanelContainer = PanelContainer.new()
	stats.name = "Stats"
	stats.position = Vector2(UiStyle.px(32), UiStyle.px(32))
	stats.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var column: VBoxContainer = VBoxContainer.new()
	coins_label = Label.new()
	coins_label.name = "Coins"
	coins_label.add_theme_color_override(&"font_color", UiStyle.ACCENT)
	coins_label.add_theme_font_size_override(&"font_size", UiStyle.px(36))
	time_label = Label.new()
	time_label.name = "Time"
	for label: Label in [coins_label, time_label]:
		# Text is already translated with tr() before the numbers go in.
		label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	column.add_child(coins_label)
	column.add_child(time_label)
	stats.add_child(column)
	root.add_child(stats)

	prompt_label = Label.new()
	prompt_label.name = "Prompt"
	prompt_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	prompt_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	prompt_label.grow_horizontal = Control.GROW_DIRECTION_BOTH
	prompt_label.grow_vertical = Control.GROW_DIRECTION_BEGIN
	prompt_label.offset_bottom = -UiStyle.px(96)
	prompt_label.add_theme_color_override(&"font_outline_color", UiStyle.BACKDROP)
	prompt_label.add_theme_constant_override(&"outline_size", UiStyle.px(8))
	prompt_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	prompt_label.visible = false
	root.add_child(prompt_label)

	_build_win_panel(root)
	set_coins(0, 0)
	set_time(0.0)


func set_coins(count: int, total: int) -> void:
	coins_label.text = tr("Coins: %d / %d") % [count, total]


func set_time(seconds: float) -> void:
	time_label.text = tr("Time: %s") % format_time(seconds)


func bind_player(player: Node) -> void:
	if _player != null and is_instance_valid(_player):
		_player.disconnect(&"interact_target_changed", _on_target_changed)
		_player.disconnect(&"carrying_changed", _on_carrying_changed)
	_player = player
	player.connect(&"interact_target_changed", _on_target_changed)
	player.connect(&"carrying_changed", _on_carrying_changed)
	_refresh_prompt()


func show_win(result: Dictionary) -> void:
	prompt_label.visible = false
	var seconds: float = float(result.get("time", 0.0))
	win_time_label.text = tr("Time: %s") % format_time(seconds)
	var score: int = int(result.get("score", 0))
	var total: int = int(result.get("total", 0))
	win_coins_label.text = tr("Coins: %d / %d") % [score, total]
	win_panel.visible = true
	again_button.grab_focus()


func hide_win() -> void:
	win_panel.visible = false
	_refresh_prompt()


## Key or button text for `action`: the controller feature's prompt when it is
## added, else the first keyboard key bound to it.
func action_hint(action: StringName) -> String:
	var controller: Node = get_node_or_null(^"/root/Controller")
	if controller != null and controller.has_method(&"prompt"):
		return str(controller.call(&"prompt", action))
	for event: InputEvent in InputMap.action_get_events(action):
		var key: InputEventKey = event as InputEventKey
		if key != null:
			var code: Key = key.physical_keycode
			if code == KEY_NONE:
				code = key.keycode
			return OS.get_keycode_string(code)
	return String(action)


func _build_win_panel(root: Control) -> void:
	var column: VBoxContainer = VBoxContainer.new()
	win_coins_label = Label.new()
	win_coins_label.name = "WinCoins"
	win_coins_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	win_time_label = Label.new()
	win_time_label.name = "WinTime"
	win_time_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	win_coins_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	win_time_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	again_button = UiStyle.button("Play again")
	again_button.name = "PlayAgain"
	menu_button = UiStyle.button("Main menu")
	menu_button.name = "MainMenu"
	again_button.pressed.connect(play_again_requested.emit)
	menu_button.pressed.connect(main_menu_requested.emit)
	for node: Control in [win_coins_label, win_time_label, again_button, menu_button]:
		column.add_child(node)
	win_panel = UiStyle.page("Arena cleared!", column)
	win_panel.name = "WinScreen"
	win_panel.visible = false
	root.add_child(win_panel)


func _on_target_changed(_target: Node) -> void:
	_refresh_prompt()


func _on_carrying_changed(_item: Node3D) -> void:
	_refresh_prompt()


func _refresh_prompt() -> void:
	if _player == null or not is_instance_valid(_player) or win_panel.visible:
		prompt_label.visible = false
		return
	var carried: Variant = _player.get(&"carrying")
	var target: Variant = carried if carried != null else _player.get(&"interact_target")
	var node: Node = target as Node
	if node == null or not node.has_method(&"interact_prompt"):
		prompt_label.visible = false
		return
	var verb: String = str(node.call(&"interact_prompt"))
	prompt_label.text = tr("%s [%s]") % [verb, action_hint(&"interact")]
	prompt_label.visible = true
