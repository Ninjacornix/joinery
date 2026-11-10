extends SceneTree

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const MOTION_PATH := "res://addons/joinery/ui_motion.gd"

var _failed := false
var _prompts: Array[Dictionary] = []


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


## With motion on, every animated transition still lands on its end state, without engine
## errors (the runner fails on any "ERROR:" line).
func _run_with_motion() -> void:
	var motion: Script = load(MOTION_PATH)
	motion.call("set_enabled", true)
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "")
	chat.prompt_submitted.connect(func(_text: String, _targets: Array) -> void: pass)
	chat._prompt.grab_focus()
	chat._prompt.text = "/"
	chat._update_commands("/")
	await create_timer(0.3).timeout
	var menu: Control = chat._command_popup
	_check(menu.visible and menu.scale.is_equal_approx(Vector2.ONE), "The menu settles in place")
	menu.close()
	await create_timer(0.3).timeout
	_check(not menu.visible, "The menu shrinks back into the composer")
	chat.set_resource_target("res://audio/sfx/door_creak.wav", "AudioStreamWAV")
	chat._chips.get_child(0).remove.pressed.emit()
	_check(chat.target_snapshot().is_empty(), "A removed chip leaves the request at once")
	_check(chat._chips.visible, "The removed chip animates out before the row hides")
	await create_timer(0.3).timeout
	_check(not chat._chips.visible, "The chip row hides once the chip is gone")
	chat._prompt.clear()
	chat._on_submit("Make the hammer taller")
	chat.set_chat_busy(true)
	chat.set_chat_state("working", "Opening hammer.blend in Blender")
	var card: Control = chat._transcript.current_card()
	card.set_elapsed(4)
	card.set_elapsed(5)
	await create_timer(0.3).timeout
	_check(chat._stop.visible and not chat._send.visible, "Stop settles in Send's slot")
	_check(card._timer.text == "0:05", "The timer rolls to the elapsed time")
	_check(
		(
			card._timer.is_visible_in_tree()
			and card._timer.size.x > chat.get_theme_default_font_size()
		),
		"The timer is shown in full beside the request"
	)
	chat.show_file_review("Taller hammer", "", [], [], "tx-9")
	chat.set_chat_busy(false)
	await create_timer(0.3).timeout
	_check(chat._send.visible and not chat._stop.visible, "Send settles back after the turn")
	_check(not card._timer.visible, "The timer leaves when the change is ready")
	_check(chat._footer.text() == "Waiting for your review", "The footer waits for review")
	chat._on_accept()
	chat.hide_edit_review("Change accepted", "success")
	await create_timer(0.8).timeout
	_check(not chat._footer.visible, "The footer leaves after the decision")
	_check(card.state == "accepted" and card.scale.is_equal_approx(Vector2.ONE), "Pulse settles")
	chat.set_chat_state("error", "Lost connection to Codex.")
	await create_timer(0.3).timeout
	chat.set_chat_state("connected", "")
	await create_timer(0.3).timeout
	_check(not chat._header._banner.visible, "The banner leaves once connected")
	chat.queue_free()
	await process_frame
	motion.call("set_enabled", false)


