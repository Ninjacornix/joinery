extends Node
## Adaptive layered music (autoload `Music`).
##
## A track is a plain Resource whose metadata `joinery_music` describes it:
## tempo, layers, sections (one stem per layer) and intensity states (how loud
## each layer is in calm, explore, danger, combat and victory). Every layer of
## the current section plays on its own AudioStreamPlayer; all of them start in
## the same audio mix, so they stay sample-locked. Intensity changes fade the
## layers starting on the next bar (or beat, per the track's `change_on`);
## section changes cut on the next bar; stingers start on the next beat and
## duck the layers by 6 dB while they play.
##
## The clock is the audio itself: the first sounding layer's playback position
## plus the time since the last mix, so bars line up with what is heard.
##
## With `auto` on, game events drive it: level_started plays the project
## setting `audio/music/track`, level_completed plays the victory stinger and
## state, level_failed and checkpoint_reached play their stingers, and every
## 0.25 s the nearest `enemy` to a `player` (and the player's health) picks
## explore, danger or combat. Project settings, all optional:
##   audio/music/track          res:// path of the track to play per level
##   audio/music/danger_radius  metres (2D: pixels / 64), default 12
##   audio/music/combat_radius  metres (2D: pixels / 64), default 5
##   audio/music/low_health     health / max_health at or below which it is danger, default 0.3
##
## A missing track or stem is a warning, never an error: the music stays silent.

signal intensity_changed(state: StringName)
signal section_changed(section: StringName)
signal bar_started(index: int)
signal stinger_played(event: StringName)

## The fixed intensity vocabulary, quietest first.
const STATES: Array[StringName] = [&"calm", &"explore", &"danger", &"combat", &"victory"]
const META_KEY := &"joinery_music"
const DRIVER_INTERVAL := 0.25
const STINGER_DUCK_DB := -6.0
const DUCK_SPEED_DB := 60.0
const EDIT_FADE_SECONDS := 0.05
const SILENT_DB := -80.0
const PIXELS_PER_METRE := 64.0
const DEFAULTS := {
	"tempo_bpm": 120.0,
	"beats_per_bar": 4,
	"default_state": "explore",
	"change_on": "bar",
	"fade_beats": 2.0,
	"bus": "Music",
}

## Game events and enemy distance change the intensity while this is on.
var auto: bool = true
## The state the layers are playing (or fading to).
var intensity: StringName = &""
## The state that takes over on the next bar (or beat); empty when none.
var pending_intensity: StringName = &""

var _track: Dictionary = {}
var _track_path: String = ""
var _streams: Dictionary = {}
var _section: StringName = &""
var _pending_section: StringName = &""
var _players: Array[AudioStreamPlayer] = []
var _layers: Array[String] = []
var _gains: Dictionary = {}
var _targets: Dictionary = {}
var _rates: Dictionary = {}
var _held: bool = false
var _celebrating: bool = false
var _last_t: float = 0.0
var _last_beat: int = -1
var _clock: float = 0.0
var _master: float = 1.0
var _master_rate: float = 0.0
var _stopping: bool = false
var _stinger_player: AudioStreamPlayer
var _pending_stinger: StringName = &""
var _pending_stinger_stream: AudioStream
var _duck_db: float = 0.0
var _driver_wait: float = 0.0
var _warned: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_stinger_player = AudioStreamPlayer.new()
	_stinger_player.name = "Stinger"
	add_child(_stinger_player)
	var events: Node = get_node_or_null(^"/root/GameEvents")
	if events == null:
		return
	events.connect(&"level_started", _on_level_started)
	events.connect(&"level_completed", _on_level_completed)
	events.connect(&"level_failed", _on_level_failed)
	events.connect(&"checkpoint_reached", _on_checkpoint_reached)


