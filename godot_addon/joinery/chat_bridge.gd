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
## The queued prompt was written to the agent's host (the optimistic bubble resolves).
signal sent
## A line from Joinery itself, not Journeyman ("info" or "error"). Without a listener it
## falls back to message_received.
signal notice(text: String, kind: String)
## Asset work stopped before anything was made: the art direction needs the user's approval
## first. [param event] is {needs_art_approval, resume, message}.
signal art_gate(event: Dictionary)
## The user approved the art direction ([param sha256] is the approved bible).
signal art_approved(sha256: String)
## Features the game brief asks for that the game lacks: [{text, command}], offered one at a
## time and never acted on unasked.
signal suggestions(items: Array)

const COMMAND_SCRIPT := preload("command_job.gd")
const ATTACHMENTS := preload("chat_attachments.gd")
const EDITOR_VIEW := preload("editor_view.gd")
var context_provider: Callable
## Captures the editor's viewport for the agent's view_editor tool; returns editor_view.gd's
## result. Tests replace it.
var view_provider := Callable(EDITOR_VIEW, "capture")
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
		status_changed.emit("error", "Journeyman isn't connected.")
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
		status_changed.emit("error", "Couldn't start the chat connection.")
		return
	status_changed.emit("connecting", "Connecting to " + agent.capitalize() + "…")


## Send [param text] about [param targets]. Image attachments among the targets
## (chat_attachments.gd) go as "attachments", a list of their saved paths.
func submit(text: String, targets: Array) -> void:
	var scene_targets: Array = []
	var images: Array[String] = []
	for target: Variant in targets:
		if ATTACHMENTS.is_attachment(target):
			images.append(str(target["path"]))
		else:
			scene_targets.append(target)
	_pending = {
		"kind": "prompt",
		"text": text,
		"targets": scene_targets.duplicate(true),
		"selection": _selection.duplicate(),
		"editor_context":
		context_provider.call(scene_targets) if context_provider.is_valid() else {},
	}
	if not images.is_empty():
		_pending["attachments"] = images
	busy_changed.emit(true)
	if _job == null:
		var pending := _pending
		reconnect()
		_pending = pending
	if _connected:
		_send_pending()
	elif _job == null:
		_notice("Couldn't start the chat connection.", "error")
		busy_changed.emit(false)


func _send_pending() -> void:
	if not _pending.is_empty() and _job != null:
		_job.send_line(JSON.stringify(_pending))
		_pending.clear()
		sent.emit()


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
		var event_json := JSON.new()
		var event: Variant = event_json.data if event_json.parse(line) == OK else null
		if event is Dictionary:
			_handle(event)
	var result: Dictionary = _job.poll()
	if not result.is_empty():
		_job = null
		_connected = false
		if _error_detail.is_empty():
			_error_detail = "Lost connection to %s." % _display_name()
		if not _pending.is_empty():
			_notice(_error_detail, "error")
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
				_error_detail = str(
					event.get("message", "Lost connection to %s." % _display_name())
				)
			status_changed.emit(
				str(event.get("state", "error")),
				(
					_agent.capitalize() + " connected"
					if _connected
					else str(event.get("message", "Lost connection to %s." % _display_name()))
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
		"art_gate":
			art_gate.emit(event)
		"art_approved":
			art_approved.emit(str(event.get("sha256", "")))
		"suggestions":
			suggestions.emit(event.get("items", []))
		"file_decision":
			file_decision.emit(event)
		"editor_action":
			editor_action.emit(event)
		"editor_view":
			_send_view(str(event.get("request_id", "")))
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
		_notice("Connect Journeyman before refreshing its models.", "info")


func _notice(text: String, kind: String) -> void:
	if notice.get_connections().is_empty():
		message_received.emit(text)
	else:
		notice.emit(text, kind)


func _display_name() -> String:
	return _agent.capitalize() if not _agent.is_empty() else "Journeyman"


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


## Journeyman asked to see the editor (view_editor): capture the viewport and answer. The
## request's card shows a step, so the user knows it looked.
func _send_view(request_id: String) -> void:
	status_changed.emit("working", "Looking at your view")
	var result: Dictionary = (
		view_provider.call()
		if view_provider.is_valid()
		else {"success": false, "message": "The editor view isn't available."}
	)
	result.erase("entry")
	if _job != null and _connected:
		var line := {"kind": "editor_view_result", "request_id": request_id, "result": result}
		_job.send_line(JSON.stringify(line))


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
