class_name AmbienceZone
extends Area3D
## A looping ambience bed (wind, a cave's drip, a market) that fades in while a
## `player` body is inside the area and fades out when the last one leaves.
## Give it a CollisionShape3D and a looping `stream`; silent until it has one.
## Sounds inside the zone also go through the Ambience bus's reverb
## (`reverb_bus_*`), so footsteps in a cave sound like a cave.

signal faded_in
signal faded_out

const SILENT_DB := -80.0

@export var stream: AudioStream
@export_range(-60.0, 12.0, 0.1, "suffix:dB") var volume_db: float = -6.0
@export var fade_seconds: float = 1.5
@export var bus: StringName = &"Ambience"
@export var player_group: StringName = &"player"

var _player: AudioStreamPlayer
var _inside: Array[Node] = []
var _tween: Tween
## 0 (silent) to 1 (at volume_db), faded linearly.
var _level: float = 0.0


func _init() -> void:
	reverb_bus_enabled = true
	reverb_bus_name = &"Ambience"
	reverb_bus_amount = 0.4


func _ready() -> void:
	_player = AudioStreamPlayer.new()
	_player.name = "Ambience"
	_player.bus = bus
	_player.volume_db = SILENT_DB
	add_child(_player)
	body_entered.connect(_on_body_entered)
	body_exited.connect(_on_body_exited)


## True while a player is inside (the bed is playing or fading in).
func is_active() -> bool:
	return not _inside.is_empty()


## How far the bed is faded in: 0 silent, 1 at volume_db.
func level() -> float:
	return _level


## The AudioStreamPlayer that carries the bed.
func audio_player() -> AudioStreamPlayer:
	return _player


func _on_body_entered(body: Node) -> void:
	if not player_group.is_empty() and not body.is_in_group(player_group):
		return
	if _inside.has(body):
		return
	_inside.append(body)
	if _inside.size() == 1:
		_fade(true)


func _on_body_exited(body: Node) -> void:
	if not _inside.has(body):
		return
	_inside.erase(body)
	if _inside.is_empty():
		_fade(false)


func _fade(fade_in: bool) -> void:
	if _tween != null:
		_tween.kill()
	if fade_in:
		if stream == null:
			faded_in.emit()
			return
		_player.stream = stream
		if not _player.playing:
			_set_level(0.0)
			_player.play()
	_tween = create_tween()
	var target: float = 1.0 if fade_in else 0.0
	var seconds: float = maxf(fade_seconds * absf(target - _level), 0.01)
	_tween.tween_method(_set_level, _level, target, seconds)
	if fade_in:
		_tween.tween_callback(faded_in.emit)
	else:
		_tween.tween_callback(_player.stop)
		_tween.tween_callback(faded_out.emit)


func _set_level(value: float) -> void:
	_level = value
	_player.volume_db = volume_db + linear_to_db(value) if value > 0.0001 else SILENT_DB
