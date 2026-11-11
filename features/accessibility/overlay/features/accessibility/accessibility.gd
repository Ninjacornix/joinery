extends Node
## Accessibility settings, applied as soon as they change. Adds the "Accessibility"
## settings page (access_page.gd), replacing the core's, and applies:
##
##   access/text_scale       0.8-2.0: menu text, and HUD text sized in code
##   access/high_contrast    bool: menus in black and white with clear edges
##   access/colour_filter    "none", "protanopia", "deuteranopia" or "tritanopia":
##                           a full-screen filter that shifts colours people with that
##                           colour vision can't tell apart into ones they can
##   access/screen_shake     0-1 (core), access/reduced_motion bool (core); reduced
##                           motion also turns screen shake off until it is turned off
##   access/reduce_flashes   bool, read by effects through flashes_reduced()
##   access/toggle_actions   bool: held actions (sprint, crouch, aim, ...) toggle instead
##   access/game_speed       0.5-1.0: Engine.time_scale while playing
##   access/subtitles, access/subtitle_size   read by the Subtitles autoload
##
## Menu controls with no text get the name screen readers say from the label beside
## them whenever a menu opens.

const SECTION := "access"
const PAGE := "res://features/accessibility/access_page.gd"
const FILTER := "res://features/accessibility/colour_filter.gdshader"
const FILTERS := ["none", "protanopia", "deuteranopia", "tritanopia"]
## Actions a player has to hold down; with toggle on, a press latches them.
const HOLD_ACTIONS: Array[StringName] = [
	&"sprint", &"crouch", &"aim", &"block", &"run", &"walk", &"zoom", &"focus"
]
const DEFAULTS := {
	"text_scale": 1.0,
	"high_contrast": false,
	"colour_filter": "none",
	"reduce_flashes": false,
	"toggle_actions": false,
	"game_speed": 1.0,
	"subtitles": true,
	"subtitle_size": 1.0,
}
const BASE_SIZE := &"access_base_font_size"

## The colour filter's full-screen layer.
var filter_layer: CanvasLayer
var filter_rect: ColorRect
## Held actions latched on by toggle mode.
var latched: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_build_filter()
	Menus.add_settings_page(&"accessibility", "Accessibility", _build_page, 30)
	Settings.changed.connect(_on_changed)
	GameEvents.menu_opened.connect(_on_menu_opened)
	get_tree().node_added.connect(_on_node_added)
	for key: String in DEFAULTS:
		_apply(key)


func value(key: String) -> Variant:
	return Settings.get_value(SECTION, key, DEFAULTS.get(key))


func text_scale() -> float:
	return clampf(float(value("text_scale")), 0.8, 2.0)


## Effects that flash (hits, lightning, strobes) skip or soften the flash when true.
func flashes_reduced() -> bool:
	return bool(value("reduce_flashes")) or Settings.reduced_motion()


## The held actions this game has, for toggle mode and the page.
func hold_actions() -> Array[StringName]:
	var found: Array[StringName] = []
	for action: StringName in HOLD_ACTIONS:
		if InputMap.has_action(action):
			found.append(action)
	return found


func _input(event: InputEvent) -> void:
	if not bool(value("toggle_actions")) or event.is_echo():
		return
	for action: StringName in hold_actions():
		if event.is_action_pressed(action):
			latched[action] = not bool(latched.get(action, false))
			if latched[action]:
				Input.action_press(action)
			else:
				Input.action_release(action)
		elif event.is_action_released(action) and bool(latched.get(action, false)):
			# The key came up, but the action stays on until the next press.
			Input.action_press(action)


func _build_page() -> Control:
	var script: GDScript = load(PAGE) as GDScript
	return script.new() as Control


func _on_changed(section: String, key: String) -> void:
	if section == SECTION:
		_apply(key)


func _apply(key: String) -> void:
	match key:
		"text_scale", "high_contrast":
			_apply_theme()
			_scale_hud(get_tree().root)
		"colour_filter":
			var mode := FILTERS.find(str(value("colour_filter")))
			filter_rect.visible = mode > 0
			(filter_rect.material as ShaderMaterial).set_shader_parameter("mode", maxi(mode, 0))
		"reduced_motion":
			_apply_motion()
		"toggle_actions":
			if not bool(value("toggle_actions")):
				for action: StringName in latched:
					if bool(latched[action]):
						Input.action_release(action)
				latched.clear()
		"game_speed":
			Engine.time_scale = clampf(float(value("game_speed")), 0.5, 1.0)


