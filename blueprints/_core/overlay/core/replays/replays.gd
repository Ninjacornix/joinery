# gdlint: disable=max-public-methods
extends Node
## Records a play session (every input per physics frame, the random seed, the start
## scene and the build) and replays it, so a session becomes a regression test.
##
## When it records: always in debug builds (editor runs and tests) and, in exported
## release builds, only when the game opts in (`game/replays/record_in_release`). The
## recording stays in memory, so a bug report can carry it; `-- --joinery-record` (or
## `--joinery-record=<name>`) also writes it to `user://joinery_replays/<name>.replay`
## as it goes. "Record this play" in the editor and `joinery playtest --record` pass it.
##
## Replaying: `-- --joinery-replay=<file>` plays the file's input back on the same frames
## with the same seed, compares the state at each checkpoint, prints one
## `JOINERY_REPLAY {json}` line and quits (`joinery replay run` does all this). Run it
## headless at the recorded `--fixed-fps` for the same frames.
##
## The file is JSON lines: a header, then `{"f": frame, "e": [events]}`,
## `{"f": frame, "check": {hash, state}}`, and an `{"end": ...}` line. An event's `t`
## names its kind; `"t": "call"` is a call made through `perform`. Frames count physics
## frames from the first one with a scene.
##
## What is replayed exactly, and what is not, is in docs/adr/0059.

signal recording_started(path: String)

enum Mode { OFF, RECORD, REPLAY }

const ReplayEvents := preload("res://core/replays/replay_events.gd")
const ReplayState := preload("res://core/replays/replay_state.gd")
const GameLog := preload("res://core/replays/game_log.gd")

const FORMAT := "joinery-replay"
const FORMAT_VERSION := 1
const FOLDER := "user://joinery_replays"
const RECORD_ARG := "--joinery-record"
const REPLAY_ARG := "--joinery-replay="
const OUT_ARG := "--joinery-replay-out="
const SEED_ARG := "--joinery-seed="
const PACE_ARG := "--joinery-pace"
const RESULT_PREFIX := "JOINERY_REPLAY "
const RECORD_IN_RELEASE := "game/replays/record_in_release"
const MAX_MINUTES := "game/replays/max_minutes"
const VERSION_SETTING := "game/joinery_version"
## Physics frames between checkpoints written to a file, and kept in memory only.
const CHECK_EVERY := 60
const CHECK_EVERY_MEMORY := 300
## User arguments that belong to a test, the bot or Joinery, not to the game.
const NOT_GAME_ARGS: Array[String] = [
	"--joinery-",
	"--settings-path=",
	"--saves-dir=",
	"--playtest-out=",
	"--seed=",
	"--seconds=",
	"--softlock=",
	"--focus=",
	"--shots=",
	"--route=",
	"--probe=",
	"--luma",
	"--scene=",
	"--out=",
]
## The only calls a replay makes itself (what the playtest bot does without input).
const CALLS := {
	"Game": ["start_game", "restart_level", "quit_to_menu"],
	"Menus": ["close_all", "open_pause", "back"],
	"SaveGame": ["save_slot", "load_slot"],
}

var mode: Mode = Mode.OFF
## The seed of the global random generator (`randi()`, `randf()`, `pick_random()`...).
var seed_value: int = 0
## Physics frames since the first one with a scene; -1 before it.
var frame: int = -1
## The recording's file, when one is written.
var path: String = ""
var log_lines: GameLog = GameLog.new()

