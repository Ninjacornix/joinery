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
	input_job.start(python, PackedStringArray([
		"-c", "import sys,hashlib; print(hashlib.sha256(sys.stdin.readline().encode()).hexdigest())",
	]))
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
