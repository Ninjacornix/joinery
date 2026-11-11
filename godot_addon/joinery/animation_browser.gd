@tool
extends VBoxContainer
## The animations of the model on show in the Blender tab: one row per action, with its length,
## whether it loops and its frame rate, and a transport under the preview that plays the picked
## action on the model itself (its AnimationPlayer, in the preview's own world).
##
## The list merges what the game copy carries (the GLB's AnimationPlayer, as Godot imported it)
## with what the source document has (`joinery parts`: armature, loop flag, frame rate), so an
## action that is not exported yet is listed too. Clicking a row, Enter on it, or "Use in
## message" makes the action the message target (part_selected); arrows only preview.
##
## Keyboard, with focus inside: Space plays or pauses, Left and Right go to the previous or
## next action.

## The user picked an action: a message target from target_chip.for_part (kind Animation).
signal part_selected(target: Dictionary)

const CHIP := preload("target_chip.gd")
const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const SPEEDS := [0.25, 0.5, 1.0, 1.5, 2.0]
const MODES := ["loop", "ping_pong", "once"]
const MODE_NAMES := {"loop": "Loop", "ping_pong": "Ping-pong", "once": "Once"}
## Godot bakes imported glTF animation at this rate; used when the source does not say.
const IMPORT_FPS := 30.0
## Rows the list shows before it scrolls, as multiples of the editor font size per row.
const ROW_EM := 2.4
const MAX_ROWS := 4

var tree: Tree
var play_button: Button
var scrub: HSlider
var mode_button: MenuButton
var speed_button: MenuButton
var use_button: Button
## Seconds into the shown action.
var playhead := 0.0
var playing := false
var speed := 1.0
var mode := "loop"
var _rows: Array[Dictionary] = []
var _index := -1
var _direction := 1.0
var _viewer: Control
var _resource := ""
var _report: Dictionary = {}
var _name_label: Label
var _frame_label: Label
var _syncing := false


func _init() -> void:
	theme_type_variation = "JoineryTight"
	accessibility_name = "Animations"
	hide()
	var transport := HBoxContainer.new()
	transport.theme_type_variation = "JoineryRow"
	add_child(transport)
	var previous := UI.icon_button("back", "Previous animation (Left)")
	previous.pressed.connect(step.bind(-1))
	transport.add_child(previous)
	play_button = UI.primary("")
	play_button.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	play_button.tooltip_text = "Play or pause (Space)"
	play_button.accessibility_name = "Play"
	play_button.pressed.connect(toggle_play)
	UI.bind_icon(play_button, "play")
	play_button.theme_changed.connect(
		func() -> void:
			var side := float(UI_THEME.control_h(play_button, "md"))
			play_button.custom_minimum_size = Vector2(side, side)
	)
	transport.add_child(play_button)
	var next := UI.icon_button("forward", "Next animation (Right)")
	next.pressed.connect(step.bind(1))
	transport.add_child(next)
	_name_label = UI.label("")
	_name_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_name_label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	transport.add_child(_name_label)
	_frame_label = UI.label("", "JoineryNumeric")
	_frame_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_frame_label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	transport.add_child(_frame_label)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	transport.add_child(spacer)
	mode_button = _menu("Playback", MODES.map(func(id: String) -> String: return MODE_NAMES[id]))
	mode_button.get_popup().id_pressed.connect(func(id: int) -> void: set_mode(MODES[id]))
	transport.add_child(mode_button)
	speed_button = _menu("Speed", SPEEDS.map(_rate_text))
	speed_button.get_popup().id_pressed.connect(func(id: int) -> void: set_speed(SPEEDS[id]))
	transport.add_child(speed_button)
	use_button = UI.secondary("Use in message")
	use_button.tooltip_text = "Ask Journeyman about this animation"
	use_button.pressed.connect(use_in_message)
	transport.add_child(use_button)
	scrub = HSlider.new()
	scrub.step = 0.0
	scrub.focus_mode = Control.FOCUS_ALL
	scrub.accessibility_name = "Position in the animation"
	scrub.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scrub.value_changed.connect(_on_scrub)
	scrub.theme_changed.connect(
		func() -> void: scrub.custom_minimum_size.y = UI_THEME.control_h(scrub, "sm")
	)
	add_child(scrub)
	tree = Tree.new()
	tree.hide_root = true
	tree.columns = 2
	tree.select_mode = Tree.SELECT_ROW
	tree.accessibility_name = "Animations of this model"
	tree.scroll_horizontal_enabled = false
	tree.set_column_expand_ratio(0, 3)
	tree.set_column_clip_content(1, false)
	tree.item_selected.connect(_on_item_selected)
	tree.item_mouse_selected.connect(func(_at: Vector2, _button: int) -> void: use_in_message())
	tree.item_activated.connect(use_in_message)
	add_child(tree)
	set_mode("loop")
	set_speed(1.0)
	set_process(false)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and tree != null:
		_style_tree()
		_fit_tree()


