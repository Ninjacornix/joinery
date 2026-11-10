# gdlint: disable=max-public-methods
@tool
extends PanelContainer
## Journeyman, the sidebar chat. Top to bottom: the header (assistant status, settings menu)
## and its error banner, the transcript of change cards and replies, the status footer, and the
## composer. The Review panel is built here but lives in the editor's bottom panel
## ([method take_review_panel]); decisions are made only there.
##
## Sending is optimistic: the request card appears at once in a "sending" state (Stop already
## in Send's slot) and resolves when the assistant picks it up, or offers Retry. A request
## lost with the connection turns the header dot red and disables Send, leaving Retry as the
## one action; once the banner shows Reconnect, the card says only "Not sent" until it is
## back. While a step is live on the card the footer stays out of the way. A chat bridge added
## as a child is attached automatically ([method attach_bridge]) for send receipts, notices
## and transaction ids.

signal setup_requested
signal focus_toggle_requested
signal toolbar_toggle_requested
signal prompt_submitted(text: String, targets: Array)
signal accept_edit_requested
signal reject_edit_requested
signal cancel_requested
signal reconnect_requested
## "Connect an assistant" in the no-assistant banner.
signal connect_requested
signal art_review_requested
signal review_requested
signal review_closed
signal selection_options_changed(selection: Dictionary)
signal models_refresh_requested
signal target_opened(path: String)
## An accepted change was reverted on disk; the editor should rescan and reload its files.
signal change_reverted(transaction_id: String)

const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const HISTORY_SCRIPT := preload("res://addons/joinery/chat_history.gd")
const SETTINGS_SCRIPT := preload("res://addons/joinery/journeyman_settings.gd")
const CHIP_SCRIPT := preload("res://addons/joinery/target_chip.gd")
const HEADER_SCRIPT := preload("res://addons/joinery/sidebar_header.gd")
const FOOTER_SCRIPT := preload("res://addons/joinery/sidebar_footer.gd")
const COMPOSER_SCRIPT := preload("res://addons/joinery/sidebar_composer.gd")
const MENU_SCRIPT := preload("res://addons/joinery/command_menu.gd")
const SLASH_SCRIPT := preload("res://addons/joinery/slash_commands.gd")
const REVIEW_PATH := "res://addons/joinery/review_panel.gd"
const REVIEW_WAIT := "Accept or reject the change first"
## Minimum dock width in multiples of the font size.
const MIN_EM := 24

## "connected", "connecting", "error" or "none" (no assistant chosen).
var connection_state := "connecting"
var _content: VBoxContainer
var _header: HEADER_SCRIPT
var _transcript: HISTORY_SCRIPT
var _settings: SETTINGS_SCRIPT
var _footer: FOOTER_SCRIPT
var _composer: COMPOSER_SCRIPT
var _prompt: TextEdit
var _chips: HFlowContainer
var _options: MenuButton
var _send: Button
var _stop: Button
var _command_layer: CanvasLayer
var _command_popup: MENU_SCRIPT
var _matches: Array[String]:
	get:
		return _command_popup.matches
var _slash: SLASH_SCRIPT
var _review: Control
var _review_pending := false
var _details_open := false
var _target_context: Array[Dictionary] = []
var _busy := false
var _agent_name := "Codex"
var _assistant_open := false
var _started_msec := 0
var _last_elapsed := -1
var _progress := ""
var _outcome := "done"
var _failure := ""
var _active_card: Control
var _bridge: Node


func _init() -> void:
	name = "Journeyman"
	child_entered_tree.connect(_on_child_entered)


