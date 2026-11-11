extends RefCounted
## Watches a running game for trouble while the playtest bot plays it: frame-time spikes,
## memory and node counts over time, a player who falls out of the world or is flung by
## physics, respawns, and a game that stops answering the player (a softlock).
##
## It sends no input (bot_drive.gd plays) and judges little: the numbers go into the bot's
## report and joinery/playtest.py decides what is worth telling the user.

## Frames after a scene change that are loading, not playing: no spikes, no softlocks.
const SETTLE_FRAMES := 90
## A frame slower than this many milliseconds, and this many times the median, is a spike.
const SPIKE_MS := 50.0
const SPIKE_FACTOR := 5.0
const SPIKE_LIMIT := 40
## Memory and node counts are sampled this often (physics frames).
const SAMPLE_EVERY := 300
## How far below its start a player may fall before it counts as out of the world.
const FALL_3D := 60.0
const FALL_2D := 6000.0
## Seconds out of the world before the game is blamed for not bringing the player back.
const OUT_SECONDS := 4.0
## A move this long in one frame is a respawn or teleport.
const JUMP_3D := 6.0
const JUMP_2D := 320.0
## Speeds no game means: physics has blown up.
const FLUNG_3D := 400.0
const FLUNG_2D := 40000.0
## Coverage cells: how much of the world the player has been to.
const CELL_3D := 2.0
const CELL_2D := 64.0

var fps: int = 60
var softlock_frames: int = 900
var frame_ms: PackedFloat32Array = PackedFloat32Array()
var spikes: Array[Dictionary] = []
var samples: Array[Dictionary] = []
var respawns: int = 0
var cells: Dictionary = {}

var _last_usec: int = 0
var _settle_until: int = SETTLE_FRAMES
var _frame: int = 0
var _player: Node = null
var _start: Vector3 = Vector3.ZERO
var _last: Vector3 = Vector3.ZERO
var _is_3d: bool = false
var _out_since: int = -1
var _progress: int = 0
var _window_cells: Dictionary = {}
var _moved_frame: int = 0
var _held_frames: int = 0
var _state_key: String = ""
var _reported: Dictionary = {}


func _init(ticks: int, softlock_seconds: float) -> void:
	fps = maxi(ticks, 1)
	softlock_frames = maxi(int(softlock_seconds * fps), fps)


## Once per rendered frame: how long the last frame took.
func tick_process() -> void:
	var now := Time.get_ticks_usec()
	if _last_usec > 0 and _frame > _settle_until:
		var ms := float(now - _last_usec) / 1000.0
		frame_ms.append(ms)
		if ms > SPIKE_MS and ms > SPIKE_FACTOR * median() and spikes.size() < SPIKE_LIMIT:
			spikes.append({"frame": _frame, "ms": snappedf(ms, 0.1)})
	_last_usec = now


## A new scene or level started: loading frames are not judged.
func scene_changed(frame: int) -> void:
	_settle_until = frame + SETTLE_FRAMES
	_progress = frame
	_out_since = -1
	_window_cells.clear()


## Something happened in the game (a GameEvents signal, a score, a menu): progress.
func progress(frame: int) -> void:
	_progress = frame


## The bot plays [param player] now (null for a game without one).
func follow(player: Node, frame: int) -> void:
	_player = player
	_is_3d = player is Node3D
	_out_since = -1
	_moved_frame = frame
	if player != null:
		_start = _position(player)
		_last = _start


func median() -> float:
	if frame_ms.size() < 30:
		return 16.0
	var tail := frame_ms.slice(maxi(0, frame_ms.size() - 240))
	tail.sort()
	return tail[tail.size() / 2]


## One physics frame. [param moving]: the bot holds a movement action; [param state] is a
## short text that changes whenever anything on screen the player can act on changes;
## [param interactive]: the game gave the bot anything to press or click at all.
## Returns new findings: {kind, detail, …}.
func observe(
	frame: int, moving: bool, paused: bool, state: String, interactive: bool = true
) -> Array[Dictionary]:
	_frame = frame
	var found: Array[Dictionary] = []
	if frame % SAMPLE_EVERY == 0:
		samples.append(_sample(frame))
	if state != _state_key:
		_state_key = state
		_progress = frame
	if _player != null and is_instance_valid(_player) and _player.is_inside_tree():
		found.append_array(_watch_player(frame, moving, paused))
	if frame <= _settle_until or frame - _progress < softlock_frames:
		return found
	if _player == null and not interactive and not paused:
		# Nothing to play yet (a blank game): not stuck, just empty.
		return found
	_push(found, _softlock(frame, paused))
	return found


