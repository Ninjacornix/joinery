@tool
extends VBoxContainer
## Journeyman's earlier sidebar layout, with explicit targets and command completion.

signal setup_requested
signal focus_toggle_requested
signal toolbar_toggle_requested
signal prompt_submitted(text: String, targets: Array)
signal accept_edit_requested
signal reject_edit_requested
signal cancel_requested
signal reconnect_requested
signal art_review_requested
signal review_requested
signal review_closed

const COMMAND_SCRIPT := preload("res://addons/joinery/command_job.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const COMMANDS := {
	"/agents": ["Supported agents", "cli"],
	"/doctor": ["Check installed tools", "cli"],
	"/tools": ["Check connected project tools", "cli"],
	"/check": ["Check project assets", "cli"],
	"/rebuild": ["Verify asset recipes", "cli"],
	"/setup": ["Project setup", "ui"],
	"/focus": ["Focus mode", "ui"],
	"/toolbar": ["Viewport controls", "ui"],
	"/help": ["All commands", "ui"],
	"/art": ["Review and approve project art direction", "ui"],
}

var _transcript: RichTextLabel
var _prompt: LineEdit
var _chips: HFlowContainer
var _command_popup: PanelContainer
var _command_layer: CanvasLayer
var _command_list: ItemList
var _matches: Array[String] = []
var _target_context: Array[Dictionary] = []
var _targets: Array[String] = []
var _busy := false
var _command_job: RefCounted
var _running_command := ""
var _connection: Label
var _connection_icon: TextureRect
var _stop: Button
var _reconnect: Button
var _review: VBoxContainer
var _review_label: Label
var _review_details: RichTextLabel
var _review_scroll: ScrollContainer
var _review_media: VBoxContainer
var _review_pending := false
var _agent_name := "Codex"
var _assistant_open := false


func _init() -> void:
	name = "Journeyman"
	custom_minimum_size = Vector2(300, 0)
	add_theme_constant_override("separation", 6)