## Show the animations of [param viewer]'s model (a model_preview). [param resource] is the
## model's res:// path; empty reads it from the instantiated scene.
func attach(viewer: Control, resource := "") -> void:
	_stop_player()
	if is_instance_valid(_viewer) and _viewer.tree_exiting.is_connected(_on_viewer_gone):
		_viewer.tree_exiting.disconnect(_on_viewer_gone)
	_viewer = viewer
	if is_instance_valid(viewer):
		viewer.tree_exiting.connect(_on_viewer_gone, CONNECT_ONE_SHOT)
	var model: Node = viewer.get("model") if is_instance_valid(viewer) else null
	_resource = resource
	if _resource.is_empty() and model != null:
		_resource = model.scene_file_path
	_rebuild()


## The preview went away (another asset is shown): nothing to list until the next model.
func _on_viewer_gone() -> void:
	_viewer = null
	_rebuild()


## Take the source's animation parts from a `joinery parts --json` report.
func set_report(report: Dictionary) -> void:
	_report = report
	_rebuild()


## The listed animations: {name, length, loop, fps, frames, part, player}.
func rows() -> Array[Dictionary]:
	return _rows


## The shown animation's index, or -1.
func current_index() -> int:
	return _index


## The shown animation's name, or "".
func current_name() -> String:
	return str(_rows[_index]["name"]) if _index >= 0 else ""


## The shown frame, from 1.
func current_frame() -> int:
	return _frame_at(playhead) if _index >= 0 else 0


func frame_count() -> int:
	if _index < 0:
		return 0
	var row: Dictionary = _rows[_index]
	return int(row["frames"]) if int(row["frames"]) > 0 else _frame_at(float(row["length"]))


## Show animation [param index] from its start (wraps around the list).
func show_animation(index: int) -> void:
	if _rows.is_empty():
		return
	_index = posmod(index, _rows.size())
	_direction = 1.0
	playhead = 0.0
	var row: Dictionary = _rows[_index]
	var player := row["player"] as AnimationPlayer
	if is_instance_valid(player):
		player.assigned_animation = str(row["key"])
	var item := tree.get_root().get_child(_index)
	if tree.get_selected() != item:
		tree.set_selected(item, 0)
	tree.scroll_to_item(item)
	_syncing = true
	scrub.max_value = maxf(float(row["length"]), 0.001)
	scrub.editable = is_instance_valid(player)
	_syncing = false
	play_button.disabled = not is_instance_valid(player)
	_apply()


## Go [param by] actions forward (1) or back (-1), keeping play/pause.
func step(by: int) -> void:
	show_animation(_index + by)


func toggle_play() -> void:
	set_playing(not playing)


func set_playing(on: bool) -> void:
	var player := _player()
	on = on and player != null
	if on and mode == "once" and playhead >= _length():
		playhead = 0.0
	playing = on
	set_process(on)
	UI.bind_icon(play_button, "pause" if on else "play")
	play_button.accessibility_name = "Pause" if on else "Play"
	_apply()


## Go to [param seconds] into the shown animation (pauses playback).
func seek(seconds: float) -> void:
	if playing:
		set_playing(false)
	playhead = clampf(seconds, 0.0, _length())
	_apply()


## Go to [param frame] (from 1) of the shown animation.
func seek_frame(frame: int) -> void:
	seek(float(frame - 1) / _fps())


## "loop", "ping_pong" or "once".
func set_mode(value: String) -> void:
	mode = value if value in MODES else "loop"
	mode_button.text = MODE_NAMES[mode]
	mode_button.tooltip_text = "Playback: " + MODE_NAMES[mode]
	_direction = 1.0


func set_speed(rate: float) -> void:
	speed = clampf(rate, SPEEDS[0], SPEEDS[-1])
	speed_button.text = _rate_text(speed)
	speed_button.tooltip_text = "Speed: " + _rate_text(speed)


## The message target for the shown animation (kind Animation, model and action).
func current_target() -> Dictionary:
	if _index < 0:
		return {}
	var row: Dictionary = _rows[_index]
	var part: Dictionary = (row["part"] as Dictionary).duplicate()
	if part.is_empty():
		part = {
			"part_kind": "animation",
			"resource": _resource,
			"action": str(row["name"]),
			"seconds": "%.3f" % float(row["length"]),
			"loop": "true" if bool(row["loop"]) else "",
			"fps": _number(float(row["fps"])) if float(row["fps"]) > 0.0 else "",
		}
		part["id"] = "%s#action=%s" % [_resource, str(row["name"]).uri_encode()]
	return CHIP.for_part(part)


