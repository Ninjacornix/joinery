# gdlint: disable=max-public-methods
@tool
extends Node
## Persistent framed chat on native pipes. No GDScript worker touches editor state.

signal status_changed(state: String, detail: String)
signal busy_changed(busy: bool)
signal message_received(text: String)
signal message_delta(text: String, new_message: bool)
signal completed(ok: bool)
signal editor_action(event: Dictionary)
## Journeyman's change passed Joinery's checks and is open for review: {transaction_id,
## summary, diff, changes, media, media_pending}. Renders still coming arrive as review_media.
signal file_preview(event: Dictionary)
## Where the turn is: {turn_id, state: working|submitting|in_review|decided|compacting|idle,
## transaction_id?, reason?}. A live edit's review waits for in_review (review_flow.gd).
signal turn_state(event: Dictionary)
## The before/after renders of the change in review: {transaction_id, media}. An item whose
## render failed carries error instead of its picture.
signal review_media(event: Dictionary)
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
## Token use as the provider reports it: {model, model_name, context_used, context_window,
## input_tokens, output_tokens, cached_tokens} (0 = not reported). {} when a new session starts.
signal usage_received(event: Dictionary)
## The host opened a chat for the next prompt: {chat_id, mode ("new", "resumed" or "summary"),
## message}. A new chat names its id here.
signal chat_changed(event: Dictionary)
## Anything from a parallel task (ADR 0036), tagged with its task_id: its state ({kind: "task",
## state, title, step, apps, transaction_id, message…}), its steps, its words and its change.
## None of these reach the signals above, which belong to Journeyman's own request.
signal task_event(event: Dictionary)
## The user asked to see a task's change ([param transaction_id]) in the Review tab.
signal task_review(transaction_id: String)
## A playtest (ADR 0038), tagged with its playtest_id: {state: starting|playing|writing|done|
## failed|stopped, elapsed, seconds, report, summary, followups, message, origin}.
signal playtest_event(event: Dictionary)
## "Make it match" (ADR 0045): {state: auditing|fixing|done|failed|stopped, tasks: [{key, title,
## area, apps, task_id, state, message}], audit?, message?}. Its changes are tasks.
signal match_event(event: Dictionary)
## The accessibility and performance checks (ADR 0049, 0050), tagged kind "access" or "perf":
## {state: auditing|fixing|done|failed|stopped|checked, tasks, audit?, message?}.
signal check_event(event: Dictionary)
## A request tried several ways (ADR 0042), tagged with its group_id: the group itself
## ({kind: "variants", state, picked, ways}) or the host refusing a pick ({kind: "notice"}).
## Each way's own events come as task events, tagged with variant_group.
signal variants_event(event: Dictionary)
## The design check's agent pass, or the check after accepted changes (design_id set).
signal design_event(event: Dictionary)
## A request's to-do (ADR 0060): {todo_id, state: waiting|going|checking|fixing|checked|
## cancelled|dropped, steps?, done_when?, results?, summary?}.
signal todo_event(event: Dictionary)
## The sound pass: {state: auditing|making|done|failed|stopped, audit?, tasks: [{key, title,
## category, categories, apps, sounds, facts, task_id, state, message}], summary?, message?}.
signal sound_pass_event(event: Dictionary)
## The console errors the host is watching (ADR 0075): {mode: ask|auto|off, warnings, groups:
## [{id, label, message, place, count, status, note, transaction_id, line}], totals, offer?}.
signal errors_event(event: Dictionary)
## The provider hit a usage or quota limit mid-request (ADR 0080): {state: offer|switched|
## declined, from, to, to_name, message}. An offer waits for [method switch_provider].
signal provider_switch(event: Dictionary)

const COMMAND_SCRIPT := preload("command_job.gd")
const ATTACHMENTS := preload("chat_attachments.gd")
const EDITOR_VIEW := preload("editor_view.gd")
## Each agent's name as the user knows it: the same as joinery/agents.py, which the chat host
## also sends in its connection event.
const AGENT_NAMES := {
	"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Gemini CLI", "opencode": "opencode"
}
var context_provider: Callable
## Captures the editor's viewport for the agent's view_editor tool; returns editor_view.gd's
## result. Tests replace it.
var view_provider := Callable(EDITOR_VIEW, "capture")
## The saved chat each prompt continues ("" starts a new one); the host names a new chat's id.
var chat_id := ""
## Turns that may run at once, Journeyman's own included ([agents] parallel); 1 = no tasks.
var parallel := 1
var _job: RefCounted
var _connected := false
var _pending: Dictionary = {}
## Go on a to-do: the line the next request carries (hold_line).
var _held_line: Dictionary = {}
var _agent := ""
## The agent's name from the host's connection event ("" until it connects).
var _name := ""
var _profile := ""
var _engine := ""
var _project := ""
var _error_detail := ""
var _selection: Dictionary = {"mode": "auto", "model": "", "effort": ""}
## True from a request's send until its done (or the connection ends).
var _busy := false
## The model's display name from the latest execution event, for usage events.
var _model_name := ""


