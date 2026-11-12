extends SceneTree
## Another agent carries a request on when the provider runs out of quota (ADR 0080): the
## offer shows one "Continue with <agent>" action under the transcript, pressing it sends that
## agent to the chat host, a switch is said in the transcript, and a declined offer goes away.
## The chat host's job is a stand-in: its events arrive as JSON lines, like the real pipe.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const OFFER := "Codex ran out of quota. The change so far stays staged."
const SAID := "Claude Code carries this request on from where Codex stopped."

var _failed := false


class Job:
	extends RefCounted
	## What the sidebar sent to the host.
	var sent: Array[Dictionary] = []
	## What the host says next.
	var incoming: Array[String] = []

	func send_line(value: String) -> void:
		sent.append(JSON.parse_string(value))

	func take_lines() -> Array[String]:
		var lines := incoming.duplicate()
		incoming.clear()
		return lines

	func poll() -> Dictionary:
		return {}


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _hear(job: Job, event: Dictionary) -> void:
	job.incoming.append(JSON.stringify(event))
	await process_frame
	await process_frame


func _offer(to: String, to_name: String, message: String) -> Dictionary:
	return {
		"kind": "provider_switch",
		"state": "offer",
		"from": "codex",
		"to": to,
		"to_name": to_name,
		"message": message,
	}


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	chat.add_child(backend)
	await process_frame
	var job := Job.new()
	backend.set("_job", job)
	backend.set("_connected", true)
	var transcript: Node = chat.get("_transcript")
	var action: Button = transcript.get("_caption_action")

	# Codex ran out of quota: one action offers Claude Code; the change stays staged.
	await _hear(job, _offer("claude", "Claude Code", OFFER))
	var caption: String = transcript.call("caption_text")
	_check(caption == OFFER, "The offer is shown: %s" % caption)
	_check(action.visible and action.text == "Continue with Claude Code", action.text)
	_check(job.sent.is_empty(), "Nothing is sent before the user answers")

	# Continue with Claude Code: the host hears the agent the user chose.
	action.pressed.emit()
	await process_frame
	_check(job.sent.size() == 1, "One answer sent: %s" % [job.sent])
	if job.sent.size() == 1:
		_check(job.sent[0].get("kind") == "provider_switch", "%s" % [job.sent[0]])
		_check(job.sent[0].get("agent") == "claude", "%s" % [job.sent[0]])

	# The host switched: the caption goes and the transcript says who carries on.
	await _hear(job, {"kind": "provider_switch", "state": "switched", "message": SAID})
	_check(transcript.call("caption_text") == "", "The offer is gone once switched")
	var notices: PackedStringArray = transcript.call("notice_texts")
	_check(notices.has(SAID), "%s" % [notices])

	# Another offer, declined (or timed out) by the host: it goes away, nothing is sent.
	job.sent.clear()
	await _hear(job, _offer("gemini", "Gemini CLI", "Claude Code ran out of quota."))
	_check(action.text == "Continue with Gemini CLI", action.text)
	await _hear(job, {"kind": "provider_switch", "state": "declined"})
	_check(transcript.call("caption_text") == "", "A declined offer goes away")
	_check(job.sent.is_empty(), "Nothing is sent for a declined offer")

	# Without a connection the answer goes nowhere.
	backend.set("_connected", false)
	backend.call("switch_provider", "claude")
	_check(job.sent.is_empty(), "No answer without a connection")

	backend.set("_job", null)
	chat.queue_free()
	await process_frame
	print("provider switch: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
