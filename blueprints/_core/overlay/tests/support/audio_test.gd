extends SceneTree
## Checks the core's audio: the bus layout, the adaptive `Music` autoload
## (intensity by game events, enemy distance and health, bar-quantised changes,
## layers kept in sync across section switches and loops), and the building
## blocks SoundCue, Footsteps, AmbienceZone and UiSounds. Every sound is made
## here in code (sine waves in AudioStreamWAV); the project ships none.
## Run: godot --headless --fixed-fps 60 --script res://tests/support/audio_test.gd
##        -- --autostart --settings-path=<file>
##
## The test is paced to real time: the audio clock is the mixer's (the Dummy
## driver headless mixes in real time), so game frames must not race ahead.

const MIX_RATE := 44100
const BPM := 240.0
const BEATS_PER_BAR := 4
const BARS := 2
const LAYERS: Array[String] = ["pads", "bass", "percussion", "tension"]
## Sync tolerance between layers of one section, in seconds.
const SYNC_TOLERANCE := 0.003
## Float rounding between two readings of the music clock, in seconds; far
## below one sample (1 / MIX_RATE, 23 us), so it cannot hide a late change.
const CLOCK_EPSILON := 0.000001
## Game frames for the stinger duck to settle: Music moves it DUCK_SPEED_DB
## (60 dB/s) by the frame delta, 1/60 s under --fixed-fps 60, so 6 frames
## cover the 6 dB; one more for the frame that starts the stinger.
const DUCK_FRAMES := 7
const WALKER_SOURCE := """extends CharacterBody3D

var drive: Vector3 = Vector3.ZERO
var jump_now: bool = false


func _physics_process(delta: float) -> void:
	velocity.x = drive.x
	velocity.z = drive.z
	if is_on_floor():
		if jump_now:
			velocity.y = 6.0
			jump_now = false
	else:
		velocity.y -= 20.0 * delta
	move_and_slide()
"""
const HEALTH_SOURCE := """extends Node3D

var health: float = 10.0
var max_health: float = 10.0
"""

var play: PlayHarness
var music: Node
var ui_sounds: Node
var world: Node3D
var max_drift: float = 0.0
var drift_samples: int = 0
## The music clock as the previous frame left it (read before Music runs).
var clock_before: float = 0.0


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	PlayHarness.pace(self, 60)
	play = PlayHarness.new(self, "audio")
	music = root.get_node_or_null(^"Music")
	ui_sounds = root.get_node_or_null(^"UiSounds")
	play.expect(music != null and ui_sounds != null, "Music and UiSounds are autoloads")
	# process_frame comes before the nodes' _process, so this is last frame's clock.
	process_frame.connect(_remember_clock)
	await play.boot(true)
	world = Node3D.new()
	world.name = "AudioTestWorld"
	world.position = Vector3(1000, 0, 0)
	root.add_child(world)
	_buses()
	_settings_on_layout()
	await _missing_files()
	await _music_bar_changes()
	await _music_events()
	await _music_enemies_and_sync()
	await _music_health()
	music.call(&"stop", 0.0)
	music.set(&"auto", false)
	await _sound_cue()
	await _footsteps_and_ambience()
	await _ui_sounds()
	print("audio: max layer drift %.2f ms over %d samples" % [max_drift * 1000.0, drift_samples])
	world.queue_free()
	play.finish()


# --- helpers -------------------------------------------------------------------


## A sine wave of `seconds`, 16-bit mono; looping over the whole file if asked.
func _wav(seconds: float, hertz: float, loop: bool) -> AudioStreamWAV:
	var frames: int = int(round(seconds * MIX_RATE))
	var data: PackedByteArray = PackedByteArray()
	data.resize(frames * 2)
	for index: int in frames:
		var value: float = sin(TAU * hertz * index / MIX_RATE) * 0.2
		data.encode_s16(index * 2, int(value * 32767.0))
	var wav: AudioStreamWAV = AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_16_BITS
	wav.mix_rate = MIX_RATE
	wav.stereo = false
	wav.data = data
	if loop:
		wav.loop_mode = AudioStreamWAV.LOOP_FORWARD
		wav.loop_begin = 0
		wav.loop_end = frames
	return wav


func _bar() -> float:
	return 60.0 / BPM * BEATS_PER_BAR