var _header: Dictionary = {}
var _lines: Array[Dictionary] = []
var _file: FileAccess
var _record_to: String = ""
var _check_every: int = CHECK_EVERY_MEMORY
var _max_frames: int = 0
var _dropped_before: int = -1
var _autostart: bool = false
var _boot: String = ""
var _start_scene: String = ""
var _last_check: Dictionary = {}
var _pending: Array = []
var _pending_frame: int = -1
## Every frame so far took exactly one physics step's time (`--fixed-fps`): only such a
## recording replays frame for frame.
var _steady: bool = true
# Replaying.
var _replay: Dictionary = {}
var _replay_lines: Dictionary = {}
var _replay_checks: Dictionary = {}
var _replay_end: int = 0
var _replay_scale: Vector2 = Vector2.ONE
var _replay_out: String = ""
var _checked: int = 0
var _divergence: Dictionary = {}
var _finished: bool = false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	OS.add_logger(log_lines)
	var replay_file: String = ""
	var fixed_seed: int = 0
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with(REPLAY_ARG):
			replay_file = argument.substr(REPLAY_ARG.length())
		elif argument.begins_with(OUT_ARG):
			_replay_out = argument.substr(OUT_ARG.length())
		elif argument.begins_with(SEED_ARG):
			fixed_seed = int(argument.substr(SEED_ARG.length()))
		elif argument == RECORD_ARG or argument.begins_with(RECORD_ARG + "="):
			_record_to = argument.substr(RECORD_ARG.length() + 1)
			if _record_to.is_empty():
				_record_to = "play-" + new_id()
	if not replay_file.is_empty():
		_start_replay(replay_file)
	else:
		randomize()
		seed_value = fixed_seed if fixed_seed != 0 else (randi() & 0x7fffffff)
		if recording_allowed() or not _record_to.is_empty():
			mode = Mode.RECORD
			get_tree().root.window_input.connect(_on_window_input)
	seed(seed_value)
	get_tree().node_added.connect(_on_node_added)
	var minutes: float = float(ProjectSettings.get_setting(MAX_MINUTES, 30.0))
	_max_frames = int(maxf(minutes, 1.0) * 60.0 * Engine.physics_ticks_per_second)
	get_tree().physics_frame.connect(_on_physics_frame)


func _process(delta: float) -> void:
	if _steady and mode == Mode.RECORD and frame > 0:
		_steady = absf(delta - 1.0 / Engine.physics_ticks_per_second) < 0.000001


func _exit_tree() -> void:
	if mode == Mode.REPLAY and not _finished:
		_finish("the game quit")
	elif _file != null:
		_flush_pending()
		_file.store_line(JSON.stringify(_end_line()))
		_file.close()
		_file = null
	OS.remove_logger(log_lines)


## Whether this build records sessions in memory: debug builds always, release builds
## when the game opts in.
static func recording_allowed() -> bool:
	return OS.is_debug_build() or bool(ProjectSettings.get_setting(RECORD_IN_RELEASE, false))


func is_recording() -> bool:
	return mode == Mode.RECORD


func is_replaying() -> bool:
	return mode == Mode.REPLAY


## The header of the replay being played, or of the session being recorded ({} before
## the first frame with a scene).
func header() -> Dictionary:
	return _replay if mode == Mode.REPLAY else _header


## A random generator of its own for `name`, seeded from the session's seed, so a game
## that keeps several generators still replays the same.
func rng(rng_name: String) -> RandomNumberGenerator:
	var generator: RandomNumberGenerator = RandomNumberGenerator.new()
	generator.seed = hash("%d:%s" % [seed_value, rng_name])
	return generator


## Runs `method` on an autoload and records the call, so a replay makes it on the same
## frame. For tools that act without input (the playtest bot's save checks and restarts).
func perform(target: Node, method: StringName, args: Array = []) -> Variant:
	if mode == Mode.RECORD and target != null and target.get_parent() == get_tree().root:
		var allowed: Array = CALLS.get(String(target.name), [])
		if String(method) in allowed:
			_queue(
				_event_frame(),
				{
					"t": "call",
					"node": String(target.name),
					"method": String(method),
					"args": _encode_args(args),
				}
			)
	return target.callv(method, args)


## Called by `Game.take_autostart`, so the header says how the session started.
func note_autostart(value: bool) -> void:
	if value:
		_autostart = true


## The session up to frame `until` (-1: now) as replay lines, header first, for a bug
## report. `focus_seconds` marks the last seconds as the part to look at; the replay
## still starts at the session's start, so it plays back exactly. Empty when this build
## does not record.
func snapshot(focus_seconds: float = 0.0, until: int = -1) -> Array[Dictionary]:
	var lines: Array[Dictionary] = []
	if _header.is_empty():
		return lines
	_flush_pending()
	var last: int = frame if until < 0 else mini(until, frame)
	var header: Dictionary = _header.duplicate(true)
	if _dropped_before >= 0:
		header["partial"] = true
		header["from_frame"] = _dropped_before
	if focus_seconds > 0.0:
		var span: int = int(focus_seconds * Engine.physics_ticks_per_second)
		header["focus"] = {"from": maxi(0, last - span), "to": last}
	lines.append(header)
	var check: Dictionary = {}
	for line: Dictionary in _lines:
		if int(line.get("f", 0)) > last:
			break
		lines.append(line)
		if line.has("check"):
			check = line
	var end: Dictionary = {"frame": maxi(last, 0), "errors": log_lines.errors, "steady": _steady}
	if not check.is_empty():
		end["check_frame"] = check["f"]
		end["hash"] = (check["check"] as Dictionary)["hash"]
	lines.append({"end": end})
	return lines


