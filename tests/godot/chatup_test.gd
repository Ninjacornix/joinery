extends SceneTree

var _failed := false
var _prompts: Array[Dictionary] = []


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	root.size = Vector2i(800, 800)
	var chat := load("res://addons/joinery/journeyman_dock.gd").new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	_check(
		chat.custom_minimum_size.x >= chat.get_theme_default_font_size() * 24,
		"Chat must start wide enough to read comfortably"
	)
	_check(chat.name == "Journeyman", "Restore the earlier sidebar identity")
	var before: String = chat._transcript.get_parsed_text()
	chat.log_system("Viewport toolbar shown.")
	_check(chat._transcript.get_parsed_text() == before, "System logs must not enter chat")
	chat.set_resource_target("res://audio/sfx/door_creak.wav", "AudioStreamWAV")
	_check(chat._chips.visible, "Selected audio must be visible in the composer")
	_check(
		(
			chat._composer.is_ancestor_of(chat._chips)
			and chat._composer.get_index() > chat._transcript.get_index()
		),
		"Selection context must sit near the input, below the conversation"
	)
	_check(chat.target_snapshot()[0]["kind"] == "Audio", "Identify audio targets")
	var snapshot: Array[Dictionary] = chat.target_snapshot()
	snapshot[0]["id"] = "wrong"
	_check(chat.target_snapshot()[0]["id"] != "wrong", "Target snapshots must be independent")
	chat.prompt_submitted.connect(
		func(text: String, targets: Array) -> void:
			_prompts.append({"text": text, "targets": targets})
	)
	chat._on_submit("Make this quieter")
	_check(
		_prompts[0]["targets"][0]["id"] == "res://audio/sfx/door_creak.wav", "Route audio context"
	)
	chat._prompt.text = "/"
	chat._update_commands("/")
	await process_frame
	_check(chat._command_popup.visible, "Slash must open command suggestions")
	_check(chat._matches.has("/agents"), "Suggestions must include agents")
	_check(
		chat._command_popup.get_global_rect().end.y <= chat._prompt.global_position.y,
		"Command suggestions must open above the composer"
	)
	chat._update_commands("/ag")
	_check(chat._matches == ["/agents"], "Filter partial slash commands")
	var tab := InputEventKey.new()
	tab.pressed = true
	tab.keycode = KEY_TAB
	chat._on_prompt_input(tab)
	_check(chat._prompt.text == "/agents ", "Tab must complete a command")
	chat._prompt.text = "/hel"
	chat._update_commands("/hel")
	chat._prompt.grab_focus()
	var enter := InputEventKey.new()
	enter.pressed = true
	enter.keycode = KEY_ENTER
	root.push_input(enter)
	await process_frame
	var transcript: String = chat._transcript.get_parsed_text()
	_check(transcript.contains("You  /hel"), "Enter must send the typed slash command in chat")
	_check(transcript.contains("Available commands:"), "Help must reply in chat")
	_check(transcript.count("Available commands:") == 1, "Enter must send only once")
	_check(chat._prompt.text.is_empty(), "Sending must clear the composer")
	_check(not chat._command_popup.visible, "Sending must close suggestions")
	chat._on_submit("/agents")
	var deadline := Time.get_ticks_msec() + 10000
	while chat._command_job != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(chat._command_job == null, "Agent command must complete without blocking UI")
	_check(
		chat._transcript.get_parsed_text().contains("Supported agents:"),
		"Show a conversational agent list"
	)
	_check(
		not chat._transcript.get_parsed_text().contains("PreToolUse"),
		"Keep CLI diagnostics out of chat"
	)
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	_check(chat._connection.text == "Codex connected", "Show the real connection state")
	chat.set_chat_busy(true)
	_check(chat._stop.visible, "A streaming reply can be stopped")
	chat.append_agent_text("Hello ", true)
	chat.append_agent_text("世界", false)
	chat.finish_agent_response(true)
	chat.set_chat_busy(false)
	_check(
		chat._transcript.get_parsed_text().contains("Codex  Hello 世界"),
		"Stream one agent message without repeated headers"
	)
	chat.set_chat_state("error", "Codex signed out")
	_check(chat._reconnect.visible, "Failed connections must have a recovery action")
	chat.queue_free()
	await process_frame
	print("sidebar chat: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
