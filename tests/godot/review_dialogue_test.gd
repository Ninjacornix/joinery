extends SceneTree
## A changed dialogue file in the review, headless: the stage plays the conversation
## through the game's own dialogue scripts (copied into the test project), After by
## default and Before on request, from any node, with choices to click, "Start with"
## variables, the file's problems by line, and the plain text when the game has no
## dialogue feature.

const PANEL := preload("res://addons/joinery/review_panel.gd")
const DIALOGUE := preload("res://addons/joinery/review_dialogue.gd")
const BEFORE := "~ start\nGuard: Halt!\n=> END\n"
const AFTER := (
	"~ start\nGuard: Welcome, {{name}}.\n- Ask for gold [if gold > 0] => rich\n"
	+ "- Leave => END\n\n~ rich\nGuard: Here, {{gold}} coins.\nset gold -= 1\n"
)

var _failed := false
var _cache: String


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _write(file: String, text: String) -> String:
	var path := _cache.path_join(file)
	var handle := FileAccess.open(path, FileAccess.WRITE)
	handle.store_string(text)
	handle.close()
	return path


func _run() -> void:
	_cache = OS.get_cache_dir().path_join("joinery-review-dialogue-test")
	DirAccess.make_dir_recursive_absolute(_cache)
	Engine.set_meta("joinery_ui_config", _cache.path_join("journeyman.cfg"))
	root.size = Vector2i(900, 560)
	var host := Control.new()
	host.size = Vector2(900, 560)
	root.add_child(host)
	var panel: PANEL = PANEL.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	host.add_child(panel)
	await _frames()
	_check(DIALOGUE.can_play(), "the test project has the dialogue feature's scripts")
	var item := {
		"path": "game/dialogue/guard.dlg",
		"media": "dialogue",
		"before": _write("before.dlg", BEFORE),
		"after": _write("after.dlg", AFTER),
	}
	panel.show_file_review(
		"A friendlier guard",
		"--- a/game/dialogue/guard.dlg\n+++ b/game/dialogue/guard.dlg\n-Guard: Halt!\n",
		[item],
		[{"path": "game/dialogue/guard.dlg", "change": "modified"}]
	)
	await _frames()
	var view: DIALOGUE = panel._media._dialogue
	_check(panel._media.current_kind() == "dialogue", "a .dlg item is a dialogue")
	_check(view.is_visible_in_tree(), "the stage shows the dialogue player")
	_check(view.side == "after", "it plays the After first")
	_check(view.log_lines() == PackedStringArray(["Guard: Welcome, ."]), "the first line shows")
	view.advance()
	_check(view.choice_buttons().size() == 1, "a choice whose condition fails is hidden")
	view._start_with.text = 'name = "Ada", gold = 3'
	view.restart()
	_check(view.log_lines()[0] == "Guard: Welcome, Ada.", "Start with fills in variables")
	view.advance()
	var buttons := view.choice_buttons()
	_check(buttons.size() == 2, "both choices show with gold")
	if not buttons.is_empty():
		buttons[0].pressed.emit()
	var lines := view.log_lines()
	_check(lines.has("→ Ask for gold"), "the picked choice is logged")
	_check(lines.has("Guard: Here, 3 coins."), "the choice jumps to its node")
	view.advance()
	_check(view.runner.get(&"variables")["gold"] == 2, "set runs")
	_check(view.log_lines()[-1] == "(end)", "the end is shown")
	view._nodes.select(1)
	view.restart()
	_check(view.log_lines()[0] == "Guard: Here, 3 coins.", "any node can start")
	panel._media.set_side("before")
	await _frames()
	_check(view.side == "before", "Before plays the old file")
	_check(view.log_lines() == PackedStringArray(["Guard: Halt!"]), "the old line shows")
	view.show_item(
		{"path": "x.dlg", "media": "dialogue", "after": _write("bad.dlg", "=> nowhere\n")}
	)
	_check(view._problems.visible, "problems show")
	_check(view._problems.text.contains("res://x.dlg:1:"), "with the file and line")
	panel.clear()
	host.queue_free()
	await _frames()
	Engine.remove_meta("joinery_ui_config")
	if _failed:
		quit(1)
		return
	print("review dialogue: ok")
	quit(0)
