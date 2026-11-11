@tool
extends RefCounted
## An adaptive music track's description, as the Mixer and the review read it.
##
## A track is a plain resource in res://music/<name>.tres whose metadata `joinery_music` holds
## the layers, sections, intensity states and stems (joinery/music_track.py writes it). This
## script only reads it and works out how loud each layer plays, the same way the game core's
## Music autoload does: layer gain = db_to_linear(layer volume_db) × the state's level for the
## layer (missing = 0); a muted layer is silent; with any layer soloed only soloed layers sound.

const FOLDER := "res://music"
const META := "joinery_music"
## Intensity states, quietest first.
const STATES: Array[String] = ["calm", "explore", "danger", "combat", "victory"]


## The track descriptions in res://music, sorted by path.
static func find_tracks() -> PackedStringArray:
	var found := PackedStringArray()
	if not DirAccess.dir_exists_absolute(FOLDER):
		return found
	for file: String in DirAccess.get_files_at(FOLDER):
		var path := FOLDER.path_join(file)
		if file.get_extension() == "tres" and not read(path).is_empty():
			found.append(path)
	found.sort()
	return found


## The description of the track at [param path] ({} when it is not one). Read fresh from the
## file, never from the resource cache, so a change accepted a moment ago is what shows.
static func read(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var resource := ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
	if resource == null or not resource.has_meta(META):
		return {}
	var data: Variant = resource.get_meta(META)
	if not data is Dictionary or not (data as Dictionary).get("layers") is Array:
		return {}
	return data


## The track whose description or stems [param path] (res:// or project-relative) belongs to:
## music/<name>.tres itself, or a stem under audio/music/<name>/. "" for anything else.
static func track_of(path: String) -> String:
	var relative := path.trim_prefix("res://")
	if relative.begins_with("music/") and relative.get_extension() == "tres":
		return "res://" + relative
	if relative.begins_with("audio/music/") and relative.count("/") >= 3:
		return FOLDER.path_join(relative.split("/")[2] + ".tres")
	return ""


## The layers as {name: {volume_db, muted, solo}}.
static func mix_of(track: Dictionary) -> Dictionary:
	var mix := {}
	for layer: Variant in track.get("layers", []):
		if layer is Dictionary:
			mix[str(layer.get("name", ""))] = {
				"volume_db": float(layer.get("volume_db", 0.0)),
				"muted": bool(layer.get("muted", false)),
				"solo": bool(layer.get("solo", false)),
			}
	return mix


## Each layer's linear gain in [param state] with the layer settings in [param mix]
## ({name: {volume_db, muted, solo}}, as [method mix_of] gives).
static func gains(track: Dictionary, state: String, mix: Dictionary) -> Dictionary:
	var levels: Dictionary = (track.get("states", {}) as Dictionary).get(state, {}).get(
		"layers", {}
	)
	var soloed := false
	for name: String in mix:
		soloed = soloed or bool(mix[name].get("solo", false))
	var out := {}
	for name: String in mix:
		var layer: Dictionary = mix[name]
		var level := float(levels.get(name, 0.0))
		if bool(layer.get("muted", false)) or (soloed and not bool(layer.get("solo", false))):
			level = 0.0
		out[name] = level * db_to_linear(float(layer.get("volume_db", 0.0)))
	return out


## The section [param state] plays (the first section when the state names none).
static func section_of(track: Dictionary, state: String) -> String:
	var named := str((track.get("states", {}) as Dictionary).get(state, {}).get("section", ""))
	if not named.is_empty():
		return named
	var sections: Array = track.get("sections", [])
	return str(sections[0].get("name", "")) if not sections.is_empty() else ""


## The res:// path of [param layer]'s stem in [param section] ("" when there is none).
static func stem(track: Dictionary, section: String, layer: String) -> String:
	for entry: Variant in track.get("sections", []):
		if entry is Dictionary and str(entry.get("name", "")) == section:
			return str((entry.get("stems", {}) as Dictionary).get(layer, ""))
	return ""


## "112 BPM · 8 bars · D minor": the parts the description has.
static func summary(track: Dictionary) -> String:
	var parts: Array[String] = []
	var tempo := float(track.get("tempo_bpm", 0.0))
	if tempo > 0.0:
		parts.append("%d BPM" % roundi(tempo))
	var bars := 0
	for entry: Variant in track.get("sections", []):
		if entry is Dictionary:
			bars += int(entry.get("bars", 0))
	if bars > 0:
		parts.append("%d bar%s" % [bars, "" if bars == 1 else "s"])
	var key := str(track.get("key", ""))
	if not key.is_empty():
		parts.append(key)
	return " · ".join(parts)


## A gain as the Mixer writes it: "−6.0 dB", "0.0 dB", "Silent".
static func gain_text(gain: float) -> String:
	if gain <= 0.0001:
		return "Silent"
	return db_text(linear_to_db(gain))


## "−3.0 dB", "+1.5 dB", "0.0 dB" (a real minus sign, one decimal).
static func db_text(db: float) -> String:
	var shown := snappedf(db, 0.1)
	var sign := "−" if shown < 0.0 else ("+" if shown > 0.0 else "")
	return "%s%.1f dB" % [sign, absf(shown)]
