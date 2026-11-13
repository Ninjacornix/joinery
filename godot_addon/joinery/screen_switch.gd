@tool
extends HBoxContainer
## The 2D | 3D | Script switch in the scene tab row (and Game while the game runs), in
## Joinery's layout. The fork hides Godot's own main screen buttons and the app rail opens
## Godot with one item, so this is where the user sees, and changes, which Godot screen shows.
## The current screen is highlighted; nothing is while an app or a Joinery page has the main
## area.

const UI_THEME := preload("ui_theme.gd")
const SCREENS := ["2D", "3D", "Script", "Game"]
const TIPS := {
	"2D": "2D view",
	"3D": "3D view",
	"Script": "Script editor",
	"Game": "The running game",
}
## Seconds between checks for a running game, which adds the Game item.
const PLAY_CHECK := 0.5

var _buttons: Dictionary = {}
var _group := ButtonGroup.new()
var _since := 0.0
## The scene tab bar this switch sits beside, and Godot's "+" button on it.
var _tabs: TabBar
var _add: Button


func _init() -> void:
	name = "JoineryScreenSwitch"
	alignment = BoxContainer.ALIGNMENT_CENTER
	_group.allow_unpress = true
	for screen: String in SCREENS:
		var button := Button.new()
		button.name = screen
		button.text = screen
		button.theme_type_variation = "JoinerySegment"
		button.toggle_mode = true
		button.button_group = _group
		button.focus_mode = Control.FOCUS_ACCESSIBILITY
		button.tooltip_text = TIPS[screen]
		button.accessibility_name = TIPS[screen]
		button.pressed.connect(func() -> void: EditorInterface.set_main_screen_editor(screen))
		add_child(button)
		_buttons[screen] = button
	(_buttons["Game"] as Button).hide()


## Put the switch in [param base]'s scene tab row, right of the tabs. Returns false when this
## editor has no scene tab row.
func place(base: Control) -> bool:
	for tabs: Node in base.find_children("*", "EditorSceneTabs", true, false):
		for bar: Node in tabs.find_children("*", "TabBar", true, false):
			var row := bar.get_parent() as HBoxContainer
			if row == null:
				continue
			row.add_child(self)
			row.move_child(self, bar.get_index() + 1)
			_tabs = bar as TabBar
			_add = _add_button(bar)
			return true
	return false


## Godot's "+" (new scene) button: a child of the tab bar, drawn right after the last tab.
static func _add_button(bar: Node) -> Button:
	for child: Node in bar.get_children(true):
		if child is Button:
			return child as Button
	return null


## Keep the tab bar at least as wide as its widest tab plus the "+" button beside it. Godot
## reserves that room with a spacer right of the tab bar, but this switch sits there now, so
## in a narrow window the tab bar shrank to one tab and the "+" button drew over 2D | 3D.
## Checked every frame: a tab's width also changes with hover and selection, which the tab
## bar does not announce.
func _keep_room_for_add() -> void:
	if not is_instance_valid(_tabs) or not is_instance_valid(_add):
		return
	var room := 0.0
	if _tabs.tab_count > 0:
		var tabs := _tabs.get_minimum_size().x
		var gap := float(_tabs.get_theme_constant("h_separation"))
		room = tabs + gap + maxf(_add.size.x, _add.get_combined_minimum_size().x)
	if not is_equal_approx(_tabs.custom_minimum_size.x, room):
		_tabs.custom_minimum_size.x = room


## The Joinery theme's segmented buttons: the current screen on an accent tint.
func _ready() -> void:
	UI_THEME.watch(self)


## Highlight [param screen], or nothing when it is not one of Godot's screens.
func select(screen: String) -> void:
	for id: String in _buttons:
		(_buttons[id] as Button).set_pressed_no_signal(id == screen)


func _process(delta: float) -> void:
	_keep_room_for_add()
	_since += delta
	if _since < PLAY_CHECK:
		return
	_since = 0.0
	var game := _buttons["Game"] as Button
	var playing := EditorInterface.is_playing_scene()
	if game.visible != playing:
		game.visible = playing
