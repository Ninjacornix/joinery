@tool
extends VBoxContainer
## The review's click-through preview of a changed dialogue file (.dlg): play the
## conversation, Before or After, from any node, picking choices as a player would.
##
## It runs the game's own dialogue parser and runner (the dialogue feature's
## res://features/dialogue/), so the preview reads a file exactly as the game will.
## Without that feature in the game it says so and shows the file's text.
## Variables start empty; "Start with" sets some ("gold = 25, met = true").

signal played(step: Dictionary)

const UI := preload("ui_components.gd")
const PARSER := "res://features/dialogue/dialogue_parser.gd"
const RUNNER := "res://features/dialogue/dialogue_runner.gd"
## Lines kept in the played-so-far log.
const LOG_MAX := 60

## The item on show ({path, media: "dialogue", before?, after?}) and its side.
var item: Dictionary = {}
var side: String = "after"
var runner: RefCounted = null
var program: Dictionary = {}

var _sides: OptionButton
var _nodes: OptionButton
var _start_with: LineEdit
var _restart: Button
var _problems: Label
var _log: VBoxContainer
var _scroll: ScrollContainer
var _choices: VBoxContainer
var _continue: Button
var _variables: Label
var _text: Label


func _init() -> void:
	name = "ReviewDialogue"
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	var bar := HBoxContainer.new()
	bar.theme_type_variation = "JoineryRow"
	add_child(bar)
	_sides = UI.picker()
	_sides.tooltip_text = "Play the file as it was (Before) or as changed (After)"
	_sides.item_selected.connect(func(_index: int) -> void: _on_side_picked())
	bar.add_child(_sides)
	_nodes = UI.picker()
	_nodes.tooltip_text = "Where the conversation starts"
	_nodes.item_selected.connect(func(_index: int) -> void: restart())
	bar.add_child(_nodes)
	_start_with = LineEdit.new()
	_start_with.placeholder_text = "Start with: gold = 25, met = true"
	_start_with.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_start_with.text_submitted.connect(func(_text_in: String) -> void: restart())
	bar.add_child(_start_with)
	_restart = UI.secondary("Restart")
	_restart.pressed.connect(restart)
	bar.add_child(_restart)
	_problems = UI.label("", "JoineryCaption", true)
	_problems.add_theme_color_override("font_color", Color("#e5484d"))
	_problems.hide()
	add_child(_problems)
	_scroll = ScrollContainer.new()
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(_scroll)
	_log = VBoxContainer.new()
	_log.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_log)
	_text = UI.label("", "", true)
	_text.hide()
	add_child(_text)
	_choices = VBoxContainer.new()
	add_child(_choices)
	_continue = UI.primary("Continue")
	_continue.pressed.connect(advance)
	add_child(_continue)
	_variables = UI.label("", "JoineryCaption", true)
	add_child(_variables)


## Shows [param shown] (a dialogue item) on [param on_side] ("before", "after").
func show_item(shown: Dictionary, on_side: String = "after") -> void:
	item = shown
	side = on_side if shown.has(on_side) else ("after" if shown.has("after") else "before")
	_sides.clear()
	for key: String in ["before", "after"]:
		if shown.has(key):
			_sides.add_item(key.capitalize())
			_sides.set_item_metadata(_sides.item_count - 1, key)
			if key == side:
				_sides.select(_sides.item_count - 1)
	_sides.visible = _sides.item_count > 1
	_load()


## True when the game has the dialogue feature, so the file can be played.
static func can_play() -> bool:
	return ResourceLoader.exists(PARSER) and ResourceLoader.exists(RUNNER)


## Plays from the chosen node again, with the "Start with" variables.
func restart() -> void:
	_clear_log()
	runner = null
	if program.is_empty() or not (program["errors"] as Array).is_empty():
		_show_step({"type": "end"})
		return
	var runner_script: GDScript = load(RUNNER) as GDScript
	runner = runner_script.new(program, start_variables())
	var node := _nodes.get_item_text(_nodes.selected) if _nodes.selected >= 0 else "start"
	_show_step(runner.call(&"start", node))


## Shows the next line.
func advance() -> void:
	if runner != null:
		_show_step(runner.call(&"advance"))


