@tool
extends PanelContainer
## One way in the variant strip (variant_strip.gd): what it looks like (its render or texture,
## a play button for a sound, the changed lines of text), its number and one-line direction
## ("2 · slim and clean"), one quiet line of facts, and Use this. While it works the picture's
## place shows the agent's step; a way waiting for a free agent says so. Clicking the picture
## opens the full before/after in the Review tab.

## The user keeps this way.
signal use_pressed
## The user wants the full before/after of this way.
signal open_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const ROW := preload("sidebar_review_row.gd")
const GLANCE := preload("review_glance.gd")
## Width : height of the picture.
const ASPECT := 0.75
## The picture's tallest, in large control heights: a glance, not a full review.
const PICTURE_MAX := 4.0
## Lines of text a text change shows on its tile.
const TEXT_LINES := 4
## State -> what the picture's place says when there is nothing to show.
const WORDS := {
	"queued": "Waiting for a free agent",
	"failed": "Didn't finish",
	"stopped": "Stopped",
	"done": "Nothing changed",
	"discarded": "Discarded",
}

var task_id := ""
var transaction_id := ""
var variant := 0
var direction := ""
## queued, working, in_review, accepted, discarded, done, failed or stopped.
var state := "queued"
## The way the user is keeping (ringed in the accent).
var chosen := false
## The picture's tallest, in large control heights (smaller above a full review).
var picture_max := PICTURE_MAX
var use_button: Button
var picture: TextureRect
var _frame: Button
var _dots: Control
var _words: Label
var _code: Label
var _play: Button
var _note: Label
var _facts: Label
var _player: AudioStreamPlayer
var _sound := ""


func _init(identifier := "", number := 0, note := "") -> void:
	task_id = identifier
	variant = number
	direction = note
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Ways in one row are one height, their Use this buttons on one line.
	size_flags_vertical = Control.SIZE_FILL
	accessibility_name = _label()
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	_frame = Button.new()
	_frame.flat = true
	_frame.clip_contents = true
	_frame.focus_mode = Control.FOCUS_ALL
	_frame.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	_frame.tooltip_text = "Open the full before and after"
	_frame.accessibility_name = "Open way %d in the Review tab" % number
	_frame.pressed.connect(func() -> void: open_pressed.emit())
	column.add_child(_frame)
	picture = TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_frame.add_child(picture)
	var middle := VBoxContainer.new()
	middle.alignment = BoxContainer.ALIGNMENT_CENTER
	middle.mouse_filter = Control.MOUSE_FILTER_IGNORE
	middle.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_frame.add_child(middle)
	_dots = INDICATOR.new()
	_dots.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_dots.hide()
	middle.add_child(_dots)
	_words = UI.label("", "JoineryCaption", true)
	_words.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_words.mouse_filter = Control.MOUSE_FILTER_IGNORE
	middle.add_child(_words)
	_code = UI.label("", "JoineryMono")
	_code.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_code.clip_text = true
	_code.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_code.hide()
	_frame.add_child(_code)
	_play = UI.icon_button("play", "Play this way")
	_play.accessibility_name = "Play way %d" % number
	_play.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	_play.pressed.connect(toggle_sound)
	_play.hide()
	_frame.add_child(_play)
	_note = UI.label(_label(), "", true)
	_note.max_lines_visible = 2
	_note.tooltip_text = _label()
	column.add_child(_note)
	_facts = UI.label("", "JoineryCaption")
	_facts.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	# Fills its tile and ends in an ellipsis rather than widening the tile.
	_facts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_facts.mouse_filter = Control.MOUSE_FILTER_PASS
	column.add_child(_facts)
	var push := Control.new()
	push.size_flags_vertical = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	column.add_child(push)
	use_button = UI.secondary("Use this")
	use_button.accessibility_name = "Keep way %d: %s" % [number, note]
	use_button.tooltip_text = "Keep this way. The other ways are discarded."
	UI.bind_height(use_button, "sm", false)
	UI_MOTION.press_feedback(use_button)
	use_button.pressed.connect(func() -> void: use_pressed.emit())
	column.add_child(use_button)
	STYLE.on_theme_overrides(self, _restyle)
	mouse_entered.connect(_restyle)
	mouse_exited.connect(_restyle)
	resized.connect(_fit)
	set_state("queued")


func _ready() -> void:
	_restyle()


## The way's state from its task event ({state, step, message, transaction_id}).
func apply(event: Dictionary) -> void:
	transaction_id = str(event.get("transaction_id", transaction_id))
	var next := str(event.get("state", state))
	if next != state:
		set_state(next)
	if state == "working" and not str(event.get("step", "")).is_empty():
		_words.text = str(event["step"])
	if state in ["failed", "done"] and not str(event.get("message", "")).is_empty():
		_words.text = str(event["message"]).left(120)