func start(agent: String, profile: String, engine: String, project: String) -> void:
	var stopped := close()
	_error_detail = ""
	_model_name = ""
	_name = ""
	# A new session: its token use starts over.
	usage_received.emit({})
	var previous := _agent
	var switched := agent != previous
	_agent = agent
	_profile = profile
	_engine = engine
	_project = project
	if stopped:
		_notice(
			"Stopped: switched to %s." % _display_name() if switched else "Stopped: reconnected.",
			"info"
		)
	elif switched and not previous.is_empty() and not agent.is_empty():
		_notice("Now using %s." % _display_name(), "info")
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
	status_changed.emit("connecting", "Connecting to " + _display_name() + "…")


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
		"chat_id": chat_id,
		"editor_context":
		context_provider.call(scene_targets) if context_provider.is_valid() else {},
	}
	if not images.is_empty():
		_pending["attachments"] = images
	if not _held_line.is_empty():
		# Go on a to-do: the same request line, as "todo_go" with the to-do as left.
		_pending.merge(_held_line, true)
		_pending["kind"] = "todo_go"
		_held_line = {}
	busy_changed.emit(true)
	if _job == null:
		var pending := _pending.duplicate(true)
		reconnect()
		_pending = pending
	_busy = _job != null
	if _connected:
		_send_pending()
	elif _job == null:
		_notice("Couldn't start the chat connection.", "error")
		busy_changed.emit(false)


## Run [param text] as a parallel task beside the request Journeyman is working on. With
## [param extra], the same line (text, targets, editor context, model choice) goes as another
## kind: the variant strip's "variants_start", "variants_pick", "variants_mix" and
## "variants_discard" (sidebar_variants.gd, ADR 0042).
func start_task(text: String, targets: Array, extra := {}) -> bool:
	if _job == null or not _connected:
		return false
	var scene_targets: Array = []
	var images: Array[String] = []
	for target: Variant in targets:
		if ATTACHMENTS.is_attachment(target):
			images.append(str(target["path"]))
		else:
			scene_targets.append(target)
	var line := {
		"kind": "task_start",
		"text": text,
		"targets": scene_targets.duplicate(true),
		"selection": _selection.duplicate(),
		"editor_context":
		context_provider.call(scene_targets) if context_provider.is_valid() else {},
	}
	if not images.is_empty():
		line["attachments"] = images
	if not extra.is_empty():
		line["chat_id"] = chat_id
		line.merge(extra, true)
	_job.send_line(JSON.stringify(line))
	return true


## Debug a player's bug report [param report_id] ("/debug report <id>"): the chat host
## finds it (`joinery reports`) and starts the debugger from its note, log and replay.
func start_debug_report(report_id: String) -> bool:
	if _job == null or not _connected:
		return false
	var line := {
		"kind": "debug_start",
		"report": report_id,
		"selection": _selection.duplicate(),
		"editor_context": context_provider.call([]) if context_provider.is_valid() else {},
	}
	_job.send_line(JSON.stringify(line))
	return true


## Debug [param error] (one Output line) with the Output [param output] and game [param log]
## around it: a debugger agent finds the cause and a worker stages the fix (sidebar_debug.gd).
## [param group_id] names the console error group it fixes, when it came from an offer.
func start_debug(error: String, output: String, log: String, scene := "", group_id := "") -> bool:
	if _job == null or not _connected:
		return false
	var line := {
		"kind": "debug_start",
		"error": error,
		"output": output,
		"log": log,
		"scene": scene,
		"selection": _selection.duplicate(),
		"editor_context": context_provider.call([]) if context_provider.is_valid() else {},
	}
	if not group_id.is_empty():
		line["group_id"] = group_id
	_job.send_line(JSON.stringify(line))
	return true


## New console text ([param source]: "output", "game" or "test") for the host to read for
## errors (ADR 0075). False while not connected.
func report_errors(source: String, text: String) -> bool:
	return _send_line({"kind": "errors_seen", "source": source, "text": text})


## Fix the console error groups [param ids]; empty fixes every open one ("Fix all").
func fix_errors(ids: Array = []) -> bool:
	var line := {"kind": "errors_fix"}
	if not ids.is_empty():
		line["ids"] = ids
	return _send_line(line)


## Ignore (or stop ignoring) one console error group.
func ignore_error(id: String, ignore := true) -> bool:
	return _send_line({"kind": "errors_ignore", "id": id, "ignore": ignore})


