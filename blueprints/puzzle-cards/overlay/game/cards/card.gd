extends Button
## One card on the table. Pressing it (click, tap or `ui_accept` while it has
## focus) asks the board to flip it through `Game.request_action`, so an
## online session can run the rules on the server. The board decides; the
## card only shows its state.
##
## Looks, all StyleBoxFlat: a framed back with a diamond pip, a cream face
## with the symbol in its own ink, a green ring for a match, a red ring for a
## miss, and an empty slot once a pair is cleared.

const CardStyle := preload("res://game/ui/card_style.gd")
const Deck := preload("res://game/deck.gd")
const FLIP_SECONDS := 0.25
const MATCH_HOLD_SECONDS := 0.1
const FADE_SECONDS := 0.3
const LOOK_BACK := &"back"
const LOOK_FACE := &"face"
const LOOK_MISS := &"miss"
const LOOK_MATCH := &"match"
const LOOK_SLOT := &"slot"

@export var save_id: String = "card"

## Slot in the grid (0 = top left).
var index: int = 0
## Symbol index into `Deck.SYMBOLS`; -1 before the deal.
var symbol: int = -1
var face_up: bool = false
var matched: bool = false
## Marked by the board while a missed pair waits to turn back.
var missed: bool = false
## The level node that owns the rules; set when the board deals.
var board: Node = null
## True once the game is over: the card keeps its look but takes no input.
var locked: bool = false:
	set(value):
		locked = value
		_apply_input_state()

var _look: StringName = &""
var _tween: Tween = null

@onready var _frame: Panel = $Frame
@onready var _pip: Panel = $Frame/Pip


func _ready() -> void:
	add_to_group(&"saveable")
	auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	toggle_mode = false
	focus_mode = Control.FOCUS_ALL
	custom_minimum_size = Vector2(CardStyle.px(150), CardStyle.px(190))
	add_theme_font_size_override(&"font_size", CardStyle.px(CardStyle.GLYPH))
	add_theme_stylebox_override(&"focus", CardStyle.focus_ring())
	var inset: int = CardStyle.px(12)
	_frame.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE)
	_frame.offset_left = inset
	_frame.offset_top = inset
	_frame.offset_right = -inset
	_frame.offset_bottom = -inset
	_frame.add_theme_stylebox_override(&"panel", CardStyle.back_frame())
	var pip: float = float(CardStyle.px(30))
	_pip.set_anchors_preset(Control.PRESET_CENTER)
	_pip.offset_left = -pip * 0.5
	_pip.offset_top = -pip * 0.5
	_pip.offset_right = pip * 0.5
	_pip.offset_bottom = pip * 0.5
	_pip.pivot_offset = Vector2(pip, pip) * 0.5
	_pip.rotation_degrees = 45.0
	_pip.add_theme_stylebox_override(&"panel", CardStyle.back_pip())
	pressed.connect(_on_pressed)
	resized.connect(_on_resized)
	focus_entered.connect(_on_focus_entered)
	_on_resized()
	_apply_input_state()
	_paint(_final_look())


## The symbol text this card shows when face up.
func glyph() -> String:
	return Deck.symbol(symbol)


## True once its pair has been cleared from the table.
func is_removed() -> bool:
	return matched


## A face-down card that may still be flipped.
func is_hidden() -> bool:
	return not face_up and not matched


## Runs on the authority (directly offline, on the server online).
func flip() -> void:
	if board != null and board.has_method(&"try_flip"):
		board.call(&"try_flip", self)


## Puts a new symbol on the card, face down (a fresh deal).
func deal(symbol_index: int) -> void:
	symbol = symbol_index
	face_up = false
	matched = false
	missed = false
	_apply_input_state()
	show_state(false)


func set_face_up(up: bool, animate: bool = true) -> void:
	face_up = up
	if not up:
		missed = false
	show_state(animate)


func set_missed(on: bool) -> void:
	missed = on
	show_state(false)


func set_matched(animate: bool = true) -> void:
	matched = true
	face_up = true
	missed = false
	_apply_input_state()
	show_state(animate)


func save_state() -> Dictionary:
	return {"symbol": symbol, "face_up": face_up, "matched": matched}


func load_state(state: Dictionary) -> void:
	symbol = int(state.get("symbol", symbol))
	face_up = bool(state.get("face_up", face_up))
	matched = bool(state.get("matched", matched))
	missed = false
	_apply_input_state()
	show_state(false)
	if board != null and board.has_method(&"resync"):
		board.call(&"resync")


