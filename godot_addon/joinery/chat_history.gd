@tool
extends ScrollContainer
## Message rows retain plain source text; streaming redraws at most once a frame.

const UI_THEME := preload("ui_theme.gd")

var _body: VBoxContainer
var _messages: Array[Dictionary] = []
var _dirty := false
var _follow := true
var _welcome: Label
var _layout_dirty := true
var _bold := RegEx.new()


func _ready() -> void:
	_bold.compile("\\*\\*([^*\\n]+)\\*\\*")
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	follow_focus = true
	var margins := MarginContainer.new()
	margins.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var gutter := int(get_theme_default_font_size() * 0.5)
	for edge: String in ["left", "right", "bottom"]:
		margins.add_theme_constant_override("margin_" + edge, gutter)
	margins.add_theme_constant_override("margin_top", get_theme_default_font_size())
	add_child(margins)
	_body = VBoxContainer.new()
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.add_theme_constant_override("separation", int(get_theme_default_font_size() * 0.9))
	margins.add_child(_body)
	_welcome = Label.new()
	_welcome.text = "Select something in your scene, then describe what to change."
	_welcome.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_welcome.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	_body.add_child(_welcome)
	get_v_scroll_bar().value_changed.connect(_on_scroll)


func _on_scroll(value: float) -> void:
	var bar := get_v_scroll_bar()
	_follow = value >= bar.max_value - bar.page - get_theme_default_font_size() * 2


func add_message(who: String, text: String) -> void:
	_welcome.hide()
	var user := who == "You"
	if user and not _messages.is_empty():
		# A new turn starts with your message: give it room above.
		var gap := Control.new()
		gap.custom_minimum_size.y = get_theme_default_font_size() * 0.6
		_body.add_child(gap)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_theme_constant_override("separation", 2)
	_body.add_child(column)
	var content_parent: Node = column
	var panel: PanelContainer
	if user:
		var row := HBoxContainer.new()
		column.add_child(row)
		var spacer := Control.new()
		spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(spacer)
		panel = PanelContainer.new()
		panel.size_flags_horizontal = Control.SIZE_SHRINK_END
		panel.add_theme_stylebox_override(
			"panel",
			UI_THEME.panel(get_theme_color("raised", "Joinery"), Color.TRANSPARENT, 12, 14, 0)
		)
		row.add_child(panel)
		content_parent = panel
	var summary := Button.new()
	summary.flat = true
	summary.alignment = HORIZONTAL_ALIGNMENT_LEFT
	summary.custom_minimum_size.y = get_theme_default_font_size() * 1.4
	summary.add_theme_font_size_override("font_size", int(get_theme_default_font_size() * 0.8))
	for state: String in ["normal", "hover", "pressed"]:
		summary.add_theme_stylebox_override(state, StyleBoxEmpty.new())
	summary.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	summary.hide()
	var details := RichTextLabel.new()
	_configure_text(details)
	details.hide()
	summary.pressed.connect(func() -> void: details.visible = not details.visible)
	var label := RichTextLabel.new()
	_configure_text(label)
	label.accessibility_name = who + " message"
	content_parent.add_child(label)
	# Timing reads after the reply it describes, small and quiet.
	column.add_child(summary)
	column.add_child(details)
	var record := {
		"who": who,
		"text": text,
		"label": label,
		"panel": panel,
		"summary": summary,
		"details": details,
		"dirty": true,
	}
	_messages.append(record)
	_follow = true if user else _follow
	_dirty = true


func _configure_text(label: RichTextLabel) -> void:
	label.fit_content = true
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.scroll_active = false
	label.selection_enabled = true
	label.bbcode_enabled = true
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.add_theme_stylebox_override("normal", StyleBoxEmpty.new())


func append_delta(who: String, text: String, new_message: bool) -> void:
	if new_message or _messages.is_empty() or _messages[-1]["who"] != who:
		add_message(who, "")
	_messages[-1]["text"] += text
	_messages[-1]["dirty"] = true
	_dirty = true