func _section_seconds() -> float:
	return _bar() * BARS


func _track() -> Dictionary:
	var layers: Array = []
	for layer: String in LAYERS:
		layers.append(
			{"name": layer, "role": layer, "volume_db": 0.0, "muted": false, "solo": false}
		)
	var sections: Array = []
	for section: String in ["main", "fight"]:
		var stems: Dictionary = {}
		for layer: String in LAYERS:
			stems[layer] = "res://audio/music/test/%s_%s.ogg" % [section, layer]
		sections.append({"name": section, "bars": BARS, "stems": stems})
	var level: Callable = func(
		pads: float, bass: float, drums: float, tension: float
	) -> Dictionary:
		return {"pads": pads, "bass": bass, "percussion": drums, "tension": tension}
	return {
		"version": 1,
		"name": "test",
		"mood": "tense",
		"key": "A minor",
		"tempo_bpm": BPM,
		"beats_per_bar": BEATS_PER_BAR,
		"layers": layers,
		"sections": sections,
		"states":
		{
			"calm": {"section": "main", "layers": level.call(1.0, 0.0, 0.0, 0.0)},
			"explore": {"section": "main", "layers": level.call(1.0, 1.0, 0.5, 0.0)},
			"danger": {"section": "main", "layers": level.call(0.8, 1.0, 0.8, 1.0)},
			"combat": {"section": "fight", "layers": level.call(0.7, 1.0, 1.0, 1.0)},
			"victory": {"section": "main", "layers": level.call(1.0, 1.0, 0.0, 0.0)},
		},
		"default_state": "explore",
		"change_on": "bar",
		"fade_beats": 2.0,
		"stingers": {"victory": "res://audio/music/test/stinger_victory.ogg"},
		"bus": "Music",
	}


func _streams() -> Dictionary:
	var streams: Dictionary = {}
	var hertz: float = 220.0
	for layer: String in LAYERS:
		streams["main/" + layer] = _wav(_section_seconds(), hertz, true)
		streams["fight/" + layer] = _wav(_section_seconds(), hertz * 1.5, true)
		hertz *= 1.25
	# Looping, so it sounds for as many frames as the duck check needs however
	# long those frames take; the test stops it.
	streams["stinger/victory"] = _wav(0.6, 880.0, true)
	streams["stinger/checkpoint"] = _wav(0.3, 660.0, false)
	return streams


func _remember_clock() -> void:
	if music != null:
		clock_before = float(music.call(&"position_seconds"))


## Like play.watch, and also records the music clock of the frame before the
## latest firing ("before") and of the frame that fired it ("at").
func _watch_clock(signal_ref: Signal) -> Dictionary:
	var record: Dictionary = {"signal": signal_ref, "count": 0, "args": []}
	var catch: Callable = func(...args: Array) -> void:
		record["count"] = int(record["count"]) + 1
		record["args"] = args
		record["before"] = clock_before
		record["at"] = float(music.call(&"position_seconds"))
		record["frame"] = Engine.get_process_frames()
	record["callable"] = catch
	signal_ref.connect(catch)
	return record


## Seconds past the last bar line (0 just after a bar starts).
func _into_bar() -> float:
	return fposmod(float(music.call(&"position_seconds")), _bar())


func _layer_db(layer: String) -> float:
	for player: AudioStreamPlayer in music.call(&"layer_players"):
		if str(player.get_meta(&"layer")) == layer:
			return player.volume_db
	return NAN


func _seconds(seconds: float) -> void:
	await play.process_frames(int(ceil(seconds * 60.0)))


## Waits until the music is `low`-`high` seconds into a bar.
func _wait_into_bar(low: float, high: float) -> void:
	for _i: int in 240:
		var into: float = _into_bar()
		if into >= low and into <= high:
			return
		await process_frame
	play.expect(false, "the music reached %.2f-%.2f s into a bar" % [low, high])