## Picks the shown choice whose index is [param index].
func choose(index: int) -> void:
	if runner == null:
		return
	for button: Node in _choices.get_children():
		if button is Button and int(button.get_meta(&"index", -1)) == index:
			_add_log("→ " + (button as Button).text, true)
	_show_step(runner.call(&"choose", index))


## The "Start with" text as variables: "gold = 25, met = true" → {gold: 25, met: true}.
func start_variables() -> Dictionary:
	var found := {}
	for part: String in _start_with.text.split(",", false):
		var pair := part.split("=", false, 1)
		if pair.size() != 2 or not pair[0].strip_edges().is_valid_identifier():
			continue
		var raw := pair[1].strip_edges()
		var value: Variant = str_to_var(raw)
		found[pair[0].strip_edges()] = raw if value == null and raw != "null" else value
	return found


## The text lines played so far ("Guard: Halt!", "→ Pay ten gold", ...).
func log_lines() -> PackedStringArray:
	var lines := PackedStringArray()
	for child: Node in _log.get_children():
		if child is Label:
			lines.append((child as Label).text)
	return lines


## The choice buttons shown now.
func choice_buttons() -> Array[Button]:
	var found: Array[Button] = []
	for child: Node in _choices.get_children():
		if child is Button and not child.is_queued_for_deletion():
			found.append(child as Button)
	return found


func _on_side_picked() -> void:
	side = str(_sides.get_item_metadata(_sides.selected))
	_load()


func _load() -> void:
	program = {}
	_problems.hide()
	_nodes.clear()
	var path := str(item.get(side, ""))
	var text := FileAccess.get_file_as_string(path) if FileAccess.file_exists(path) else ""
	var playable := can_play()
	_text.visible = not playable
	_scroll.visible = playable
	_nodes.visible = playable
	_start_with.visible = playable
	_restart.visible = playable
	if not playable:
		_text.text = (
			"Add the dialogue feature to play this conversation here. The file:\n\n" + text
		)
		_show_step({"type": "end"})
		return
	var parser: GDScript = load(PARSER) as GDScript
	program = parser.call(&"parse", text, "res://" + str(item.get("path", "")))
	for node: String in program["node_order"]:
		_nodes.add_item(node)
	var errors: Array = program["errors"]
	if not errors.is_empty():
		_problems.text = "\n".join(PackedStringArray(errors))
		_problems.show()
	restart()


func _show_step(step: Dictionary) -> void:
	for child: Node in _choices.get_children():
		_choices.remove_child(child)
		child.queue_free()
	var kind := str(step.get("type", "end"))
	_continue.visible = kind == "line"
	match kind:
		"line":
			var speaker: String = runner.call(&"speaker_of", step)
			var words: String = runner.call(&"text_of", step)
			_add_log(words if speaker.is_empty() else "%s: %s" % [speaker, words], false)
		"choices":
			for option: Dictionary in step["options"]:
				var button := UI.secondary(str(runner.call(&"text_of", option)))
				button.set_meta(&"index", int(option["index"]))
				button.alignment = HORIZONTAL_ALIGNMENT_LEFT
				button.pressed.connect(choose.bind(int(option["index"])))
				_choices.add_child(button)
		_:
			if runner != null:
				_add_log("(end)", true)
	_show_variables()
	played.emit(step)


func _show_variables() -> void:
	if runner == null:
		_variables.text = ""
		return
	var parts := PackedStringArray()
	var values: Dictionary = runner.get(&"variables")
	for key: Variant in values:
		parts.append("%s = %s" % [key, var_to_str(values[key])])
	var problems: Array = runner.get(&"problems")
	var text := "Variables: " + (", ".join(parts) if not parts.is_empty() else "none set")
	if not problems.is_empty():
		text += "\n" + "\n".join(PackedStringArray(problems))
	_variables.text = text


func _add_log(text: String, quiet: bool) -> void:
	var line := UI.label(text, "JoineryCaption" if quiet else "", true)
	_log.add_child(line)
	while _log.get_child_count() > LOG_MAX:
		var first := _log.get_child(0)
		_log.remove_child(first)
		first.queue_free()
	_scroll.set_deferred(&"scroll_vertical", 1 << 20)


func _clear_log() -> void:
	for child: Node in _log.get_children():
		_log.remove_child(child)
		child.queue_free()