## Writes `lines` (from `snapshot`) to `file_path` as JSON lines. Returns the error.
static func write_lines(lines: Array[Dictionary], file_path: String) -> Error:
	DirAccess.make_dir_recursive_absolute(file_path.get_base_dir())
	var out: FileAccess = FileAccess.open(file_path, FileAccess.WRITE)
	if out == null:
		return FileAccess.get_open_error()
	for line: Dictionary in lines:
		out.store_line(JSON.stringify(line))
	out.close()
	return OK


## "20261112-153012-a3f9": when, then four hex digits from a generator of its own (the
## game's seeded one is left alone, so the session still replays).
static func new_id() -> String:
	var stamp: String = Time.get_datetime_string_from_system(false, true)
	stamp = stamp.replace("-", "").replace(":", "").replace(" ", "-")
	var extra: RandomNumberGenerator = RandomNumberGenerator.new()
	extra.randomize()
	return "%s-%04x" % [stamp, extra.randi() & 0xffff]


## The build a hash belongs to: engine version and commit, platform and architecture.
static func build_id() -> String:
	var info: Dictionary = Engine.get_version_info()
	return (
		"%s %s %s %s"
		% [
			info.get("string", ""),
			str(info.get("hash", "")).left(9),
			OS.get_name(),
			Engine.get_architecture_name(),
		]
	)


## The first scene the session loads (not an autoload) is where a replay starts.
func _on_node_added(node: Node) -> void:
	if node.get_parent() != get_tree().root:
		return
	if ProjectSettings.has_setting("autoload/" + String(node.name)):
		return
	_start_scene = node.scene_file_path
	get_tree().node_added.disconnect(_on_node_added)


func _on_window_input(event: InputEvent) -> void:
	if mode != Mode.RECORD:
		return
	var data: Dictionary = ReplayEvents.encode(event)
	if not data.is_empty():
		_queue(_event_frame(), data)


## Keeps an input (or call) for frame `at`; a frame's items are written together.
func _queue(at: int, item: Dictionary) -> void:
	if at != _pending_frame:
		_flush_pending()
		_pending_frame = at
	_pending.append(item)


func _flush_pending() -> void:
	if not _pending.is_empty():
		_add({"f": _pending_frame, "e": _pending})
	_pending = []


## The frame an event belongs to: this one inside a physics step (sent by a test or the
## bot on the step's signal), else the next, which is the first to see it.
func _event_frame() -> int:
	if frame < 0:
		return 0
	return frame if Engine.is_in_physics_frame() else frame + 1


func _on_physics_frame() -> void:
	if frame < 0:
		if get_tree().current_scene == null:
			if _boot.is_empty():
				_boot = "script"
			return
		if _boot.is_empty():
			_boot = "engine"
		frame = 0
		_begin()
	else:
		frame += 1
	log_lines.frame = frame
	if _pending_frame < frame:
		_flush_pending()
	if mode == Mode.REPLAY:
		_replay_step()
	elif mode == Mode.RECORD and frame > 0 and frame % _check_every == 0:
		_last_check = ReplayState.capture(get_tree())
		_last_check["f"] = frame
		_add({"f": frame, "check": {"hash": _last_check["hash"], "state": _last_check["state"]}})


func _begin() -> void:
	if mode != Mode.RECORD:
		return
	var scene: Node = get_tree().current_scene
	var window: Vector2i = get_tree().root.size
	var settings_text: String = ""
	var settings: Node = get_tree().root.get_node_or_null(^"Settings")
	if settings != null:
		var settings_path: String = str(settings.get(&"path"))
		if FileAccess.file_exists(settings_path):
			settings_text = FileAccess.get_file_as_string(settings_path)
	_header = {
		"format": FORMAT,
		"version": FORMAT_VERSION,
		"game": str(ProjectSettings.get_setting("application/config/name", "")),
		"game_version": str(ProjectSettings.get_setting("application/config/version", "")),
		"joinery": str(ProjectSettings.get_setting(VERSION_SETTING, "")),
		"engine": str(Engine.get_version_info().get("string", "")),
		"build": build_id(),
		"debug": OS.is_debug_build(),
		"created": Time.get_datetime_string_from_system(true),
		"seed": seed_value,
		"boot": _boot,
		"start":
		{
			"scene": _start_scene if not _start_scene.is_empty() else scene.scene_file_path,
			"first_scene": scene.scene_file_path,
			"main_scene": str(ProjectSettings.get_setting("application/run/main_scene", "")),
			"autostart": _autostart,
			"args": _game_args(),
		},
		"physics_ticks": Engine.physics_ticks_per_second,
		"window": [window.x, window.y],
		"settings": settings_text,
	}
	if not _record_to.is_empty():
		_check_every = CHECK_EVERY
		_open_file()
		if PACE_ARG in OS.get_cmdline_user_args():
			_pace(Engine.physics_ticks_per_second)


