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
			return true
	return false


## The Joinery theme's segmented buttons: the current screen on an accent tint.
func _ready() -> void:
	UI_THEME.watch(self)


## Highlight [param screen], or nothing when it is not one of Godot's screens.
func select(screen: String) -> void:
	for id: String in _buttons:
		(_buttons[id] as Button).set_pressed_no_signal(id == screen)


func _process(delta: float) -> void:
	_since += delta
	if _since < PLAY_CHECK:
		return
	_since = 0.0
	var game := _buttons["Game"] as Button
	var playing := EditorInterface.is_playing_scene()
	if game.visible != playing:
		game.visible = playing