## Every layer's playback position matches the first within SYNC_TOLERANCE.
func _expect_sync(where: String) -> void:
	var players: Array = music.call(&"layer_players")
	play.expect(players.size() == LAYERS.size(), "%s: one player per layer" % where)
	if players.is_empty():
		return
	# Read under the audio-server lock: read while the mixer runs, one layer
	# may already have moved on by a chunk and the next not yet.
	var positions: Array[float] = []
	AudioServer.lock()
	for player: AudioStreamPlayer in players:
		positions.append(player.get_playback_position())
	AudioServer.unlock()
	for index: int in players.size():
		var player: AudioStreamPlayer = players[index]
		play.expect(player.playing, "%s: layer %s plays" % [where, player.get_meta(&"layer")])
		var drift: float = absf(positions[index] - positions[0])
		max_drift = maxf(max_drift, drift)
		drift_samples += 1
		play.expect(
			drift <= SYNC_TOLERANCE,
			"%s: layer %s is %.1f ms out" % [where, player.get_meta(&"layer"), drift * 1000.0]
		)


## Waits for the music to change to `state`; the change's _watch_clock record
## (`record` when given: one started before the change was asked for).
func _await_intensity(state: StringName, max_seconds: float, record: Dictionary = {}) -> Dictionary:
	if record.is_empty():
		record = _watch_clock(music.intensity_changed)
	var frames: int = 0
	while frames < int(max_seconds * 60.0):
		await process_frame
		frames += 1
		if StringName(music.get(&"intensity")) == state:
			break
	play.unwatch(record)
	play.expect(
		StringName(music.get(&"intensity")) == state,
		"music reached %s within %.1f s (at %s)" % [state, max_seconds, music.get(&"intensity")]
	)
	return record


## A quantised change happens in the first frame whose clock has passed its
## line (a bar, or a beat): the line lies between the clock of the frame
## before and the clock of the frame that made the change. How far past the
## line that frame is depends only on frame timing (a busy machine stalls a
## frame for 100 ms and more), so it is not bounded here; music.gd places the
## change on the line itself, and the section and stinger checks show that.
func _expect_on_line(record: Dictionary, line: float, what: String) -> void:
	if int(record["count"]) == 0:
		play.expect(false, "%s happened" % what)
		return
	var step: float = fposmod(float(record["at"]) - float(record["before"]), _section_seconds())
	var into: float = fposmod(float(record["at"]), line)
	play.expect(
		into <= step + CLOCK_EPSILON,
		(
			"%s lands on the first frame past a line (%.3f s past it, the frame moved %.3f s)"
			% [what, into, step]
		)
	)


func _expect_on_bar(record: Dictionary, what: String) -> void:
	_expect_on_line(record, _bar(), what)


# --- buses and settings --------------------------------------------------------


func _buses() -> void:
	var names: Array[String] = ["Master", "Music", "SFX", "Ambience", "UI", "Voice"]
	play.expect(AudioServer.bus_count == names.size(), "six buses (%d)" % AudioServer.bus_count)
	for index: int in names.size():
		play.expect(
			AudioServer.get_bus_name(index) == names[index],
			"bus %d is %s (%s)" % [index, names[index], AudioServer.get_bus_name(index)]
		)
		if index > 0:
			play.expect(
				AudioServer.get_bus_send(index) == &"Master", "%s sends to Master" % names[index]
			)
	var music_bus: int = AudioServer.get_bus_index(&"Music")
	var ducking: AudioEffectCompressor = (
		AudioServer.get_bus_effect(music_bus, 0) as AudioEffectCompressor
	)
	play.expect(ducking != null, "Music has a compressor")
	if ducking != null:
		play.expect(ducking.sidechain == &"Voice", "the Music compressor listens to Voice")
		play.expect_near(ducking.threshold, -24.0, 0.01, "ducking threshold")
		play.expect_near(ducking.ratio, 4.0, 0.01, "ducking ratio")
		play.expect(AudioServer.is_bus_effect_enabled(music_bus, 0), "ducking is on")
	var ambience: int = AudioServer.get_bus_index(&"Ambience")
	var reverb: AudioEffectReverb = AudioServer.get_bus_effect(ambience, 0) as AudioEffectReverb
	play.expect(reverb != null, "Ambience has a reverb")
	if reverb != null:
		play.expect_near(reverb.room_size, 0.6, 0.001, "reverb room size")
		play.expect_near(reverb.wet, 0.25, 0.001, "reverb wet")
	for bus: StringName in [&"Master", &"SFX", &"UI", &"Voice"]:
		var index: int = AudioServer.get_bus_index(bus)
		play.expect(AudioServer.get_bus_effect_count(index) == 0, "%s has no effects" % bus)


