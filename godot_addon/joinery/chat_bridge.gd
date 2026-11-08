@tool
extends Node
## Persistent framed chat on native pipes. No GDScript worker touches editor state.

signal status_changed(state: String, detail: String)
signal busy_changed(busy: bool)
signal message_received(text: String)
signal message_delta(text: String, new_message: bool)
signal completed(ok: bool)
signal editor_action(event: Dictionary)
signal file_preview(event: Dictionary)
signal file_decision(event: Dictionary)
signal art_review(event: Dictionary)
signal models_received(models: Array)
signal execution_changed(event: Dictionary)
signal timing_received(event: Dictionary)

const COMMAND_SCRIPT := preload("command_job.gd")
var context_provider: Callable
var _job: RefCounted
var _connected := false
var _pending: Dictionary = {}
var _agent := ""
var _profile := ""
var _engine := ""
var _project := ""
var _error_detail := ""
var _selection: Dictionary = {"mode": "auto", "model": "", "effort": ""}


func start(agent: String, profile: String, engine: String, project: String) -> void:
	close()
	_error_detail = ""
	_agent = agent
	_profile = profile
	_engine = engine
	_project = project
	if agent.is_empty():
		status_changed.emit("error", "Choose an agent in the Project Manager.")
		return
	var args := PackedStringArray(
		[
			"run",
			"--frozen",
			"--quiet",
			"--project",
			engine,
			"joinery",
			"chat",
			"--agent",
			agent,
			"--project",
			project,
			"--addon-protocol",
			"2",
		]
	)
	if not profile.is_empty():
		args.append_array(PackedStringArray(["--profile", profile]))
	_job = COMMAND_SCRIPT.new()
	if _job.start("uv", args) != OK:
		_job = null
		status_changed.emit("error", "Could not start the chat connection.")
		return
	status_changed.emit("connecting", "Connecting to " + agent.capitalize() + "…")


func submit(text: String, targets: Array) -> void:
	_pending = {
		"kind": "prompt",
		"text": text,
		"targets": targets.duplicate(true),
		"selection": _selection.duplicate(),
		"editor_context": context_provider.call(targets) if context_provider.is_valid() else {},
	}
	busy_changed.emit(true)
	if _job == null:
		var pending := _pending
		reconnect()
		_pending = pending
	if _connected:
		_send_pending()
	elif _job == null:
		message_received.emit("The chat connection could not start.")
		busy_changed.emit(false)


func _send_pending() -> void:
	if not _pending.is_empty() and _job != null:
		_job.send_line(JSON.stringify(_pending))
		_pending.clear()


func cancel_turn() -> void:
	_pending.clear()
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "cancel"}))
	else:
		busy_changed.emit(false)


func _process(_delta: float) -> void:
	if _job == null:
		return
	for line: String in _job.take_lines():
		var event: Variant = JSON.parse_string(line)
		if event is Dictionary:
			_handle(event)
	var result: Dictionary = _job.poll()
	if not result.is_empty():
		_job = null
		_connected = false
		if _error_detail.is_empty():
			_error_detail = "Chat connection closed. Reconnect to try again."
		if not _pending.is_empty():
			message_received.emit(_error_detail)
		_pending.clear()
		status_changed.emit("error", _error_detail)
		completed.emit(false)
		busy_changed.emit(false)


func _handle(event: Dictionary) -> void:
	match str(event.get("kind", "")):
		"connection":
			models_received.emit(event.get("models", []))
			_connected = event.get("state") == "connected"
			if not _connected:
				_error_detail = str(event.get("message", "Disconnected"))
			status_changed.emit(
				str(event.get("state", "error")),
				(
					_agent.capitalize() + " connected"
					if _connected
					else str(event.get("message", "Disconnected"))
				)
			)
			if _connected:
				_send_pending()
		"message_delta":
			message_delta.emit(str(event.get("text", "")), bool(event.get("new_message", false)))
		"message":
			message_received.emit(str(event.get("text", "")))
		"progress":
			status_changed.emit("working", str(event.get("text", "")))
		"execution":
			execution_changed.emit(event)
		"models":
			models_received.emit(event.get("models", []))
		"timing":
			timing_received.emit(event)
		"file_preview":
			file_preview.emit(event)
		"art_review":
			art_review.emit(event)
		"file_decision":
			file_decision.emit(event)
		"editor_action":
			editor_action.emit(event)
		"done":
			status_changed.emit("connected", _agent.capitalize() + " connected")
			completed.emit(bool(event.get("ok", false)))
			busy_changed.emit(false)


func set_selection(selection: Dictionary) -> void:
	_selection = selection.duplicate()


func refresh_models() -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "models_request"}))
	else:
		message_received.emit("Connect your agent before refreshing its model list.")


func send_editor_result(request_id: String, result: Dictionary) -> void:
	if _job != null and _connected:
		(
			_job
			. send_line(
				(
					JSON
					. stringify(
						{
							"kind": "editor_result",
							"request_id": request_id,
							"result": result,
						}
					)
				)
			)
		)


func decide_files(identifier: String, accept: bool) -> void:
	if _job != null and _connected:
		(
			_job
			. send_line(
				(
					JSON
					. stringify(
						{
							"kind": "decision",
							"transaction_id": identifier,
							"accept": accept,
						}
					)
				)
			)
		)


func rollback_files(identifier: String) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "rollback_decision", "transaction_id": identifier}))


func finish_live_review() -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "live_decision"}))


func review_art() -> void:
	if _job != null:
		_job.send_line(JSON.stringify({"kind": "art_review"}))


func approve_art(sha256: String) -> void:
	if _job != null:
		_job.send_line(JSON.stringify({"kind": "art_approval", "sha256": sha256}))


func reconnect() -> void:
	start(_agent, _profile, _engine, _project)


func close() -> void:
	_connected = false
	_pending.clear()
	if _job != null:
		_job.close_pipes()
		_job = null


func _exit_tree() -> void:
	close()
