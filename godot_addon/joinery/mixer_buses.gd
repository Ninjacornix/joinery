@tool
extends RefCounted
## The editor's audio buses, live: what the Mixer tab reads from and writes to the AudioServer.
##
## A bus's state is {name, volume_db, mute, solo, bypass_fx, send, reverb, compressor}, where
## reverb is {enabled, wet} and compressor {enabled, threshold, sidechain} for the bus's first
## effect of that kind, or null when it has none. Changes play at once; the file is written only
## by Save mix, as a reviewed change (bridge/mixer_save.py writes it with joinery/mixer.py).
##
## Godot's own Audio panel saves the layout file whenever the AudioServer reports an edit.
## Every change here ends with [method settle], which hands the server its own layout back and
## so clears that flag: the live mix is heard but never written behind the review's back.

## The game core's buses, in index order.
const STANDARD: Array[String] = ["Master", "Music", "SFX", "Ambience", "UI", "Voice"]
## What music may duck under ("" = off).
const DUCK_SOURCES: Array[String] = ["", "Voice", "SFX"]
## The fader's range in dB; the bottom silences the bus.
const MIN_DB := -60.0
const MAX_DB := 6.0
const SILENT_DB := -80.0
## The compressor amount's range (threshold in dB).
const MIN_THRESHOLD := -48.0
## Defaults the game core uses for a new reverb and the ducking compressor.
const ROOM := {"room_size": 0.6, "damping": 0.5, "wet": 0.25, "dry": 1.0}
const DUCKING := {
	"threshold": -24.0, "ratio": 4.0, "attack_us": 20000.0, "release_ms": 250.0, "gain": 0.0
}


## The project's bus layout file, res:// (audio/buses/default_bus_layout).
static func layout_path() -> String:
	var path := str(
		ProjectSettings.get_setting(
			"audio/buses/default_bus_layout", "res://default_bus_layout.tres"
		)
	)
	if path.begins_with("uid://"):
		var id := ResourceUID.text_to_id(path)
		path = ResourceUID.get_id_path(id) if ResourceUID.has_id(id) else ""
	return path if not path.is_empty() else "res://default_bus_layout.tres"


## Every bus's state, in index order.
static func read() -> Array[Dictionary]:
	var buses: Array[Dictionary] = []
	for index: int in AudioServer.bus_count:
		buses.append(bus_state(index))
	return buses


static func bus_state(index: int) -> Dictionary:
	var state := {
		"name": str(AudioServer.get_bus_name(index)),
		"volume_db": snappedf(AudioServer.get_bus_volume_db(index), 0.1),
		"mute": AudioServer.is_bus_mute(index),
		"solo": AudioServer.is_bus_solo(index),
		"bypass_fx": AudioServer.is_bus_bypassing_effects(index),
		"send": str(AudioServer.get_bus_send(index)) if index > 0 else "",
		"reverb": null,
		"compressor": null,
	}
	var room := _slot(index, "AudioEffectReverb")
	if room >= 0:
		var reverb := AudioServer.get_bus_effect(index, room) as AudioEffectReverb
		state["reverb"] = {
			"enabled": AudioServer.is_bus_effect_enabled(index, room),
			"wet": snappedf(reverb.get_wet(), 0.01),
		}
	var squeeze := _slot(index, "AudioEffectCompressor")
	if squeeze >= 0:
		var compressor := AudioServer.get_bus_effect(index, squeeze) as AudioEffectCompressor
		state["compressor"] = {
			"enabled": AudioServer.is_bus_effect_enabled(index, squeeze),
			"threshold": snappedf(compressor.get_threshold(), 0.1),
			"sidechain": str(compressor.get_sidechain()),
		}
	return state


## Play [param state] on its bus (found by name). Adds a reverb or compressor the bus lacks
## when the state turns one on.
static func apply(state: Dictionary) -> void:
	var index := AudioServer.get_bus_index(str(state.get("name", "")))
	if index < 0:
		return
	var db := float(state.get("volume_db", 0.0))
	AudioServer.set_bus_volume_db(index, SILENT_DB if db <= MIN_DB else db)
	AudioServer.set_bus_mute(index, bool(state.get("mute", false)))
	AudioServer.set_bus_solo(index, bool(state.get("solo", false)))
	AudioServer.set_bus_bypass_effects(index, bool(state.get("bypass_fx", false)))
	var reverb: Variant = state.get("reverb")
	if reverb is Dictionary:
		var slot := _ensure(index, "AudioEffectReverb", bool(reverb.get("enabled", false)))
		if slot >= 0:
			var effect := AudioServer.get_bus_effect(index, slot) as AudioEffectReverb
			effect.set_wet(clampf(float(reverb.get("wet", effect.get_wet())), 0.0, 1.0))
			AudioServer.set_bus_effect_enabled(index, slot, bool(reverb.get("enabled", false)))
	var compressor: Variant = state.get("compressor")
	if compressor is Dictionary:
		var on := bool(compressor.get("enabled", false))
		var slot := _ensure(index, "AudioEffectCompressor", on)
		if slot >= 0:
			var effect := AudioServer.get_bus_effect(index, slot) as AudioEffectCompressor
			var threshold := float(compressor.get("threshold", effect.get_threshold()))
			effect.set_threshold(clampf(threshold, -60.0, 0.0))
			effect.set_sidechain(
				StringName(str(compressor.get("sidechain", effect.get_sidechain())))
			)
			AudioServer.set_bus_effect_enabled(index, slot, on)
	settle()