func _ready() -> void:
	UI_THEME.watch(self)
	theme_type_variation = "JoineryDock"
	custom_minimum_size.x = get_theme_default_font_size() * MIN_EM
	_content = VBoxContainer.new()
	_content.theme_type_variation = "JoineryRow"
	add_child(_content)
	_header = HEADER_SCRIPT.new()
	_header.reconnect_pressed.connect(func() -> void: reconnect_requested.emit())
	_header.connect_pressed.connect(func() -> void: connect_requested.emit())
	_content.add_child(_header)
	_transcript = HISTORY_SCRIPT.new()
	_transcript.accessibility_name = "Conversation"
	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_transcript.example_chosen.connect(_fill_prompt)
	_transcript.retry_requested.connect(_retry)
	_transcript.revert_requested.connect(func(id: String) -> void: _slash.revert(id))
	_content.add_child(_transcript)
	_footer = FOOTER_SCRIPT.new()
	_footer.review_requested.connect(func() -> void: review_requested.emit())
	_content.add_child(_footer)
	_composer = COMPOSER_SCRIPT.new()
	_composer.agent_name = _agent_name
	_composer.submitted.connect(_on_submit)
	_composer.stop_pressed.connect(_request_cancel)
	_content.add_child(_composer)
	_prompt = _composer.prompt
	_chips = _composer.chips
	_options = _composer.options
	_send = _composer.send
	_stop = _composer.stop
	_prompt.text_changed.connect(func() -> void: _update_commands(_prompt.text))
	_prompt.gui_input.connect(_on_prompt_input)
	_options.changed.connect(func(value: Dictionary) -> void: selection_options_changed.emit(value))
	_command_layer = CanvasLayer.new()
	_command_layer.layer = 100
	add_child(_command_layer)
	_command_popup = MENU_SCRIPT.new()
	_command_popup.anchor = _composer
	_command_popup.theme = theme
	_command_popup.chosen.connect(_on_command_chosen)
	_command_layer.add_child(_command_popup)
	_slash = SLASH_SCRIPT.new()
	_slash.requested.connect(_on_slash_requested)
	_slash.started.connect(func(_command: String) -> void: _sync())
	_slash.finished.connect(_on_slash_finished)
	_slash.reverted.connect(_on_reverted)
	add_child(_slash)
	_review = _make_review()
	_settings = SETTINGS_SCRIPT.attach(self, _header.row, _content)
	_show_connection()
	_settings.apply_all()


func _exit_tree() -> void:
	if _slash != null:
		_slash.cancel()


func _notification(what: int) -> void:
	if (
		what == NOTIFICATION_PREDELETE
		and is_instance_valid(_review)
		and _review.get_parent() == null
	):
		_review.free()


func _on_theme_rebuilt() -> void:
	custom_minimum_size.x = get_theme_default_font_size() * MIN_EM
	if _command_popup != null:
		_command_popup.theme = theme


func _process(_delta: float) -> void:
	if _busy and _started_msec > 0:
		var seconds := int((Time.get_ticks_msec() - _started_msec) / 1000.0)
		if seconds != _last_elapsed:
			_last_elapsed = seconds
			var card := _card()
			if card != null:
				card.set_elapsed(seconds)
	if _command_popup != null and _command_popup.visible:
		_command_popup.place()


func _input(event: InputEvent) -> void:
	var click := event as InputEventMouseButton
	if click == null or not click.pressed or not _command_popup.visible:
		return
	var inside_menu := _command_popup.get_global_rect().has_point(click.position)
	if not inside_menu and not _prompt.get_global_rect().has_point(click.position):
		_command_popup.close()


# --- Bridge ------------------------------------------------------------------------------------


## Listen to a chat bridge for send receipts, Joinery notices and transaction ids. The plugin's
## own wiring of the other bridge signals is unchanged.
func attach_bridge(bridge: Node) -> void:
	_bridge = bridge
	var links := {"sent": _on_sent, "notice": _on_notice, "file_preview": _on_bridge_preview}
	for signal_name: String in links:
		if (
			bridge.has_signal(signal_name)
			and not bridge.is_connected(signal_name, links[signal_name])
		):
			bridge.connect(signal_name, links[signal_name])


func _on_child_entered(node: Node) -> void:
	if node.has_signal("sent") and node.has_signal("file_preview"):
		attach_bridge(node)


func _on_sent() -> void:
	var card := _card()
	if card != null:
		card.set_sent()
	_sync_send_slot()


func _on_notice(text: String, kind: String) -> void:
	var card := _card()
	if card != null and card.state == "sending" and kind == "error":
		# The card says it in place ("Not sent. …") with Retry.
		_failure = text
		return
	_transcript.add_notice(text, kind)


func _on_bridge_preview(event: Dictionary) -> void:
	var card := _card()
	if card != null and card.transaction_id.is_empty():
		card.transaction_id = str(event.get("transaction_id", ""))


# --- Composer and commands ---------------------------------------------------------------------


func _update_commands(text: String) -> void:
	if not text.begins_with("/") or text.contains(" ") or text.contains("\n") or _busy:
		_command_popup.close()
		return
	_command_popup.open_for(text)


