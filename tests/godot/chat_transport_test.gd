extends SceneTree
## Exercise the editor transport on real nonblocking pipes, without inference.

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
	var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	root.add_child(chat)
	chat.set_chat_agent("codex")
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	root.add_child(backend)
	backend.status_changed.connect(chat.set_chat_state)
	backend.busy_changed.connect(chat.set_chat_busy)
	backend.message_delta.connect(chat.append_agent_text)
	backend.message_received.connect(chat.show_agent_message)
	backend.completed.connect(chat.finish_agent_response)
	backend._agent = "codex"
	backend._job = load("res://addons/joinery/command_job.gd").new()
	var code := (
		"import sys,json,time,os\n"
		+ "def emit(x):\n"
		+ " b=(json.dumps(x,ensure_ascii=False)+'\\n').encode()\n"
		+ " for v in b: os.write(1,bytes([v])); time.sleep(.0001)\n"
		+ "emit({'kind':'connection','state':'connected'})\n"
		+ "for line in sys.stdin:\n"
		+ " d=json.loads(line)\n"
		+ " if d['text']=='error':\n"
		+ "  emit({'kind':'connection','state':'error','message':'Sign in to Codex first.'}); break\n"
		+ " emit({'kind':'message_delta','text':'世界 '+d['targets'][0]['id'],'new_message':True})\n"
		+ " emit({'kind':'done','ok':True})\n"
	)
	_check(backend._job.start(python, PackedStringArray(["-c", code])) == OK,
		"Start a fixture host")
	var target := [{"id": "Door"}]
	backend.submit("hello", target)
	target[0]["id"] = "Wrong"
	_check(chat._busy, "Queueing a prompt must show Stop")
	var deadline := Time.get_ticks_msec() + 5000
	while chat._busy and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not chat._busy, "A reply must release the composer")
	_check(backend._connected, "Connect before sending a queued prompt")
	_check(chat._transcript.get_parsed_text().contains("Codex  世界 Door"),
		"Preserve split UTF-8 frames and the submitted target snapshot")
	backend.submit("error", [])
	deadline = Time.get_ticks_msec() + 5000
	while backend._job != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(chat._connection.text == "Sign in to Codex first.",
		"A useful connection error must survive host exit")
	_check(not chat._busy and chat._reconnect.visible, "A failed host must allow recovery")
	backend.queue_free()
	chat.queue_free()
	await process_frame
	print("chat transport: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