## Put back every bus in [param buses] (states from [method read]).
static func apply_all(buses: Array) -> void:
	for state: Variant in buses:
		if state is Dictionary:
			apply(state)


## Read [param path] (a saved AudioBusLayout) into the server; false when it isn't one.
static func load_layout(path: String) -> bool:
	if not FileAccess.file_exists(path):
		return false
	var layout := ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE) as AudioBusLayout
	if layout == null:
		return false
	AudioServer.set_bus_layout(layout)
	return true


## What music ducks under in [param buses]: "Voice", "SFX" or "" (the Music compressor's
## sidechain while it is on).
static func ducking(buses: Array) -> String:
	for state: Variant in buses:
		if state is Dictionary and str(state.get("name", "")) == "Music":
			var squeeze: Variant = state.get("compressor")
			if squeeze is Dictionary and bool(squeeze.get("enabled", false)):
				return str(squeeze.get("sidechain", ""))
	return ""


## Duck music under [param source]. "" turns it off: a compressor keyed to another bus is
## switched off (it keeps its settings); one that only compresses the music stays. Returns the
## Music bus's new state, or {} without a Music bus.
static func set_ducking(buses: Array, source: String) -> Dictionary:
	for state: Variant in buses:
		if not state is Dictionary or str(state.get("name", "")) != "Music":
			continue
		var squeeze: Variant = state.get("compressor")
		var keyed := squeeze is Dictionary and not str(squeeze.get("sidechain", "")).is_empty()
		if source.is_empty() and not keyed:
			return state
		if not squeeze is Dictionary:
			squeeze = {"enabled": false, "threshold": DUCKING["threshold"], "sidechain": ""}
			state["compressor"] = squeeze
		squeeze["sidechain"] = source
		squeeze["enabled"] = not source.is_empty()
		apply(state)
		return state
	return {}


## The game core's buses that the server lacks.
static func missing() -> Array[String]:
	var names: Array[String] = []
	for name: String in STANDARD:
		if AudioServer.get_bus_index(name) < 0:
			names.append(name)
	return names


## Add the game core's buses the server lacks, after the others, set up as the core sets them
## (Music's ducking compressor, Ambience's reverb). Returns their names.
static func add_missing() -> Array[String]:
	var added := missing()
	for name: String in added:
		AudioServer.add_bus()
		var index := AudioServer.bus_count - 1
		AudioServer.set_bus_name(index, name)
		AudioServer.set_bus_send(index, &"Master")
		if name == "Music":
			var squeeze := AudioEffectCompressor.new()
			for key: String in DUCKING:
				squeeze.call("set_" + key, DUCKING[key])
			squeeze.set_sidechain(&"Voice")
			AudioServer.add_bus_effect(index, squeeze)
		elif name == "Ambience":
			AudioServer.add_bus_effect(index, _room())
	settle()
	return added


## The bus's peak level now, in dB (the louder channel).
static func peak_db(index: int) -> float:
	if index < 0 or index >= AudioServer.bus_count or AudioServer.get_bus_channels(index) < 1:
		return SILENT_DB
	return maxf(
		AudioServer.get_bus_peak_volume_left_db(index, 0),
		AudioServer.get_bus_peak_volume_right_db(index, 0)
	)


## The bus layout part of the save message.
static func message(buses: Array, path: String) -> Dictionary:
	var rows: Array = []
	for state: Variant in buses:
		if not state is Dictionary:
			continue
		var row := (state as Dictionary).duplicate(true)
		for key: String in ["reverb", "compressor"]:
			if row.get(key) == null:
				row.erase(key)
		if row.has("compressor"):
			(row["compressor"] as Dictionary).erase("sidechain")
		rows.append(row)
	return {"path": path, "buses": rows, "ducking": ducking(buses)}


## Hand the server its own layout back: the sound stays, and Godot's Audio panel no longer
## sees an edit to save.
static func settle() -> void:
	AudioServer.set_bus_layout(AudioServer.generate_bus_layout())


static func _slot(index: int, type: String) -> int:
	for slot: int in AudioServer.get_bus_effect_count(index):
		if AudioServer.get_bus_effect(index, slot).is_class(type):
			return slot
	return -1


static func _ensure(index: int, type: String, wanted: bool) -> int:
	var slot := _slot(index, type)
	if slot >= 0 or not wanted:
		return slot
	if type == "AudioEffectReverb":
		AudioServer.add_bus_effect(index, _room())
	else:
		var squeeze := AudioEffectCompressor.new()
		for key: String in DUCKING:
			squeeze.call("set_" + key, DUCKING[key])
		AudioServer.add_bus_effect(index, squeeze)
	return AudioServer.get_bus_effect_count(index) - 1


static func _room() -> AudioEffectReverb:
	var room := AudioEffectReverb.new()
	room.set_room_size(ROOM["room_size"])
	room.set_damping(ROOM["damping"])
	room.set_wet(ROOM["wet"])
	room.set_dry(ROOM["dry"])
	return room