## Plays the track described by the Resource at `track_path`, in `state` (the
## track's default_state when empty). False, with a warning, when it cannot.
func play(track_path: String, state: StringName = &"") -> bool:
	if track_path.is_empty() or not ResourceLoader.exists(track_path):
		_warn("Music: track %s does not exist; the music stays silent." % track_path)
		return false
	var resource: Resource = load(track_path)
	if resource == null or not resource.has_meta(META_KEY):
		_warn("Music: %s has no %s metadata; the music stays silent." % [track_path, META_KEY])
		return false
	var data: Variant = resource.get_meta(META_KEY)
	if not data is Dictionary:
		_warn("Music: %s metadata is not a dictionary." % track_path)
		return false
	if not play_data(data, {}, state):
		return false
	_track_path = track_path
	return true


## Plays a track from its description. `streams` overrides stems and stingers
## with streams already in memory: keys are "<layer>" (every section),
## "<section>/<layer>" (one section) or "stinger/<event>".
func play_data(track: Dictionary, streams: Dictionary = {}, state: StringName = &"") -> bool:
	var sections: Array = track.get("sections", [])
	if sections.is_empty():
		_warn("Music: track %s has no sections." % track.get("name", "?"))
		return false
	stop(0.0)
	_track = track.duplicate(true)
	for key: String in DEFAULTS:
		if not _track.has(key):
			_track[key] = DEFAULTS[key]
	_track_path = ""
	_streams = streams
	_celebrating = false
	var start: StringName = state if not state.is_empty() else _default_state()
	if not _states().has(String(start)):
		_warn("Music: state %s is not in the track; using %s." % [start, _default_state()])
		start = _default_state()
	intensity = start
	pending_intensity = &""
	_pending_section = &""
	_pending_stinger = &""
	_master = 1.0
	_stopping = false
	for layer: String in _layer_names():
		_gains[layer] = _layer_target(layer, start)
		_targets[layer] = _gains[layer]
		_rates[layer] = 0.0
	_start_section(_state_section(start, StringName(str(sections[0].get("name", "")))))
	intensity_changed.emit(intensity)
	return true


## Fades the music out over `fade_seconds` and stops it.
func stop(fade_seconds: float = 1.0) -> void:
	_pending_stinger = &""
	if fade_seconds <= 0.0 or _players.is_empty():
		_clear()
		return
	_stopping = true
	_master_rate = _master / fade_seconds


## Moves to `state` on the next bar (or beat). With `hold`, the game events
## and enemy distance leave the intensity alone until `release()`.
func set_intensity(state: StringName, hold: bool = false) -> void:
	if not STATES.has(state):
		_warn("Music: %s is not an intensity state (%s)." % [state, STATES])
		return
	if hold:
		_held = true
	if not is_playing():
		intensity = state
		pending_intensity = &""
		intensity_changed.emit(state)
		return
	pending_intensity = &"" if state == intensity else state


## Hands the intensity back to the game events and enemy distance.
func release() -> void:
	_held = false
	_driver_wait = 0.0


## Switches to section `section_name` on the next bar; it starts at its bar 0.
func set_section(section_name: StringName) -> void:
	if _section_info(section_name).is_empty():
		_warn("Music: the track has no section %s." % section_name)
		return
	_pending_section = &"" if section_name == _section else section_name


## Plays the stinger for `event` (victory, checkpoint, fail) on the next beat.
func stinger(event: StringName) -> bool:
	if not is_playing():
		return false
	var key: String = "stinger/%s" % event
	var stream: AudioStream = _streams.get(key) as AudioStream
	if stream == null:
		var stingers: Dictionary = _track.get("stingers", {})
		stream = _load_stream(str(stingers.get(String(event), "")), key)
	if stream == null:
		return false
	_pending_stinger = event
	_pending_stinger_stream = stream
	return true


func set_layer_volume(layer: String, db: float) -> void:
	_edit_layer(layer, "volume_db", db)


func set_layer_muted(layer: String, muted: bool) -> void:
	_edit_layer(layer, "muted", muted)


func set_layer_solo(layer: String, solo: bool) -> void:
	_edit_layer(layer, "solo", solo)


## The playing track's description (empty when none plays).
func track() -> Dictionary:
	return _track


func is_playing() -> bool:
	return not _track.is_empty() and not _stopping


func current_section() -> StringName:
	return _section