func _ready() -> void:
	theme = UI_THEME.build(self)
	var font_size := get_theme_default_font_size()
	add_theme_constant_override("separation", maxi(8, int(font_size * 0.6)))
	custom_minimum_size.x = get_theme_default_font_size() * 24
	var heading := Label.new()
	heading.text = "Journeyman"
	heading.add_theme_font_size_override("font_size", int(font_size * 1.15))
	add_child(heading)
	var connection_row := HBoxContainer.new()
	connection_row.add_theme_constant_override("separation", maxi(6, int(font_size * 0.45)))
	add_child(connection_row)
	_connection_icon = TextureRect.new()
	_connection_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_connection_icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_connection_icon.custom_minimum_size = Vector2(font_size, font_size)
	_connection_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	connection_row.add_child(_connection_icon)
	_connection = Label.new()
	_connection.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	_connection.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_connection.text = "Connecting…"
	_connection.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	connection_row.add_child(_connection)
	_reconnect = Button.new()
	_reconnect.text = "Reconnect"
	_reconnect.accessibility_name = "Reconnect your coding agent"
	_reconnect.hide()
	_reconnect.pressed.connect(func() -> void: reconnect_requested.emit())
	connection_row.add_child(_reconnect)
	_transcript = RichTextLabel.new()
	_transcript.scroll_following = true
	_transcript.selection_enabled = true
	_transcript.accessibility_name = "Conversation"
	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_transcript)
	_review = VBoxContainer.new()
	_review.theme = theme
	_review.add_theme_constant_override("separation", maxi(8, int(font_size * 0.6)))
	_review.hide()
	add_child(_review)
	_review_label = Label.new()
	_review_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_review_label.add_theme_font_size_override("font_size", int(font_size * 1.1))
	_review.add_child(_review_label)
	_review_scroll = ScrollContainer.new()
	_review_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_review_scroll.follow_focus = true
	_review_scroll.accessibility_name = "Proposed changes and previews"
	_review_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_review_scroll.custom_minimum_size.y = get_theme_default_font_size() * 8
	_review.add_child(_review_scroll)
	var review_body := VBoxContainer.new()
	review_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_review_scroll.add_child(review_body)
	_review_details = RichTextLabel.new()
	_review_details.fit_content = true
	_review_details.scroll_active = false
	_review_details.selection_enabled = true
	_review_details.accessibility_name = "File changes"
	_review_details.hide()
	review_body.add_child(_review_details)
	_review_media = VBoxContainer.new()
	review_body.add_child(_review_media)
	review_body.move_child(_review_media, 0)
	var choices := HBoxContainer.new()
	choices.add_theme_constant_override("separation", maxi(8, int(font_size * 0.6)))
	_review.add_child(choices)
	var accept_button := Button.new()
	accept_button.text = "Accept changes"
	accept_button.theme_type_variation = "JoineryPrimaryButton"
	accept_button.accessibility_name = "Accept reviewed changes"
	accept_button.tooltip_text = "Keep the changes shown in this review"
	accept_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accept_button.pressed.connect(func() -> void: accept_edit_requested.emit())
	choices.add_child(accept_button)
	var reject_button := Button.new()
	reject_button.text = "Reject changes"
	reject_button.accessibility_name = "Reject reviewed changes"
	reject_button.tooltip_text = "Discard this proposal and preserve the original"
	reject_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	reject_button.pressed.connect(func() -> void: reject_edit_requested.emit())
	choices.add_child(reject_button)
	_chips = HFlowContainer.new()
	_chips.hide()
	add_child(_chips)
	_prompt = LineEdit.new()
	_prompt.accessibility_name = "Message to your coding agent"
	_prompt.tooltip_text = "Describe a change. Enter sends; type / for commands."
	_prompt.custom_minimum_size.y = font_size * 2.5
	_prompt.placeholder_text = "Say what to change, or / for commands"
	_prompt.text_changed.connect(_update_commands)
	_prompt.text_submitted.connect(_on_submit)
	_prompt.gui_input.connect(_on_prompt_input)
	add_child(_prompt)
	_stop = Button.new()
	_stop.text = "Stop"
	_stop.accessibility_name = "Stop the current agent response"
	_stop.hide()
	_stop.pressed.connect(func() -> void: cancel_requested.emit())
	add_child(_stop)
	_command_layer = CanvasLayer.new()
	_command_layer.layer = 100
	add_child(_command_layer)
	_command_popup = PanelContainer.new()
	_command_popup.theme = theme
	var panel: StyleBoxFlat = UI_THEME.panel(
		get_theme_color("raised", "Joinery"), get_theme_color("border", "Joinery"), 4, 8
	)
	_command_popup.add_theme_stylebox_override("panel", panel)
	_command_layer.add_child(_command_popup)
	_command_list = ItemList.new()
	_command_list.accessibility_name = "Available slash commands"
	_command_list.item_clicked.connect(_on_command_clicked)
	_command_popup.add_child(_command_list)
	_command_popup.hide()
	_say("Journeyman", "Ready. Select something and say what to change.")


func _exit_tree() -> void:
	if _command_job != null:
		_command_job.cancel()
		_command_job = null


func _process(_delta: float) -> void:
	if _command_popup.visible:
		_position_commands()
	if _command_job == null:
		return
	var result: Dictionary = _command_job.poll()
	if result.is_empty():
		return
	_command_job = null
	_busy = false
	_prompt.editable = true
	_prompt.placeholder_text = "Say what to change, or / for commands"
	var output := str(result["output"])
	print("[Joinery] ", _running_command, "\n", output)
	_say("Journeyman", _command_reply(_running_command, int(result["code"]), output))


func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and _command_popup.visible:
		if not _command_popup.get_global_rect().has_point(event.position):
			if not _prompt.get_global_rect().has_point(event.position):
				_command_popup.hide()


func _update_commands(text: String) -> void:
	_matches.clear()
	_command_list.clear()
	if not text.begins_with("/") or text.contains(" ") or _busy:
		_command_popup.hide()
		return
	for command: String in COMMANDS:
		if command.begins_with(text.to_lower()):
			_matches.append(command)
			_command_list.add_item("%s   %s" % [command, COMMANDS[command][0]])
			_command_list.set_item_tooltip(_matches.size() - 1, COMMANDS[command][0])
	_command_popup.visible = not _matches.is_empty()
	if not _matches.is_empty():
		_command_list.select(0)
		_position_commands()