func _settings_on_layout() -> void:
	var settings: Node = play.settings
	var bus: int = AudioServer.get_bus_index(&"Music")
	var level: float = float(settings.get_value("audio", "music"))
	play.expect_near(
		AudioServer.get_bus_volume_db(bus),
		float(settings.call(&"base_volume_db", &"Music")) + linear_to_db(level),
		0.01,
		"the music slider scales the layout's own Music volume"
	)


# --- music ---------------------------------------------------------------------


func _missing_files() -> void:
	play.expect(
		not bool(music.call(&"play", "res://audio/music/nowhere/nowhere.tres")),
		"a missing track is refused, not a crash"
	)
	# A track whose stems are missing plays silently on a clock of its own.
	play.expect(bool(music.call(&"play_data", _track())), "a track with missing stems plays")
	var bars: Dictionary = play.watch(music.bar_started)
	await _seconds(_bar() * 1.5)
	play.expect(play.fire_count(bars) >= 1, "bars still pass without stems")
	play.unwatch(bars)
	music.call(&"stop", 0.0)
	play.expect(not bool(music.call(&"is_playing")), "stop(0) stops at once")


func _music_bar_changes() -> void:
	play.expect(bool(music.call(&"play_data", _track(), _streams())), "the test track plays")
	play.expect(music.get(&"intensity") == &"explore", "it starts in the default state")
	await _seconds(0.3)
	_expect_sync("start")
	await _wait_into_bar(0.3, 0.6)
	var bar_before: int = int(music.call(&"bar_index"))
	var calm: Dictionary = _watch_clock(music.intensity_changed)
	# Held, so the drivers (no enemy near: explore) leave it alone.
	music.call(&"set_intensity", &"calm", true)
	play.expect(music.get(&"pending_intensity") == &"calm", "calm waits for the bar")
	play.expect(music.get(&"intensity") == &"explore", "the state does not change mid-bar")
	# By the audio clock, not by frames: every frame whose clock is still in
	# the bar calm was asked in must still be explore. At process_frame the
	# state and the clock are both as Music's last _process left them.
	var early: float = -1.0
	for _i: int in 240:
		await process_frame
		if int(music.call(&"bar_index")) != bar_before:
			break
		if early < 0.0 and music.get(&"intensity") != &"explore":
			early = _into_bar()
	play.expect(
		early < 0.0,
		"still explore before the bar line (%s at %.3f s)" % [music.get(&"intensity"), early]
	)
	_expect_on_bar(await _await_intensity(&"calm", 1.5, calm), "calm")
	play.expect(int(music.call(&"bar_index")) != bar_before, "calm starts in a new bar")
	await _seconds(60.0 / BPM * 2.0 + 0.2)
	play.expect_near(_layer_db("pads"), 0.0, 0.5, "calm: pads at full")
	play.expect(_layer_db("bass") <= -79.0, "calm: bass faded out (%.1f dB)" % _layer_db("bass"))
	_expect_sync("after a fade")
	# Mixer controls apply straight away.
	music.call(&"set_layer_volume", "pads", -6.0)
	await _seconds(0.2)
	play.expect_near(_layer_db("pads"), -6.0, 0.5, "a layer's fader")
	music.call(&"set_layer_muted", "pads", true)
	await _seconds(0.2)
	play.expect(_layer_db("pads") <= -79.0, "mute silences a layer")
	music.call(&"set_layer_muted", "pads", false)
	music.call(&"set_layer_volume", "pads", 0.0)
	music.call(&"set_layer_solo", "bass", true)
	await _seconds(0.2)
	play.expect(_layer_db("pads") <= -79.0, "solo on bass silences pads")
	music.call(&"set_layer_solo", "bass", false)
	# Calm held for seconds; once released, the drivers bring back explore.
	play.expect(music.get(&"intensity") == &"calm", "a held state stays")
	music.call(&"release")
	await _await_intensity(&"explore", 1.8)


