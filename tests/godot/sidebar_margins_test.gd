extends SceneTree
## One gutter: at 340, 480 and 1000 px every sidebar row starts and ends UI_THEME.gutter() from
## the dock's edges. The header's last icon glyph, the readiness line and "Check again", the
## user's bubble, the reply, the review row, a check card, the footer and "Show review", and
## the composer share the same left and right edge; the transcript's scrollbar sits in the
## gutter, and the header row starts SPACE.1 under the top edge. Chips with long names end in an
## ellipsis inside the gutter instead of widening the sidebar, and Settings' reviewer Model field
## shows "Same as Journeyman" uncut.

const WIDTHS: Array[int] = [340, 480, 1000]
const HEIGHT := 760
const REQUEST := "Make the door a little wider and darken the wood."

var _failed := false
## Edges measured, so a script error that cuts a width short cannot pass quietly.
var _measured := 0
var _theme: GDScript
var _style: GDScript


func _initialize() -> void:
	_theme = load("res://addons/joinery/ui_theme.gd")
	_style = load("res://addons/joinery/sidebar_style.gd")
	# Earlier cases in the same project may leave a larger text size behind.
	_theme.set_pref("text_size", "Normal")
	load("res://addons/joinery/ui_motion.gd").set_enabled(false)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 4) -> void:
	for index: int in count:
		await process_frame


func _run() -> void:
	for width: int in WIDTHS:
		await _measure(width)
	_check(_measured >= WIDTHS.size() * 20, "Every edge was measured (%d)" % _measured)
	print("sidebar margins: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## [param edge] ("left" or "right") of [param control] lies on [param line], to a pixel.
func _on(control: Control, edge: String, line: float, what: String, width: int) -> void:
	if control == null or not control.is_visible_in_tree():
		_check(false, "%s shows at %d px" % [what, width])
		return
	_measured += 1
	var rect := control.get_global_rect()
	var at := rect.position.x if edge == "left" else rect.end.x
	_check(
		absf(at - line) <= 1.0,
		"%s: %s edge at %.0f, gutter at %.0f (%d px)" % [what, edge, at, line, width]
	)


func _both(control: Control, left: float, right: float, what: String, width: int) -> void:
	_on(control, "left", left, what, width)
	_on(control, "right", right, what, width)


func _reply(transcript: Node) -> RichTextLabel:
	for node: Node in transcript.find_children("*", "RichTextLabel", true, false):
		var label := node as RichTextLabel
		if label.is_visible_in_tree() and label.text.begins_with("I widened"):
			return label
	return null


func _measure(width: int) -> void:
	root.size = Vector2i(width, HEIGHT)
	var host := Control.new()
	host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	host.theme = Theme.new()
	host.theme.set_color("base_color", "Editor", Color("f7f6f3"))
	# Like the editor theme, pad scroll bars: the transcript's bar must still fit the gutter.
	for side: String in ["padding_left", "padding_right"]:
		host.theme.set_constant(side, "VScrollBar", _theme.space(root, 3))
	root.add_child(host)
	var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	# 340 px is below the dock's own minimum: the narrower one it gets beside other docks.
	if width < WIDTHS[1]:
		chat.min_em = chat.SHARED_MIN_EM
	host.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	chat.prompt_submitted.connect(func(_text: String, _targets: Array) -> void: pass)
	await _frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	chat.set_resource_target("res://art/props/door.blend", "PackedScene")
	var header: Node = chat.get("_header")
	header.call("show_readiness", "Godot isn't available yet.")
	var transcript: Node = chat.get("_transcript")
	transcript.call("add_message", "You", REQUEST)
	var card: Control = transcript.call("current_card")
	card.call("set_sent")
	chat.show_agent_message("I widened **Door** by 20% in Blender and darkened the wood.")
	chat.show_file_review(
		"Widen the door", "~ art/props/door.blend", [{"path": "art/props/door.glb"}]
	)
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("design_fixture.gd")
	)
	var designer: Node = tasks.get_node("Design")
	designer.set(
		"check_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("report")
	)
	designer.call("open")
	await _frames(6)

	var gutter := float(_theme.gutter(chat))
	var dock := chat.get_global_rect()
	_check(absf(dock.size.x - width) <= 1.0, "The dock fits %d px, not %.0f" % [width, dock.size.x])
	var left := dock.position.x + gutter
	var right := dock.end.x - gutter
	_check(gutter > 0.0, "The sidebar has a gutter")

	# Every row of the dock's column spans the gutter to gutter; the transcript's content does.
	var content: Control = chat.get("_content")
	for child: Node in content.get_children():
		var row := child as Control
		if row == null or not row.is_visible_in_tree() or row == transcript:
			continue
		_both(row, left, right, "Row " + str(row.name), width)
	var body: Control = transcript.get("_body")
	_both(body, left, right, "The conversation", width)
	var scroller := transcript as Control
	_check(
		scroller.get_global_rect().end.x <= dock.end.x + 0.5,
		"The transcript's scrollbar stays inside the dock (%d px)" % width
	)
	_check(
		scroller.get_global_rect().end.x > right,
		"The transcript's scrollbar sits in the gutter, past the content (%d px)" % width
	)

	# The header: its row starts SPACE.1 under the top; the last icon's glyph ends on the edge.
	var row: HBoxContainer = header.get("row")
	_check(
		absf(row.get_global_rect().position.y - dock.position.y - _theme.space(chat, 1)) <= 1.0,
		"The header row sits SPACE.1 under the sidebar's top (%d px)" % width
	)
	var trailing: Button = null
	for child: Node in row.get_children():
		if child is Button and (child as Button).is_visible_in_tree():
			trailing = child
	if trailing != null:
		var glyph := trailing.get_global_rect().end.x - float(_style.icon_inset(trailing))
		_check(
			absf(glyph - right) <= 1.0,
			"The last header icon ends at %.0f, gutter at %.0f (%d px)" % [glyph, right, width]
		)
	else:
		_check(false, "The header shows its icon buttons (%d px)" % width)
	_on(header.get("_readiness_text"), "left", left, "The readiness line", width)
	_on(header.get("_readiness_action"), "right", right, "Check again", width)

	# The conversation: the bubble, the reply and the review row on the same edges.
	_both(card.get("_bubble"), left, right, "The user's bubble", width)
	_on(_reply(transcript), "left", left, "The reply", width)
	_on(_reply(transcript), "right", right, "The reply", width)
	_on(card.get("_ready_row"), "left", left, "The review row", width)

	# A check card, the footer's line and link, and the composer.
	_both(tasks.get("design_card"), left, right, "The design card", width)
	var footer: Node = chat.get("_footer")
	_on(footer.get("_text"), "left", left, "Waiting for your review", width)
	_on(footer.get("_show"), "right", right, "Show review", width)
	_both(chat.get("_composer"), left, right, "The composer", width)
	await _long_chips(chat, width, right)
	await _model_field(chat, width)
	host.free()
	await _frames(2)


