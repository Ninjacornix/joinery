@tool
extends VBoxContainer
## The review's music layers: when a change writes a music track description (music/<name>.tres)
## or its stems (audio/music/<name>/…), one compact row per layer under the preview: its name,
## a small waveform of its stem, and how loud it plays at the track's default intensity. The
## line above names the track with its tempo, bars and key. Where the change's facts carry a
## loudness (LUFS) for a layer or the mix, the row shows it, the fact itself as its tooltip.
##
## The proposed description comes from the change's diff (the project still has the old one
## until the change is accepted); stems the change writes come from their preview files, the
## others from the project.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const TRACK := preload("music_track.gd")
const TIMELINE := preload("audio_timeline.gd")
const LOUDNESS := "[−-]?\\d+(?:\\.\\d+)?(?:\\s*→\\s*[−-]?\\d+(?:\\.\\d+)?)?\\s*LUFS"

## The track description on show ({} without one).
var track: Dictionary = {}
var _title: Label
var _rows: VBoxContainer
var _streams: Dictionary = {}
var _facts: Array = []
var _loudness := RegEx.create_from_string(LOUDNESS)


func _init() -> void:
	name = "Music"
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Music layers"
	_title = UI.label("", "JoineryCaption")
	_title.name = "Track"
	add_child(_title)
	_rows = VBoxContainer.new()
	_rows.name = "Layers"
	_rows.theme_type_variation = "JoineryFlush"
	add_child(_rows)
	hide()


## Show the music in a change: [param diff] (unified, all files), [param media] (review items
## {path, before?, after?}) and [param changes] ({path, change}). Hides when there is none.
func show_change(diff: String, media: Array, changes: Array) -> void:
	clear()
	var paths: Array[String] = []
	for entry: Variant in changes + media:
		if entry is Dictionary:
			paths.append(str((entry as Dictionary).get("path", "")))
	var track_path := ""
	for path: String in paths:
		track_path = TRACK.track_of(path)
		if not track_path.is_empty():
			break
	if track_path.is_empty():
		return
	track = proposed(diff, track_path.trim_prefix("res://"))
	if track.is_empty():
		track = TRACK.read(track_path)
	if track.is_empty():
		return
	for item: Variant in media:
		if item is Dictionary and str(item.get("after", "")).is_absolute_path():
			_streams["res://" + str(item.get("path", "")).trim_prefix("res://")] = str(
				item["after"]
			)
	_build()
	show()
	UI_MOTION.stagger(_rows.get_children())


## The change's facts ("drums −15.8 LUFS", "Mix −16.1 LUFS (target −16)"): loudness joins the
## rows it names.
func set_facts(facts: Array) -> void:
	_facts = facts.duplicate()
	if visible:
		_sync_loudness()


func clear() -> void:
	track = {}
	_streams.clear()
	for child: Node in _rows.get_children():
		_rows.remove_child(child)
		child.queue_free()
	hide()


## The rows, for tests: layer name -> its row.
func rows() -> Dictionary:
	var out := {}
	for row: Node in _rows.get_children():
		out[str(row.name)] = row
	return out


## The description a diff writes for [param path] ({} when it writes none).
static func proposed(diff: String, path: String) -> Dictionary:
	var inside := false
	for line: String in diff.split("\n"):
		if line.begins_with("+++ "):
			inside = line.substr(4).trim_prefix("b/").strip_edges() == path
		elif inside and line.begins_with("+metadata/" + TRACK.META):
			var raw := line.substr(line.find("=") + 1).strip_edges()
			var parsed: Variant = JSON.parse_string(raw.replace('&"', '"'))
			if parsed is Dictionary and (parsed as Dictionary).get("layers") is Array:
				return parsed
	return {}


func _build() -> void:
	var title := str(track.get("name", "")).capitalize()
	var summary := TRACK.summary(track)
	_title.text = title + (" · " + summary if not summary.is_empty() else "")
	var state := str(track.get("default_state", "explore"))
	var section := TRACK.section_of(track, state)
	var gains := TRACK.gains(track, state, TRACK.mix_of(track))
	for layer: Variant in track.get("layers", []):
		if not layer is Dictionary:
			continue
		var layer_name := str(layer.get("name", ""))
		var row := HBoxContainer.new()
		row.name = layer_name
		row.theme_type_variation = "JoineryRow"
		var title_label := UI.label(layer_name.capitalize())
		title_label.name = "Name"
		title_label.tooltip_text = str(layer.get("role", "")).capitalize()
		row.add_child(title_label)
		var wave: TIMELINE = TIMELINE.new()
		wave.name = "Wave"
		wave.mini = true
		wave.accessibility_name = layer_name.capitalize() + " stem"
		row.add_child(wave)
		wave.load_stream(_stem(TRACK.stem(track, section, layer_name)))
		var level := UI.label(TRACK.gain_text(float(gains.get(layer_name, 0.0))), "JoineryNumeric")
		level.name = "Level"
		level.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		level.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		level.tooltip_text = "How loud it plays at %s, the track's start" % state
		row.add_child(level)
		var loud := UI.label("", "JoineryCaption")
		loud.name = "Loudness"
		loud.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		loud.hide()
		row.add_child(loud)
		row.theme_changed.connect(
			func() -> void:
				var em := float(UI_THEME.font_size(row, "body"))
				title_label.custom_minimum_size.x = em * 6
				level.custom_minimum_size.x = em * 4.5
		)
		_rows.add_child(row)
	_sync_loudness()


func _stem(path: String) -> AudioStream:
	if path.is_empty():
		return null
	var file := str(_streams.get(path, ""))
	if not file.is_empty():
		match file.get_extension().to_lower():
			"ogg":
				return AudioStreamOggVorbis.load_from_file(file)
			"wav":
				return AudioStreamWAV.load_from_file(file)
	return load(path) as AudioStream if ResourceLoader.exists(path) else null


func _sync_loudness() -> void:
	var mix := ""
	for fact: Variant in _facts:
		var text := str(fact)
		var found := _loudness.search(text)
		if found == null:
			continue
		var named := false
		for row: Node in _rows.get_children():
			if text.to_lower().contains(str(row.name).to_lower()):
				var loud := row.get_node("Loudness") as Label
				loud.text = found.get_string()
				loud.tooltip_text = text
				loud.show()
				named = true
		if not named and mix.is_empty():
			mix = found.get_string()
	var summary := TRACK.summary(track)
	var title := str(track.get("name", "")).capitalize()
	_title.text = " · ".join(
		[title, summary, "mix " + mix if not mix.is_empty() else ""].filter(
			func(part: String) -> bool: return not part.is_empty()
		)
	)
