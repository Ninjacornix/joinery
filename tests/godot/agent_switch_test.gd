extends SceneTree
## Switching Journeyman's agent from the editor (Settings → Replies → Switch): the Connect
## screen opens on the agent in use and waits for the user's choice; choosing the same agent
## again restarts nothing, choosing another one continues with it. A request still running
## when the agent changes ends: the sidebar is no longer busy and says why.

const CONNECT_PATH := "res://addons/joinery/agent_connect.gd"
const BRIDGE_PATH := "res://addons/joinery/chat_bridge.gd"
const SIGNED_IN := [
	{"id": "codex", "installed": true, "connected": true, "supported": true, "method": "ChatGPT"},
	{"id": "claude", "installed": true, "connected": true, "supported": true, "method": "Claude"},
]

var _failed := false


## A stand-in for the chat host's pipe.
class Job:
	extends RefCounted
	var sent: Array[Dictionary] = []
	var closed := false

	func send_line(value: String) -> void:
		sent.append(JSON.parse_string(value))

	func take_lines() -> Array[String]:
		return []

	func poll() -> Dictionary:
		return {}

	func close_pipes() -> void:
		closed = true


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 5) -> void:
	for _index: int in count:
		await process_frame


## The Connect screen with both agents signed in, its helper call replaced by [SIGNED_IN].
func _screen(initial: String, confirm: bool, emitted: Array) -> Control:
	var view: Control = load(CONNECT_PATH).new()
	view.set("initial_agent", initial)
	view.set("confirm_choice", confirm)
	view.connect("connected", func(agent: String, _method: String) -> void: emitted.append(agent))
	root.add_child(view)
	await process_frame
	var job: RefCounted = view.get("_job")
	if job != null:
		job.call("cancel")
	view.set("_job", null)
	view.call("_apply_status", {"agents": SIGNED_IN})
	view.call("_update")
	await _frames()
	return view


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	await _check_switch_dialog()
	await _check_first_run()
	await _check_same_agent()
	await _check_mid_request()
	print("agent switch: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_switch_dialog() -> void:
	var emitted: Array = []
	var view: Control = await _screen("codex", true, emitted)
	_check(emitted.is_empty(), "The switch dialog waits for a choice: %s" % [emitted])
	var cards: Dictionary = view.get("_buttons")
	_check((cards["codex"] as Button).button_pressed, "The agent in use is highlighted")
	var go: Button = view.get("_continue")
	_check(go.visible and not go.disabled, "Continue is offered for the agent in use")
	# Continue on the highlighted agent: it continues with that agent (the editor then
	# closes the dialog without a restart, see _check_same_agent).
	go.pressed.emit()
	await _frames()
	_check(emitted == ["codex"], "Continue picks the agent in use: %s" % [emitted])
	# Another agent, chosen by the user: it continues with that one.
	emitted.clear()
	(cards["claude"] as Button).pressed.emit()
	await _frames()
	_check(emitted == ["claude"], "Choosing another agent continues: %s" % [emitted])
	view.queue_free()
	await process_frame


## The Project Manager's first screen still continues on its own with a signed-in agent.
func _check_first_run() -> void:
	var emitted: Array = []
	var view: Control = await _screen("codex", false, emitted)
	_check(emitted == ["codex"], "First run continues with the saved agent: %s" % [emitted])
	view.queue_free()
	await process_frame


## The editor restarts the chat only for another agent (plugin.gd asks is_current).
func _check_same_agent() -> void:
	var bridge: Node = load(BRIDGE_PATH).new()
	root.add_child(bridge)
	bridge.set("_agent", "codex")
	bridge.set("_job", Job.new())
	bridge.set("_connected", true)
	_check(bridge.call("is_current", "codex", ""), "The agent in use is current")
	_check(not bridge.call("is_current", "claude", ""), "Another agent is not")
	_check(not bridge.call("is_current", "codex", "api_one"), "Another connection is not")
	bridge.set("_connected", false)
	_check(not bridge.call("is_current", "codex", ""), "A lost connection reconnects")
	bridge.set("_job", null)
	bridge.queue_free()
	await process_frame


## The agent changes while a request runs: it ends as failed, the sidebar stops waiting, and
## the transcript says why.
func _check_mid_request() -> void:
	var bridge: Node = load(BRIDGE_PATH).new()
	root.add_child(bridge)
	var heard: Array = []
	var notices: Array[String] = []
	bridge.connect("busy_changed", func(busy: bool) -> void: heard.append(busy))
	bridge.connect("completed", func(ok: bool) -> void: heard.append("ok" if ok else "failed"))
	bridge.connect("notice", func(text: String, _kind: String) -> void: notices.append(text))
	bridge.set("_agent", "codex")
	var job := Job.new()
	bridge.set("_job", job)
	bridge.set("_connected", true)
	bridge.call("submit", "make a hammer", [])
	_check(heard == [true], "The request is running: %s" % [heard])
	# A missing engine folder keeps the new agent's host from starting for real.
	bridge.call("start", "claude", "", "/nonexistent-joinery-engine", "/nonexistent-project")
	bridge.call("close")
	await process_frame
	_check(job.closed, "The old agent's pipe is closed")
	_check(heard == [true, "failed", false], "The request ends and busy clears: %s" % [heard])
	_check(
		notices == ["Stopped: switched to Claude Code."], "The transcript says why: %s" % [notices]
	)
	# Closing with nothing running says nothing more.
	heard.clear()
	bridge.call("close")
	_check(heard.is_empty(), "An idle close is quiet: %s" % [heard])
	# Switching with nothing running: the transcript says which agent answers now.
	notices.clear()
	bridge.call("start", "opencode", "", "/nonexistent-joinery-engine", "/nonexistent-project")
	bridge.call("close")
	_check(notices == ["Now using opencode."], "The switch is said: %s" % [notices])
	bridge.call("start", "opencode", "", "/nonexistent-joinery-engine", "/nonexistent-project")
	bridge.call("close")
	_check(notices.size() == 1, "A reconnect to the same agent says nothing: %s" % [notices])
	bridge.queue_free()
	await process_frame