func _on_prompt_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or not _command_popup.visible:
		return
	match key.keycode:
		KEY_UP, KEY_DOWN:
			_command_popup.move(-1 if key.keycode == KEY_UP else 1)
		KEY_TAB:
			_complete(_command_popup.current())
		KEY_ENTER, KEY_KP_ENTER:
			if key.shift_pressed:
				_command_popup.close()
				return
			var typed := _prompt.text.strip_edges()
			var exact := SLASH_SCRIPT.COMMANDS.has(typed.split(" ")[0].to_lower())
			_on_submit(typed if exact else _command_popup.current())
		KEY_ESCAPE:
			_command_popup.close()
		_:
			return
	_prompt.accept_event()


func _complete(command: String) -> void:
	if command.is_empty():
		return
	_command_popup.close()
	_prompt.text = command + " "
	_prompt.set_caret_column(_prompt.text.length())
	_prompt.grab_focus()


func _on_command_chosen(command: String) -> void:
	_prompt.clear()
	_on_submit(command)


func _fill_prompt(text: String) -> void:
	_prompt.text = text
	_prompt.set_caret_column(text.length())
	_prompt.grab_focus()


func _on_submit(text: String) -> void:
	var entry := text.strip_edges()
	if entry.is_empty() or _busy or _review_pending:
		return
	_command_popup.close()
	_prompt.clear()
	if entry.begins_with("/"):
		run_command(entry)
		return
	_assistant_open = false
	_send_request(_transcript.add_request(entry, target_snapshot()))


func _send_request(card: Control) -> void:
	_active_card = card
	_failure = ""
	if not card.is_connected("failed", _on_card_failed):
		card.connect("failed", _on_card_failed)
	card.set_sending()
	_sync_send_slot()
	if prompt_submitted.get_connections().is_empty():
		card.set_failed("Open this project through Joinery to chat.")
		return
	prompt_submitted.emit(card.request, card.targets)


func _retry(card: Control) -> void:
	if not _busy and not _review_pending:
		_send_request(card)


## A request that never reached the assistant. When the connection went with it, every
## indicator says so: the dot turns red and Send rests, while the card's Retry is the action.
func _on_card_failed(reason: String) -> void:
	_sync_send_slot()
	if connection_state == "error" or reason.to_lower().contains("connection"):
		connection_state = "error"
		_header.show_state("error", _agent_name, reason, false)
		_composer.set_connection(connection_state)
		_transcript.set_examples_enabled(false)


## Stop takes Send's slot while the assistant works or a request is on its way to it.
func _sync_send_slot() -> void:
	var card := _card()
	_composer.set_busy(_busy or (card != null and card.state == "sending"))


## Run a slash command ("/doctor", "/help", …) without posting it as a message. Unknown
## commands get a notice.
func run_command(entry: String) -> void:
	if entry == "reconnect":
		reconnect_requested.emit()
		return
	var command := entry if entry.begins_with("/") else "/" + entry
	if not _slash.run(command):
		_transcript.add_notice("Unknown command. Type / to see commands.", "info")


func _on_slash_requested(command: String) -> void:
	match command:
		"/help":
			_prompt.grab_focus()
			_command_popup.open_for("", true)
		"/setup":
			setup_requested.emit()
		"/art":
			art_review_requested.emit()
		"/models":
			models_refresh_requested.emit()
		"/focus":
			focus_toggle_requested.emit()
		"/toolbar":
			toolbar_toggle_requested.emit()


func _on_slash_finished(_command: String, text: String, kind: String) -> void:
	_sync()
	_transcript.add_notice(text, kind)


func _on_reverted(transaction_id: String, ok: bool, message: String) -> void:
	var card := _transcript.card_for(transaction_id)
	if card != null:
		card.revert_finished(ok, message)
	if ok:
		show_status("Change reverted")
		change_reverted.emit(transaction_id)


## Footer and composer follow the state: a running check, a reply, a pending review, or idle.
func _sync() -> void:
	var command: String = _slash.running()
	if not command.is_empty():
		_footer.set_mode("command", command)
	elif _busy:
		# A step live on the card is the one indicator; the footer only covers the rest.
		var card := _card()
		var shown: bool = card != null and card.shows_step(_progress)
		_footer.set_mode("idle" if shown else "busy", _progress)
	elif _review_pending:
		_footer.set_mode("review")
	else:
		_footer.set_mode("idle")
	_composer.set_locked(_review_pending or not command.is_empty())
	_composer.set_placeholder(REVIEW_WAIT if _review_pending else "")


