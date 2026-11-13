extends SceneTree
## A failed connection's banner offers the fix for its reason (Get, Sign in, Update, Repair,
## Reconnect), always "Switch agent", and "Copy details" when the agent left error output.
## The chat host's events arrive through a stand-in pipe, like the real one.

const HEADER_PATH := "res://addons/joinery/sidebar_header.gd"
const BRIDGE_PATH := "res://addons/joinery/chat_bridge.gd"

var _failed := false


class Job:
	extends RefCounted
	var incoming: Array[String] = []
	var result: Dictionary = {}

	func send_line(_value: String) -> void:
		pass

	func take_lines() -> Array[String]:
		var lines := incoming.duplicate()
		incoming.clear()
		return lines

	func poll() -> Dictionary:
		return result

	func close_pipes() -> void:
		pass


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	await _check_actions()
	await _check_bridge()
	print("connection banner: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_actions() -> void:
	var header: VBoxContainer = load(HEADER_PATH).new()
	root.add_child(header)
	await process_frame
	var cases := [
		[{"reason": "not_installed", "name": "Claude Code"}, "Get Claude Code"],
		[{"reason": "signed_out", "name": "Claude Code"}, "Sign in"],
		[{"reason": "outdated", "name": "Codex CLI"}, "Update Codex CLI"],
		[{"reason": "gate", "name": "Gemini CLI"}, "Repair"],
		[{"reason": "sandbox", "name": "opencode"}, "Reconnect"],
		[{"reason": "crashed", "name": "Claude Code"}, "Reconnect"],
		[{}, "Reconnect"],
	]
	for each: Array in cases:
		header.call("show_problem", each[0])
		header.call("show_state", "error", "Claude Code", "It failed.")
		var action: String = header.call("banner_action")
		_check(action == each[1], "%s offers %s, not %s" % [each[0], each[1], action])
		var links: PackedStringArray = header.call("banner_links")
		_check(links == PackedStringArray(["Switch agent"]), "Switch agent always: %s" % [links])
	header.call("show_problem", {"reason": "crashed", "details": "Traceback…"})
	header.call("show_state", "error", "Claude Code", "It stopped.")
	var with_details: PackedStringArray = header.call("banner_links")
	_check(with_details.has("Copy details"), "Details can be copied: %s" % [with_details])
	# Sign in and Switch agent open the Connect screen; Reconnect reconnects.
	var heard: Array[String] = []
	header.connect("connect_pressed", func() -> void: heard.append("connect"))
	header.connect("reconnect_pressed", func() -> void: heard.append("reconnect"))
	header.call("show_problem", {"reason": "signed_out"})
	header.call("show_state", "error", "Claude Code", "Signed out.")
	var buttons := header.find_children("*", "Button", true, false)
	for button: Button in buttons:
		if button.text in ["Sign in", "Switch agent"]:
			button.pressed.emit()
	header.call("show_problem", {"reason": "crashed"})
	header.call("show_state", "error", "Claude Code", "Stopped.")
	for button: Button in buttons:
		if button.text == "Reconnect":
			button.pressed.emit()
	_check(heard == ["connect", "connect", "reconnect"], "The actions: %s" % [heard])
	# Connected again: no banner.
	header.call("show_problem", {})
	header.call("show_state", "connected", "Claude Code")
	await process_frame
	header.queue_free()
	await process_frame


## The host's connection error reaches the sidebar with its reason before the error state.
func _check_bridge() -> void:
	var bridge: Node = load(BRIDGE_PATH).new()
	root.add_child(bridge)
	var heard: Array = []
	bridge.connect("connection_problem", func(problem: Dictionary) -> void: heard.append(problem))
	bridge.connect(
		"status_changed", func(state: String, _detail: String) -> void: heard.append(state)
	)
	bridge.set("_agent", "claude")
	var job := Job.new()
	bridge.set("_job", job)
	var event := {
		"kind": "connection",
		"state": "error",
		"agent": "claude",
		"name": "Claude Code",
		"message": "Sign in with Claude Code before connecting this chat.",
		"reason": "signed_out",
	}
	job.incoming.append(JSON.stringify(event))
	await process_frame
	await process_frame
	_check(heard.size() == 2 and heard[1] == "error", "Problem, then error: %s" % [heard])
	if heard.size() == 2:
		_check(heard[0].get("reason") == "signed_out", "The reason arrives: %s" % [heard[0]])
	# The host itself stops: its last error lines are the details, keys taken out.
	heard.clear()
	bridge.set("_error_detail", "")
	var lines: Array[String] = []
	for index: int in 30:
		lines.append("line %d" % index)
	lines.append("OPENAI_API_KEY=sk-abcdefghijklmnop")
	job.result = {"code": 1, "output": "", "error": "\n".join(lines)}
	await process_frame
	await process_frame
	_check(heard.size() == 2 and heard[1] == "error", "Crash, then error: %s" % [heard])
	if heard.size() == 2:
		var details: String = heard[0].get("details", "")
		_check(heard[0].get("reason") == "crashed", "A stopped host is a crash: %s" % [heard[0]])
		_check(details.split("\n").size() == 20, "The last 20 lines: %s" % details)
		_check(not details.contains("sk-abcdefghijklmnop"), "No key in details: " + details)
	bridge.queue_free()
	await process_frame