## Seconds into the current section, from the audio clock.
func position_seconds() -> float:
	return _last_t


func bar_index() -> int:
	var bar: float = _bar_length()
	return int(floor(_last_t / bar)) if bar > 0.0 else 0


func beat_seconds() -> float:
	return 60.0 / maxf(float(_track.get("tempo_bpm", 120.0)), 1.0)


## The AudioStreamPlayers of the current section, one per layer, in layer order.
func layer_players() -> Array:
	return _players.duplicate()


func _process(delta: float) -> void:
	if _track.is_empty():
		return
	if _stopping:
		_master = move_toward(_master, 0.0, _master_rate * delta)
		if _master <= 0.0:
			_clear()
			return
	var t: float = _time(delta)
	var beat: int = int(floor(t / beat_seconds()))
	if beat != _last_beat:
		_last_beat = beat
		_on_beat(beat, t - beat * beat_seconds())
	for layer: String in _gains:
		_gains[layer] = move_toward(
			float(_gains[layer]), float(_targets[layer]), float(_rates[layer]) * delta
		)
	var duck: float = STINGER_DUCK_DB if _stinger_player.playing else 0.0
	_duck_db = move_toward(_duck_db, duck, DUCK_SPEED_DB * delta)
	_apply_volumes()
	_drive(delta)


func _on_beat(beat: int, overshoot: float) -> void:
	var per_bar: int = maxi(int(_track.get("beats_per_bar", 4)), 1)
	var on_bar: bool = beat % per_bar == 0
	if not _pending_stinger.is_empty():
		_stinger_player.bus = _bus()
		_stinger_player.stream = _pending_stinger_stream
		_stinger_player.play()
		var event: StringName = _pending_stinger
		_pending_stinger = &""
		stinger_played.emit(event)
	if not pending_intensity.is_empty() and (on_bar or str(_track.get("change_on")) == "beat"):
		_apply_intensity()
	if not on_bar:
		return
	if not _pending_section.is_empty():
		var next: StringName = _pending_section
		_pending_section = &""
		_start_section(next, overshoot)
		return
	@warning_ignore("integer_division")
	bar_started.emit(beat / per_bar)


func _apply_intensity() -> void:
	intensity = pending_intensity
	pending_intensity = &""
	var fade: float = maxf(float(_track.get("fade_beats", 2.0)) * beat_seconds(), 0.001)
	for layer: String in _layer_names():
		_retarget(layer, fade)
	var section: StringName = _state_section(intensity, _section)
	if section != _section:
		_pending_section = section
	intensity_changed.emit(intensity)


func _retarget(layer: String, fade: float) -> void:
	var target: float = _layer_target(layer, intensity)
	_targets[layer] = target
	_rates[layer] = absf(target - float(_gains.get(layer, 0.0))) / maxf(fade, 0.001)
	if not _gains.has(layer):
		_gains[layer] = 0.0


func _start_section(section_name: StringName, offset: float = 0.0) -> void:
	var info: Dictionary = _section_info(section_name)
	if info.is_empty():
		_warn("Music: the track has no section %s." % section_name)
		return
	_free_players()
	var stems: Dictionary = info.get("stems", {})
	var bus: StringName = _bus()
	for layer: String in _layer_names():
		var player: AudioStreamPlayer = AudioStreamPlayer.new()
		player.name = "Layer%d" % _players.size()
		player.set_meta(&"layer", layer)
		player.bus = bus
		player.stream = _stem(section_name, layer, str(stems.get(layer, "")))
		add_child(player)
		_players.append(player)
		_layers.append(layer)
	_section = section_name
	_last_t = offset
	_clock = offset
	_last_beat = -1
	_apply_volumes()
	_start_players(offset)
	section_changed.emit(section_name)


## Starts every layer inside one audio-server lock, so the mixer picks them all
## up in the same mix and they stay sample-locked.
func _start_players(offset: float) -> void:
	AudioServer.lock()
	for player: AudioStreamPlayer in _players:
		if player.stream != null:
			player.play(offset)
	AudioServer.unlock()


