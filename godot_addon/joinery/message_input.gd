@tool
extends TextEdit
## Multiline chat input: Enter sends, Shift+Enter inserts a line break.

signal text_submitted(value: String)

var caret_column: int:
	get:
		return get_caret_column()
	set(value):
		set_caret_column(value)


func _ready() -> void:
	wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	add_theme_stylebox_override("focus", StyleBoxEmpty.new())
	add_theme_stylebox_override("read_only", StyleBoxEmpty.new())


func _gui_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode in [KEY_ENTER, KEY_KP_ENTER]:
			if event.shift_pressed:
				insert_text_at_caret("\n")
			else:
				text_submitted.emit(text)
			accept_event()
