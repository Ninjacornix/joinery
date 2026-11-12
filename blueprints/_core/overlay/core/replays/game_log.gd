extends Logger
## Keeps the game's last log lines (printed text, warnings and errors) for bug reports
## and replays, each with the replay frame it happened on. Godot calls a Logger from any
## thread, so a mutex guards it.

## Lines kept; older ones are dropped.
const LIMIT := 400
const KINDS := ["error", "warning", "script_error", "shader_error"]

## The replay frame the game is on; set by Replays every physics frame.
var frame: int = 0
var errors: int = 0
var _lines: Array[Dictionary] = []
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
	var where: String = "%s:%d" % [file, line]
	var caller: String = function
	for trace: ScriptBacktrace in script_backtraces:
		if trace.get_frame_count() > 0:
			where = "%s:%d" % [trace.get_frame_file(0), trace.get_frame_line(0)]
			caller = trace.get_frame_function(0)
			break
	var kind: String = KINDS[clampi(error_type, 0, KINDS.size() - 1)]
	_add(
		{
			"kind": kind,
			"message": rationale if not rationale.is_empty() else code,
			"where": where,
			"function": caller,
		},
		kind != "warning"
	)


func _log_message(message: String, error: bool) -> void:
	_add({"kind": "stderr" if error else "print", "message": message.strip_edges()}, false)


## Every line kept so far (a copy).
func lines() -> Array[Dictionary]:
	_lock.lock()
	var copy: Array[Dictionary] = _lines.duplicate()
	_lock.unlock()
	return copy


## The kept lines as text, one per line.
func text() -> String:
	var out: PackedStringArray = PackedStringArray()
	for entry: Dictionary in lines():
		var where: String = str(entry.get("where", ""))
		var kind: String = str(entry["kind"]).to_upper().replace("_", " ")
		var line: String = "[%d] %s: %s" % [int(entry.get("frame", 0)), kind, entry["message"]]
		if not where.is_empty():
			line += " (%s)" % where
		out.append(line)
	return "\n".join(out)


func _add(entry: Dictionary, is_error: bool) -> void:
	_lock.lock()
	entry["frame"] = frame
	_lines.append(entry)
	if _lines.size() > LIMIT:
		_lines.remove_at(0)
	if is_error:
		errors += 1
	_lock.unlock()
