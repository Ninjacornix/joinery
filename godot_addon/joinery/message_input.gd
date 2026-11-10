@tool
extends TextEdit
## Multiline chat input: Enter sends, Shift+Enter inserts a line break. It sits inside the
## composer box, so its own styleboxes are empty apart from a 2u left inset that puts the caret
## just before the placeholder. It grows with its text from two lines up to MAX_LINES, then
## scrolls, and shrinks back when the text is cleared or replaced (sending clears it).

signal text_submitted(value: String)

const UI_THEME := preload("ui_theme.gd")
const MIN_LINES := 2
const MAX_LINES := 5

var caret_column: int:
	get:
		return get_caret_column()
	set(value):
		set_caret_column(value)

var _restyling := false


func _ready() -> void:
	wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	scroll_fit_content_height = false
	text_changed.connect(_fit)
	# text_changed fires only for typing; clear() and setting text fire text_set.
	text_set.connect(_fit)
	_restyle()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and is_inside_tree():
		_restyle()
	elif what == NOTIFICATION_RESIZED:
		_fit.call_deferred()


func _gui_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode in [KEY_ENTER, KEY_KP_ENTER]:
			if event.shift_pressed:
				insert_text_at_caret("\n")
			else:
				text_submitted.emit(text)
			accept_event()


func _restyle() -> void:
	if _restyling:
		return
	_restyling = true
	var inset := 2.0 * UI_THEME.unit(self)
	for state: String in ["normal", "focus", "read_only"]:
		var box := StyleBoxEmpty.new()
		box.content_margin_left = inset
		box.content_margin_right = inset
		add_theme_stylebox_override(state, box)
	_restyling = false
	_fit()


func _fit() -> void:
	if not is_inside_tree():
		return
	var lines := clampi(get_visible_line_count_in_range(0, maxi(0, get_line_count() - 1)), 1, 999)
	var shown := clampi(lines, MIN_LINES, MAX_LINES)
	var height := get_line_height() * shown
	if not is_equal_approx(custom_minimum_size.y, height):
		custom_minimum_size.y = height
