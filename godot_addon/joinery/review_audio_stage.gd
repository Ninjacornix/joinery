@tool
extends MarginContainer
## A sound in review (review_media.gd): one compact audio_preview, so Before and After play A/B
## at the same position, with the review's side tags over it. Keeps the streams it loaded.

## The audio preview in use, or null.
var preview: Control
var _streams := {}


func _init() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	hide()


## Show [param stream]; with [param swap] playback keeps its position (Before/After).
func play_stream(stream: AudioStream, swap: bool) -> void:
	if preview == null:
		preview = _make(stream)
	elif preview.has_method("set_stream"):
		preview.call("set_stream", stream, swap)


## Stop playback, if any.
func stop() -> void:
	if preview == null:
		return
	if preview.has_method("stop"):
		preview.call("stop")
	var player: Variant = preview.get("player")
	if player is AudioStreamPlayer and is_instance_valid(player):
		(player as AudioStreamPlayer).stop()


## Stop, drop the preview and the loaded streams.
func forget() -> void:
	stop()
	if preview != null:
		preview.queue_free()
		preview = null
	_streams.clear()


## The sound at [param source] (WAV, OGG or MP3), loaded once; null when it can't be read.
func stream(source: String) -> AudioStream:
	if _streams.has(source):
		return _streams[source]
	var result: AudioStream = null
	match source.get_extension().to_lower():
		"wav":
			result = AudioStreamWAV.load_from_file(source)
		"ogg":
			result = AudioStreamOggVorbis.load_from_file(source)
		"mp3":
			result = AudioStreamMP3.load_from_file(source)
	if result != null:
		_streams[source] = result
	return result


## A compact audio_preview (it owns its player). Falls back to handing it a player for the
## older preview that needs one before it enters the tree.
func _make(first: AudioStream) -> Control:
	var here := (get_script() as Script).resource_path.get_base_dir()
	var script: Script = load(here.path_join("audio_preview.gd"))
	var made: Control = script.new()
	made.size_flags_vertical = Control.SIZE_EXPAND_FILL
	if "compact" in made:
		made.set("compact", true)
	if made.has_method("set_stream"):
		add_child(made)
		made.call("set_stream", first, false)
		return made
	var player := AudioStreamPlayer.new()
	player.stream = first
	add_child(player)
	made.set("player", player)
	add_child(made)
	return made