## Reduced motion turns screen shake off, and back to where it was when turned off.
func _apply_motion() -> void:
	var reduced := Settings.reduced_motion()
	var shake := float(Settings.get_value(SECTION, "screen_shake"))
	if reduced and shake > 0.0:
		Settings.set_value(SECTION, "screen_shake_before", shake)
		Settings.set_value(SECTION, "screen_shake", 0.0)
	elif not reduced and Settings.has_value(SECTION, "screen_shake_before"):
		Settings.set_value(
			SECTION, "screen_shake", float(Settings.get_value(SECTION, "screen_shake_before"))
		)
		Settings.set_value(SECTION, "screen_shake_before", null)


## The menus' theme at the chosen text size, in high contrast when asked.
func _apply_theme() -> void:
	var root := Menus.get_node_or_null(^"Root") as Control
	if root == null:
		return
	var theme := UiStyle.build_theme()
	var size := text_scale()
	theme.default_font_size = roundi(theme.default_font_size * size)
	for type: StringName in [&"TitleLabel", &"HintLabel"]:
		theme.set_font_size(
			&"font_size", type, roundi(theme.get_font_size(&"font_size", type) * size)
		)
	if bool(value("high_contrast")):
		_high_contrast(theme)
	root.theme = theme
	ThemeDB.fallback_font_size = roundi(16 * size)


static func _high_contrast(theme: Theme) -> void:
	var edge := UiStyle.px(3)
	var radius := UiStyle.px(10)
	var pad := UiStyle.px(14)
	theme.set_stylebox(
		&"panel",
		&"PanelContainer",
		UiStyle.box(Color.BLACK, radius, UiStyle.px(28), Color.WHITE, edge)
	)
	theme.set_stylebox(
		&"panel", &"Panel", UiStyle.box(Color.BLACK, radius, UiStyle.px(28), Color.WHITE, edge)
	)
	for type: StringName in [&"Button", &"CheckButton", &"OptionButton", &"MenuButton"]:
		theme.set_stylebox(
			&"normal", type, UiStyle.box(Color.BLACK, radius, pad, Color.WHITE, edge)
		)
		theme.set_stylebox(
			&"hover", type, UiStyle.box(Color("#303030"), radius, pad, Color.WHITE, edge)
		)
		theme.set_color(&"font_disabled_color", type, Color("#c8c8c8"))
	for type: StringName in [&"Label", &"TitleLabel", &"HintLabel"]:
		theme.set_color(&"font_color", type, Color.WHITE)


## HUD text sized in code (a font size override) follows the text size too.
func _scale_hud(node: Node) -> void:
	_scale_label(node)
	for child: Node in node.get_children():
		_scale_hud(child)


func _scale_label(node: Node) -> void:
	if not (node is Label or node is RichTextLabel or node is Button):
		return
	var control := node as Control
	if Menus.is_ancestor_of(control):
		return
	if not control.has_meta(BASE_SIZE):
		if not control.has_theme_font_size_override(&"font_size"):
			return
		control.set_meta(BASE_SIZE, control.get_theme_font_size(&"font_size"))
	var base := int(control.get_meta(BASE_SIZE))
	control.add_theme_font_size_override(&"font_size", roundi(base * text_scale()))


func _on_node_added(node: Node) -> void:
	if node is Label or node is RichTextLabel or node is Button:
		# HUD code sets its size after adding the label; look once it has.
		# The game may free it first (cards are dealt and discarded in one frame).
		_scale_later.call_deferred(weakref(node))


func _scale_later(ref: WeakRef) -> void:
	var node := ref.get_ref() as Node
	if node != null:
		_scale_label(node)


func _on_menu_opened(page: Control) -> void:
	_name_controls(page)


## Controls with no text of their own take the text of the label beside them.
func _name_controls(node: Node) -> void:
	if node is Control:
		var control := node as Control
		var text := str(control.get(&"text")) if "text" in control else ""
		if (
			control.focus_mode == Control.FOCUS_ALL
			and text.strip_edges().is_empty()
			and str(control.accessibility_name).is_empty()
		):
			var label := _label_beside(control)
			if not label.is_empty():
				control.accessibility_name = tr(label)
	for child: Node in node.get_children():
		_name_controls(child)


static func _label_beside(control: Control) -> String:
	var parent := control.get_parent()
	if parent == null:
		return ""
	for sibling: Node in parent.get_children():
		if sibling is Label and sibling != control:
			return str((sibling as Label).text)
	return ""


func _build_filter() -> void:
	filter_layer = CanvasLayer.new()
	filter_layer.name = "ColourFilter"
	filter_layer.layer = 120
	add_child(filter_layer)
	filter_rect = ColorRect.new()
	filter_rect.name = "Filter"
	filter_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	filter_rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var material := ShaderMaterial.new()
	material.shader = load(FILTER) as Shader
	filter_rect.material = material
	filter_rect.visible = false
	filter_layer.add_child(filter_rect)
