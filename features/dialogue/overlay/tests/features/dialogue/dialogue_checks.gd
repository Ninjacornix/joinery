extends RefCounted
## The checks behind dialogue_test.gd, loaded once the game runs (so this file can
## use the autoloads). Works on every blueprint: it plays guard.dlg over the level.

const GUARD := "res://tests/features/dialogue/guard.dlg"
const Parser := preload("res://features/dialogue/dialogue_parser.gd")

var tree: SceneTree
var h: PlayHarness
var events: Array[String] = []


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	await h.boot()
	h.expect(Game.current_level() != null, "the level starts")
	_check_parser()
	_check_loader()
	Dialogue.event.connect(_on_event)
	await _check_first_visit()
	await _check_second_visit()
	await _check_accessibility()
	await _check_translation()
	_check_playtest_state()
	Dialogue.event.disconnect(_on_event)
	Dialogue.stop()


func _check_parser() -> void:
	var file: DialogueFile = DialogueFile.open(GUARD)
	h.expect(file != null, "guard.dlg reads")
	if file == null:
		return
	h.expect(file.errors().is_empty(), "guard.dlg has no problems: %s" % [file.errors()])
	h.expect(file.node_names() == ["start", "paid", "shrug"], "the nodes in file order")
	var keys: Array[String] = []
	for entry: Dictionary in file.program()["texts"]:
		keys.append(str(entry["key"]))
	h.expect(keys.has("guard_halt"), "[ID:…] is the line's translation key")
	var bad: Dictionary = Parser.parse(
		"~ start\nelif x\nset = 3\n=> nowhere\n- \nGuard: [if (]\n", "res://bad.dlg"
	)
	var errors: Array = bad["errors"]
	h.expect(errors.size() >= 5, "problems are found: %s" % [errors])
	var joined: String = "\n".join(PackedStringArray(errors))
	h.expect(joined.contains("res://bad.dlg:2: elif"), "a problem names its file and line")
	h.expect(joined.contains("res://bad.dlg:5: a choice needs text"), "a choice needs text")
	h.expect(joined.contains("there is no node ~ nowhere"), "an unknown jump is a problem")
	h.expect(joined.contains("elif without an if"), "an elif needs an if")
	var plain: Dictionary = Parser.parse("Hello.\n- Yes\n- No\n")
	h.expect((plain["errors"] as Array).is_empty(), "a file without nodes starts at start")
	h.expect((plain["nodes"] as Dictionary).has("start"), "text before any ~ is node start")


func _check_loader() -> void:
	var loaded: Resource = load(GUARD)
	h.expect(loaded is DialogueFile, "load() gives a DialogueFile for a .dlg file")


func _check_first_visit() -> void:
	Dialogue.variables.clear()
	Dialogue.variables["gold"] = 25
	Dialogue.variables["has_cake"] = false
	Settings.set_value("access", "reduced_motion", false)
	var ended: Dictionary = h.watch(Dialogue.ended)
	h.expect(Dialogue.start(GUARD), "the guard's dialogue starts")
	await h.process_frames(2)
	var box: CanvasLayer = Dialogue.box
	h.expect(box.panel.visible, "the dialogue box shows")
	h.expect(box.speaker_label.text == "Guard", "the speaker's name shows")
	h.expect(box.text_label.text == "Halt! Who goes there?", "the first line shows")
	h.expect(box.is_typing(), "the line types out")
	await h.tap(&"ui_accept")
	h.expect(not box.is_typing(), "Accept shows the whole line first")
	h.expect(box.text_label.text == "Halt! Who goes there?", "and stays on it")
	await h.tap(&"ui_accept")
	h.expect(box.text_label.text == "You have 25 gold.", "{{gold}} is filled in")
	await _next()
	h.expect(box.text_label.text == "First time here?", "if/elif/else picks the else")
	await _next()
	var buttons: Array[Button] = box.choice_buttons()
	var texts: Array[String] = []
	for button: Button in buttons:
		texts.append(button.text)
	h.expect(
		texts == ["Pay ten gold", "Ask about the castle", "Walk away"],
		"choices whose condition fails are hidden (got %s)" % [texts]
	)
	h.expect(not box.hint.visible, "no Continue hint while choosing")
	if buttons.size() == 3:
		await h.process_frames(2)
		h.expect(h.focused() == buttons[0], "the first choice has focus")
		await h.tap(&"ui_down")
		await h.tap(&"ui_accept")
	h.expect(box.text_label.text == "It's old.", "a choice runs its block")
	await _next()
	h.expect(events == ["castle_asked"], "do castle_asked fires an event")
	h.expect(box.speaker_label.visible == false, "narration has no speaker")
	h.expect(
		box.text_label.text == "The guard shrugs: nothing more to say.",
		"=> jumps to a node; \\: writes a colon"
	)
	await _next()
	h.expect(h.fire_count(ended) == 1, "=> END ends the dialogue")
	h.unwatch(ended)
	h.expect(not box.panel.visible, "the box closes at the end")
	h.expect(not Dialogue.is_running(), "nothing runs after the end")
	h.expect(Dialogue.variables.get("visits") == 1, "set visits += 1 counts from nothing")


