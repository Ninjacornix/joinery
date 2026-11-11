extends SceneTree
## The bottom panel's Mixer tab, headless, on the editor's real AudioServer. The project starts
## with only Master and a music track planted by run_widget_tests.py (music_fixture.py):
## adding the game core's buses, faders, Mute and Solo, a reverb and its amount, ducking (the
## Music compressor's sidechain), the music layers at each intensity with layer Mute and Solo,
## Save mix off without an agent and the exact message it sends with one, then Revert.

const TAB := preload("res://addons/joinery/mixer_tab.gd")
const TRACK := preload("res://addons/joinery/music_track.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
const BUSES := preload("res://addons/joinery/mixer_buses.gd")
const TRACK_PATH := "res://music/harbour.tres"

var _failed := false


## The chat bridge as far as the Mixer uses it.
class FakeBridge:
	extends Node
	signal status_changed(state: String, detail: String)
	var connected := false
	var lines: Array[Dictionary] = []

	func is_ready() -> bool:
		return connected

	func send_mixer(line: Dictionary) -> bool:
		if not connected:
			return false
		lines.append(line.duplicate(true))
		return true


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _near(a: float, b: float, tolerance := 0.01) -> bool:
	return absf(a - b) <= tolerance


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-mixer-test")
	DirAccess.make_dir_recursive_absolute(cache)
	Engine.set_meta("joinery_ui_config", cache.path_join("journeyman.cfg"))
	Engine.remove_meta("joinery_ui_prefs")
	UI_MOTION.set_enabled(false)
	root.size = Vector2i(1200, 900)
	_check_track_helpers()
	var bridge := FakeBridge.new()
	root.add_child(bridge)
	var tab: TAB = TAB.new()
	tab.bridge = bridge
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_child(tab)
	await _frames()
	tab.refresh()
	await _frames()
	_check(AudioServer.bus_count == 1, "The test project starts with Master only")
	_check(tab._missing.visible, "The missing game core buses are offered")
	_check(not tab.is_dirty(), "Nothing is unsaved after opening")
	_check(tab.save_button.disabled, "Save is off with nothing to save")
	tab.add_missing_buses()
	await _frames()
	_check(AudioServer.bus_count == 6, "Add them adds the five game core buses")
	_check(not tab._missing.visible, "The offer goes once the buses are there")
	_check(tab.is_dirty(), "Added buses are unsaved")
	_check(BUSES.ducking(tab.buses) == "Voice", "Music ducks under Voice by default")
	_check_buses(tab)
	await _check_music(tab)
	await _check_save(tab, bridge)
	tab.queue_free()
	bridge.queue_free()
	await _frames()
	print("mixer: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_track_helpers() -> void:
	_check(TRACK.find_tracks() == PackedStringArray([TRACK_PATH]), "The planted track is found")
	_check(TRACK.track_of("audio/music/harbour/main_pads.wav") == TRACK_PATH, "Stems find it")
	_check(TRACK.track_of("res://music/harbour.tres") == TRACK_PATH, "So does the description")
	_check(TRACK.track_of("audio/sfx/door.wav").is_empty(), "Other sounds are not music")
	var track := TRACK.read(TRACK_PATH)
	_check(TRACK.summary(track) == "120 BPM · 2 bars · D minor", TRACK.summary(track))
	var mix := TRACK.mix_of(track)
	mix["bass"]["solo"] = true
	var gains := TRACK.gains(track, "explore", mix)
	_check(gains["bass"] == 1.0 and gains["pads"] == 0.0, "Solo silences the other layers")
	_check(TRACK.db_text(-3.04) == "−3.0 dB" and TRACK.gain_text(0.0) == "Silent", "Words")


func _check_buses(tab: TAB) -> void:
	var sfx := AudioServer.get_bus_index(&"SFX")
	var strip := tab.strip("SFX")
	strip.fader.value = -12.0
	_check(_near(AudioServer.get_bus_volume_db(sfx), -12.0), "The fader sets the bus volume")
	strip.mute.button_pressed = true
	_check(AudioServer.is_bus_mute(sfx), "Mute mutes the bus")
	strip.mute.button_pressed = false
	strip.solo.button_pressed = true
	_check(AudioServer.is_bus_solo(sfx), "Solo solos the bus")
	strip.solo.button_pressed = false
	strip.reverb.button_pressed = true
	_check(AudioServer.get_bus_effect_count(sfx) == 1, "Reverb adds a reverb to a bare bus")
	var room := AudioServer.get_bus_effect(sfx, 0) as AudioEffectReverb
	_check(room != null and AudioServer.is_bus_effect_enabled(sfx, 0), "and turns it on")
	strip.reverb_amount.value = 0.5
	_check(room != null and _near(room.wet, 0.5), "The reverb amount is its wet mix")
	strip.reverb.button_pressed = false
	_check(not AudioServer.is_bus_effect_enabled(sfx, 0), "Reverb off keeps it, switched off")
	_check(not strip.reverb_amount.editable, "and its amount waits")
	var music := AudioServer.get_bus_index(&"Music")
	var squeeze := AudioServer.get_bus_effect(music, 0) as AudioEffectCompressor
	(tab.duck_buttons["SFX"] as Button).button_pressed = true
	_check(squeeze.sidechain == &"SFX", "Ducking under SFX keys the compressor to SFX")
	_check(AudioServer.is_bus_effect_enabled(music, 0), "with the compressor on")
	(tab.duck_buttons[""] as Button).button_pressed = true
	_check(not AudioServer.is_bus_effect_enabled(music, 0), "Off switches the ducking off")
	_check(not tab.strip("Music").compressor.button_pressed, "and the strip shows it")
	(tab.duck_buttons["Voice"] as Button).button_pressed = true
	_check(squeeze.sidechain == &"Voice", "Back under Voice")
	_check(tab.strip("Music").compressor.button_pressed, "with the strip's compressor on")


func _check_music(tab: TAB) -> void:
	var music := tab.music
	_check(music.path == TRACK_PATH, "The track is picked")
	_check(music.players.size() == 4, "One player per layer")
	_check(music.intensity == "explore", "Auditions start at the default state")
	var track := TRACK.read(TRACK_PATH)
	for player: AudioStreamPlayer in music.players.values():
		_check(player.bus == &"Music", "Stems play on the track's bus")
		_check(player.stream != null, "Each layer has its stem")
	music.intensity_slider.value = 0
	_check(music.intensity == "calm", "The slider's left end is calm")
	_check(_levels_match(music, TRACK.gains(track, "calm", music.mix)), "Calm levels")
	_check(_near(music.levels()["bass"], 0.0, 0.001), "No bass at calm")
	music.intensity_slider.value = 3
	_check(music.intensity == "combat", "Combat")
	_check(_near(music.levels()["drums"], 1.0) and _near(music.levels()["pads"], 0.7), "Combat")
	var row := music.get_node("Layers/pads") as HBoxContainer
	(row.get_node("Fader") as HSlider).value = -6.0
	_check(_near(music.levels()["pads"], 0.7 * db_to_linear(-6.0)), "The layer fader scales it")
	(row.get_node("Mute") as Button).button_pressed = true
	_check(_near(music.levels()["pads"], 0.0, 0.001), "Mute silences the layer")
	(row.get_node("Mute") as Button).button_pressed = false
	var drums := music.get_node("Layers/drums") as HBoxContainer
	(drums.get_node("Solo") as Button).button_pressed = true
	var heard := music.levels()
	_check(_near(heard["drums"], 1.0) and _near(heard["bass"], 0.0, 0.001), "Solo")
	(drums.get_node("Solo") as Button).button_pressed = false
	_check(music.is_dirty(), "Layer changes are unsaved")
	music.play()
	await _frames()
	_check(music.players.values().all(func(p: AudioStreamPlayer) -> bool: return p.playing), "")
	music.stop()
	_check(not music.is_playing(), "Stop stops every layer")


func _levels_match(music: Node, gains: Dictionary) -> bool:
	var heard: Dictionary = music.call("levels")
	for layer: String in gains:
		if not _near(float(heard[layer]), float(gains[layer]), 0.001):
			return false
	return true


func _check_save(tab: TAB, bridge: FakeBridge) -> void:
	_check(tab.save_button.disabled, "Save is off without an agent")
	_check(tab.save_blocked() == TAB.NO_AGENT, "and says why: " + tab.save_blocked())
	_check(tab._note.text == TAB.NO_AGENT, "The reason is on screen")
	bridge.connected = true
	bridge.status_changed.emit("connected", "")
	await _frames()
	_check(not tab.save_button.disabled, "Save is on once an agent is connected")
	tab.save_button.pressed.emit()
	_check(bridge.lines.size() == 1, "Save sends one message")
	var line: Dictionary = bridge.lines[0] if not bridge.lines.is_empty() else {}
	_check(line.get("kind") == "mixer_save", "of kind mixer_save")
	var layout: Dictionary = line.get("bus_layout", {})
	_check(layout.get("path") == "res://default_bus_layout.tres", "to the project's layout")
	_check(layout.get("ducking") == "Voice", "with the ducking")
	var names: Array = layout.get("buses", []).map(func(b: Dictionary) -> String: return b.name)
	_check(names == ["Master", "Music", "SFX", "Ambience", "UI", "Voice"], str(names))
	var sfx: Dictionary = layout.get("buses", [{}, {}, {}])[2]
	_check(_near(float(sfx.get("volume_db", 0.0)), -12.0), "The SFX level goes along")
	_check(sfx.get("reverb", {}).get("enabled") == false, "and its reverb, off")
	_check(_near(float(sfx.get("reverb", {}).get("wet", 0.0)), 0.5), "at its amount")
	_check(not sfx.has("compressor"), "A bus without a compressor sends none")
	var tracks: Dictionary = line.get("tracks", {})
	_check(tracks.keys() == [TRACK_PATH], "The track goes along: " + str(tracks.keys()))
	var layers: Dictionary = tracks.get(TRACK_PATH, {}).get("layers", {})
	_check(layers.keys() == ["pads"], "Only the changed layer: " + str(layers.keys()))
	_check(_near(float(layers.get("pads", {}).get("volume_db", 0.0)), -6.0), "at its level")
	_check(not tab.is_dirty(), "Saved changes are no longer unsaved")
	_check(tab._note.text == TAB.SENT, "The tab says it went to review")
	_check(tab.save_button.disabled, "and Save rests")
	tab.strip("UI").fader.value = -3.0
	_check(tab.is_dirty() and tab.message().keys() == ["kind", "bus_layout"], "Only buses")
	tab.revert()
	_check(AudioServer.bus_count == 1, "Revert puts back the files: no layout, only Master")
	_check(not tab.is_dirty(), "Nothing is unsaved after Revert")
