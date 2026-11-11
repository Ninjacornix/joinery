extends Node
## Plays the character model's clips from how the player moves: idle, walk, run,
## and jump_start / jump_air / jump_land. Drop a model with an AnimationPlayer
## anywhere under the player; clips it lacks are skipped, and without one this does nothing,
## so the placeholder body keeps working. Clip names may carry import prefixes
## or a loop suffix ("Armature|Run", "run_loop"). Runs on every peer from the
## synced velocity, so other players animate too.

## The clip names this player plays (blueprint hooks.animations).
const ANIMATION_NAMES: Array[StringName] = [
	&"idle", &"walk", &"run", &"jump_start", &"jump_air", &"jump_land"
]
const LOOPING: Array[StringName] = [&"idle", &"walk", &"run", &"jump_air"]
## Crossfade between clips, in seconds.
const BLEND := 0.15
## Below this horizontal speed (m/s) the player stands.
const STILL_SPEED := 0.1
const PLACE := "."

## The clip playing (one of ANIMATION_NAMES), or &"" when none plays.
var current: StringName = &""

var _body: CharacterBody3D
var _player: AnimationPlayer
var _clips: Dictionary = {}
var _airborne: bool = false
var _once: StringName = &""


func _ready() -> void:
	_body = get_parent() as CharacterBody3D
	find_player()


## Look again for the model's AnimationPlayer (after swapping the model).
## Returns true when one with at least one known clip was found.
func find_player() -> bool:
	_player = null
	_clips.clear()
	current = &""
	if _body == null:
		return false
	var place: Node = _body.get_node_or_null(PLACE)
	if place == null:
		place = _body
	var found: Array[Node] = place.find_children("*", "AnimationPlayer", true, false)
	if found.is_empty():
		return false
	_player = found[0] as AnimationPlayer
	for full: StringName in _player.get_animation_list():
		var name: StringName = clip_name(String(full))
		if name in ANIMATION_NAMES and not _clips.has(name):
			_clips[name] = full
			if name in LOOPING:
				_player.get_animation(full).loop_mode = Animation.LOOP_LINEAR
	return not _clips.is_empty()


## "Armature|Run_loop" -> &"run": the clip name without prefix, case or loop suffix.
static func clip_name(full: String) -> StringName:
	var name: String = full.get_slice("/", full.get_slice_count("/") - 1)
	name = name.get_slice("|", name.get_slice_count("|") - 1).to_lower().strip_edges()
	for suffix: String in ["_loop", "-loop", " loop"]:
		if name.ends_with(suffix):
			name = name.trim_suffix(suffix)
	return StringName(name)


func has_clip(name: StringName) -> bool:
	return _clips.has(name)


func _physics_process(_delta: float) -> void:
	if _player == null or _clips.is_empty():
		return
	var on_floor: bool = _body.is_on_floor()
	var speed: float = Vector2(_body.velocity.x, _body.velocity.z).length()
	var walk_speed: float = float(_body.get(&"walk_speed"))
	var sprint_speed: float = float(_body.get(&"sprint_speed"))
	if not _once.is_empty() and not (_player.is_playing() and current == _once):
		_once = &""
	if not on_floor:
		var started: bool = not _airborne and _body.velocity.y > 0.0 and _play_once(&"jump_start")
		if not started and _once != &"jump_start":
			_play(&"jump_air", 1.0)
		_airborne = true
		return
	# A landing plays out unless the player is already moving off.
	var slow: bool = speed < walk_speed * 0.5
	if _airborne:
		_airborne = false
		_once = &""
		if slow and _play_once(&"jump_land"):
			return
	if _once == &"jump_land" and slow:
		return
	if speed < STILL_SPEED:
		_play(&"idle", 1.0)
	elif speed > (walk_speed + sprint_speed) * 0.5:
		var run: StringName = &"run" if has_clip(&"run") else &"walk"
		_play(run, speed / (sprint_speed if run == &"run" else walk_speed))
	else:
		var walk: StringName = &"walk" if has_clip(&"walk") else &"run"
		_play(walk, speed / (walk_speed if walk == &"walk" else sprint_speed))


func _play(name: StringName, rate: float) -> bool:
	if not has_clip(name):
		return false
	_player.speed_scale = clampf(rate, 0.5, 2.0) if name in [&"walk", &"run"] else 1.0
	if current != name or not _player.is_playing():
		_player.play(_clips[name], BLEND)
		current = name
	return true


func _play_once(name: StringName) -> bool:
	if not has_clip(name):
		return false
	_player.speed_scale = 1.0
	_player.play(_clips[name], BLEND)
	current = name
	_once = name
	return true