## Long target names: every chip ends inside the gutter in an ellipsis; the dock keeps its width.
func _long_chips(chat: Control, width: int, right: float) -> void:
	var composer: Control = chat.get("_composer")
	var targets: Array[Dictionary] = [
		{
			"id": "gate",
			"label": "Hinge",
			"kind": "Object",
			"resource": "res://levels/courtyard.tscn",
			"crumbs": ["courtyard", "ExtremelyLongNodeNameForTheWroughtIronGateHingeAndLatch"],
		},
		{
			"id": "albedo",
			"label": "Albedo",
			"kind": "Image",
			"resource": "res://art/textures/a_very_long_texture_name_for_the_gate_albedo.png",
		},
	]
	composer.call("set_targets", targets)
	await _frames(6)
	var dock := chat.get_global_rect()
	_check(absf(dock.size.x - width) <= 1.0, "Long chips keep the dock at %d px" % width)
	var chips: Node = composer.get("chips")
	_check(chips.get_child_count() == 2, "Both chips show (%d px)" % width)
	for node: Node in chips.get_children():
		var chip := node as Control
		var end := chip.get_global_rect().end.x
		_measured += 1
		_check(
			end <= right + 1.0, "A long chip ends at %.0f, past %.0f (%d px)" % [end, right, width]
		)
		var label: Label = chip.get("label")
		_check(
			label.text_overrun_behavior == TextServer.OVERRUN_TRIM_ELLIPSIS,
			"A long name ends in an ellipsis (%d px)" % width
		)
	var none: Array[Dictionary] = []
	composer.call("set_targets", none)
	await _frames(2)


## Settings: the reviewer's Model field has room for "Same as Journeyman".
func _model_field(chat: Control, width: int) -> void:
	var settings: Node = chat.get("_settings")
	settings.call("set_open", true)
	await _frames(4)
	var reviewer: Node = settings.find_child("ReviewerSetting", true, false)
	_check(reviewer != null, "Settings shows the Reviewer agent (%d px)" % width)
	if reviewer != null:
		reviewer.call("apply_status", {"enabled": true, "model": ""})
		await _frames(4)
		var field: LineEdit = reviewer.get("model")
		var font := field.get_theme_font("font")
		var size := field.get_theme_font_size("font_size")
		var text := font.get_string_size(field.placeholder_text, 0, -1, size).x
		var box := field.get_theme_stylebox("normal")
		var room := field.size.x - box.get_margin(SIDE_LEFT) - box.get_margin(SIDE_RIGHT)
		_measured += 1
		_check(
			room >= text, "Model has %.0f px for %.0f of placeholder (%d px)" % [room, text, width]
		)
		_check(
			field.get_global_rect().end.x <= chat.get_global_rect().end.x + 0.5,
			"The Model field stays inside the sidebar (%d px)" % width
		)
	settings.call("set_open", false)
	await _frames(2)
