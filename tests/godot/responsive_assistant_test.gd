extends SceneTree
## Provider-reported controls, progress, cancellation and light/dark layout.

var _failed := false
var _selection: Dictionary = {}


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	root.size = Vector2i(1000, 760)
	for light: bool in [false, true]:
		var host := Control.new()
		host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		host.theme = Theme.new()
		host.theme.set_color("base_color", "Editor", Color.WHITE if light else Color.BLACK)
		root.add_child(host)
		var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
		host.add_child(chat)
		chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		chat.selection_options_changed.connect(func(value: Dictionary) -> void: _selection = value)
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
		_check(chat._model.item_count == 3, "Offer only reported models plus Automatic")
		chat._model.select(1)
		chat._model_changed(1)
		_check(chat._effort.item_count == 4, "Efforts must follow the reported model")
		chat.restore_selection_options({"mode": "fast", "model": "fixture-sol", "effort": "ultra"})
		_check(
			chat._effort.get_item_text(chat._effort.selected).begins_with("Unavailable:"),
			"A removed effort preference must remain visible until the user changes it"
		)
		chat.restore_selection_options({"mode": "fast", "model": "fixture-sol", "effort": ""})
		chat._mode.select(1)
		chat._options_changed(1)
		_check(
			_selection["model"] == "fixture-sol" and _selection["mode"] == "fast",
			"Send explicit model/mode"
		)
		chat.set_chat_agent("codex")
		chat.set_chat_state("connected", "Codex connected")
		chat.set_resource_target("res://models/assembly.glb", "PackedScene")
		chat._transcript.add_message("You", "Make this assembly a little wider")
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
		chat._prompt.clear()
		chat.set_chat_busy(true)
		chat.show_execution({"name": "GPT-6.1 Sol", "mode": "fast", "effort": "low"})
		var before: String = chat._transcript.get_parsed_text()
		chat.set_chat_state("working", "Exporting the model…")
		_check(chat._transcript.get_parsed_text() == before, "Progress must stay out of chat")
		_check(
			chat._connection.text == "Codex connected", "Keep connection status distinct from work"
		)
		_check(
			chat._stop.visible and chat._model.disabled,
			"Stop stays available while model changes wait"
		)
		_check(chat._route_label.text.contains("GPT-6.1 Sol"), "Show the model actually used")
		for width: int in [480, 1000]:
			root.size = Vector2i(width, 760)
			await process_frame
			await process_frame
			_check(
				chat._model.get_global_rect().end.x <= chat.get_global_rect().end.x + 1,
				"Model controls must fit the sidebar"
			)
			_check(
				chat._prompt.get_global_rect().end.y <= chat.get_global_rect().end.y + 1,
				"Composer must remain on screen"
			)
			_check(
				chat._stop.size.x > 0 and chat._stop.is_visible_in_tree(),
				"Stop must remain visible"
			)
			_check(chat._composer.is_ancestor_of(chat._mode), "Keep model controls in the composer")
			var records: Array[Dictionary] = chat._transcript._messages
			_check(records[0]["panel"] != null, "User messages have their own bubble")
			_check(records[1]["panel"] == null, "Assistant replies use the plain chat surface")
			_check(records[1]["copy"].visible, "Assistant replies have a copy action")
			_check(
				chat._chips.get_child(0).size.y <= chat.get_theme_default_font_size() * 2.25,
				"Selected resources must remain compact above the message"
			)
			_check(
				(
					records[0]["panel"].get_global_rect().position.y
					>= (
						chat._transcript.get_global_rect().position.y
						+ chat.get_theme_default_font_size()
					)
				),
				"First message needs space below the connection status"
			)
			var capture := OS.get_environment("JOINERY_CAPTURE_DIR")
			if not capture.is_empty() and DisplayServer.get_name() != "headless":
				RenderingServer.force_draw()
				var image := root.get_texture().get_image()
				var filename := "assistant-%s-%d.png" % ["light" if light else "dark", width]
				_check(
					image.save_png(capture.path_join("working-" + filename)) == OK, "Save active UI"
				)
				chat.set_chat_busy(false)
				chat.show_timing(
					{
						"outcome": "done",
						"total_ms": 12000,
						"first_response_ms": 250,
						"phases_ms": {"tools": 800}
					}
				)
				chat._prompt.grab_focus()
				await process_frame
				await process_frame
				RenderingServer.force_draw()
				image = root.get_texture().get_image()
				_check(image.save_png(capture.path_join(filename)) == OK, "Save completed UI")
				chat.set_chat_busy(true)
				chat.set_chat_state("working", "Exporting the model…")
		chat.set_chat_busy(false)
		var send_style: StyleBoxFlat = chat._send.get_theme_stylebox("normal")
		_check(
			not chat._send.flat and send_style.bg_color.a == 1.0,
			"Send must have a visible filled background"
		)
		_check(
			chat._send.get_global_rect().end.x <= chat._composer.get_global_rect().end.x,
			"Send must fit beside all three dropdowns"
		)
		chat.show_timing(
			{
				"outcome": "cancelled",
				"total_ms": 1530,
				"first_response_ms": 250,
				"phases_ms": {"tools": 800}
			}
		)
		_check(
			chat._progress.text == "Stopped" and chat._elapsed_label.text == "1.5s",
			"Show actual completion timing"
		)
		var submitted := {"count": 0, "text": "", "targets": []}
		chat.prompt_submitted.connect(
			func(message: String, targets: Array) -> void:
				submitted["count"] += 1
				submitted["text"] = message
				submitted["targets"] = targets
		)
		chat._prompt.text = "Widen the selected assembly"
		for pressed: bool in [true, false]:
			var click := InputEventMouseButton.new()
			click.button_index = MOUSE_BUTTON_LEFT
			click.position = chat._send.get_global_rect().get_center()
			click.pressed = pressed
			root.push_input(click)
		await process_frame
		_check(
			(
				submitted["count"] == 1
				and submitted["text"] == "Widen the selected assembly"
				and submitted["targets"].size() == 1
			),
			"Clicking the filled Send action submits once with the selected resource"
		)
		chat.restore_selection_options({"mode": "thorough", "model": "missing", "effort": ""})
		chat.set_model_options([])
		_check(
			chat._model.get_item_text(chat._model.selected).begins_with("Unavailable:"),
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
		chat.queue_free()
		host.queue_free()
		await process_frame
	print("responsive assistant: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
