@tool
extends VBoxContainer
## The Mixer tab's music: pick a track (res://music/<name>.tres), mix its layers (a fader,
## Mute and Solo each) and audition them at an intensity (calm … victory) with Play.
##
## Auditioning plays every layer's stem of the state's section on its own AudioStreamPlayer on
## the track's bus, all started in the same frame so they stay in sync. Each player's level is
## the track's gain for the layer in the state (music_track.gd: state level × layer fader, with
## Mute and Solo), so what you hear is what the game plays. Layer changes are unsaved until the
## tab's Save mix; [method edits] lists them.

signal changed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const TRACK := preload("music_track.gd")
const BUSES := preload("mixer_buses.gd")
const EMPTY := "No music yet. Ask Journeyman to compose a track; it shows here to mix."

## The track on show ("" without one) and its description.
var path := ""
var track: Dictionary = {}
## The intensity being auditioned.
var intensity := "explore"
## Layer settings now and as saved: {name: {volume_db, muted, solo}}.
var mix: Dictionary = {}
var saved: Dictionary = {}
## Layer name -> its AudioStreamPlayer.
var players: Dictionary = {}
var picker: OptionButton
var play_button: Button
var intensity_slider: HSlider
var _tracks := PackedStringArray()
var _section := ""
var _intensity_label: Label
var _summary: Label
var _controls: HBoxContainer
var _rows: VBoxContainer
var _empty: PanelContainer
var _player_box: Node
var _playing := false


func _init() -> void:
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	add_child(head)
	var title := UI.label("Music", "JoinerySection")
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title)
	picker = UI.picker([], "md")
	picker.name = "Tracks"
	picker.accessibility_name = "Music track"
	picker.item_selected.connect(func(index: int) -> void: select(_tracks[index]))
	head.add_child(picker)
	_summary = UI.label("", "JoineryNumeric")
	_summary.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(_summary)
	_controls = HBoxContainer.new()
	_controls.theme_type_variation = "JoineryRow"
	add_child(_controls)
	play_button = UI.secondary("Play")
	play_button.name = "Play"
	UI.bind_icon(play_button, "play")
	play_button.pressed.connect(toggle_play)
	UI_MOTION.press_feedback(play_button)
	_controls.add_child(play_button)
	var intensity_title := UI.label("Intensity", "JoineryCaption")
	intensity_title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_controls.add_child(intensity_title)
	intensity_slider = HSlider.new()
	intensity_slider.name = "Intensity"
	intensity_slider.min_value = 0
	intensity_slider.max_value = TRACK.STATES.size() - 1
	intensity_slider.step = 1
	intensity_slider.tick_count = TRACK.STATES.size()
	intensity_slider.ticks_on_borders = true
	intensity_slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(intensity_slider, "sm")
	intensity_slider.accessibility_name = "Intensity"
	intensity_slider.tooltip_text = "Hear the music at each intensity, as the game plays it"
	intensity_slider.value_changed.connect(
		func(value: float) -> void: set_intensity(TRACK.STATES[int(value)])
	)
	_controls.add_child(intensity_slider)
	_intensity_label = UI.label("", "JoineryNumeric")
	_intensity_label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_intensity_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_controls.add_child(_intensity_label)
	_rows = VBoxContainer.new()
	_rows.name = "Layers"
	_rows.theme_type_variation = "JoineryFlush"
	add_child(_rows)
	_empty = UI.notice(EMPTY, "", "JoineryCard")
	_empty.name = "Empty"
	_empty.find_child("Action", true, false).queue_free()
	add_child(_empty)
	_player_box = Node.new()
	_player_box.name = "Players"
	add_child(_player_box)
	_render()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		intensity_slider.custom_minimum_size.x = UI_THEME.font_size(self, "body") * 10
		custom_minimum_size.x = UI_THEME.font_size(self, "body") * 34
		_intensity_label.custom_minimum_size.x = UI_THEME.font_size(self, "body") * 5
	elif what == NOTIFICATION_EXIT_TREE:
		stop()


# --- Public ------------------------------------------------------------------------------------


## Look for tracks again; keeps the one on show when it is still there.
func refresh() -> void:
	_tracks = TRACK.find_tracks()
	picker.clear()
	for found: String in _tracks:
		picker.add_item(found.get_file().get_basename().capitalize())
	if _tracks.is_empty():
		select("")
	elif path in _tracks:
		picker.select(_tracks.find(path))
		_render()
	else:
		select(_tracks[0])


