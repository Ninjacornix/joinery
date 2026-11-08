extends SceneTree
## Regression: reload and cancel while a CLI process runs, without script threads.

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		python = "python3"
	await _exited_writer_eof(python)
	var script := load("res://addons/joinery/command_job.gd") as GDScript
	var job: RefCounted = script.new()
	var code := (
		"import sys,time; print('start'); sys.stderr.write('note'); "
		+ "time.sleep(.1); print('🌳'*10000)"
	)
	_check(job.start(python, PackedStringArray(["-c", code])) == OK, "Start nonblocking CLI")
	_check(script.reload(true) == OK, "Reload must be safe while a CLI is running")
	var result: Dictionary = {}
	var deadline := Time.get_ticks_msec() + 5000
	while result.is_empty() and Time.get_ticks_msec() < deadline:
		result = job.poll()
		await process_frame
	_check(result.get("code", -1) == 0, "CLI must finish after script reload")
	_check(str(result.get("output", "")).begins_with("start"), "Capture stdout")
	_check(str(result.get("output", "")).count("🌳") == 10000, "Preserve UTF-8 across pipe reads")
	_check(result.get("error") == "note", "Capture stderr separately")
	var input_job: RefCounted = script.new()
	(
		input_job
		. start(
			python,
			PackedStringArray(
				[
					"-c",
					"import sys,hashlib; print(hashlib.sha256(sys.stdin.readline().encode()).hexdigest())",
				]
			)
		)
	)
	var message := "🔑".repeat(10000)
	input_job.send_line(message)
	result = {}
	deadline = Time.get_ticks_msec() + 5000
	while result.is_empty() and Time.get_ticks_msec() < deadline:
		result = input_job.poll()
		await process_frame
	_check(result.get("code", -1) == 0, "Large stdin must finish without blocking")
	_check(result.get("output") == (message + "\n").sha256_text(), "Preserve chunked stdin bytes")
	var cancelled: RefCounted = script.new()
	cancelled.start(python, PackedStringArray(["-c", "import time; time.sleep(60)"]))
	_check(cancelled.cancel() == OK, "Closing a job must stop its child")
	_check(cancelled._process.is_empty(), "Cancellation must close native pipes")
	# Closing a workspace must cancel its in-flight CLI instead of joining script code.
	var workspace := load("res://addons/joinery/tool_workspace.gd").new() as Control
	root.add_child(workspace)
	workspace.refresh()
	var workspace_job: RefCounted = workspace._command_job
	_check(workspace.get_script().reload(true) == OK, "Reload workspace while its CLI is running")
	workspace.queue_free()
	await process_frame
	_check(workspace_job._process.is_empty(), "Workspace teardown must stop its CLI")
	print("command lifecycle: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _exited_writer_eof(python: String) -> void:
	# Stay below the native pipe buffer so the writer can exit before any read.
	# This exercises queued tail bytes followed by a closed writer, not a timing
	# race where an empty live pipe is confused with EOF.
	var expected := "tail 🌳 café 世界\n".repeat(16).to_utf8_buffer()
	var code := (
		"import sys; sys.stdout.buffer.write(bytes.fromhex('"
		+ expected.hex_encode()
		+ "')); sys.stdout.buffer.flush()"
	)
	var owned := OS.execute_with_pipe(python, PackedStringArray(["-c", code]), false)
	_check(not owned.is_empty(), "Start short native EOF writer")
	if owned.is_empty():
		return
	var stdout_pipe: FileAccess = owned["stdio"]
	var stderr_pipe: FileAccess = owned["stderr"]
	var pid := int(owned["pid"])
	var deadline := Time.get_ticks_msec() + 10000
	while OS.is_process_running(pid) and Time.get_ticks_msec() < deadline:
		await process_frame
	var exited := not OS.is_process_running(pid)
	_check(exited, "Short writer exits before tail bytes are drained")
	if not exited:
		OS.kill(pid)
	else:
		_check(OS.get_process_exit_code(pid) == 0, "Short EOF writer succeeds")
		_check(stdout_pipe.get_length() == expected.size(), "Exited writer retains all tail bytes")
		var received := PackedByteArray()
		var available := int(stdout_pipe.get_length())
		while available > 0 and Time.get_ticks_msec() < deadline:
			# Split multibyte code points across reads and compare the actual bytes.
			var chunk := stdout_pipe.get_buffer(mini(available, 7))
			if chunk.is_empty():
				break
			received.append_array(chunk)
			available = int(stdout_pipe.get_length())
		_check(received == expected, "Drain exact Unicode tail bytes after process exit")
		_check(stdout_pipe.get_length() == 0, "Drained stdout has zero available bytes")
		if OS.get_name() == "Windows":
			_check(stdout_pipe.get_error() == ERR_FILE_EOF, "Closed Windows stdout reports EOF")
		_check(stderr_pipe.get_length() == 0, "Short EOF writer has no stderr output")
		if OS.get_name() == "Windows":
			_check(stderr_pipe.get_error() == ERR_FILE_EOF, "Closed Windows stderr reports EOF")
	stdout_pipe.close()
	stderr_pipe.close()
	_check(not stdout_pipe.is_open() and not stderr_pipe.is_open(), "Close owned EOF fixture pipes")