func use_in_message() -> void:
	var target := current_target()
	if not target.is_empty():
		part_selected.emit(target)
		UI_MOTION.pulse(use_button)


func _process(delta: float) -> void:
	var player := _player()
	if not playing or player == null:
		set_process(false)
		return
	var length := _length()
	playhead += delta * speed * _direction
	match mode:
		"loop":
			playhead = fposmod(playhead, length) if length > 0.0 else 0.0
		"ping_pong":
			if playhead > length:
				playhead = length - (playhead - length)
				_direction = -1.0
			elif playhead < 0.0:
				playhead = -playhead
				_direction = 1.0
			playhead = clampf(playhead, 0.0, length)
		_:
			if playhead >= length:
				playhead = length
				set_playing(false)
				return
	_apply()


func _input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or key.echo or not is_visible_in_tree():
		return
	if key.ctrl_pressed or key.meta_pressed or key.alt_pressed or key.shift_pressed:
		return
	var focus := get_viewport().gui_get_focus_owner()
	if focus == null or (focus != self and not is_ancestor_of(focus)):
		return
	match key.keycode:
		KEY_LEFT:
			step(-1)
		KEY_RIGHT:
			step(1)
		KEY_SPACE:
			if focus is BaseButton:
				return
			toggle_play()
		_:
			return
	get_viewport().set_input_as_handled()


func _rebuild() -> void:
	var keep := current_name()
	playing = false
	set_process(false)
	_rows.clear()
	var by_name := {}
	var model: Node = _viewer.get("model") if is_instance_valid(_viewer) else null
	if model == null:
		# No model on show: a report alone does not make a list.
		_fill_tree()
		_index = -1
		hide()
		return
	if model != null:
		var players: Array[Node] = model.find_children("*", "AnimationPlayer", true, false)
		if model is AnimationPlayer:
			players.push_front(model)
		for node: Node in players:
			var player := node as AnimationPlayer
			for key: StringName in player.get_animation_list():
				var name := str(key).get_slice("/", str(key).get_slice_count("/") - 1)
				if name == "RESET" or by_name.has(name):
					continue
				var animation := player.get_animation(key)
				by_name[name] = _rows.size()
				(
					_rows
					. append(
						{
							"name": name,
							"key": str(key),
							"length": animation.length,
							"loop": animation.loop_mode != Animation.LOOP_NONE,
							"fps": 0.0,
							"frames": 0,
							"part": {},
							"player": player,
						}
					)
				)
	for part: Dictionary in _report_parts():
		var name := str(part.get("action", ""))
		var row: Dictionary = {}
		if by_name.has(name):
			row = _rows[by_name[name]]
		else:
			row = {"name": name, "key": name, "length": 0.0, "loop": false, "player": null}
			by_name[name] = _rows.size()
			_rows.append(row)
		var frames := str(part.get("frames", "")).split(",")
		var fps := str(part.get("fps", "")).to_float()
		row["fps"] = fps
		row["frames"] = (frames[1].to_int() - frames[0].to_int() + 1 if frames.size() == 2 else 0)
		row["loop"] = str(part.get("loop", "")) == "true"
		var seconds := str(part.get("seconds", "")).to_float()
		if row["player"] == null and seconds > 0.0:
			row["length"] = seconds
		row["part"] = part
	_fill_tree()
	visible = not _rows.is_empty()
	if _rows.is_empty():
		_index = -1
		return
	var found := 0
	for index: int in _rows.size():
		if _rows[index]["name"] == keep:
			found = index
	show_animation(found)


## The report's animation parts, when the report is about the model on show.
func _report_parts() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	var resource := str(_report.get("resource", ""))
	if resource != _resource and "res://" + resource != _resource:
		return found
	for part: Variant in _report.get("parts", []):
		if part is Dictionary and str(part.get("part_kind", "")) == "animation":
			found.append(part)
	return found


func _fill_tree() -> void:
	tree.clear()
	var root := tree.create_item()
	var numeric := get_theme_font("font", "JoineryNumeric") if is_inside_tree() else null
	for row: Dictionary in _rows:
		var item := tree.create_item(root)
		item.set_text(0, str(row["name"]))
		item.set_text(1, _row_facts(row))
		item.set_text_alignment(1, HORIZONTAL_ALIGNMENT_RIGHT)
		item.set_custom_color(1, UI_THEME.color(self, "text_muted"))
		if numeric != null:
			item.set_custom_font(1, numeric)
		item.set_tooltip_text(
			0, CHIP.part_tooltip(row["part"]) if not row["part"].is_empty() else ""
		)
	_fit_tree()