func summary() -> Dictionary:
	var sorted := frame_ms.duplicate()
	sorted.sort()
	var count := sorted.size()
	return {
		"frames_timed": count,
		"frame_ms_p50": snappedf(sorted[count / 2], 0.01) if count else 0.0,
		"frame_ms_p95":
		snappedf(sorted[mini(count - 1, int(count * 0.95))], 0.01) if count else 0.0,
		"frame_ms_p99":
		snappedf(sorted[mini(count - 1, int(count * 0.99))], 0.01) if count else 0.0,
		"frame_ms_max": snappedf(sorted[count - 1], 0.01) if count else 0.0,
		"spikes": spikes,
		"samples": samples,
		"respawns": respawns,
		"cells": cells.size(),
	}


## The softlock found at [param frame] was dealt with: start counting again.
func recovered(frame: int) -> void:
	_progress = frame
	_held_frames = 0
	_window_cells.clear()


func _watch_player(frame: int, moving: bool, paused: bool) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	var at := _position(_player)
	if not (is_finite(at.x) and is_finite(at.y) and is_finite(at.z)):
		_push(found, _once("physics", "The player's position became NaN or infinite.", frame))
		return found
	var step := at.distance_to(_last)
	if step > (JUMP_3D if _is_3d else JUMP_2D):
		respawns += 1
		_progress = frame
		_out_since = -1
	_last = at
	var speed := _speed(_player)
	if speed > (FLUNG_3D if _is_3d else FLUNG_2D):
		_push(
			found,
			_once("physics", "The player was flung at %d units/s; physics blew up." % speed, frame)
		)
	var fallen := at.y < _start.y - FALL_3D if _is_3d else at.y > _start.y + FALL_2D
	if fallen and not paused:
		if _out_since < 0:
			_out_since = frame
		elif frame - _out_since == int(OUT_SECONDS * fps):
			_push(
				found,
				_once(
					"fell",
					"The player fell out of the world and nothing brought them back.",
					_out_since
				)
			)
	else:
		_out_since = -1
	var cell := _cell(at)
	if not cells.has(cell):
		cells[cell] = frame
		_progress = frame
	_window_cells[cell] = frame
	if step > (0.02 if _is_3d else 0.5):
		_moved_frame = frame
		_held_frames = 0
	elif moving and not paused:
		# Frames the bot held a movement key and the player stayed put.
		_held_frames += 1
	return found


func _softlock(frame: int, paused: bool) -> Dictionary:
	var seconds := int(softlock_frames / fps)
	if paused:
		return _once(
			"softlock",
			"The game stayed paused for %d s and no input brought it back." % seconds,
			_progress
		)
	if _player != null and is_instance_valid(_player):
		var still := frame - _moved_frame
		if still >= softlock_frames / 2 and _held_frames >= softlock_frames / 3:
			return _once(
				"softlock",
				(
					"The player couldn't move for %d s while the bot held movement keys."
					% int(still / fps)
				),
				_moved_frame
			)
		var recent := 0
		for cell: Variant in _window_cells:
			if frame - int(_window_cells[cell]) < softlock_frames:
				recent += 1
		if recent <= 2:
			return _once(
				"softlock",
				"The player was trapped in one spot for %d s with nothing happening." % seconds,
				_progress
			)
		return {}
	return _once(
		"softlock", "Nothing in the game responded to input for %d s." % seconds, _progress
	)


static func _push(found: Array[Dictionary], item: Dictionary) -> void:
	if not item.is_empty():
		found.append(item)


## A finding, once per kind and detail: a softlock that keeps on is one softlock.
func _once(kind: String, detail: String, frame: int) -> Dictionary:
	# The same trouble again, however long it lasted this time, is still the same.
	var key := (
		kind + detail.left(detail.find(" for ")) if detail.contains(" for ") else kind + detail
	)
	if _reported.has(key):
		return {}
	_reported[key] = true
	return {"kind": kind, "detail": detail, "frame": frame}


func _sample(frame: int) -> Dictionary:
	return {
		"frame": frame,
		"static_mb": snappedf(OS.get_static_memory_usage() / 1048576.0, 0.01),
		"nodes": int(Performance.get_monitor(Performance.OBJECT_NODE_COUNT)),
		"orphans": int(Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT)),
		"objects": int(Performance.get_monitor(Performance.OBJECT_COUNT)),
	}


func _cell(at: Vector3) -> Vector3i:
	var size := CELL_3D if _is_3d else CELL_2D
	return Vector3i(floori(at.x / size), floori(at.y / size), floori(at.z / size))


static func _position(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at := (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	if node is Control:
		var at := (node as Control).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO


static func _speed(node: Node) -> float:
	if node is CharacterBody3D:
		return (node as CharacterBody3D).velocity.length()
	if node is CharacterBody2D:
		return (node as CharacterBody2D).velocity.length()
	if node is RigidBody3D:
		return (node as RigidBody3D).linear_velocity.length()
	if node is RigidBody2D:
		return (node as RigidBody2D).linear_velocity.length()
	return 0.0