func finish_response() -> void:
	pass


## A status line from Joinery itself (accepted, rejected), not a chat message.
func add_notice(text: String) -> void:
	_welcome.hide()
	var notice := Label.new()
	notice.text = (
		"✓  " + text
		if text.begins_with("Accepted") or text.begins_with("Changes accepted")
		else "·  " + text
	)
	notice.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	notice.add_theme_font_size_override("font_size", int(get_theme_default_font_size() * 0.8))
	notice.add_theme_color_override("font_color", get_theme_color("secondary", "Joinery"))
	notice.accessibility_name = "Status: " + text
	_body.add_child(notice)
	_follow = true
	_dirty = true


func show_timing(event: Dictionary) -> void:
	if _messages.is_empty() or _messages[-1]["who"] == "You":
		return
	var record := _messages[-1]
	var elapsed := float(event.get("total_ms", 0)) / 1000.0
	var status := (
		"Completed"
		if event.get("outcome") == "done"
		else "Stopped" if event.get("outcome") == "cancelled" else "Failed"
	)
	record["summary"].text = "%s in %.1fs" % [status, elapsed]
	record["summary"].tooltip_text = "Show timing details"
	record["summary"].accessibility_name = "Show response timing details"
	record["summary"].show()
	var lines := PackedStringArray()
	if not str(event.get("model", "")).is_empty():
		lines.append(
			(
				"%s · %s · %s"
				% [event["model"], str(event.get("mode", "auto")), str(event.get("effort", ""))]
			)
		)
	if event.get("first_response_ms") != null:
		lines.append("First response: %.1fs" % (float(event["first_response_ms"]) / 1000.0))
	for phase: String in event.get("phases_ms", {}):
		lines.append("%s: %.1fs" % [phase.capitalize(), float(event["phases_ms"][phase]) / 1000.0])
	record["details"].text = "\n".join(lines)
	_dirty = true


func get_parsed_text() -> String:
	var lines := PackedStringArray()
	for record: Dictionary in _messages:
		lines.append(str(record["who"]) + "  " + str(record["text"]))
	return "\n\n".join(lines)


func _format(source: String) -> String:
	# Escape BBCode before interpreting a small, safe Markdown subset.
	var result := ""
	var blocks := source.split("```")
	for index: int in blocks.size():
		if index % 2 == 1:
			var block := blocks[index]
			var newline := block.find("\n")
			if newline >= 0:
				block = block.substr(newline + 1)
			result += "\n[code]" + block.replace("[", "[lb]") + "[/code]\n"
		else:
			var spans := blocks[index].split("`")
			for part: int in spans.size():
				var safe := spans[part].replace("[", "[lb]")
				result += (
					"[code]" + safe + "[/code]"
					if part % 2 == 1
					else _bold.sub(safe, "[b]$1[/b]", true)
				)
	return result


func _process(_delta: float) -> void:
	if not _dirty or _body == null:
		return
	_dirty = false
	var font := get_theme_default_font()
	var font_size := get_theme_default_font_size()
	for record: Dictionary in _messages:
		var label: RichTextLabel = record["label"]
		if record["dirty"]:
			label.text = _format(str(record["text"]))
		if record["panel"] != null and (_layout_dirty or record["dirty"]):
			var measured := (
				font
				. get_string_size(str(record["text"]), HORIZONTAL_ALIGNMENT_LEFT, -1, font_size)
				. x
			)
			label.custom_minimum_size.x = minf(
				maxf(30, measured + 4), maxf(80, _body.size.x * 0.85 - 28)
			)
		record["dirty"] = false
	_layout_dirty = false
	if _follow:
		_scroll_end.call_deferred()


func _scroll_end() -> void:
	scroll_vertical = int(get_v_scroll_bar().max_value)


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED:
		_dirty = true
		_layout_dirty = true
