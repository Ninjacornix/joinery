@tool
extends VBoxContainer
## The Journeyman sidebar: the single surface you direct Joinery from.
##
## What is live today talks to the real `joinery` CLI, so the output in this
## panel is the real policy gate, the real tool check and the real recipe
## replay, not a mock. What is not built yet says so plainly rather than
## pretending: free-form prompts need the agent bridge, and before/after needs
## the transaction engine.

signal setup_requested
signal focus_toggle_requested

const ACCENT := Color(0.83, 0.61, 0.23)
const DIM := Color(0.62, 0.60, 0.56)

var _transcript: RichTextLabel
var _prompt: TextEdit
var _chips: HFlowContainer
var _steps: VBoxContainer
var _review: PanelContainer
var _send: Button
var _targets: Array[String] = []
var _busy := false


func _init() -> void:
	name = "Journeyman"
	custom_minimum_size = Vector2(340, 0)
	add_theme_constant_override("separation", 6)


func _ready() -> void:
	_build_header()
	_build_targets()
	_build_transcript()
	_build_steps()
	_build_review()
	_build_prompt()
	_greet()


func _build_header() -> void:
	var row := HBoxContainer.new()
	add_child(row)

	var title := Label.new()
	title.text = "Journeyman"
	title.add_theme_font_size_override("font_size", 16)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)

	var setup := Button.new()
	setup.text = "Setup"
	setup.tooltip_text = "Project questionnaire: tools, and how this project is made."
	setup.pressed.connect(func() -> void: setup_requested.emit())
	row.add_child(setup)

	var focus := Button.new()
	focus.text = "Focus"
	focus.tooltip_text = "Hide everything but the viewport and this sidebar."
	focus.pressed.connect(func() -> void: focus_toggle_requested.emit())
	row.add_child(focus)

	var tag := Label.new()
	tag.text = "Made in the Joinery, not generated."
	tag.add_theme_color_override("font_color", DIM)
	tag.add_theme_font_size_override("font_size", 10)
	add_child(tag)
	add_child(HSeparator.new())


func _build_targets() -> void:
	var label := Label.new()
	label.text = "Selection"
	label.add_theme_color_override("font_color", DIM)
	label.add_theme_font_size_override("font_size", 11)
	add_child(label)

	_chips = HFlowContainer.new()
	add_child(_chips)


func _build_transcript() -> void:
	_transcript = RichTextLabel.new()
	_transcript.bbcode_enabled = true
	_transcript.scroll_following = true
	_transcript.selection_enabled = true
	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_transcript.custom_minimum_size = Vector2(0, 220)
	add_child(_transcript)


func _build_steps() -> void:
	_steps = VBoxContainer.new()
	_steps.visible = false
	add_child(_steps)


func _build_review() -> void:
	## Before/after and accept/reject. Visible but inert until the transaction
	## engine exists, so the shape of the workflow is honest about its state.
	_review = PanelContainer.new()
	_review.visible = false
	var box := VBoxContainer.new()
	_review.add_child(box)

	var heading := Label.new()
	heading.text = "Review"
	heading.add_theme_color_override("font_color", ACCENT)
	box.add_child(heading)

	var row := HBoxContainer.new()
	box.add_child(row)
	var accept := Button.new()
	accept.text = "Accept"
	accept.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(accept)
	var reject := Button.new()
	reject.text = "Reject"
	reject.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(reject)
	add_child(_review)


func _build_prompt() -> void:
	add_child(HSeparator.new())
	_prompt = TextEdit.new()
	_prompt.placeholder_text = "Tell Journeyman what to change…"
	_prompt.custom_minimum_size = Vector2(0, 54)
	_prompt.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	add_child(_prompt)

	var row := HBoxContainer.new()
	add_child(row)

	_send = Button.new()
	_send.text = "Send"
	_send.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_send.pressed.connect(_on_send)
	row.add_child(_send)

	for entry: Array in [
		["Doctor", "doctor", "Which tools this project needs, and whether they are installed"],
		["Agents", "agents", "Which agents are gated, and how a Rule Zero hit lands"],
		["Check", "check", "Run the policy and asset gates"],
		["Rebuild", "rebuild", "Replay every recipe and verify the assets come back"],
	]:
		var button := Button.new()
		button.text = entry[0]
		button.tooltip_text = entry[2]
		var command: String = entry[1]
		button.pressed.connect(func() -> void: _run_command(command))
		row.add_child(button)