## Show the track at [param track_path] ("" for none): its layers as saved.
func select(track_path: String) -> void:
	stop()
	path = track_path
	track = TRACK.read(path) if not path.is_empty() else {}
	if track.is_empty():
		path = ""
	mix = TRACK.mix_of(track)
	saved = mix.duplicate(true)
	var start := str(track.get("default_state", "explore"))
	intensity = start if start in TRACK.STATES else "explore"
	if not path.is_empty() and path in _tracks:
		picker.select(_tracks.find(path))
	_build_rows()
	_load_players()
	_render()


## Hear [param state] (one of music_track.gd's STATES): sets every layer player's level and,
## when the state plays another section, switches the stems.
func set_intensity(state: String) -> void:
	if not state in TRACK.STATES:
		return
	intensity = state
	if TRACK.section_of(track, state) != _section:
		_load_players()
	_apply_levels()
	_render()


## Change one layer setting ([param key]: volume_db, muted or solo) and hear it.
func set_layer(layer: String, key: String, value: Variant) -> void:
	if not mix.has(layer):
		return
	mix[layer][key] = snappedf(float(value), 0.1) if key == "volume_db" else bool(value)
	_apply_levels()
	_sync_rows()
	changed.emit()


func toggle_play() -> void:
	if _playing:
		stop()
	else:
		play()


## Start every layer from the top, in the same frame.
func play() -> void:
	if players.is_empty():
		return
	_apply_levels()
	for player: AudioStreamPlayer in players.values():
		player.play(0.0)
	_playing = true
	_render()


func stop() -> void:
	for player: Variant in players.values():
		if is_instance_valid(player):
			(player as AudioStreamPlayer).stop()
	_playing = false
	if play_button != null:
		_render()


func is_playing() -> bool:
	return _playing


func is_dirty() -> bool:
	return not path.is_empty() and mix != saved


## Unsaved layer changes for the save message: {path: {"layers": {name: {…}}}} ({} if none).
func edits() -> Dictionary:
	if not is_dirty():
		return {}
	var changed_layers := {}
	for layer: String in mix:
		if mix[layer] != saved.get(layer):
			changed_layers[layer] = (mix[layer] as Dictionary).duplicate()
	return {path: {"layers": changed_layers}}


## Back to the saved layer settings.
func revert() -> void:
	mix = saved.duplicate(true)
	_apply_levels()
	_sync_rows()


## The changes went to review: they count as saved from now on.
func mark_saved() -> void:
	saved = mix.duplicate(true)


## Each layer player's level now, linear ({name: gain}), for tests.
func levels() -> Dictionary:
	var out := {}
	for layer: String in players:
		out[layer] = db_to_linear((players[layer] as AudioStreamPlayer).volume_db)
	return out


# --- Building ----------------------------------------------------------------------------------


func _build_rows() -> void:
	for child: Node in _rows.get_children():
		_rows.remove_child(child)
		child.queue_free()
	var roles := {}
	for layer: Variant in track.get("layers", []):
		if layer is Dictionary:
			roles[str(layer.get("name", ""))] = str(layer.get("role", ""))
	for layer: String in mix:
		_rows.add_child(_layer_row(layer, roles.get(layer, "")))
	_sync_rows()
	UI_MOTION.stagger(_rows.get_children())