func _music_events() -> void:
	var events: Node = play.events
	await _wait_into_bar(0.1, 0.2)
	var beat: float = 60.0 / BPM
	var stinger_player: AudioStreamPlayer = music.get_node(^"Stinger")
	# Where the stinger starts, read as it is started (the frame it is played in).
	var started: Array[float] = []
	var on_stinger: Callable = func(_event: StringName) -> void:
		started.append(stinger_player.get_playback_position())
	music.stinger_played.connect(on_stinger)
	var stingers: Dictionary = _watch_clock(music.stinger_played)
	var victory: Dictionary = _watch_clock(music.intensity_changed)
	events.level_completed.emit({"time": 1.0, "score": 0})
	play.expect(music.get(&"pending_intensity") == &"victory", "level_completed asks for victory")
	await play.wait_watched(stingers, 60)
	music.stinger_played.disconnect(on_stinger)
	_expect_on_line(stingers, beat, "the stinger")
	play.expect(
		stingers["args"] == [&"victory"], "the victory stinger played %s" % [stingers["args"]]
	)
	if not started.is_empty() and int(stingers["count"]) > 0:
		# As far in as the clock is past the beat; the mixer may have run on
		# since, never back. One sample covers the playback's whole-sample position.
		var past_beat: float = fposmod(float(stingers["at"]), beat)
		play.expect(
			started[0] >= past_beat - 1.0 / MIX_RATE,
			(
				"the stinger starts %.3f s in, as far as the beat is past (%.3f s)"
				% [started[0], past_beat]
			)
		)
	await play.process_frames(DUCK_FRAMES)
	play.expect(stinger_player.playing, "the stinger is sounding")
	play.expect_near(_layer_db("pads"), -6.0, 0.6, "layers duck 6 dB under the stinger")
	_expect_on_bar(await _await_intensity(&"victory", 1.5, victory), "victory")
	stinger_player.stop()
	await play.process_frames(DUCK_FRAMES)
	play.expect_near(_layer_db("pads"), 0.0, 0.6, "the duck lets go after the stinger")
	# The drivers leave victory alone until the next level starts.
	await _seconds(0.6)
	play.expect(music.get(&"intensity") == &"victory", "victory holds until the next level")
	var checkpoints: Dictionary = play.watch(music.stinger_played)
	events.checkpoint_reached.emit("test")
	await play.wait_watched(checkpoints, 60)
	play.expect(checkpoints["args"] == [&"checkpoint"], "a checkpoint plays its stinger")
	events.level_failed.emit("test")
	play.expect(not bool(music.call(&"stinger", &"fail")), "no fail stinger, no sound")
	events.level_started.emit(play.game.call(&"current_level"))
	await _await_intensity(&"explore", 2.5)


func _music_enemies_and_sync() -> void:
	var player: Node3D = Node3D.new()
	player.name = "MusicPlayer"
	player.add_to_group(&"player")
	world.add_child(player)
	var enemy: Node3D = Node3D.new()
	enemy.name = "MusicEnemy"
	enemy.add_to_group(&"enemy")
	world.add_child(enemy)
	enemy.position = Vector3(30, 0, 0)
	await _seconds(0.6)
	play.expect(music.get(&"intensity") == &"explore", "a far enemy keeps explore")
	enemy.position = Vector3(10, 0, 0)
	_expect_on_bar(await _await_intensity(&"danger", 1.8), "danger")
	var sections: Dictionary = _watch_clock(music.section_changed)
	enemy.position = Vector3(3, 0, 0)
	var combat: Dictionary = await _await_intensity(&"combat", 1.8)
	_expect_on_bar(combat, "combat")
	await play.wait_watched(sections, 150)
	play.expect(
		sections["args"] == [&"fight"], "combat switches to its section %s" % [sections["args"]]
	)
	play.expect(music.call(&"current_section") == &"fight", "the fight section plays")
	if int(sections["count"]) > 0 and int(combat["count"]) > 0:
		# On the same bar line, at bar 0, already as far in as the clock is past that line.
		var past_line: float = fposmod(float(combat["at"]), _bar())
		play.expect(
			sections["frame"] == combat["frame"], "the section switches on combat's bar line"
		)
		play.expect_near(
			float(sections["at"]), past_line, CLOCK_EPSILON, "a new section starts at bar 0"
		)
	await _seconds(0.3)
	_expect_sync("after a section switch")
	# Several seconds and two loops of the section later, still sample-locked.
	var bars: Dictionary = play.watch(music.bar_started)
	for _i: int in 6:
		await _seconds(_section_seconds() * 0.37)
		_expect_sync("over loops")
	play.expect(play.fire_count(bars) >= 4, "bars keep coming (%d)" % play.fire_count(bars))
	play.unwatch(bars)
	enemy.queue_free()
	player.queue_free()
	await _await_intensity(&"explore", 2.5)