## "ask", "auto" or "off" for console errors; with [param group_id], fix that one too.
func set_fix_errors(mode: String, group_id := "") -> bool:
	return _send_line({"kind": "errors_mode", "mode": mode, "group_id": group_id})


func _send_line(line: Dictionary) -> bool:
	if _job == null or not _connected:
		return false
	_job.send_line(JSON.stringify(line))
	return true


## The next [method submit] is Go on a to-do: [param line] ({todo_id, steps, done_when}) goes
## with it (sidebar_todo.gd).
func hold_line(line: Dictionary) -> void:
	_held_line = line.duplicate(true)


## Drop the to-do [param todo_id] still waiting for Go.
func cancel_todo(todo_id: String) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "todo_cancel", "todo_id": todo_id}))


## Stop one parallel task.
func cancel_task(task_id: String) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "cancel", "task_id": task_id}))


## Answer a quota offer: carry the request on with [param agent], or "" to let it end.
func switch_provider(agent: String) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "provider_switch", "agent": agent}))


## Stop every parallel task (Journeyman's own request is stopped with [method cancel_turn]).
func cancel_tasks() -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "cancel_tasks"}))


## "/playtest": the playtester plays the game and reports; [param focus] says what to aim for.
func start_playtest(focus: String) -> bool:
	if _job == null or not _connected:
		return false
	_job.send_line(JSON.stringify({"kind": "playtest_start", "focus": focus}))
	return true


## Stop the playtest [param playtest_id].
func cancel_playtest(playtest_id: String) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "playtest_cancel", "playtest_id": playtest_id}))


## "Make it match": with [param stop] false the chat host audits again and makes the fixes in
## [param areas] (all when empty), or only [param findings], as parallel tasks; with [param stop]
## it stops those still queued or working (changes in review stay). False when not connected.
func send_match(areas: Array, findings: Array, stop := false) -> bool:
	if _job == null or not _connected:
		return false
	var line := {"kind": "match_cancel"}
	if not stop:
		line = {"kind": "match_fix", "areas": areas, "findings": findings}
	_job.send_line(JSON.stringify(line))
	return true


## A check's fixes: with [param stop] false the chat host plans what [param check] ("access"
## or "perf") found in [param areas] (all when empty), or only [param findings], and makes the
## changes as parallel tasks; with [param stop] it stops them. False when not connected.
func send_check(check: String, areas: Array, findings: Array, stop := false, target := "") -> bool:
	if _job == null or not _connected:
		return false
	var line := {"kind": "check_cancel", "check": check}
	if not stop:
		line = {
			"kind": "check_fix",
			"check": check,
			"areas": areas,
			"findings": findings,
			"target": target
		}
	_job.send_line(JSON.stringify(line))
	return true


## The design check: with [param deep] the designer agent reads the game after Joinery's
## checks; with [param stop] the run [param design_id] stops. False when not connected.
func send_design(deep: bool, stop := false, design_id := "") -> bool:
	if _job == null or not _connected:
		return false
	var line := {"kind": "design_start", "deep": deep}
	if stop:
		line = {"kind": "design_cancel", "design_id": design_id}
	_job.send_line(JSON.stringify(line))
	return true


## "/sound-pass": with [param stop] false the chat host listens to the level ([param scene],
## or the project's) and makes the sounds [param focus] names (all when empty) as parallel
## tasks; with [param stop] it stops those still queued or working. False when not connected.
func send_sound_pass(focus: String, scene := "", stop := false) -> bool:
	if _job == null or not _connected:
		return false
	var line := {"kind": "sound_pass_cancel"}
	if not stop:
		line = {"kind": "sound_pass_start", "focus": focus, "scene": scene}
	_job.send_line(JSON.stringify(line))
	return true


## "/idle [now|clear]": the runs waiting for when you're away (bridge/idle_queue.py). The
## host answers with a notice. False when not connected.
func send_idle(action: String) -> bool:
	if not is_ready():
		return false
	_job.send_line(JSON.stringify({"kind": "idle", "action": action}))
	return true


## True while an agent is connected and takes requests.
func is_ready() -> bool:
	return _job != null and _connected


## True when [param agent] with [param profile] is the connected agent already, so choosing
## it again changes nothing.
func is_current(agent: String, profile: String) -> bool:
	return is_ready() and agent == _agent and profile == _profile


## The Mixer tab Save mix ({kind: "mixer_save", bus_layout?, tracks?}): the host writes it as
## a reviewed change (bridge/mixer_save.py). False when not connected.
func send_mixer(line: Dictionary) -> bool:
	if not is_ready():
		return false
	var message := line.duplicate(true)
	message["kind"] = "mixer_save"
	_job.send_line(JSON.stringify(message))
	return true