## Seconds into the section, read from the audio clock; never steps backwards
## except where the section loops (the mixer moves in chunks).
func _time(delta: float) -> float:
	var length: float = _section_length()
	var raw: float
	var reference: AudioStreamPlayer = _reference()
	if reference == null:
		_clock = fposmod(_clock + delta, length)
		raw = _clock
	elif not reference.playing:
		# A stem that does not loop: start the whole section again, still in sync.
		_start_players(0.0)
		raw = 0.0
	else:
		raw = reference.get_playback_position() + AudioServer.get_time_since_last_mix()
	raw = fposmod(raw, length)
	if raw < _last_t and _last_t - raw < length * 0.5:
		raw = _last_t
	_last_t = raw
	return raw


func _reference() -> AudioStreamPlayer:
	for player: AudioStreamPlayer in _players:
		if player.stream != null:
			return player
	return null


func _apply_volumes() -> void:
	for index: int in _players.size():
		var gain: float = float(_gains.get(_layers[index], 0.0)) * _master
		var db: float = linear_to_db(gain) + _duck_db if gain > 0.0001 else SILENT_DB
		_players[index].volume_db = maxf(db, SILENT_DB)


func _free_players() -> void:
	for player: AudioStreamPlayer in _players:
		player.stop()
		# Out of the tree now, so the next section's players get the same names.
		remove_child(player)
		player.queue_free()
	_players.clear()
	_layers.clear()


func _clear() -> void:
	_free_players()
	_stinger_player.stop()
	_track = {}
	_track_path = ""
	_streams = {}
	_section = &""
	_pending_section = &""
	pending_intensity = &""
	_pending_stinger = &""
	_gains.clear()
	_targets.clear()
	_rates.clear()
	_stopping = false
	_master = 1.0
	_duck_db = 0.0
	_last_t = 0.0
	_last_beat = -1


func _edit_layer(layer: String, key: String, value: Variant) -> void:
	for entry: Variant in _track.get("layers", []):
		if entry is Dictionary and str(entry.get("name", "")) == layer:
			entry[key] = value
			for name: String in _layer_names():
				_retarget(name, EDIT_FADE_SECONDS)
			return
	_warn("Music: the track has no layer %s." % layer)


## db_to_linear(volume_db) times the state's level; muted and solo apply.
func _layer_target(layer: String, state: StringName) -> float:
	var info: Dictionary = {}
	var any_solo: bool = false
	for entry: Variant in _track.get("layers", []):
		if not entry is Dictionary:
			continue
		any_solo = any_solo or bool(entry.get("solo", false))
		if str(entry.get("name", "")) == layer:
			info = entry
	if info.is_empty() or bool(info.get("muted", false)):
		return 0.0
	if any_solo and not bool(info.get("solo", false)):
		return 0.0
	var state_info: Dictionary = _states().get(String(state), {})
	var levels: Dictionary = state_info.get("layers", {})
	var level: float = clampf(float(levels.get(layer, 0.0)), 0.0, 1.0)
	return db_to_linear(float(info.get("volume_db", 0.0))) * level


func _layer_names() -> Array[String]:
	var names: Array[String] = []
	for entry: Variant in _track.get("layers", []):
		if entry is Dictionary:
			names.append(str(entry.get("name", "")))
	return names


func _states() -> Dictionary:
	return _track.get("states", {})


func _default_state() -> StringName:
	var state: String = str(_track.get("default_state", "explore"))
	if _states().has(state) or _states().is_empty():
		return StringName(state)
	return StringName(str(_states().keys()[0]))


func _state_section(state: StringName, fallback: StringName) -> StringName:
	var info: Dictionary = _states().get(String(state), {})
	var section: StringName = StringName(str(info.get("section", "")))
	return section if not _section_info(section).is_empty() else fallback


func _section_info(section_name: StringName) -> Dictionary:
	for entry: Variant in _track.get("sections", []):
		if entry is Dictionary and str(entry.get("name", "")) == String(section_name):
			return entry
	return {}


func _bar_length() -> float:
	return beat_seconds() * maxi(int(_track.get("beats_per_bar", 4)), 1)