## "0.67 s · Loop · 24 fps", and "Not in the game yet" for an action the GLB lacks.
static func _row_facts(row: Dictionary) -> String:
	var facts := PackedStringArray()
	if float(row["length"]) > 0.0:
		facts.append("%.2f s" % float(row["length"]))
	if bool(row["loop"]):
		facts.append("Loop")
	if float(row.get("fps", 0.0)) > 0.0:
		facts.append("%s fps" % _number(float(row["fps"])))
	if row["player"] == null:
		facts.append("Not in the game yet")
	return " · ".join(facts)


func _fit_tree() -> void:
	var em := float(UI_THEME.font_size(self, "body"))
	var shown := clampi(_rows.size(), 1, MAX_ROWS)
	tree.custom_minimum_size.y = roundf(em * ROW_EM * shown + UI_THEME.space(self, 2) * 2)


func _on_item_selected() -> void:
	var item := tree.get_selected()
	if item != null and item.get_index() != _index:
		var was := playing
		show_animation(item.get_index())
		if was:
			set_playing(true)


func _on_scrub(value: float) -> void:
	if not _syncing:
		seek(value)


## Pose the model at [member playhead] and update the label and scrub bar.
func _apply() -> void:
	var player := _player()
	if player != null:
		player.seek(playhead, true)
	if _index < 0:
		return
	var row: Dictionary = _rows[_index]
	_name_label.text = str(row["name"])
	_frame_label.text = (
		"frame %d / %d" % [current_frame(), frame_count()]
		if player != null
		else "Not in the game yet"
	)
	_syncing = true
	scrub.set_value_no_signal(playhead)
	_syncing = false


func _player() -> AnimationPlayer:
	if _index < 0 or not is_instance_valid(_viewer):
		return null
	var player := _rows[_index]["player"] as AnimationPlayer
	return player if is_instance_valid(player) else null


func _stop_player() -> void:
	playing = false
	set_process(false)
	var player := _player()
	if player != null:
		player.seek(0.0, true)


func _length() -> float:
	return float(_rows[_index]["length"]) if _index >= 0 else 0.0


func _fps() -> float:
	if _index < 0:
		return IMPORT_FPS
	var fps := float(_rows[_index]["fps"])
	return fps if fps > 0.0 else IMPORT_FPS


func _frame_at(seconds: float) -> int:
	return roundi(seconds * _fps()) + 1


func _menu(name: String, items: Array) -> MenuButton:
	var menu := MenuButton.new()
	menu.theme_type_variation = "JoineryQuietButton"
	menu.flat = false
	menu.focus_mode = Control.FOCUS_ALL
	menu.accessibility_name = name
	UI.bind_height(menu, "md")
	for item: Variant in items:
		menu.get_popup().add_item(str(item))
	return menu


## "24" or "23.98".
static func _number(value: float) -> String:
	return "%d" % roundi(value) if is_equal_approx(value, roundf(value)) else "%.2f" % value


static func _rate_text(rate: float) -> String:
	return ("%.2f" % rate).trim_suffix("0").trim_suffix("0").trim_suffix(".") + "×"


## The list in Joinery's colours, like the parts list: a sunken panel and the accent tint for
## the picked row.
func _style_tree() -> void:
	var colors := UI_THEME.palette(self)
	var pad := UI_THEME.space(self, 2)
	var outer := UI_THEME.radius(self, "md")
	var panel := StyleBoxFlat.new()
	panel.bg_color = colors["surface_sunken"]
	panel.border_color = colors["border_subtle"]
	panel.set_border_width_all(1)
	panel.set_corner_radius_all(outer)
	panel.set_content_margin_all(pad)
	tree.add_theme_stylebox_override("panel", panel)
	tree.add_theme_stylebox_override("focus", StyleBoxEmpty.new())
	var row := StyleBoxFlat.new()
	row.set_corner_radius_all(roundi(UI_THEME.nested(outer, pad)))
	for state: String in ["selected", "selected_focus"]:
		var box := row.duplicate() as StyleBoxFlat
		box.bg_color = colors["accent_tint"]
		tree.add_theme_stylebox_override(state, box)
	var hover := row.duplicate() as StyleBoxFlat
	hover.bg_color = colors["surface_raised"]
	tree.add_theme_stylebox_override("hovered", hover)
	tree.add_theme_stylebox_override("hovered_dimmed", hover)
	for key: String in ["font_color", "font_selected_color", "font_hovered_color"]:
		tree.add_theme_color_override(key, colors["text"])
	tree.add_theme_constant_override("draw_guides", 0)
	tree.add_theme_constant_override("v_separation", UI_THEME.space(self, 1))
	if tree.get_root() != null:
		for item: TreeItem in tree.get_root().get_children():
			item.set_custom_color(1, colors["text_muted"])