## "/tidy"'s Remove ({kind: "tidy_remove", ids}): the host makes the removal one change in
## Review (bridge/tidy.py); an empty [param ids] takes every sure finding. False when not
## connected.
func send_tidy(ids: Array) -> bool:
	if not is_ready():
		return false
	_job.send_line(JSON.stringify({"kind": "tidy_remove", "ids": ids}))
	return true


## "/remember <text>" ({kind: "remember", text}): the host adds the line to JOURNEYMAN.md as one
## change in Review (bridge/remember.py). False when not connected.
func send_remember(text: String) -> bool:
	if not is_ready():
		return false
	_job.send_line(JSON.stringify({"kind": "remember", "text": text}))
	return true


## Show a task's change in the Review tab.
func show_task_review(transaction_id: String) -> void:
	task_review.emit(transaction_id)


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
		_busy = false
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
		_busy = false
		if _error_detail.is_empty():
			_error_detail = "Lost connection to %s." % _display_name()
		if not _pending.is_empty():
			_notice(_error_detail, "error")
		_pending.clear()
		status_changed.emit("error", _error_detail)
		completed.emit(false)
		busy_changed.emit(false)


func _handle(event: Dictionary) -> void:
	if not str(event.get("task_id", "")).is_empty():
		task_event.emit(event)
		return
	if not str(event.get("playtest_id", "")).is_empty():
		playtest_event.emit(event)
		return
	if not str(event.get("design_id", "")).is_empty():
		design_event.emit(event)
		return
	if not str(event.get("group_id", "")).is_empty():
		variants_event.emit(event)
		return
	if str(event.get("kind", "")) == "todo":
		todo_event.emit(event)
		return
	match str(event.get("kind", "")):
		"connection":
			parallel = int(event.get("parallel", 1))
			_name = str(event.get("name", _name))
			models_received.emit(event.get("models", []))
			_connected = event.get("state") == "connected"
			if not _connected:
				_error_detail = str(
					event.get("message", "Lost connection to %s." % _display_name())
				)
			status_changed.emit(
				str(event.get("state", "error")),
				(
					_display_name() + " connected"
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
			_model_name = str(event.get("name", event.get("model", "")))
			execution_changed.emit(event)
		"usage":
			var usage := event.duplicate()
			usage["model_name"] = _model_name
			usage_received.emit(usage)
		"models":
			models_received.emit(event.get("models", []))
		"timing":
			timing_received.emit(event)
		"turn_state":
			turn_state.emit(event)
		"review_media":
			review_media.emit(event)
		"notice":
			_notice(str(event.get("text", "")), "error" if event.get("level") == "warn" else "info")
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
		"match":
			match_event.emit(event)
		"access", "perf":
			check_event.emit(event)
		"sound_pass":
			sound_pass_event.emit(event)
		"errors":
			errors_event.emit(event)
		"chat":
			chat_id = str(event.get("chat_id", ""))
			chat_changed.emit(event)
		"provider_switch":
			provider_switch.emit(event)
		"file_decision":
			file_decision.emit(event)
		"editor_action":
			editor_action.emit(event)
		"editor_view":
			_send_view(str(event.get("request_id", "")))
		"done":
			status_changed.emit("connected", _display_name() + " connected")
			_busy = false
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
	if not _name.is_empty():
		return _name
	return agent_name(_agent) if not _agent.is_empty() else "Journeyman"


## [param agent]'s name as the user knows it ("claude" -> "Claude Code").
static func agent_name(agent: String) -> String:
	return str(AGENT_NAMES.get(agent, agent))


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


## The live preview on screen was decided ([param accepted]) or dropped: the agent that made it
## hears, and the scene is free for the next one.
func finish_live_review(accepted := true) -> void:
	if _job != null and _connected:
		_job.send_line(JSON.stringify({"kind": "live_decision", "accepted": accepted}))


func review_art() -> void:
	if _job != null:
		_job.send_line(JSON.stringify({"kind": "art_review"}))


func approve_art(sha256: String) -> void:
	if _job != null:
		_job.send_line(JSON.stringify({"kind": "art_approval", "sha256": sha256}))


func reconnect() -> void:
	start(_agent, _profile, _engine, _project)


## End the connection. A request still running ends with it: the sidebar hears it failed
## and is no longer busy. True when one was running.
func close() -> bool:
	var stopped := _busy and _job != null
	_connected = false
	_pending.clear()
	_busy = false
	if _job != null:
		_job.close_pipes()
		_job = null
	if stopped:
		completed.emit(false)
		busy_changed.emit(false)
	return stopped


func _exit_tree() -> void:
	close()