func _music_health() -> void:
	var script: GDScript = GDScript.new()
	script.source_code = HEALTH_SOURCE
	play.expect(script.reload() == OK, "the health probe compiles")
	var player: Node3D = Node3D.new()
	player.set_script(script)
	player.add_to_group(&"player")
	world.add_child(player)
	await _seconds(0.6)
	play.expect(music.get(&"intensity") == &"explore", "full health keeps explore")
	player.set(&"health", 2.0)
	_expect_on_bar(await _await_intensity(&"danger", 1.8), "low health")
	player.set(&"health", 10.0)
	await _await_intensity(&"explore", 1.8)
	player.queue_free()


# --- building blocks -----------------------------------------------------------


func _sound_cue() -> void:
	var coin: Node3D = Node3D.new()
	coin.name = "Coin"
	coin.add_user_signal("collected", [{"name": "body", "type": TYPE_OBJECT}])
	world.add_child(coin)
	coin.position = Vector3(2, 1, 0)
	var cue: SoundCue = SoundCue.new()
	cue.stream = _wav(0.5, 990.0, false)
	cue.trigger = &"collected"
	coin.add_child(cue)
	var plays: Dictionary = play.watch(cue.played)
	var stranger: Node = Node.new()
	coin.emit_signal(&"collected", stranger)
	play.expect(play.fire_count(plays) == 0, "a body outside only_group plays nothing")
	var hero: Node = Node.new()
	hero.add_to_group(&"player")
	coin.emit_signal(&"collected", hero)
	play.expect(play.fire_count(plays) == 1, "the cue plays on its parent's signal")
	var args: Array = plays["args"]
	play.unwatch(plays)
	stranger.free()
	hero.free()
	coin.queue_free()
	await play.process_frames(6)
	play.expect(not is_instance_valid(coin), "the coin is gone")
	var sound: AudioStreamPlayer3D = args[0] as AudioStreamPlayer3D if not args.is_empty() else null
	play.expect(sound != null and is_instance_valid(sound), "the cue outlives its parent")
	if sound != null and is_instance_valid(sound):
		play.expect(sound.playing, "and keeps playing")
		play.expect(sound.bus == &"SFX", "on the SFX bus")
		play.expect(
			sound.global_position.distance_to(world.global_position + Vector3(2, 1, 0)) < 0.01,
			"where the coin was"
		)
	await _seconds(0.7)
	play.expect(sound == null or not is_instance_valid(sound), "the cue frees itself when done")