func _section_length() -> float:
	var bars: int = maxi(int(_section_info(_section).get("bars", 1)), 1)
	return _bar_length() * bars


func _bus() -> StringName:
	var bus: StringName = StringName(str(_track.get("bus", "Music")))
	if AudioServer.get_bus_index(bus) < 0:
		_warn("Music: there is no %s bus; playing on Master." % bus)
		return &"Master"
	return bus


func _stem(section_name: StringName, layer: String, path: String) -> AudioStream:
	var key: String = "%s/%s" % [section_name, layer]
	if _streams.has(key):
		return _streams[key] as AudioStream
	if _streams.has(layer):
		return _streams[layer] as AudioStream
	return _load_stream(path, key)


func _load_stream(path: String, what: String) -> AudioStream:
	if path.is_empty():
		return null
	if not ResourceLoader.exists(path):
		_warn("Music: %s (%s) is missing; it stays silent." % [path, what])
		return null
	var stream: AudioStream = load(path) as AudioStream
	if stream == null:
		_warn("Music: %s (%s) is not an audio stream." % [path, what])
	return stream


func _warn(message: String) -> void:
	if _warned.has(message):
		return
	_warned[message] = true
	push_warning(message)


# --- drivers -----------------------------------------------------------------


func _on_level_started(_level: Node) -> void:
	_celebrating = false
	if not auto:
		return
	var path: String = str(ProjectSettings.get_setting("audio/music/track", ""))
	if path.is_empty():
		return
	if path == _track_path and is_playing():
		if not _held:
			set_intensity(_default_state())
		return
	play(path)


func _on_level_completed(_result: Dictionary) -> void:
	if not auto or not is_playing():
		return
	stinger(&"victory")
	_celebrating = true
	if not _held:
		set_intensity(&"victory")


func _on_level_failed(_reason: String) -> void:
	if auto:
		stinger(&"fail")


func _on_checkpoint_reached(_id: String) -> void:
	if auto:
		stinger(&"checkpoint")


func _drive(delta: float) -> void:
	if not auto or _held or _celebrating or not is_playing():
		return
	_driver_wait -= delta
	if _driver_wait > 0.0:
		return
	_driver_wait = DRIVER_INTERVAL
	var wanted: StringName = _wanted_state()
	var heading: StringName = pending_intensity if not pending_intensity.is_empty() else intensity
	if wanted != heading:
		set_intensity(wanted)


## combat or danger by the nearest enemy to any player, at least danger on low
## health, else the track's default state.
func _wanted_state() -> StringName:
	var tree: SceneTree = get_tree()
	var players: Array[Node] = tree.get_nodes_in_group(&"player")
	var enemies: Array[Node] = tree.get_nodes_in_group(&"enemy")
	var nearest: float = INF
	for player: Node in players:
		for enemy: Node in enemies:
			nearest = minf(nearest, _distance(player, enemy))
	var combat: float = float(ProjectSettings.get_setting("audio/music/combat_radius", 5.0))
	var danger: float = float(ProjectSettings.get_setting("audio/music/danger_radius", 12.0))
	var state: StringName = _default_state()
	if nearest <= combat:
		state = &"combat"
	elif nearest <= danger:
		state = &"danger"
	if STATES.find(state) < STATES.find(&"danger"):
		for player: Node in players:
			if _low_health(player):
				state = &"danger"
	return state


func _distance(a: Node, b: Node) -> float:
	if a is Node3D and b is Node3D:
		return (a as Node3D).global_position.distance_to((b as Node3D).global_position)
	if a is Node2D and b is Node2D:
		var pixels: float = (a as Node2D).global_position.distance_to((b as Node2D).global_position)
		return pixels / PIXELS_PER_METRE
	return INF


func _low_health(player: Node) -> bool:
	if not ("health" in player and "max_health" in player):
		return false
	var most: float = float(player.get(&"max_health"))
	if most <= 0.0:
		return false
	var ratio: float = float(ProjectSettings.get_setting("audio/music/low_health", 0.3))
	return float(player.get(&"health")) / most <= ratio
