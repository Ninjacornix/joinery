@tool
extends VBoxContainer
## The Journeyman sidebar: the single surface you direct Joinery from.
##
## One input, no button rows. Everything is a slash command typed in the prompt,
## the way the agent CLIs this talks to already work. A row of buttons has to
## grow with every capability and was already overflowing the dock at narrow
## widths; a command list scrolls, and reads the same at any size.
##
## What is live today talks to the real `joinery` CLI, so the output here is the
## real policy gate, tool check and recipe replay. What is not built yet says so
## rather than pretending: free-form prompts need the agent bridge.

signal setup_requested
signal focus_toggle_requested

const ACCENT := Color(0.85, 0.63, 0.26)
const DIM := Color(0.66, 0.64, 0.60)

## command -> [description, kind]. `cli` shells out to joinery, `ui` acts here.
const COMMANDS := {
	"/doctor": ["Which tools this project needs, and whether they are installed", "cli"],
	"/agents": ["Which agents are gated, and how a Rule Zero hit lands", "cli"],
	"/check": ["Run the policy and asset gates", "cli"],
	"/rebuild": ["Replay every recipe and verify the assets come back", "cli"],
	"/setup": ["Change which tools make this project", "ui"],
	"/focus": ["Toggle the stripped-back layout", "ui"],
	"/help": ["List these commands", "ui"],
}

var _transcript: RichTextLabel
var _prompt: LineEdit
var _chips: HFlowContainer
var _targets: Array[String] = []
var _busy := false


func _init() -> void:
	name = "Journeyman"
	custom_minimum_size = Vector2(300, 0)
	add_theme_constant_override("separation", 6)


## No font sizes are set here, deliberately.
##
## The editor theme sizes its fonts in points and then multiplies by the
## interface scale, which is 2 on a HiDPI display. An explicit
## add_theme_font_size_override takes a raw pixel value and skips that, so a
## hand-picked 14 renders at half the size of every Godot label beside it.
## That is why this panel kept looking tiny however the numbers were tuned.
## Inheriting the theme gets the right size at any scale, for free.
func _ready() -> void:
	_build_header()
	_build_targets()
	_build_transcript()
	_build_prompt()
	_greet()


func _build_header() -> void:
	var title := Label.new()
	title.text = "Journeyman"
	# The theme's own heading size, which scales with the interface.
	title.set_theme_type_variation("HeaderLarge")
	add_child(title)


func _build_targets() -> void:
	_chips = HFlowContainer.new()
	add_child(_chips)


func _build_transcript() -> void:
	_transcript = RichTextLabel.new()
	_transcript.bbcode_enabled = true
	_transcript.scroll_following = true
	_transcript.selection_enabled = true
	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_transcript.custom_minimum_size = Vector2(0, 200)
	add_child(_transcript)


func _build_prompt() -> void:
	## A single line. Commands and prose go in the same way, which is how the
	## agent CLIs behave and leaves nothing extra to find.
	_prompt = LineEdit.new()
	_prompt.placeholder_text = "Say what to change, or / for commands"
	_prompt.text_submitted.connect(_on_submit)
	add_child(_prompt)


func _greet() -> void:
	_say("Journeyman", "Ready. Select something and say what to change.")
	log_system("Type [b]/help[/b] for what works today.")


# --- selection ---------------------------------------------------------------


func on_selection_changed() -> void:
	_targets.clear()
	for child: Node in _chips.get_children():
		child.queue_free()

	var nodes := EditorInterface.get_selection().get_selected_nodes()
	if nodes.is_empty():
		return
	for node: Node in nodes:
		_targets.append(node.name)
		var chip := Button.new()
		chip.text = "@%s" % node.name
		chip.tooltip_text = "%s (%s)" % [node.get_path(), node.get_class()]
		chip.disabled = true
		chip.add_theme_color_override("font_disabled_color", ACCENT)
		_chips.add_child(chip)


# --- input -------------------------------------------------------------------


func _on_submit(text: String) -> void:
	var entry := text.strip_edges()
	if entry.is_empty() or _busy:
		return
	_prompt.clear()

	if entry.begins_with("/"):
		_say("You", entry)
		_run_slash(entry)
		return

	var chips := ""
	if not _targets.is_empty():
		var tagged := _targets.map(func(t: String) -> String: return "@" + t)
		chips = " [" + ", ".join(tagged) + "]"
	_say("You", entry + chips)
	# No agent transport yet. Be straight about it rather than invent a reply.
	_say(
		"Journeyman",
		"I cannot act on that yet: the agent bridge is not connected, so there "
		+ "is nothing to plan or apply this with. [b]/help[/b] lists what does work."
	)


func _run_slash(entry: String) -> void:
	var command := entry.split(" ")[0].to_lower()
	if not COMMANDS.has(command):
		_say("Journeyman", "No such command: %s. Try [b]/help[/b]." % command)
		return
	match command:
		"/help":
			_show_help()
		"/setup":
			setup_requested.emit()
		"/focus":
			focus_toggle_requested.emit()
		_:
			_run_cli(command.substr(1))


func _show_help() -> void:
	var lines: Array[String] = []
	for command: String in COMMANDS:
		lines.append("[b]%s[/b]  %s" % [command, COMMANDS[command][0]])
	_say("Journeyman", "\n".join(lines))


func _run_cli(command: String) -> void:
	_busy = true
	_prompt.editable = false
	_say("Journeyman", "Running [b]joinery %s[/b]…" % command)

	var out: Array = []
	var code := OS.execute("uv", _args_for(command), out, true)
	var text: String = "\n".join(out).strip_edges()
	_log_block(text if not text.is_empty() else "(no output)")
	if code != 0:
		_say("Journeyman", "`%s` exited %d." % [command, code])
	_busy = false
	_prompt.editable = true
	_prompt.grab_focus()


func _args_for(command: String) -> PackedStringArray:
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	var args := PackedStringArray(["run", "--project", _engine_dir(), "joinery"])
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
	_transcript.append_text("[color=#%s]%s[/color]\n\n" % [DIM.to_html(false), what])


func _log_block(text: String) -> void:
	_transcript.append_text("[code]%s[/code]\n\n" % text)