func _footsteps_and_ambience() -> void:
	var ground: StaticBody3D = StaticBody3D.new()
	ground.name = "Stone"
	ground.set_meta(&"surface", "stone")
	var floor_shape: CollisionShape3D = CollisionShape3D.new()
	var box: BoxShape3D = BoxShape3D.new()
	box.size = Vector3(80, 1, 80)
	floor_shape.shape = box
	ground.add_child(floor_shape)
	world.add_child(ground)
	ground.position = Vector3(0, -0.5, 0)

	var zone: AmbienceZone = AmbienceZone.new()
	zone.stream = _wav(1.0, 110.0, true)
	zone.fade_seconds = 0.3
	var zone_shape: CollisionShape3D = CollisionShape3D.new()
	var zone_box: BoxShape3D = BoxShape3D.new()
	zone_box.size = Vector3(4, 4, 4)
	zone_shape.shape = zone_box
	zone.add_child(zone_shape)
	world.add_child(zone)
	zone.position = Vector3(20, 1, 0)
	play.expect(zone.reverb_bus_enabled and zone.reverb_bus_name == &"Ambience", "zone reverb")

	var script: GDScript = GDScript.new()
	script.source_code = WALKER_SOURCE
	play.expect(script.reload() == OK, "the walker compiles")
	var walker: CharacterBody3D = CharacterBody3D.new()
	walker.set_script(script)
	walker.name = "Walker"
	walker.add_to_group(&"player")
	var body_shape: CollisionShape3D = CollisionShape3D.new()
	var capsule: CapsuleShape3D = CapsuleShape3D.new()
	capsule.radius = 0.4
	capsule.height = 1.8
	body_shape.shape = capsule
	walker.add_child(body_shape)
	var steps: Footsteps = Footsteps.new()
	var thud: AudioStreamWAV = _wav(0.08, 140.0, false)
	steps.surfaces = {"default": thud, "stone": _wav(0.08, 400.0, false)}
	steps.jump = thud
	steps.land = thud
	walker.add_child(steps)
	world.add_child(walker)
	walker.position = Vector3(0, 1.0, 0)
	await play.physics_frames(30)
	play.expect(walker.is_on_floor(), "the walker stands on the stone")

	var stepped: Dictionary = play.watch(steps.stepped)
	walker.set(&"drive", Vector3(5, 0, 0))
	await play.physics_frames(72)
	walker.set(&"drive", Vector3.ZERO)
	var count: int = play.fire_count(stepped)
	# 1.2 s at 5 m/s is 6 m: three 1.7 m strides.
	play.expect(count == 3, "a step every stride (%d steps over 6 m)" % count)
	play.expect(
		stepped["args"] == ["stone"], "steps read the floor's surface %s" % [stepped["args"]]
	)
	play.unwatch(stepped)
	var footstep_player: AudioStreamPlayer3D = walker.get_node_or_null(^"FootstepPlayer")
	play.expect(footstep_player != null and footstep_player.bus == &"SFX", "steps on the SFX bus")

	var jumped: Dictionary = play.watch(steps.jumped)
	var landed: Dictionary = play.watch(steps.landed)
	walker.set(&"jump_now", true)
	await play.wait_watched(jumped, 30)
	await play.wait_watched(landed, 120)

	# Into the ambience zone and out again.
	var faded_in: Dictionary = play.watch(zone.faded_in)
	walker.position = Vector3(20, 0.9, 0)
	await play.wait_watched(faded_in, 60)
	play.expect(zone.is_active(), "the zone knows the player is inside")
	play.expect(zone.audio_player().playing, "the ambience plays")
	play.expect_near(zone.level(), 1.0, 0.001, "faded all the way in")
	play.expect_near(zone.audio_player().volume_db, zone.volume_db, 0.01, "at the zone's volume")
	play.expect(zone.audio_player().bus == &"Ambience", "on the Ambience bus")
	var faded_out: Dictionary = play.watch(zone.faded_out)
	walker.position = Vector3(0, 0.9, 0)
	await play.physics_frames(4)
	play.expect(zone.level() < 1.0, "fading out (%.2f)" % zone.level())
	await play.wait_watched(faded_out, 60)
	play.expect(not zone.is_active(), "the zone knows the player left")
	play.expect(not zone.audio_player().playing, "the ambience stops after its fade")
	walker.queue_free()
	zone.queue_free()
	ground.queue_free()


func _ui_sounds() -> void:
	ui_sounds.set(&"click", _wav(0.05, 1200.0, false))
	ui_sounds.set(&"hover", _wav(0.05, 1500.0, false))
	var layer: CanvasLayer = CanvasLayer.new()
	layer.layer = 100
	var button: Button = Button.new()
	button.text = "Test"
	button.position = Vector2(40, 40)
	button.size = Vector2(160, 48)
	layer.add_child(button)
	root.add_child(layer)
	await play.process_frames(2)
	var sounds: Dictionary = play.watch(ui_sounds.played)
	await play.click_control(button)
	await play.process_frames(2)
	play.expect(play.fire_count(sounds) >= 1, "a button press makes a sound")
	play.unwatch(sounds)
	var clicks: Dictionary = play.watch(ui_sounds.played)
	button.pressed.emit()
	play.expect(clicks["args"] == [&"click"], "pressed plays the click %s" % [clicks["args"]])
	play.unwatch(clicks)
	var hovers: Dictionary = play.watch(ui_sounds.played)
	button.release_focus()
	button.grab_focus()
	play.expect(hovers["args"] == [&"hover"], "focus plays the hover sound %s" % [hovers["args"]])
	play.unwatch(hovers)
	var voice: AudioStreamPlayer = ui_sounds.get_node(^"Voice0")
	play.expect(voice.bus == &"UI", "interface sounds use the UI bus")
	layer.queue_free()