func set_state(value: String) -> void:
	var before := state
	state = value
	var waiting := state in ["queued", "working"]
	_dots.visible = state == "working"
	_dots.set("running", state == "working")
	if state == "working":
		_words.text = "Starting…"
	elif WORDS.has(state):
		_words.text = str(WORDS[state])
	elif state == "in_review" and not _has_media():
		_words.text = "Ready"
	_words.visible = not _has_media() or waiting or WORDS.has(state)
	use_button.visible = state == "in_review"
	_frame.disabled = not state in ["in_review", "accepted"]
	modulate.a = 0.55 if state in ["failed", "stopped", "done", "discarded"] else 1.0
	if state == "in_review" and before != state:
		UI_MOTION.pulse(self)
	_restyle()


## Show the way's change: its file_preview ({media, facts, diff, changes, checks, notes}).
func show_change(preview: Dictionary) -> void:
	var media: Array = preview.get("media", [])
	var texture := ROW.first_picture(media)
	picture.texture = texture
	_sound = ""
	for item: Variant in media:
		if item is Dictionary and str(item.get("media", "")) == "audio" and item.has("after"):
			_sound = str(item["after"])
			break
	_play.visible = texture == null and not _sound.is_empty()
	var diff := str(preview.get("diff", ""))
	_code.text = changed_lines(diff) if texture == null and _sound.is_empty() else ""
	_code.visible = not _code.text.is_empty()
	_facts.text = GLANCE.facts_line(preview)
	_facts.tooltip_text = _facts.text
	set_state(state)


## The first added lines of a unified [param diff], for a text change's tile.
static func changed_lines(diff: String) -> String:
	var lines: PackedStringArray = []
	for line: String in diff.split("\n"):
		if line.begins_with("+") and not line.begins_with("+++"):
			lines.append(line.substr(1).strip_edges(false, true))
		if lines.size() >= TEXT_LINES:
			break
	return "\n".join(lines)


## Play or stop the way's sound; another way's sound stops first (variant_strip.gd).
func toggle_sound() -> void:
	if _player != null and _player.playing:
		stop_sound()
		return
	if _sound.is_empty():
		return
	var stream: AudioStream = null
	match _sound.get_extension().to_lower():
		"wav":
			stream = AudioStreamWAV.load_from_file(_sound)
		"ogg":
			stream = AudioStreamOggVorbis.load_from_file(_sound)
		"mp3":
			stream = AudioStreamMP3.load_from_file(_sound)
	if stream == null:
		return
	if _player == null:
		_player = AudioStreamPlayer.new()
		_player.finished.connect(stop_sound)
		add_child(_player)
	_player.stream = stream
	_player.play()
	UI.bind_icon(_play, "stop")
	_play.tooltip_text = "Stop"


func stop_sound() -> void:
	if _player != null:
		_player.stop()
	UI.bind_icon(_play, "play")
	_play.tooltip_text = "Play this way"


## True while the way's sound plays.
func playing() -> bool:
	return _player != null and _player.playing


## What the tile says under its picture ("2 · slim and clean").
func note_text() -> String:
	return _note.text


func facts_text() -> String:
	return _facts.text


## What the picture's place says ("Waiting for a free agent", the step), "" when hidden.
func words_text() -> String:
	return _words.text if _words.visible else ""


func _has_media() -> bool:
	return picture.texture != null or not _sound.is_empty() or not _code.text.is_empty()


func _label() -> String:
	return "%d · %s" % [variant, direction] if variant > 0 else direction


func _fit() -> void:
	var tallest := float(UI_THEME.control_h(self, "lg")) * picture_max if is_inside_tree() else INF
	var height := ceilf(minf(size.x * ASPECT, tallest))
	if absf(_frame.custom_minimum_size.y - height) >= 1.0:
		_frame.custom_minimum_size.y = height


func _restyle() -> void:
	if not is_inside_tree():
		# Outside the tree the theme is Godot's default; the card styles itself on entering.
		return
	var source := get_theme_stylebox("panel", "JoineryCard")
	if source == null:
		return
	var box := source.duplicate() as StyleBoxFlat
	if box == null:
		return
	var pad := float(UI_THEME.space(self, 1))
	box.set_content_margin_all(pad)
	var hovered := (
		get_global_rect().has_point(get_global_mouse_position()) if is_inside_tree() else false
	)
	if chosen:
		box.border_color = UI_THEME.color(self, "accent")
		box.set_border_width_all(maxi(2, int(UI_THEME.unit(self) / 2.0)))
	elif hovered and state == "in_review":
		box.border_color = UI_THEME.color(self, "border_strong")
	add_theme_stylebox_override("panel", box)
	# The picture's corners sit inside the card's: inner radius = outer - padding.
	var frame := StyleBoxFlat.new()
	frame.bg_color = UI_THEME.color(self, "surface_sunken")
	frame.set_corner_radius_all(int(UI_THEME.nested(float(box.corner_radius_top_left), pad)))
	for name: String in ["normal", "hover", "pressed", "disabled", "focus", "hover_pressed"]:
		_frame.add_theme_stylebox_override(name, frame)
	_frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
