extends Node
## Conversations from .dlg files, shown in a dialogue box in the menus' style.
##
##   Dialogue.start("res://game/dialogue/guard.dlg")          # from node "start"
##   Dialogue.start("res://game/dialogue/guard.dlg", "shop")
##   Dialogue.variables["gold"] = 25                          # read by the file
##   Dialogue.event.connect(_on_dialogue_event)               # `do open_gate`
##   if Dialogue.is_running(): return                         # e.g. in player input
##
## The player advances with Accept, Interact or a click, and picks choices with
## the arrows or the mouse. Text is translated (English is the message id, [ID:key]
## its context) and sized by the Accessibility page's subtitle size and text size;
## reduced motion shows each line at once instead of typing it out.
## The file format is described in dialogue_parser.gd and features/dialogue/GUIDE.md.

signal started(source: String, node: String)
signal line_shown(line: Dictionary)
signal choices_shown(options: Array)
signal chosen(index: int, text: String)
## A `do <name>` statement ran.
signal event(name: String)
signal ended(source: String)

const Runner := preload("res://features/dialogue/dialogue_runner.gd")
const Box := preload("res://features/dialogue/dialogue_box.gd")
const DialogueFileScript := preload("res://features/dialogue/dialogue_file.gd")
const PLAYTEST_GROUP := &"playtest_state"

## Variables every dialogue reads and sets. Kept between conversations.
var variables: Dictionary = {}
## The open conversation (null when none).
var runner: Runner = null
var box: Box = null
## The last conversation's problems (bad expressions, missing nodes), for tests and
## the playtester.
var problems: Array[String] = []
var _source: String = ""
var _started_frame: int = -1


func _ready() -> void:
	add_to_group(PLAYTEST_GROUP)
	box = Box.new()
	box.name = "DialogueBox"
	add_child(box)
	box.advance_requested.connect(advance)
	box.choice_made.connect(choose)


## Starts `dialogue` (a .dlg path or a DialogueFile) at `node`. Returns false and
## pushes an error when the file cannot be read or has problems.
func start(dialogue: Variant, node: String = "start") -> bool:
	var file: DialogueFileScript = _file_of(dialogue)
	if file == null:
		push_error("Dialogue: cannot read %s" % str(dialogue))
		return false
	var errors: Array = file.errors()
	if not errors.is_empty():
		push_error("Dialogue: %s" % "; ".join(PackedStringArray(errors)))
		return false
	if is_running():
		stop()
	_source = file.source
	runner = Runner.new(file.program(), variables)
	runner.event.connect(_on_event)
	problems = runner.problems
	_started_frame = Engine.get_process_frames()
	started.emit(_source, node)
	_show(runner.start(node))
	return true


## Shows the next line (what Accept does while a line is shown).
func advance() -> void:
	if runner == null or Engine.get_process_frames() == _started_frame:
		return
	if box.is_typing():
		box.finish_typing()
		return
	_show(runner.advance())


## Picks a shown choice by its "index".
func choose(index: int) -> void:
	if runner == null or runner.step.get("type") != "choices":
		return
	for option: Dictionary in runner.step["options"]:
		if int(option["index"]) == index:
			chosen.emit(index, runner.text_of(option))
	_show(runner.choose(index))


func stop() -> void:
	if runner == null:
		return
	var source: String = _source
	runner = null
	box.close()
	ended.emit(source)


func is_running() -> bool:
	return runner != null and runner.is_running()


## What is shown now: a step (see dialogue_runner.gd), or {} when nothing is.
func current() -> Dictionary:
	return runner.step if runner != null else {}


## The shown line's text, translated and filled in ("" when none).
func current_text() -> String:
	var step: Dictionary = current()
	return runner.text_of(step) if step.get("type") == "line" else ""


## For the playtester: where the conversation is and what it has set.
func playtest_state() -> Dictionary:
	if runner == null:
		return {"kind": "dialogue", "running": false, "variables": variables.duplicate(true)}
	var state: Dictionary = runner.state()
	state["kind"] = "dialogue"
	state["running"] = is_running()
	return state


func _file_of(dialogue: Variant) -> DialogueFileScript:
	if dialogue is DialogueFileScript:
		return dialogue
	var path: String = str(dialogue)
	if ResourceLoader.exists(path):
		var loaded: Resource = load(path)
		if loaded is DialogueFileScript:
			return loaded
	return DialogueFileScript.open(path)


func _show(step: Dictionary) -> void:
	match str(step.get("type", "end")):
		"line":
			box.show_line(runner.speaker_of(step), runner.text_of(step))
			line_shown.emit(step)
		"choices":
			var shown: Array[Dictionary] = []
			for option: Dictionary in step["options"]:
				shown.append({"index": option["index"], "text": runner.text_of(option)})
			box.show_choices(shown)
			choices_shown.emit(step["options"])
		_:
			stop()


func _on_event(name: String) -> void:
	event.emit(name)