func _greet() -> void:
	_say("Journeyman", "Ready. Select something in the scene and say what to change.")
	log_system(
		"Live now: the buttons below run the real joinery CLI. Free-form "
		+ "prompts need the agent bridge, which is not connected yet."
	)


# --- selection ---------------------------------------------------------------


func on_selection_changed() -> void:
	_targets.clear()
	for child: Node in _chips.get_children():
		child.queue_free()

	# EditorInterface is a global singleton in 4.2+; fetching it through
	# Engine.get_singleton and casting trips an internal script error.
	var nodes := EditorInterface.get_selection().get_selected_nodes()
	if nodes.is_empty():
		var empty := Label.new()
		empty.text = "nothing selected"
		empty.add_theme_color_override("font_color", DIM)
		empty.add_theme_font_size_override("font_size", 11)
		_chips.add_child(empty)
		return

	for node: Node in nodes:
		_targets.append(node.name)
		var chip := Button.new()
		chip.text = "@%s" % node.name
		chip.tooltip_text = "%s (%s)" % [node.get_path(), node.get_class()]
		chip.disabled = true
		chip.add_theme_color_override("font_disabled_color", ACCENT)
		_chips.add_child(chip)


# --- talking -----------------------------------------------------------------


func _on_send() -> void:
	var text := _prompt.text.strip_edges()
	if text.is_empty() or _busy:
		return
	_prompt.text = ""
	var chips := ""
	if not _targets.is_empty():
		chips = " [" + ", ".join(_targets.map(func(t: String) -> String: return "@" + t)) + "]"
	_say("You", text + chips)

	var word := text.to_lower()
	for known: String in ["doctor", "agents", "rebuild", "check"]:
		if word.begins_with(known):
			_run_command(known)
			return

	# No agent transport yet. Be straight about it instead of inventing a reply.
	_say(
		"Journeyman",
		"I cannot act on that yet: the agent bridge is not connected, so there "
		+ "is nothing to plan or apply this request with. What works today is "
		+ "below the prompt, and `joinery` on the command line."
	)


func _run_command(command: String) -> void:
	if _busy:
		return
	_busy = true
	_send.disabled = true
	_say("Journeyman", "Running `joinery %s`…" % command)

	var args := _args_for(command)
	var out: Array = []
	var code := OS.execute("uv", args, out, true)
	var text: String = "\n".join(out).strip_edges()
	if text.is_empty():
		text = "(no output)"
	_log_block(text)
	if code == 0:
		_say("Journeyman", "`%s` finished cleanly." % command)
	else:
		_say("Journeyman", "`%s` exited %d. Output above." % [command, code])
	_busy = false
	_send.disabled = false


func _args_for(command: String) -> PackedStringArray:
	var engine_dir := _engine_dir()
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var args := PackedStringArray(["run", "--project", engine_dir, "joinery"])
	match command:
		"doctor":
			args.append_array(["doctor", "--project", project])
		"agents":
			args.append("agents")
		"check":
			args.append_array(["check", "assets", "--project", project])
		"rebuild":
			args.append_array(["rebuild", "--project", project])
		_:
			args.append(command)
	return args


func _engine_dir() -> String:
	## The Joinery install. The addon lives inside the project, so walk out of
	## it; a user with the engine elsewhere sets JOINERY_HOME.
	var from_env := OS.get_environment("JOINERY_HOME")
	if not from_env.is_empty():
		return from_env
	return ProjectSettings.globalize_path("res://").path_join("..").simplify_path()


# --- transcript --------------------------------------------------------------


func _say(who: String, what: String) -> void:
	var colour := ACCENT if who == "Journeyman" else Color(0.75, 0.82, 0.90)
	_transcript.append_text(
		"[color=#%s][b]%s[/b][/color]  %s\n\n" % [colour.to_html(false), who, what]
	)


func log_system(what: String) -> void:
	_transcript.append_text(
		"[color=#%s][i]%s[/i][/color]\n\n" % [DIM.to_html(false), what]
	)


func _log_block(text: String) -> void:
	_transcript.append_text("[code]%s[/code]\n\n" % text)