func _position_commands() -> void:
	var font := _command_list.get_theme_font("font")
	var row_height := font.get_height(_command_list.get_theme_font_size("font_size"))
	row_height += _command_list.get_theme_constant("v_separation") + 8
	var height := minf(row_height * _matches.size() + 16, get_viewport_rect().size.y * 0.4)
	_command_popup.size = Vector2(_prompt.size.x, height)
	var prompt_rect := (
		_prompt.get_global_transform_with_canvas() * Rect2(Vector2.ZERO, _prompt.size)
	)
	_command_popup.global_position = Vector2(
		prompt_rect.position.x, maxf(4, prompt_rect.position.y - _command_popup.size.y - 6)
	)


func _on_prompt_input(event: InputEvent) -> void:
	if not event is InputEventKey or not event.pressed or not _command_popup.visible:
		return
	var selected := _command_list.get_selected_items()
	var index := selected[0] if not selected.is_empty() else 0
	match event.keycode:
		KEY_UP, KEY_DOWN:
			var direction := -1 if event.keycode == KEY_UP else 1
			index = posmod(index + direction, _matches.size())
			_command_list.select(index)
			_command_list.ensure_current_is_visible()
		KEY_TAB:
			_choose_command(index)
		KEY_ENTER, KEY_KP_ENTER:
			_on_submit(_prompt.text)
		KEY_ESCAPE:
			_command_popup.hide()
		_:
			return
	_prompt.accept_event()


func _on_command_clicked(index: int, _position: Vector2, button: int) -> void:
	if button == MOUSE_BUTTON_LEFT:
		_choose_command(index)


func _choose_command(index: int) -> void:
	var command := _matches[index]
	_command_popup.hide()
	_prompt.text = command + " "
	_prompt.caret_column = _prompt.text.length()
	_prompt.grab_focus()


func on_selection_changed() -> void:
	var targets: Array[Dictionary] = []
	var scene := EditorInterface.get_edited_scene_root()
	for node: Node in EditorInterface.get_selection().get_selected_nodes():
		var scene_path := scene.scene_file_path if scene != null else "current-scene"
		var node_path := str(scene.get_path_to(node)) if scene != null else str(node.get_path())
		var kind := "Object"
		var resource := ""
		if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
			kind = "Audio"
			var stream := node.get("stream") as AudioStream
			if stream != null:
				resource = stream.resource_path
		(
			targets
			. append(
				{
					"id": scene_path + "::" + node_path,
					"label": str(node.name),
					"kind": kind,
					"resource": resource,
					"source": node.scene_file_path,
				}
			)
		)
	_set_targets(targets)


func set_resource_target(path: String, type: String = "Resource", source: String = "") -> void:
	if path.is_empty():
		return
	var filename := path.split("::")[0].get_file().get_basename()
	var kind := "Resource"
	var extension := path.get_extension().to_lower()
	if type.begins_with("Audio") or extension in ["wav", "ogg", "mp3", "scd"]:
		kind = "Audio"
	elif type in ["Object", "PackedScene"] or extension in ["blend", "glb", "gltf", "tscn"]:
		kind = "Object"
	elif type.begins_with("Texture") or extension in ["png", "jpg", "kra", "aseprite"]:
		kind = "Image"
	_set_targets(
		[
			{
				"id": path,
				"label": filename.replace("_", " ").capitalize(),
				"kind": kind,
				"resource": path,
				"source": source if not source.is_empty() else path,
			}
		]
	)


func target_snapshot() -> Array[Dictionary]:
	return _target_context.duplicate(true)


func _set_targets(targets: Array[Dictionary]) -> void:
	_target_context = targets.duplicate(true)
	_targets.clear()
	for child: Node in _chips.get_children():
		_chips.remove_child(child)
		child.queue_free()
	for target: Dictionary in _target_context:
		_targets.append(str(target["id"]))
		var context := HBoxContainer.new()
		context.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		context.tooltip_text = "%s: %s" % [target["kind"], target["id"]]
		_chips.add_child(context)
		var label := Label.new()
		label.text = "Editing · " + str(target["label"])
		label.add_theme_font_size_override("font_size", int(get_theme_default_font_size() * 0.85))
		label.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
		label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		label.custom_minimum_size.x = 60
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		context.add_child(label)
		var clear := Button.new()
		clear.text = "×"
		clear.flat = true
		clear.tooltip_text = "Clear selection for this message"
		clear.accessibility_name = "Clear target " + str(target["label"])
		clear.pressed.connect(_remove_target.bind(str(target["id"])))
		context.add_child(clear)
	_chips.visible = not _target_context.is_empty()