## Shows the current state, with the flip and fade motion unless `animate`
## is false or the player turned on reduced motion.
func show_state(animate: bool) -> void:
	if _tween != null and _tween.is_valid():
		_tween.kill()
	_tween = null
	scale = Vector2.ONE
	modulate.a = 1.0
	var target: StringName = _final_look()
	if not animate or _reduced_motion() or not is_inside_tree():
		_paint(target)
		return
	var turning: bool = (_look == LOOK_BACK) != (target == LOOK_BACK)
	if _look == LOOK_SLOT and target != LOOK_SLOT:
		turning = true
	if not turning and target != LOOK_SLOT:
		_paint(target)
		return
	_tween = create_tween()
	if turning:
		var half: float = FLIP_SECONDS * 0.5
		var shown: StringName = LOOK_MATCH if target == LOOK_SLOT else target
		_tween.tween_property(self, ^"scale:x", 0.0, half).set_trans(Tween.TRANS_SINE).set_ease(
			Tween.EASE_IN
		)
		_tween.tween_callback(_paint.bind(shown))
		_tween.tween_property(self, ^"scale:x", 1.0, half).set_trans(Tween.TRANS_SINE).set_ease(
			Tween.EASE_OUT
		)
	if target == LOOK_SLOT:
		if not turning:
			_paint(LOOK_MATCH)
		_tween.tween_interval(MATCH_HOLD_SECONDS)
		_tween.tween_property(self, ^"modulate:a", 0.0, FADE_SECONDS)
		_tween.tween_callback(_paint.bind(LOOK_SLOT))
		_tween.tween_property(self, ^"modulate:a", 1.0, 0.0)


## True while a flip or fade is still playing.
func is_animating() -> bool:
	return _tween != null and _tween.is_valid() and _tween.is_running()


## The look currently on screen: back, face, miss, match or slot.
func look() -> StringName:
	return _look


func _final_look() -> StringName:
	if matched:
		return LOOK_SLOT
	if face_up:
		return LOOK_MISS if missed else LOOK_FACE
	return LOOK_BACK


func _paint(look_name: StringName) -> void:
	_look = look_name
	var normal: StyleBoxFlat
	var hover: StyleBoxFlat
	match look_name:
		LOOK_BACK:
			normal = CardStyle.back()
			hover = CardStyle.back_hover()
		LOOK_MISS:
			normal = CardStyle.missed()
			hover = normal
		LOOK_MATCH:
			normal = CardStyle.matched()
			hover = normal
		LOOK_SLOT:
			normal = CardStyle.slot()
			hover = normal
		_:
			normal = CardStyle.face()
			hover = normal
	for state: StringName in [&"normal", &"pressed", &"disabled"]:
		add_theme_stylebox_override(state, normal)
	for state: StringName in [&"hover", &"hover_pressed"]:
		add_theme_stylebox_override(state, hover)
	var showing_face: bool = look_name in [LOOK_FACE, LOOK_MISS, LOOK_MATCH]
	text = glyph() if showing_face else ""
	var ink: Color = Deck.ink(symbol)
	for colour: StringName in [
		&"font_color",
		&"font_hover_color",
		&"font_pressed_color",
		&"font_hover_pressed_color",
		&"font_focus_color",
		&"font_disabled_color",
	]:
		add_theme_color_override(colour, ink)
	if _frame != null:
		_frame.visible = look_name == LOOK_BACK


func _apply_input_state() -> void:
	var usable: bool = not matched and not locked
	disabled = not usable
	focus_mode = Control.FOCUS_ALL if usable else Control.FOCUS_NONE
	mouse_filter = Control.MOUSE_FILTER_STOP if usable else Control.MOUSE_FILTER_IGNORE


func _reduced_motion() -> bool:
	var settings: Node = get_tree().root.get_node_or_null(^"Settings") if is_inside_tree() else null
	return settings != null and bool(settings.call(&"reduced_motion"))


func _on_pressed() -> void:
	if (
		board != null
		and board.has_method(&"may_flip_here")
		and not bool(board.call(&"may_flip_here"))
	):
		return
	Game.request_action(self, &"flip")


func _on_resized() -> void:
	pivot_offset = size * 0.5


func _on_focus_entered() -> void:
	if board != null and board.has_method(&"remember_focus"):
		board.call(&"remember_focus", self)
