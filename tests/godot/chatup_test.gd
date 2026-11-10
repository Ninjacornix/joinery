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
	var input: TextEdit = chat._prompt
	var two_lines: float = input.custom_minimum_size.y
	input.text = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten"
	input.text_changed.emit()
	await process_frame
	var most: float = input.get_line_height() * 5
	_check(input.custom_minimum_size.y > two_lines, "The message box grows with its text")
	_check(input.custom_minimum_size.y <= most + 0.5, "The message box stops growing at five lines")
	input.clear()
	await process_frame
	_check(
		is_equal_approx(input.custom_minimum_size.y, two_lines),
		"The message box shrinks back once cleared"
	)
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


func _visible_text(node: Node) -> String:
	var texts := PackedStringArray()
	for child: Node in node.find_children("*", "", true, false):
		if child is CanvasItem and not (child as CanvasItem).is_visible_in_tree():
			continue
		if child is Label or child is Button:
			texts.append(child.get("text"))
		elif child is TextEdit:
			texts.append((child as TextEdit).placeholder_text)
	return "\n".join(texts)


## Revert is honest: "Reverting…" until Joinery confirms; a change past retention says
## "Too old to revert". The art gate holds a request and sends it again after approval.
func _run_revert_and_gate() -> void:
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "")
	var sent: Array[String] = []
	chat.prompt_submitted.connect(func(text: String, _targets: Array) -> void: sent.append(text))
	# An accepted change whose restore data was pruned.
	chat._on_submit("Darken the floor")
	chat.set_chat_busy(true)
	chat.show_file_review("Darker floor", "", [], [], "tx-old")
	chat.set_chat_busy(false)
	var old: Control = chat._transcript.current_card()
	chat._on_accept()
	chat.hide_edit_review("Change accepted", "success")
	chat._on_revertible({"tx-old": false})
	_check(not old._revert.visible, "A change past retention offers no Revert")
	_check(
		old._too_old.is_visible_in_tree() and old._too_old.text == "Too old to revert",
		"A card with revertible=false says Too old to revert"
	)
	# A change that can be reverted: not optimistic.
	chat._on_submit("Widen the door")
	chat.set_chat_busy(true)
	chat.show_file_review("Wider door", "", [], [], "tx-new")
	chat.set_chat_busy(false)
	var card: Control = chat._transcript.current_card()
	chat._on_accept()
	chat.hide_edit_review("Change accepted", "success")
	chat._on_revertible({"tx-new": true})
	_check(card._revert.visible and not card._too_old.visible, "A recent change offers Revert")
	var asked: Array[String] = []
	# Stand in for the CLI: this test confirms the revert itself.
	for link: Dictionary in chat._transcript.revert_requested.get_connections():
		chat._transcript.revert_requested.disconnect(link["callable"])
	chat._transcript.revert_requested.connect(func(id: String) -> void: asked.append(id))
	card._revert.pressed.emit()
	_check(asked == ["tx-new"], "Revert asks Joinery to revert that change")
	_check(
		card.state == "accepted" and card.reverting() and card._reverting.is_visible_in_tree(),
		"Revert shows Reverting… and keeps the change accepted until confirmed"
	)
	_check(not card._revert.visible, "Revert can't be pressed twice")
	chat._on_reverted("tx-new", false, "door.blend changed since")
	_check(
		card.state == "accepted" and card._revert.visible and card._error.visible,
		"A failed revert says why in place and offers Revert again"
	)
	card._revert.pressed.emit()
	chat._on_reverted("tx-new", true, "")
	_check(
		card.state == "reverted" and card._outcome_text.text == "Reverted",
		"The card reads Reverted once Joinery confirms"
	)
	# The art gate holds a request; approval sends it again without retyping.
	var gate_reviews := [0]
	chat.art_review_requested.connect(func() -> void: gate_reviews[0] += 1)
	chat._on_submit("Make the door look older")
	var held: Control = chat._transcript.current_card()
	chat.set_chat_busy(true)
	chat.set_chat_state("working", "Opening door.blend in Blender")
	chat._on_art_gate({"needs_art_approval": true, "resume": "Make the door look older"})
	chat.set_chat_busy(false)
	_check(held.gated(), "The art gate holds the request")
	_check(
		held.gate_text() == "Before Blender makes anything, agree how your game should look.",
		"The gate card says what to agree first"
	)
	var review: Button = held._gate.find_children("*", "Button", true, false)[0]
	_check(review.text == "Review the look", "Review the look is the primary action")
	review.pressed.emit()
	_check(gate_reviews[0] == 1, "Review the look opens the art direction")
	var before := sent.size()
	chat._on_art_approved("abc")
	_check(
		sent.size() == before + 1 and sent[-1] == "Make the door look older",
		"After approval the held request goes out again as it was"
	)
	_check(held.state == "sending" and not held._gate.visible, "The card resumes in place")
	_check(chat._transcript.caption_text() == "Art direction approved", "Approval is confirmed")
	# Discard: the panel and the card both say the files were not touched.
	chat._on_submit("Add a creak")
	chat.set_chat_busy(true)
	chat.show_file_review("Creak", "", [], [], "tx-creak")
	chat.set_chat_busy(false)
	var creak: Control = chat._transcript.current_card()
	chat._on_reject()
	chat.hide_edit_review("Change rejected. Original restored.", "rejected")
	_check(
		creak.state == "rejected" and creak._outcome_text.text == chat.DISCARDED,
		"A discard reads Discarded. Your files were not touched."
	)
	_check(
		chat._review._outcome_text.text == chat.DISCARDED,
		"The Review panel confirms the discard with the same words"
	)
	chat.queue_free()
	await process_frame


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
		chat._transcript.get_parsed_text().contains("Journeyman can use:"),
		"Show which accounts Journeyman can use"
	)
	_check(
		not chat._transcript.get_parsed_text().contains("PreToolUse"),
		"Keep CLI diagnostics out of chat"
	)
	chat.set_chat_agent("codex")
	chat.set_chat_state("connecting", "Connecting to Codex…")
	_check(chat._header._name.text == "Connecting…", "Say it is connecting, without a name")
	chat.set_chat_state("connected", "Codex connected")
	_check(chat.connection_state == "connected", "Track the real connection state")
	_check(
		chat._header._name.text.is_empty() and not chat._header._dot.visible,
		"A healthy header shows no name and no dot"
	)
	_check(chat._prompt.placeholder_text == "Ask Journeyman…", "The persona is Journeyman")
	chat._on_submit("Say hello")
	chat.set_chat_busy(true)
	_check(chat._stop.visible, "A streaming reply can be stopped")
	chat.append_agent_text("Hello ", true)
	chat.append_agent_text("世界", false)
	chat.finish_agent_response(true)
	chat.set_chat_busy(false)
	_check(
		chat._transcript.get_parsed_text().contains("Journeyman  Hello 世界"),
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
	_check(chat._header._banner_action.text == "Connect", "Offer to connect an account")
	_check(not _visible_text(chat).to_lower().contains("assistant"), "No copy says assistant")
	var connects := [0]
	chat.connect_requested.connect(func() -> void: connects[0] += 1)
	chat._header._banner_action.pressed.emit()
	_check(connects[0] == 1, "Connect asks the editor to connect")
	var undone := [0]
	chat.show_status("Focus mode on", 0.1, "Undo", func() -> void: undone[0] += 1)
	_check(
		chat._transcript.caption_text() == "Focus mode on" and not chat._footer.visible,
		"A transient note is a caption over the transcript, not a footer line"
	)
	_check(chat._transcript._caption.top_level, "The caption takes no layout height")
	chat._transcript._caption_action.pressed.emit()
	_check(undone[0] == 1, "The caption's link runs its action")
	chat.show_status("Panels hidden", 0.1)
	await create_timer(0.3).timeout
	_check(chat._transcript.caption_text().is_empty(), "The transient caption clears")
	chat.queue_free()
	await process_frame
	await _run_revert_and_gate()
	await _run_with_motion()
	print("sidebar chat: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
