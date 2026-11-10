@tool
extends RefCounted
## Native nonblocking pipes: no GDScript worker can race script reload or teardown.

const _SELF := preload("command_job.gd")

var _process: Dictionary = {}
var _stdout := PackedByteArray()
var _stderr := PackedByteArray()
var _input := PackedByteArray()
var _input_offset := 0


func start(command: String, arguments: PackedStringArray) -> Error:
	_process = OS.execute_with_pipe(command, arguments, false)
	return OK if not _process.is_empty() else ERR_CANT_FORK


func poll() -> Dictionary:
	if _process.is_empty():
		return {}
	_write_input()
	_drain("stdio", _stdout)
	_drain("stderr", _stderr)
	var pid := int(_process["pid"])
	if OS.is_process_running(pid):
		return {}
	var out: FileAccess = _process["stdio"]
	var err: FileAccess = _process["stderr"]
	if out.get_length() > 0 or err.get_length() > 0:
		return {}
	var result := {
		"code": OS.get_process_exit_code(pid),
		"output": _stdout.get_string_from_utf8().strip_edges(),
		"error": _stderr.get_string_from_utf8().strip_edges(),
	}
	_close()
	return result


func send_line(value: String) -> void:
	if not _process.is_empty():
		_input.append_array((value + "\n").to_utf8_buffer())
		_write_input()


func take_lines() -> PackedStringArray:
	var lines := PackedStringArray()
	if _process.is_empty():
		return lines
	_write_input()
	_drain("stdio", _stdout)
	_drain("stderr", _stderr)
	var end := _stdout.find(10)
	while end >= 0:
		lines.append(_stdout.slice(0, end).get_string_from_utf8())
		_stdout = _stdout.slice(end + 1)
		end = _stdout.find(10)
	return lines


func _write_input() -> void:
	if _input.is_empty():
		return
	var pipe: FileAccess = _process["stdio"]
	for _chunk: int in 8:
		var end := mini(_input_offset + 512, _input.size())
		if not pipe.store_buffer(_input.slice(_input_offset, end)):
			return
		_input_offset = end
		if _input_offset == _input.size():
			_input = PackedByteArray()
			_input_offset = 0
			return


func _drain(key: String, destination: PackedByteArray) -> void:
	var pipe: FileAccess = _process[key]
	var available := mini(pipe.get_length(), 65536)
	if available > 0:
		destination.append_array(pipe.get_buffer(available))


func cancel() -> Error:
	var result := OK
	if not _process.is_empty() and OS.is_process_running(int(_process["pid"])):
		result = OS.kill(int(_process["pid"]))
	_close()
	return result


func close_pipes() -> void:
	# Persistent hosts receive EOF and shut down their own subprocesses cleanly.
	_close()


func _close() -> void:
	_input = PackedByteArray()
	_input_offset = 0
	for key: String in ["stdio", "stderr"]:
		if _process.has(key):
			var pipe: FileAccess = _process[key]
			pipe.close()
	_process.clear()


## Start the Joinery CLI with [param arguments]: [param cli_prefix] (a program and its leading
## arguments, as tests set it) or, when empty, the engine checkout's `uv run … joinery`.
## Returns the running job, or null when it can't start.
static func joinery(cli_prefix: PackedStringArray, arguments: Array) -> RefCounted:
	var program := "uv"
	var args := PackedStringArray()
	if cli_prefix.is_empty():
		var engine := OS.get_environment("JOINERY_HOME")
		if engine.is_empty():
			engine = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
		args = PackedStringArray(["run", "--frozen", "--quiet", "--project", engine, "joinery"])
	else:
		program = cli_prefix[0]
		args = cli_prefix.slice(1)
	for argument: Variant in arguments:
		args.append(str(argument))
	var job: RefCounted = _SELF.new()
	return job if job.call("start", program, args) == OK else null


## Poll [param job]; when it has finished, call [param done] with its result and return null
## (unless [param done] started a new job in the same slot), else return the job.
static func finish(job: RefCounted, done: Callable) -> RefCounted:
	if job == null:
		return null
	var result: Dictionary = job.call("poll")
	if result.is_empty():
		return job
	done.call(result)
	return null