func _request_cancel() -> void:
	var card := _card()
	if not _busy and card != null and card.state == "sending":
		# Stopped before the assistant took it: the request stays here, ready to Retry.
		card.set_failed()
		cancel_requested.emit()
		return
	_progress = "Stopping…"
	_composer.stopping()
	_sync()
	cancel_requested.emit()


## A transient footer line ("Focus mode on · /focus to undo") that clears after [param seconds].
func show_status(text: String, seconds := 3.0) -> void:
	_footer.show_status(text, seconds)


## Apply settings: {mode, text_scale, show_timings, details_open}; missing keys are kept.
func apply_preferences(prefs: Dictionary) -> void:
	if prefs.has("mode"):
		_options.set_mode(str(prefs["mode"]))
	if prefs.has("text_scale"):
		_transcript.set_text_scale(float(prefs["text_scale"]))
	if prefs.has("show_timings"):
		_transcript.set_show_timings(bool(prefs["show_timings"]))
	if prefs.has("details_open"):
		_details_open = bool(prefs["details_open"])
		_review_call("set_details_open", [_details_open])


# --- Targets -----------------------------------------------------------------------------------


func on_selection_changed() -> void:
	if Engine.is_editor_hint():
		_set_targets(CHIP_SCRIPT.from_selection())


func set_resource_target(path: String, type: String = "Resource", source: String = "") -> void:
	if not path.is_empty():
		_set_targets([CHIP_SCRIPT.for_resource(path, type, source)])


func target_snapshot() -> Array[Dictionary]:
	return _target_context.duplicate(true)


func _set_targets(targets: Array[Dictionary]) -> void:
	_target_context = targets.duplicate(true)
	for child: Node in _chips.get_children():
		_chips.remove_child(child)
		child.queue_free()
	for target: Dictionary in _target_context:
		var id := str(target["id"])
		var chip := CHIP_SCRIPT.build(self, target, target_opened.emit, _remove_target.bind(id))
		chip.connect("removed", func() -> void: _sync_chips.call_deferred())
		_chips.add_child(chip)
	_sync_chips()


## Optimistic: the target leaves the request at once; the chip row hides after it animates out.
func _remove_target(id: String) -> void:
	var remaining: Array[Dictionary] = []
	for target: Dictionary in _target_context:
		if str(target["id"]) != id:
			remaining.append(target)
	_target_context = remaining


func _sync_chips() -> void:
	_chips.visible = not _target_context.is_empty()


# --- Assistant state ---------------------------------------------------------------------------


func set_chat_agent(agent: String) -> void:
	_agent_name = agent.capitalize()
	if _composer != null:
		_composer.agent_name = _agent_name
		_composer.set_placeholder(REVIEW_WAIT if _review_pending else "")
		_header.show_state(connection_state, _agent_name)
		_transcript.set_examples_enabled(connection_state == "connected")


func set_chat_state(state: String, detail: String) -> void:
	if state == "working":
		_progress = detail
		var card := _card()
		if card != null and _busy:
			card.add_step(detail)
		_sync()
		return
	connection_state = "none" if state == "error" and _agent_name.is_empty() else state
	_show_connection(detail if state == "error" else "")


## Header, Send and the example prompts all follow [member connection_state]. While the
## header's banner offers the fix (Reconnect, Connect), unsent cards say only "Not sent".
func _show_connection(detail := "") -> void:
	_header.show_state(connection_state, _agent_name, detail)
	_composer.set_connection(connection_state)
	_transcript.set_examples_enabled(connection_state == "connected")
	_transcript.set_retry_allowed(not connection_state in ["error", "none"])


func set_chat_busy(busy: bool) -> void:
	var was_busy := _busy
	_busy = busy
	if busy:
		_started_msec = Time.get_ticks_msec()
		_last_elapsed = -1
		_progress = ""
		_outcome = "done"
		_command_popup.close()
	else:
		_started_msec = 0
		var card := _card()
		if card != null and was_busy:
			if card.state == "sending":
				card.set_failed(_failure)
			else:
				card.finish(_outcome)
	_sync_send_slot()
	_sync()


func set_model_options(models: Array) -> void:
	_options.set_models(models)


func restore_selection_options(selection: Dictionary) -> void:
	_options.restore(selection)


func show_execution(event: Dictionary) -> void:
	_options.show_execution(event)
	_on_sent()
	_progress = "Thinking…"
	_sync_send_slot()
	_sync()