func _check_second_visit() -> void:
	events.clear()
	Settings.set_value("access", "reduced_motion", true)
	h.expect(Dialogue.start(GUARD), "the dialogue starts again")
	await h.process_frames(2)
	var box: CanvasLayer = Dialogue.box
	h.expect(not box.is_typing(), "reduced motion shows the line at once")
	await _next()
	await _next()
	h.expect(box.text_label.text == "Back again?", "variables last between conversations")
	await _next()
	var buttons: Array[Button] = box.choice_buttons()
	h.expect(buttons.size() == 3, "three choices again")
	if not buttons.is_empty():
		await _click(buttons[0])
	h.expect(box.text_label.text == "The gate is open.", "clicking a choice jumps to its node")
	h.expect(Dialogue.variables.get("gold") == 15, "set gold -= 10")
	h.expect(events == ["open_gate"], "do open_gate fires")
	await _click(box.panel)
	h.expect(not Dialogue.is_running(), "a click on the box advances")
	Settings.set_value("access", "reduced_motion", false)


func _check_accessibility() -> void:
	h.expect(Dialogue.start(GUARD), "the dialogue starts for the size checks")
	await h.process_frames(2)
	var box: CanvasLayer = Dialogue.box
	var label: Label = box.text_label
	var before: int = label.get_theme_font_size(&"font_size")
	h.expect(before >= UiStyle.min_text_px(), "dialogue text is at least the minimum size")
	Settings.set_value("access", "subtitle_size", 1.5)
	var after: int = label.get_theme_font_size(&"font_size")
	h.expect(after > before, "the subtitle size grows dialogue text (%d -> %d)" % [before, after])
	Settings.set_value("access", "subtitle_size", 1.0)
	Settings.set_value("access", "high_contrast", true)
	var style: StyleBoxFlat = box.panel.get_theme_stylebox(&"panel") as StyleBoxFlat
	h.expect(style != null and style.bg_color == Color.BLACK, "high contrast makes the box black")
	Settings.set_value("access", "high_contrast", false)
	h.expect(int(label.get(&"accessibility_live")) == 1, "lines are read out as they show")
	Dialogue.stop()
	h.expect(not box.panel.visible, "stop() closes the box")


func _check_translation() -> void:
	var french: Translation = Translation.new()
	french.locale = "fr"
	french.add_message("Halt! Who goes there?", "Halte ! Qui va là ?", "guard_halt")
	french.add_message("Guard", "Garde")
	TranslationServer.add_translation(french)
	var locale: String = TranslationServer.get_locale()
	TranslationServer.set_locale("fr")
	h.expect(Dialogue.start(GUARD), "the dialogue starts in French")
	await h.process_frames(2)
	h.expect(Dialogue.box.text_label.text == "Halte ! Qui va là ?", "lines translate by key")
	h.expect(Dialogue.box.speaker_label.text == "Garde", "speaker names translate")
	Dialogue.stop()
	TranslationServer.set_locale(locale)
	TranslationServer.remove_translation(french)


func _check_playtest_state() -> void:
	Dialogue.start(GUARD, "shrug")
	var state: Dictionary = Dialogue.playtest_state()
	h.expect(state.get("kind") == "dialogue", "the playtester sees the dialogue")
	h.expect(state.get("node") == "shrug", "it starts at the node asked for")
	h.expect(str(state.get("source")) == GUARD, "the state names the file")
	h.expect((state.get("variables") as Dictionary).has("gold"), "and its variables")
	h.expect(tree.get_nodes_in_group(&"playtest_state").has(Dialogue), "in the playtest group")
	Dialogue.stop()


## Clicks a control. A headless game without stretch has a 64 px window, too small
## for the box, so there the click goes to the control itself.
func _click(control: Control) -> void:
	var visible: Rect2 = control.get_viewport().get_visible_rect()
	if visible.encloses(control.get_global_rect()):
		await h.click_control(control)
		return
	var click: InputEventMouseButton = InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	if control is Button:
		(control as Button).pressed.emit()
	else:
		Dialogue.box.root.gui_input.emit(click)
	await h.physics_frames(2)


## Shows the next line: the first Accept finishes a line still typing out.
func _next() -> void:
	if Dialogue.box.is_typing():
		await h.tap(&"ui_accept")
	await h.tap(&"ui_accept")


func _on_event(name: String) -> void:
	events.append(name)