func _run() -> void:
	load(MOTION_PATH).set_enabled(false)
	root.size = Vector2i(800, 800)
	var chat := load(DOCK_PATH).new() as Control
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
	chat._transcript.example_chosen.emit("Add a footstep sound")
	_check(chat._prompt.text == "Add a footstep sound", "An example fills the message box")
	chat._prompt.clear()
	var unsent: Control = null
	chat._on_submit("Make this quieter")
	unsent = chat._transcript.current_card()
	_check(unsent.state == "failed", "Without a chat connection the request offers Retry")
	chat.prompt_submitted.connect(
		func(text: String, targets: Array) -> void:
			_prompts.append({"text": text, "targets": targets})
	)
	unsent.retry_requested.emit()
	_check(_prompts.size() == 1 and unsent.state == "sending", "Retry sends the same request")
	_check(
		_prompts[0]["targets"][0]["id"] == "res://audio/sfx/door_creak.wav", "Route audio context"
	)
	chat.set_chat_busy(true)
	chat.set_chat_busy(false)
	_check(unsent.state == "failed", "A request the assistant never took shows Not sent")
	chat._prompt.text = "/"
	chat._update_commands("/")
	await process_frame
	_check(chat._command_popup.visible, "Slash must open command suggestions")
	_check(chat._matches.has("/art"), "Suggestions start with everyday commands")
	_check(not chat._matches.has("/agents"), "Troubleshooting waits until it is typed")
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
	_check(chat._prompt.text.is_empty(), "Sending must clear the composer")
	_check(chat._command_popup.visible, "/help opens the command menu")
	_check(
		chat._matches.has("/agents") and chat._matches.has("/art"),
		"/help lists everyday and troubleshooting commands"
	)
	_check(
		not chat._transcript.get_parsed_text().contains("/help"),
		"Commands are not posted as messages"
	)
	var escape := InputEventKey.new()
	escape.pressed = true
	escape.keycode = KEY_ESCAPE
	chat._on_prompt_input(escape)
	_check(not chat._command_popup.visible, "Escape closes the menu")
	chat._on_submit("/nonsense")
	_check(
		chat._transcript.notice_texts()[-1] == "Unknown command. Type / to see commands.",
		"Unknown commands get a notice"
	)
	chat._on_submit("/agents")
	_check(chat._footer.text() == "Running /agents…", "A running check shows in the footer")
	_check(not chat._prompt.editable, "The composer waits for the check")
	var deadline := Time.get_ticks_msec() + 10000
	while not chat._slash.running().is_empty() and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(chat._slash.running().is_empty(), "Agent command must complete without blocking UI")
	_check(chat._prompt.editable, "The composer returns after the check")
	_check(
		chat._transcript.get_parsed_text().contains("Supported assistants:"),
		"Show a conversational assistant list"
	)
	_check(
		not chat._transcript.get_parsed_text().contains("PreToolUse"),
		"Keep CLI diagnostics out of chat"
	)
	chat.set_chat_agent("codex")
	chat.set_chat_state("connecting", "Connecting to Codex…")
	_check(chat._header._name.text == "Connecting to Codex…", "Say what is connecting")
	chat.set_chat_state("connected", "Codex connected")
	_check(chat.connection_state == "connected", "Track the real connection state")
	chat._on_submit("Say hello")
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
	chat.set_chat_state("error", "Lost connection to Codex.")
	_check(chat._header._banner.visible, "Failed connections must have a recovery action")
	_check(chat._header._banner_action.text == "Reconnect", "The banner offers Reconnect")
	var reconnects := [0]
	chat.reconnect_requested.connect(func() -> void: reconnects[0] += 1)
	chat._header._banner_action.pressed.emit()
	_check(reconnects[0] == 1, "Reconnect asks the bridge to reconnect")
	chat.set_chat_agent("")
	chat.set_chat_state("error", "No assistant connected.")
	_check(chat.connection_state == "none", "No assistant chosen is its own state")
	_check(chat._header._banner_action.text == "Connect an assistant", "Offer to connect one")
	var connects := [0]
	chat.connect_requested.connect(func() -> void: connects[0] += 1)
	chat._header._banner_action.pressed.emit()
	_check(connects[0] == 1, "Connect an assistant asks the editor to connect")
	chat.show_status("Focus mode on · /focus to undo", 0.1)
	_check(chat._footer.text() == "Focus mode on · /focus to undo", "Transient footer line")
	await create_timer(0.3).timeout
	_check(not chat._footer.visible, "The transient line clears")
	chat.queue_free()
	await process_frame
	await _run_with_motion()
	print("sidebar chat: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
