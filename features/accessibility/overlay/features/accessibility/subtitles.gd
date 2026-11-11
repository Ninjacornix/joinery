extends CanvasLayer
## Subtitles and captions: one line at a time at the bottom of the screen, on a dark
## panel so it reads on any scene. The game calls say() where a voice line or an
## important sound plays; the player turns subtitles off or changes their size on the
## Accessibility page (access/subtitles, access/subtitle_size).
##
##   Subtitles.say("Over here!", 2.5, "Guard")
##   Subtitles.caption("[door creaks]")

const SECTION := "access"
const BASE_SIZE := 30

var panel: PanelContainer
var label: Label
var _queue: Array[Dictionary] = []
var _left: float = 0.0


func _ready() -> void:
	layer = 90
	process_mode = Node.PROCESS_MODE_ALWAYS
	panel = PanelContainer.new()
	panel.name = "Panel"
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var box := StyleBoxFlat.new()
	box.bg_color = Color(0.0, 0.0, 0.0, 0.82)
	box.set_corner_radius_all(UiStyle.px(8))
	box.set_content_margin_all(UiStyle.px(14))
	panel.add_theme_stylebox_override(&"panel", box)
	panel.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	panel.offset_bottom = -UiStyle.px(96)
	add_child(panel)
	label = Label.new()
	label.name = "Line"
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	label.add_theme_color_override(&"font_color", Color.WHITE)
	panel.add_child(label)
	panel.hide()
	Settings.changed.connect(_on_changed)
	_resize()


## Shows [param text] (an English msgid) for [param seconds], after any line before it.
func say(text: String, seconds: float = 3.0, speaker: String = "") -> void:
	if not enabled():
		return
	_queue.append({"text": text, "seconds": maxf(seconds, 0.5), "speaker": speaker})
	if not panel.visible:
		_next()


## A caption for an important sound ("[alarm rings]").
func caption(text: String, seconds: float = 2.0) -> void:
	say(text, seconds)


func enabled() -> bool:
	return bool(Settings.get_value(SECTION, "subtitles", true))


func showing() -> String:
	return label.text if panel.visible else ""


func _process(delta: float) -> void:
	if not panel.visible:
		return
	_left -= delta
	if _left <= 0.0:
		_next()


func _next() -> void:
	if _queue.is_empty():
		panel.hide()
		return
	var line: Dictionary = _queue.pop_front()
	var words := tr(str(line["text"]))
	var speaker := str(line["speaker"])
	label.text = words if speaker.is_empty() else "%s: %s" % [tr(speaker), words]
	_left = float(line["seconds"])
	panel.show()


func _resize() -> void:
	var size := clampf(float(Settings.get_value(SECTION, "subtitle_size", 1.0)), 0.75, 2.0)
	label.add_theme_font_size_override(&"font_size", UiStyle.px(BASE_SIZE * size))


func _on_changed(section: String, key: String) -> void:
	if section != SECTION:
		return
	if key == "subtitle_size":
		_resize()
	elif key == "subtitles" and not enabled():
		_queue.clear()
		panel.hide()
