extends Logger
## Collects the game's errors and warnings while the playtest bot plays, each with the
## physics frame it happened on. Godot calls it from any thread, so a mutex guards it.

## At most this many entries are kept; the rest are only counted.
const LIMIT := 500
const KINDS := ["error", "warning", "script_error", "shader_error"]

## The physics frame the bot is on; set by the bot every frame.
var frame: int = 0
var dropped: int = 0
var _entries: Array[Dictionary] = []
var _lock: Mutex = Mutex.new()


func _log_error(
	function: String,
	file: String,
	line: int,
	code: String,
	rationale: String,
	_editor_notify: bool,
	error_type: int,
	script_backtraces: Array[ScriptBacktrace]
) -> void:
	var where := "%s:%d" % [file, line]
	var caller := function
	# A script's error belongs where the script was, not where the engine noticed.
	for trace: ScriptBacktrace in script_backtraces:
		if trace.get_frame_count() > 0:
			where = "%s:%d" % [trace.get_frame_file(0), trace.get_frame_line(0)]
			caller = trace.get_frame_function(0)
			break
	var entry := {
		"kind": KINDS[clampi(error_type, 0, KINDS.size() - 1)],
		"message": rationale if not rationale.is_empty() else code,
		"where": where,
		"function": caller,
		"frame": frame,
	}
	_lock.lock()
	if _entries.size() < LIMIT:
		_entries.append(entry)
	else:
		dropped += 1
	_lock.unlock()


func _log_message(_message: String, _error: bool) -> void:
	pass


## Every entry so far (a copy).
func entries() -> Array[Dictionary]:
	_lock.lock()
	var copy: Array[Dictionary] = _entries.duplicate()
	_lock.unlock()
	return copy