func _remove_target(id: String) -> void:
	var remaining: Array[Dictionary] = []
	for target: Dictionary in _target_context:
		if str(target["id"]) != id:
			remaining.append(target)
	_set_targets(remaining)


func _on_submit(text: String) -> void:
	var entry := text.strip_edges()
	if entry.is_empty() or _busy or _review_pending:
		return
	_command_popup.hide()
	_prompt.clear()
	_say("You", entry)
	_assistant_open = false
	if entry.begins_with("/"):
		_run_slash(entry)
		return
	var connected := not prompt_submitted.get_connections().is_empty()
	prompt_submitted.emit(entry, target_snapshot())
	if not connected:
		_say("Journeyman", "Open this project through Joinery to start its chat connection.")


func set_chat_agent(agent: String) -> void:
	_agent_name = agent.capitalize()


func set_chat_state(state: String, detail: String) -> void:
	_connection.text = detail
	_connection.tooltip_text = detail
	_reconnect.visible = state == "error"
	var icon := {"connected": "StatusSuccess", "error": "StatusError"}.get(state, "Progress1")
	_connection_icon.visible = has_theme_icon(icon, "EditorIcons")
	if _connection_icon.visible:
		_connection_icon.texture = get_theme_icon(icon, "EditorIcons")


func set_chat_busy(busy: bool) -> void:
	_busy = busy
	_prompt.editable = not _review_pending
	_stop.visible = busy
	_prompt.placeholder_text = (
		"Review changes below the viewport"
		if _review_pending
		else "%s is responding…" % _agent_name if busy else "Say what to change, or / for commands"
	)


func take_review_panel() -> Control:
	remove_child(_review)
	_review.name = "Review"
	_review.custom_minimum_size.y = get_theme_default_font_size() * 18
	return _review


func show_edit_review(summary: String) -> void:
	_review_details.hide()
	_review_pending = true
	_review_label.text = "Preview · " + summary
	_review.show()
	_prompt.editable = false
	_prompt.placeholder_text = "Accept or reject changes in the Review panel"
	review_requested.emit()


func show_file_review(summary: String, text_diff: String, media: Array = []) -> void:
	show_edit_review(summary)
	_review_scroll.scroll_vertical = 0
	_review_details.text = text_diff
	_review_details.visible = not text_diff.is_empty()
	for child: Node in _review_media.get_children():
		child.queue_free()
	for item: Dictionary in media:
		var caption := Label.new()
		caption.text = str(item.get("path", ""))
		caption.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		_review_media.add_child(caption)
		var row := HBoxContainer.new()
		_review_media.add_child(row)
		for side: String in ["before", "after"]:
			if not item.has(side):
				continue
			var column := VBoxContainer.new()
			column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			row.add_child(column)
			var label := Label.new()
			label.text = side.capitalize()
			column.add_child(label)
			var path := str(item[side])
			if item.get("media") == "image":
				var image := Image.load_from_file(path)
				if image != null:
					var view := TextureRect.new()
					view.texture = ImageTexture.create_from_image(image)
					view.custom_minimum_size = Vector2(100, 160)
					view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
					view.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
					column.add_child(view)
			else:
				var player := AudioStreamPlayer.new()
				player.stream = (
					AudioStreamWAV.load_from_file(path)
					if path.ends_with(".wav")
					else AudioStreamOggVorbis.load_from_file(path)
				)
				column.add_child(player)
				var play := Button.new()
				play.text = "Play / Stop"
				play.pressed.connect(
					func() -> void:
						if player.playing:
							player.stop()
						else:
							player.play()
				)
				column.add_child(play)


func hide_edit_review(message: String = "") -> void:
	var was_visible := _review.is_visible_in_tree()
	_review_details.hide()
	_review_pending = false
	_review.hide()
	for child: Node in _review_media.get_children():
		child.queue_free()
	_prompt.editable = true
	_prompt.placeholder_text = "Say what to change, or / for commands"
	if was_visible:
		review_closed.emit()
	if not message.is_empty():
		show_agent_message(message)