func _open_file() -> void:
	path = _record_to
	if not (path.begins_with("/") or path.begins_with("user://") or path.contains(":")):
		path = FOLDER.path_join(path.get_file())
	if not path.ends_with(".replay"):
		path += ".replay"
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	_file = FileAccess.open(path, FileAccess.WRITE)
	if _file == null:
		push_warning("The replay could not be written to %s." % path)
		return
	_file.store_line(JSON.stringify(_header))
	for line: Dictionary in _lines:
		_file.store_line(JSON.stringify(line))
	recording_started.emit(path)
	print("Recording this play to %s" % ProjectSettings.globalize_path(path))


func _add(line: Dictionary) -> void:
	_lines.append(line)
	_stream(line)
	# Past the limit, the oldest tenth goes at once (one slice, not a shift per line).
	var oldest: int = frame - _max_frames
	if oldest > 0 and int(_lines[0].get("f", 0)) < oldest - int(_max_frames * 0.1):
		var cut: int = 0
		while cut < _lines.size() - 1 and int(_lines[cut].get("f", 0)) < oldest:
			cut += 1
		_dropped_before = int(_lines[cut].get("f", 0))
		_lines = _lines.slice(cut)


func _stream(line: Dictionary) -> void:
	if _file == null:
		return
	_file.store_line(JSON.stringify(line))
	if frame % CHECK_EVERY == 0:
		_file.flush()


func _end_line() -> Dictionary:
	var end: Dictionary = {"frame": maxi(frame, 0), "errors": log_lines.errors, "steady": _steady}
	if not _last_check.is_empty():
		end["check_frame"] = _last_check["f"]
		end["hash"] = _last_check["hash"]
	return {"end": end}


func _game_args() -> Array[String]:
	var kept: Array[String] = []
	for argument: String in OS.get_cmdline_user_args():
		var ours: bool = false
		for prefix: String in NOT_GAME_ARGS:
			if argument.begins_with(prefix):
				ours = true
		if not ours:
			kept.append(argument)
	return kept


## Holds a windowed recording at real time (`--joinery-pace`): `--fixed-fps` alone runs
## as fast as it draws.
func _pace(fps: int) -> void:
	var frame_usec: int = 1000000 / maxi(fps, 1)
	var due: Array[int] = [Time.get_ticks_usec()]
	get_tree().process_frame.connect(
		func() -> void:
			var now: int = Time.get_ticks_usec()
			if now < due[0]:
				OS.delay_usec(due[0] - now)
				now = due[0]
			due[0] = now + frame_usec
	)


static func _encode_args(args: Array) -> Array:
	var out: Array = []
	for value: Variant in args:
		if value is int:
			out.append({"i": value})
		elif value is bool:
			out.append({"b": value})
		else:
			out.append({"j": JSON.stringify(value)})
	return out


static func _decode_args(args: Array) -> Array:
	var out: Array = []
	for value: Variant in args:
		var item: Dictionary = value if value is Dictionary else {}
		if item.has("i"):
			out.append(int(item["i"]))
		elif item.has("b"):
			out.append(bool(item["b"]))
		else:
			out.append(JSON.parse_string(str(item.get("j", "null"))))
	return out


# --- Replaying ----------------------------------------------------------------------


