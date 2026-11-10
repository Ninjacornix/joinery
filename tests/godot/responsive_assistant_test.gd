extends SceneTree
## Provider-reported options, progress, cancellation, change cards and light/dark layout.

var _failed := false
var _selection: Dictionary = {}


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _count(node: Node, script_path: String) -> int:
	var found := 0
	for child: Node in node.find_children("*", "", true, false):
		var script := child.get_script() as Script
		if script != null and script.resource_path.ends_with(script_path):
			found += 1
	return found


func _texts(node: Node) -> PackedStringArray:
	var texts := PackedStringArray()
	for child: Node in node.find_children("*", "Label", true, false):
		if (child as Label).is_visible_in_tree():
			texts.append((child as Label).text)
	return texts


func _capture(name: String) -> void:
	var capture := OS.get_environment("JOINERY_CAPTURE_DIR")
	if capture.is_empty() or DisplayServer.get_name() == "headless":
		return
	await process_frame
	await process_frame
	RenderingServer.force_draw()
	var image := root.get_texture().get_image()
	_check(image.save_png(capture.path_join(name)) == OK, "Save " + name)


func _run() -> void:
	# Captures run in a window, where motion would otherwise be on: keep states deterministic.
	load("res://addons/joinery/ui_motion.gd").set_enabled(false)
	root.size = Vector2i(1000, 760)
	for light: bool in [false, true]:
		var theme_name := "light" if light else "dark"
		var host := Control.new()
		host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		host.theme = Theme.new()
		host.theme.set_color("base_color", "Editor", Color.WHITE if light else Color.BLACK)
		root.add_child(host)
		var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
		host.add_child(chat)
		chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		chat.selection_options_changed.connect(func(value: Dictionary) -> void: _selection = value)
		await process_frame
		_check(not chat._footer.visible, "The footer is hidden when idle")
		_check(not "Ready" in _texts(chat), "The sidebar never says Ready")
		_check(chat._transcript._empty.visible, "An empty chat shows what to do first")
		(
			chat
			. set_model_options(
				[
					{
						"id": "fixture-sol",
						"name": "GPT-6.1 Sol",
						"efforts": ["low", "medium", "high"]
					},
					{"id": "fixture-luna", "name": "GPT-6 Luna", "efforts": ["low", "high"]},
				]
			)
		)
		_check(
			(
				_count(chat._composer, "composer_options.gd") == 1
				and chat._composer.find_children("*", "OptionButton", true, false).is_empty()
			),
			"Exactly one options picker in the composer"
		)
		_check(
			chat._options.model_items() == ["Automatic", "GPT-6.1 Sol", "GPT-6 Luna"],
			"Offer only reported models plus Automatic"
		)
		chat._options._on_model(1)
		_check(_selection.get("model") == "fixture-sol", "Choosing a model reports it")
		_check(_selection.get("effort") == "", "The mode decides the effort")
		var labels: Array[String] = []
		var popup: PopupMenu = chat._options.get_popup()
		for index: int in popup.item_count:
			labels.append(popup.get_item_text(index))
		_check(not labels.has("Effort"), "There is no Effort menu")
		_check(labels.has("Model: GPT-6.1 Sol"), "The menu names the chosen model")
		chat.restore_selection_options({"mode": "fast", "model": "fixture-sol", "effort": "ultra"})
		_check(chat._options.selection()["effort"] == "", "A saved effort is dropped")
		_check(chat._options.text == "Fast", "The closed picker shows the mode only")
		_check(chat._options.tooltip_text.contains("GPT-6.1 Sol"), "The tooltip names the model")
		chat._options._on_mode(2)
		_check(
			_selection["model"] == "fixture-sol" and _selection["mode"] == "thorough",
			"Send explicit model/mode"
		)
		chat.set_chat_agent("codex")
		chat.set_chat_state("connected", "Codex connected")
		_check(chat._header._name.text.is_empty(), "A healthy header names nobody")
		_check(not chat._header._banner.visible, "No banner while connected")
		chat.set_resource_target("res://models/assembly.glb", "PackedScene")
		var submitted := {"count": 0, "text": "", "targets": []}
		chat.prompt_submitted.connect(
			func(message: String, targets: Array) -> void:
				submitted["count"] += 1
				submitted["text"] = message
				submitted["targets"] = targets
		)
		chat._on_submit("Make this assembly a little wider")
		await process_frame
		var card: Control = chat._transcript.current_card()
		_check(card != null and card.state == "sending", "A request shows at once, sending")
		_check(
			submitted["count"] == 1 and submitted["targets"].size() == 1,
			"The request goes out once with the selected resource"
		)
		_check(not chat._transcript._empty.visible, "The empty state leaves after a message")
		chat.set_chat_busy(true)
		chat.show_execution({"name": "GPT-6.1 Sol", "mode": "fast", "effort": "low"})
		_check(card.state == "working", "The request resolves when the assistant starts")
		var before: String = chat._transcript.get_parsed_text()
		chat.set_chat_state("working", "Opening assembly.blend in Blender")
		chat.set_chat_state("working", "Exporting the model…")
		_check(chat._transcript.get_parsed_text() == before, "Progress must stay out of chat")
		_check(card._steps.get_child_count() == 2, "Steps list under the request")
		_check(card._seen_apps == ["Blender"], "The card names the app at work")
		_check(card.shows_step("Exporting the model…"), "The card says what is happening")
		_check(chat._footer.text().is_empty(), "The footer leaves the live step to the card")
		_check(chat._options.disabled, "Options wait while the assistant works")
		chat.show_agent_message("The selected assembly is ready to edit.")
		chat._prompt.text = "First line"
		chat._prompt.caret_column = chat._prompt.text.length()
		var newline := InputEventKey.new()
		newline.keycode = KEY_ENTER
		newline.pressed = true
		newline.shift_pressed = true
		chat._prompt.grab_focus()
		root.push_input(newline)
		await process_frame
		_check(chat._prompt.text == "First line\n", "Shift+Enter inserts a new line")
		_check(chat._prompt.editable, "The field stays editable while the assistant works")
		chat._on_submit("Another request")
		_check(submitted["count"] == 1, "Enter waits while the assistant works")
		chat._prompt.clear()
		for width: int in [480, 1000]:
			root.size = Vector2i(width, 760)
			await process_frame
			await process_frame
			_check(chat._stop.is_visible_in_tree() and not chat._send.visible, "Stop replaces Send")
			_check(chat._stop.get_parent() == chat._send.get_parent(), "Stop sits in Send's slot")
			_check(
				chat._stop.size.is_equal_approx(chat._send.get_combined_minimum_size()),
				"Stop has Send's size and place"
			)
			_check(
				chat._options.get_global_rect().end.x <= chat.get_global_rect().end.x + 1,
				"Options must fit the sidebar"
			)
			_check(
				chat._prompt.get_global_rect().end.y <= chat.get_global_rect().end.y + 1,
				"Composer must remain on screen"
			)
			_check(chat._composer.is_ancestor_of(chat._options), "Keep options in the composer")
			var records: Array[Dictionary] = chat._transcript._messages
			_check(records[0]["panel"] == card, "Requests are change cards")
			_check(records[1]["panel"] == null, "Assistant replies use the plain chat surface")
			_check(not records[1].has("copy"), "Replies stay uncluttered: text is selectable")
			chat._settings.set_open(true)
			_check(chat._settings.visible, "Settings open from the sidebar")
			_check(not chat._transcript.visible, "Settings take the place of the chat")
			_check(not chat._composer.visible, "The composer is hidden while in settings")
			chat._settings.set_open(false)
			_check(chat._transcript.visible and chat._composer.visible, "Back restores the chat")
			chat._transcript.set_text_scale(1.15)
			var size: int = records[1]["label"].get_theme_font_size("normal_font_size")
			_check(size > chat.get_theme_default_font_size(), "Text size applies to replies")
			chat._transcript.set_text_scale(1.0)
			var opened: Array[String] = []
			chat.target_opened.connect(func(path: String) -> void: opened.append(path))
			var chip: Control = chat._chips.get_child(0)
			var click := InputEventMouseButton.new()
			click.button_index = MOUSE_BUTTON_LEFT
			click.pressed = true
			chip.gui_input.emit(click)
			_check(opened.size() == 1 and opened[0].ends_with(".glb"), "A chip opens its asset")
			_check(chip.label.text == "assembly.glb", "A file chip shows the file name")
			_check(
				chip.label.size.x >= chip.label.get_minimum_size().x, "Chip names are not cut short"
			)
			_check(
				chip.size.y <= chat.get_theme_default_font_size() * 2.25,
				"Selected resources must remain compact above the message"
			)
			await _capture("working-assistant-%s-%d.png" % [theme_name, width])
		chat.show_timing(
			{
				"outcome": "cancelled",
				"total_ms": 1530,
				"first_response_ms": 250,
				"phases_ms": {"tools": 800}
			}
		)
		chat.set_chat_busy(false)
		_check(not chat._footer.visible, "The footer hides when the turn ends")
		_check(
			chat._transcript._messages[1]["summary"].text == "Stopped after 0:01",
			"Show actual completion timing on the reply"
		)
		_check(chat._send.visible and not chat._stop.visible, "Send returns after the turn")
		for width: int in [480, 1000]:
			root.size = Vector2i(width, 760)
			await _capture("assistant-%s-%d.png" % [theme_name, width])
		var send_style: StyleBoxFlat = chat._send.get_theme_stylebox("normal")
		_check(
			not chat._send.flat and send_style.bg_color.a == 1.0,
			"Send must have a visible filled background"
		)
		_check(
			chat._send.get_global_rect().end.x <= chat._composer.get_global_rect().end.x,
			"Send must fit inside the composer"
		)
		# A change: the card offers the review, the decision is optimistic, and accepted file
		# changes can be reverted.
		chat._on_submit("Widen the selected assembly")
		chat.set_chat_busy(true)
		var change: Control = chat._transcript.current_card()
		chat.append_agent_text("Widened it.", true)
		chat.show_file_review("Widen the assembly", "+ a\n- b", [], [], "tx-1")
		_check(change.state == "ready", "The card offers the review")
		chat.set_chat_busy(false)
		_check(chat._footer.text() == "Waiting for your review", "The footer waits for review")
		_check(not chat._prompt.editable, "The composer waits for the decision")
		_check(
			chat._prompt.placeholder_text == "Accept or discard the change first",
			"Say how to continue during review"
		)
		chat._on_accept()
		_check(change.state == "accepted", "Accept updates the card at once")
		_check(not change._revert.visible, "Revert waits until the accept is confirmed")
		chat.show_review_error("Blender is still busy.")
		_check(change.state == "ready", "A failed accept rolls the card back")
		chat._on_accept()
		chat.hide_edit_review("Change accepted", "success")
		_check(change.state == "accepted" and change._revert.visible, "Accepted changes can revert")
		_check(
			chat._transcript.notice_texts().count("Change accepted") == 1,
			"One accepted notice per decision"
		)
		_check(chat._prompt.editable and not chat._footer.visible, "Back to idle after review")
		var reverted: Array[String] = []
		chat._transcript.revert_requested.connect(func(id: String) -> void: reverted.append(id))
		change._revert.pressed.emit()
		_check(
			reverted == ["tx-1"] and change.state == "accepted" and change.reverting(),
			"Revert waits for Joinery: Reverting…"
		)
		change.revert_finished(false, "files changed since")
		_check(
			change.state == "accepted" and change._error.visible and change._revert.visible,
			"A failed revert says why and offers Revert again"
		)
		chat._slash.cancel()
		chat.restore_selection_options({"mode": "thorough", "model": "missing", "effort": ""})
		chat.set_model_options([])
		_check(
			(
				chat._options.model_items()[-1] == "Unavailable: missing"
				and chat._options.tooltip_text.contains("missing")
			),
			"Stale model preference must be visible"
		)
		chat._transcript.add_message("Codex", "**Strong** and `code`, literal [color=red] markup")
		await process_frame
		await process_frame
		var last: Dictionary = chat._transcript._messages[-1]
		_check(
			last["label"].get_parsed_text() == "Strong and code, literal [color=red] markup",
			"Render formatting and escape injected BBCode"
		)
		for index: int in 30:
			chat._transcript.add_message(
				"Codex", "Earlier reply %d\nMore detail\nMore detail" % index
			)
		for frame: int in 4:
			await process_frame
		chat._transcript.scroll_vertical = 0
		await process_frame
		chat._transcript.append_delta("Codex", "New streamed text", false)
		await process_frame
		await process_frame
		_check(
			chat._transcript.scroll_vertical == 0,
			"Streaming must not move readers away from earlier messages"
		)
		_check(not "Ready" in _texts(chat), "The sidebar never says Ready")
		chat.queue_free()
		host.queue_free()
		await process_frame
	print("responsive assistant: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