func _layer_row(layer: String, role: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = layer
	row.theme_type_variation = "JoineryRow"
	UI.bind_height(row, "md", false)
	var title := UI.label(layer.capitalize())
	title.name = "Name"
	title.tooltip_text = role.capitalize() if not role.is_empty() else ""
	row.add_child(title)
	var fader := HSlider.new()
	fader.name = "Fader"
	fader.min_value = BUSES.MIN_DB
	fader.max_value = BUSES.MAX_DB
	fader.step = 0.5
	fader.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(fader, "sm")
	fader.accessibility_name = layer.capitalize() + " level"
	fader.value_changed.connect(func(value: float) -> void: set_layer(layer, "volume_db", value))
	row.add_child(fader)
	var value := UI.label("", "JoineryNumeric")
	value.name = "Value"
	value.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	value.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.add_child(value)
	for spec: Array in [["muted", "Mute"], ["solo", "Solo"]]:
		var button := Button.new()
		button.name = str(spec[1])
		button.text = str(spec[1])
		button.theme_type_variation = "JoinerySegment"
		button.toggle_mode = true
		button.focus_mode = Control.FOCUS_ALL
		button.accessibility_name = "%s %s" % [spec[1], layer]
		UI.bind_height(button, "sm")
		UI_MOTION.press_feedback(button)
		var key := str(spec[0])
		button.toggled.connect(func(on: bool) -> void: set_layer(layer, key, on))
		row.add_child(button)
	var playing := UI.label("", "JoineryCaption")
	playing.name = "Plays"
	playing.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	playing.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.add_child(playing)
	row.theme_changed.connect(
		func() -> void:
			var em := float(UI_THEME.font_size(row, "body"))
			title.custom_minimum_size.x = em * 7
			value.custom_minimum_size.x = em * 4.5
			playing.custom_minimum_size.x = em * 5
	)
	return row


func _load_players() -> void:
	var was_playing := _playing
	for child: Node in _player_box.get_children():
		(child as AudioStreamPlayer).stop()
		_player_box.remove_child(child)
		child.queue_free()
	players.clear()
	_section = TRACK.section_of(track, intensity)
	var bus := _bus()
	for layer: String in mix:
		var stem := TRACK.stem(track, _section, layer)
		var stream: AudioStream = (
			load(stem) as AudioStream
			if not stem.is_empty() and ResourceLoader.exists(stem)
			else null
		)
		var player := AudioStreamPlayer.new()
		player.name = layer
		player.stream = stream
		player.bus = bus
		player.finished.connect(_on_finished)
		_player_box.add_child(player)
		players[layer] = player
	_apply_levels()
	if was_playing:
		play()


## Put the players on the track bus again (after buses were added or put back).
func rebus() -> void:
	var bus := _bus()
	for player: AudioStreamPlayer in players.values():
		player.bus = bus


func _bus() -> String:
	var bus := str(track.get("bus", "Music"))
	return bus if AudioServer.get_bus_index(bus) >= 0 else "Master"


## A stem that doesn't loop on its own ends with the others: start them all again together.
func _on_finished() -> void:
	if _playing:
		for player: AudioStreamPlayer in players.values():
			player.play(0.0)


func _apply_levels() -> void:
	var gains := TRACK.gains(track, intensity, mix)
	for layer: String in players:
		var gain := float(gains.get(layer, 0.0))
		(players[layer] as AudioStreamPlayer).volume_db = (
			linear_to_db(gain) if gain > 0.0001 else BUSES.SILENT_DB
		)
	_sync_rows()


func _sync_rows() -> void:
	var gains := TRACK.gains(track, intensity, mix)
	for row: Node in _rows.get_children():
		var layer := str(row.name)
		if not mix.has(layer):
			continue
		var settings: Dictionary = mix[layer]
		var fader := row.get_node("Fader") as HSlider
		fader.set_value_no_signal(float(settings["volume_db"]))
		var db := float(settings["volume_db"])
		(row.get_node("Value") as Label).text = "Off" if db <= BUSES.MIN_DB else TRACK.db_text(db)
		(row.get_node("Mute") as Button).set_pressed_no_signal(bool(settings["muted"]))
		(row.get_node("Solo") as Button).set_pressed_no_signal(bool(settings["solo"]))
		var plays := row.get_node("Plays") as Label
		plays.text = TRACK.gain_text(float(gains.get(layer, 0.0)))
		plays.tooltip_text = "How loud %s plays at %s" % [layer, intensity]
		var title := row.get_node("Name") as Label
		if float(gains.get(layer, 0.0)) <= 0.0001:
			title.add_theme_color_override("font_color", UI_THEME.color(title, "text_muted"))
		else:
			title.remove_theme_color_override("font_color")


func _render() -> void:
	var has := not path.is_empty()
	picker.visible = has
	_summary.visible = has
	_controls.visible = has
	_rows.visible = has
	_empty.visible = not has
	_summary.text = TRACK.summary(track)
	intensity_slider.set_value_no_signal(TRACK.STATES.find(intensity))
	_intensity_label.text = intensity.capitalize()
	play_button.text = "Stop" if _playing else "Play"
	UI.bind_icon(play_button, "stop" if _playing else "play")
	play_button.disabled = players.is_empty()
