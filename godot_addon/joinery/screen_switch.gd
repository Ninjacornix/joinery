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
		button.flat = true
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


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_style.call_deferred()


## The current screen on a raised pill in accent text, like the selected rail item.
func _style() -> void:
	var pill := StyleBoxFlat.new()
	pill.bg_color = UI_THEME.color(self, "surface_raised")
	pill.set_corner_radius_all(UI_THEME.radius(self, "md"))
	pill.set_content_margin_all(UI_THEME.space(self, 1))
	pill.content_margin_left = UI_THEME.space(self, 2)
	pill.content_margin_right = UI_THEME.space(self, 2)
	var plain := pill.duplicate() as StyleBoxFlat
	plain.draw_center = false
	for button: Button in _buttons.values():
		button.begin_bulk_theme_override()
		for state: String in ["normal", "hover", "focus", "disabled"]:
			button.add_theme_stylebox_override(state, plain)
		button.add_theme_stylebox_override("pressed", pill)
		button.add_theme_stylebox_override("hover_pressed", pill)
		button.add_theme_color_override("font_color", UI_THEME.color(self, "text_muted"))
		button.add_theme_color_override("font_hover_color", UI_THEME.color(self, "text"))
		for state: String in ["font_pressed_color", "font_hover_pressed_color"]:
			button.add_theme_color_override(state, UI_THEME.color(self, "accent"))
		button.end_bulk_theme_override()


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