func show_timing(event: Dictionary) -> void:
	var outcome := str(event.get("outcome", ""))
	_outcome = (
		"done" if outcome == "done" else "cancelled" if outcome == "cancelled" else "failed"
	)
	var placed := _transcript.show_timing(event)
	if _outcome == "cancelled" and not placed:
		_transcript.add_decision("Stopped", "stopped", _card())


func append_agent_text(text: String, new_message: bool) -> void:
	_on_sent()
	_transcript.append_delta(_agent_name, text, new_message or not _assistant_open)
	_assistant_open = true


func show_agent_message(text: String) -> void:
	var card := _card()
	if card != null and card.state == "sending" and _bridge != null:
		# Before the bridge sent the request, anything it says is a delivery failure.
		_failure = text
		_transcript.add_notice(text, "error")
		return
	_on_sent()
	finish_agent_response(false)
	_transcript.add_message(_agent_name, text)


func finish_agent_response(_ok: bool) -> void:
	_transcript.finish_response()
	_assistant_open = false


func log_system(what: String) -> void:
	print("[Joinery] ", what)


func _card() -> Control:
	if is_instance_valid(_active_card):
		return _active_card
	return _transcript.current_card() if _transcript != null else null


# --- Review ------------------------------------------------------------------------------------


## The Review panel for the editor's bottom panel (review_panel.gd).
func take_review_panel() -> Control:
	return _review


func show_edit_review(summary: String, count := 0) -> void:
	_review_call("show_scene_review", [summary, count])
	_open_review([])


func show_file_review(
	summary: String, text_diff: String, media: Array = [], changes: Array = [], transaction_id := ""
) -> void:
	_review_call("show_file_review", [summary, text_diff, media, changes])
	_review_call("set_details_open", [_details_open])
	_open_review(media)
	var card := _card()
	if card != null and not transaction_id.is_empty():
		card.transaction_id = transaction_id


## Close the review. [param kind] is "success", "rejected", "info" or "error"; the message is
## shown on the request's card, or as a notice when there is none.
func hide_edit_review(message: String = "", kind := "info") -> void:
	var was_pending := _review_pending
	_review_pending = false
	var decided := _decision_kind(message, kind)
	if decided == "success" and was_pending and _review.has_method("show_result"):
		_review.call("show_result", true, message)
	else:
		_review_call("clear", [])
	_sync()
	if was_pending:
		review_closed.emit()
	if message.is_empty():
		return
	finish_agent_response(false)
	_transcript.add_decision(message, decided, _card() if was_pending else null)


func set_review_applying(on: bool) -> void:
	_review_call("set_applying", [on])


## The decision failed: the panel shows why and takes the decision again.
func show_review_error(text: String) -> void:
	_review_call("show_error", [text])
	var card := _card()
	if card != null:
		card.roll_back()


func _open_review(media: Array) -> void:
	_review_pending = true
	var card := _card()
	if card != null:
		card.set_ready(media)
	_command_popup.close()
	_sync()
	review_requested.emit()


func _on_accept() -> void:
	var card := _card()
	if card != null and card.awaiting_decision():
		card.set_outcome("success", "Change accepted", true)
	accept_edit_requested.emit()


func _on_reject() -> void:
	var card := _card()
	if card != null and card.awaiting_decision():
		card.set_outcome("rejected", "Change rejected. Original restored.", true)
	reject_edit_requested.emit()


func _make_review() -> Control:
	var panel: Control = null
	if ResourceLoader.exists(REVIEW_PATH):
		var script := load(REVIEW_PATH) as Script
		if script != null and script.can_instantiate():
			panel = script.new() as Control
	if panel == null:
		panel = VBoxContainer.new()
	panel.name = "Review"
	var links := {
		"accept_pressed": _on_accept,
		"reject_pressed": _on_reject,
		"show_requested": func() -> void: review_requested.emit(),
	}
	for signal_name: String in links:
		if panel.has_signal(signal_name):
			panel.connect(signal_name, links[signal_name])
	if panel.has_method("set_details_open"):
		panel.call("set_details_open", _details_open)
	return panel


func _review_call(method: String, arguments: Array) -> void:
	if is_instance_valid(_review) and _review.has_method(method):
		_review.callv(method, arguments)


## Kind of a decision message; older callers pass only the message.
static func _decision_kind(message: String, kind: String) -> String:
	if kind != "info":
		return kind
	var lower := message.to_lower()
	if lower.contains("accepted"):
		return "success"
	if lower.contains("rejected"):
		return "rejected"
	return "info"


func _engine_dir() -> String:
	return SLASH_SCRIPT.engine_dir()