func append_agent_text(text: String, new_message: bool) -> void:
	if new_message or not _assistant_open:
		if _assistant_open:
			_transcript.add_text("\n\n")
		_transcript.push_color(get_theme_color("accent", "Joinery"))
		_transcript.add_text(_agent_name + "  ")
		_transcript.pop()
		_assistant_open = true
	_transcript.add_text(text)


func show_agent_message(text: String) -> void:
	finish_agent_response(false)
	_say(_agent_name, text)


func finish_agent_response(_ok: bool) -> void:
	if _assistant_open:
		_transcript.add_text("\n\n")
		_assistant_open = false


func _run_slash(entry: String) -> void:
	var command := entry.split(" ")[0].to_lower()
	if not COMMANDS.has(command):
		var matches: Array[String] = []
		for candidate: String in COMMANDS:
			if candidate.begins_with(command):
				matches.append(candidate)
		if matches.size() == 1:
			command = matches[0]
	if not COMMANDS.has(command):
		_say("Journeyman", "Unknown command. Type / to see the available commands.")
		return
	match command:
		"/help":
			var lines := PackedStringArray()
			for candidate: String in COMMANDS:
				lines.append("%s — %s" % [candidate, COMMANDS[candidate][0]])
			_say("Journeyman", "Available commands:\n" + "\n".join(lines))
		"/setup":
			setup_requested.emit()
		"/art":
			art_review_requested.emit()
		"/focus":
			focus_toggle_requested.emit()
		"/toolbar":
			toolbar_toggle_requested.emit()
		_:
			_run_cli(command.substr(1))


func _run_cli(command: String) -> void:
	_busy = true
	_running_command = command
	_prompt.editable = false
	_prompt.placeholder_text = "Working…"
	_command_job = COMMAND_SCRIPT.new()
	var error: Error = _command_job.start("uv", _args_for(command))
	if error != OK:
		_command_job = null
		_busy = false
		_prompt.editable = true
		_say("Journeyman", "Could not start the command.")


func _command_reply(command: String, code: int, output: String) -> String:
	if command == "tools":
		var data: Variant
		for line: String in output.split("\n"):
			if line.begins_with('{"servers"'):
				data = JSON.parse_string(line)
		if data is Dictionary:
			var connected: Array[String] = []
			var unavailable: Array[String] = []
			for server: Dictionary in data.get("servers", []):
				if server.get("state") == "discovered":
					connected.append(str(server["name"]))
				else:
					unavailable.append(str(server["name"]))
			return (
				"Connected: "
				+ ", ".join(connected)
				+ (
					". Unavailable: " + ", ".join(unavailable)
					if not unavailable.is_empty()
					else "."
				)
			)
	if code != 0:
		return "That command could not finish. Details are in Output."
	if command == "agents":
		var data: Variant = JSON.parse_string(output)
		var names: Array[String] = []
		if data is Dictionary:
			for agent: Dictionary in data.get("agents", []):
				names.append(str(agent["name"]))
		if names.is_empty():
			return "Could not read the supported agent list. Details are in Output."
		return "Supported agents: " + ", ".join(names) + "."
	return (
		{
			"doctor": "Your tools are ready.",
			"check": "Asset checks passed.",
			"rebuild": "Recipe verification passed.",
		}
		. get(command, "Done.")
	)


func _args_for(command: String) -> PackedStringArray:
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var args := PackedStringArray(
		[
			"run",
			"--frozen",
			"--quiet",
			"--project",
			_engine_dir(),
			"joinery",
		]
	)
	match command:
		"tools":
			args.append_array(["mcp", "status", "--project", project, "--probe"])
		"agents":
			args.append_array(["agents", "--json"])
		"doctor", "rebuild":
			args.append_array([command, "--project", project])
		"check":
			args.append_array(["check", "assets", "--project", project])
		_:
			args.append(command)
	return args


func _engine_dir() -> String:
	var from_env := OS.get_environment("JOINERY_HOME")
	return (
		from_env
		if not from_env.is_empty()
		else ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	)


func _say(who: String, what: String) -> void:
	_transcript.push_color(
		(
			get_theme_color("accent", "Joinery")
			if who in ["Journeyman", _agent_name]
			else get_theme_color("secondary", "Joinery")
		)
	)
	_transcript.add_text(who + "  ")
	_transcript.pop()
	_transcript.add_text(what + "\n\n")


func log_system(what: String) -> void:
	print("[Joinery] ", what)