func _start_replay(file_path: String) -> void:
	mode = Mode.REPLAY
	if not FileAccess.file_exists(file_path):
		_fail_early("There is no replay at %s." % file_path)
		return
	var text: String = FileAccess.get_file_as_string(file_path)
	for raw: String in text.split("\n", false):
		var parsed: Variant = JSON.parse_string(raw)
		if not parsed is Dictionary:
			continue
		var line: Dictionary = parsed
		if _replay.is_empty():
			_replay = line
			continue
		if line.has("end"):
			var end: Dictionary = line["end"]
			_replay_end = maxi(_replay_end, int(end.get("frame", 0)))
			continue
		var at: int = int(line.get("f", 0))
		_replay_end = maxi(_replay_end, at)
		if line.has("check"):
			_replay_checks[at] = line["check"]
		elif line.has("e"):
			var queued: Array = _replay_lines.get(at, [])
			queued.append_array(line["e"] as Array)
			_replay_lines[at] = queued
	if str(_replay.get("format", "")) != FORMAT:
		_fail_early("%s is not a Joinery replay." % file_path)
		return
	seed_value = int(_replay.get("seed", 0))
	var window: Array = _replay.get("window", [])
	if window.size() == 2 and float(window[0]) > 0.0 and float(window[1]) > 0.0:
		var recorded: Vector2i = Vector2i(int(window[0]), int(window[1]))
		# The game lays out and stretches for its window: replay in the recorded size.
		if DisplayServer.get_name() == "headless":
			get_tree().root.size = recorded
		var size: Vector2i = get_tree().root.size
		_replay_scale = Vector2(size.x / float(recorded.x), size.y / float(recorded.y))


func _fail_early(message: String) -> void:
	_divergence = {"frame": 0, "error": message}
	_finish.call_deferred("")


func _replay_step() -> void:
	if _finished:
		return
	if _replay_checks.has(frame):
		var check: Dictionary = _replay_checks[frame]
		var now: Dictionary = ReplayState.capture(get_tree())
		_checked += 1
		if str(now["hash"]) != str(check.get("hash", "")):
			# Through JSON, as the recorded state was, so 78 and 78.0 read the same.
			var replayed: Dictionary = JSON.parse_string(JSON.stringify(now["state"]))
			_divergence = {
				"frame": frame,
				"seconds": snappedf(frame / float(Engine.physics_ticks_per_second), 0.01),
				"differs": ReplayState.differences(check.get("state", {}), replayed),
				"recorded": check.get("state", {}),
				"replayed": replayed,
			}
			_finish("")
			return
	for item: Variant in _replay_lines.get(frame, []):
		var data: Dictionary = item if item is Dictionary else {}
		if str(data.get("t", "")) == "call":
			_replay_call(data)
			continue
		var event: InputEvent = ReplayEvents.decode(data, _replay_scale)
		if event != null:
			Input.parse_input_event(event)
			Input.flush_buffered_events()
	if frame >= _replay_end:
		_finish("")


func _replay_call(call: Dictionary) -> void:
	var node_name: String = str(call.get("node", ""))
	var method: String = str(call.get("method", ""))
	if not method in CALLS.get(node_name, []):
		push_warning("The replay asks for %s.%s, which a replay never calls." % [node_name, method])
		return
	var target: Node = get_tree().root.get_node_or_null(NodePath(node_name))
	if target == null or not target.has_method(method):
		return
	target.callv(method, _decode_args(call.get("args", []) as Array))


func _finish(reason: String) -> void:
	if _finished:
		return
	_finished = true
	var errors: Array[Dictionary] = []
	for line: Dictionary in log_lines.lines():
		if str(line["kind"]) in ["error", "script_error", "shader_error"]:
			errors.append(line)
	var end: Dictionary = {}
	var result: Dictionary = {
		"ok": _divergence.is_empty() and errors.is_empty(),
		"frames": maxi(frame, 0),
		"end_frame": _replay_end,
		"checks": _checked,
		"seed": seed_value,
		"build": build_id(),
		"recorded_build": str(_replay.get("build", "")),
		"divergence": _divergence,
		"errors": errors.slice(0, 20),
		"ended": reason,
	}
	if reason == "the game quit" and frame + 1 < _replay_end:
		result["ok"] = false
		result["divergence"] = {
			"frame": frame,
			"error":
			"The game quit at frame %d; the recording went on to %d." % [frame, _replay_end],
		}
	elif reason.is_empty() and get_tree().current_scene != null:
		end = ReplayState.capture(get_tree())
		result["final_hash"] = end["hash"]
	print(RESULT_PREFIX + JSON.stringify(result))
	if not _replay_out.is_empty():
		var out: FileAccess = FileAccess.open(_replay_out, FileAccess.WRITE)
		if out != null:
			out.store_string(JSON.stringify(result, "  "))
			out.close()
	if reason.is_empty():
		get_tree().quit(0 if bool(result["ok"]) else 1)
