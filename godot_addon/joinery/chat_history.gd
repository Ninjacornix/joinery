@tool
extends ScrollContainer
## The conversation. Each request is a change card (sidebar_change_card.gd); replies are plain
## text on the dock surface; Joinery's own lines are quiet notices. Turns are SPACE.5 apart;
## inside a turn the request card and the reply are SPACE.3 apart (JoineryStack) in every
## state, and notices sit SPACE.2 below the reply.
## Message rows keep their plain source text; streaming redraws at most once a frame.
##
## The transcript fills its slot exactly, sharing the right edge of the header and composer.
## The scrollbar's gutter is always reserved inside it, so bubbles keep one right edge with or
## without a bar. The bottom edge fades instead of cutting messages. Bold text is the body
## family at a semibold weight and the same size, never a separate display font.

signal example_chosen(text: String)
signal retry_requested(card: Control)
signal revert_requested(transaction_id: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const CARD := preload("sidebar_change_card.gd")
const EXAMPLES := [
	"Make the selected model taller", "Add a footstep sound", "Repaint this texture warmer"
]
## Notice kind -> [icon ("" for none), tint role].
const NOTICE_ICONS := {
	"success": ["success", "success"],
	"rejected": ["close", "text_muted"],
	"stopped": ["close", "text_muted"],
	"info": ["", "text_muted"],
	"error": ["error", "danger"],
}

var _body: VBoxContainer
var _empty: Control
var _examples: Array[Button] = []
var _examples_enabled := true
var _turn: VBoxContainer
var _messages: Array[Dictionary] = []
var _cards: Array[Control] = []
var _log := PackedStringArray()
var _last_notice := ""
var _dirty := false
var _follow := true
var _bold := RegEx.new()
var _text_scale := 1.0
var _show_timings := true
var _scaled: Array = []
var _retry_allowed := true


func _ready() -> void:
	_bold.compile("\\*\\*([^*\\n]+)\\*\\*")
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	vertical_scroll_mode = ScrollContainer.SCROLL_MODE_RESERVE
	follow_focus = true
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_body)
	_build_empty()
	get_v_scroll_bar().value_changed.connect(_on_scroll)
	UI_MOTION.rubber_band(self)
	STYLE.on_theme_overrides(self, _apply_fade)
	resized.connect(_on_resized)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and _body != null:
		_rescale()


## Width the scrollbar takes beside the content, reserved whether or not it shows.
func gutter() -> int:
	var bar := get_v_scroll_bar()
	return int(bar.get_minimum_size().x) + get_theme_constant("scrollbar_h_separation")


## A fade over the bottom edge (and the top once scrolled), in the dock's ground colour.
func _apply_fade() -> void:
	if not "scroll_hint_mode" in self:
		return
	var gradient := Gradient.new()
	gradient.set_color(0, Color.WHITE)
	gradient.set_color(1, Color(1, 1, 1, 0))
	var texture := GradientTexture2D.new()
	texture.gradient = gradient
	texture.fill_from = Vector2.ZERO
	texture.fill_to = Vector2(0, 1)
	texture.width = 4
	texture.height = maxi(UI_THEME.space(self, 5), 1)
	add_theme_icon_override("scroll_hint_vertical", texture)
	add_theme_color_override("scroll_hint_vertical_color", get_theme_color("surface", "Joinery"))
	set("scroll_hint_mode", 1)


## When the view gets shorter (a banner opened above it), keep the newest message in sight.
func _on_resized() -> void:
	if _follow:
		_scroll_end.call_deferred()


func _build_empty() -> void:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	_body.add_child(inset)
	# Two groups: what this is (title and hint, SPACE.1 apart) and the prompts you can click
	# (SPACE.1 apart), with a SPACE.4 group gap between them.
	var groups := VBoxContainer.new()
	STYLE.on_theme_overrides(
		groups,
		func() -> void: groups.add_theme_constant_override("separation", UI_THEME.space(groups, 4))
	)
	inset.add_child(groups)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	groups.add_child(column)
	var title := UI.label("What should we change?", "JoineryTitle", true)
	column.add_child(title)
	_scale(title, "title")
	var hint := UI.label(
		"Select something in your scene, then describe the change. Type / for commands.",
		"JoineryCaption",
		true
	)
	column.add_child(hint)
	_scale(hint, "caption")
	var examples := VBoxContainer.new()
	examples.theme_type_variation = "JoineryTight"
	groups.add_child(examples)
	for example: String in EXAMPLES:
		var button := UI.quiet(example)
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		button.tooltip_text = "Write this in the message box"
		button.pressed.connect(func() -> void: example_chosen.emit(example))
		UI.bind_height(button, "sm")
		STYLE.filled(button)
		examples.add_child(button)
		_scale(button, "body")
		_examples.append(button)
	_empty = inset
	set_examples_enabled(_examples_enabled)


## The example prompts act only while an assistant can take them; otherwise they rest muted.
func set_examples_enabled(on: bool) -> void:
	_examples_enabled = on
	for button: Button in _examples:
		button.disabled = not on
		button.focus_mode = Control.FOCUS_ALL if on else Control.FOCUS_NONE
		button.tooltip_text = (
			"Write this in the message box" if on else "Connect an assistant to use this"
		)


func _on_scroll(value: float) -> void:
	var bar := get_v_scroll_bar()
	_follow = value >= bar.max_value - bar.page - UI_THEME.space(self, 6)


## A message. "You" starts a new turn with a change card; anyone else is a reply.
func add_message(who: String, text: String) -> void:
	if who == "You":
		add_request(text)
		return
	_hide_empty()
	var inset := _inset(_current_turn())
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	inset.add_child(column)
	var label := RichTextLabel.new()
	_configure_text(label)
	label.accessibility_name = who + " message"
	column.add_child(label)
	var summary := UI.quiet("")
	summary.icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	summary.alignment = HORIZONTAL_ALIGNMENT_LEFT
	summary.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	summary.theme_type_variation = "JoineryQuietButton"
	UI.bind_height(summary, "xs")
	STYLE.hug_left(summary)
	summary.hide()
	column.add_child(summary)
	var details := UI.label("", "JoineryNumeric", true)
	details.hide()
	column.add_child(details)
	summary.pressed.connect(_toggle_details.bind(summary, details))
	_scale(summary, "caption")
	_scale(details, "caption")
	(
		_messages
		. append(
			{
				"who": who,
				"text": text,
				"label": label,
				"panel": null,
				"summary": summary,
				"details": details,
				"dirty": true,
			}
		)
	)
	_dirty = true


## Start a turn with the user's request and return its change card.
func add_request(text: String, targets: Array = []) -> Control:
	_hide_empty()
	_turn = VBoxContainer.new()
	_turn.theme_type_variation = "JoineryStack"
	_body.add_child(_turn)
	var card := CARD.new(text, targets)
	card.set_retry_allowed(_retry_allowed)
	_turn.add_child(card)
	card.retry_requested.connect(func() -> void: retry_requested.emit(card))
	card.outcome_placing.connect(_place_outcome.bind(_turn))
	card.revert_requested.connect(func(id: String) -> void: revert_requested.emit(id))
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_cards.append(card)
	(
		_messages
		. append(
			{
				"who": "You",
				"text": text,
				"label": card.text_label,
				"panel": card,
				"summary": null,
				"details": null,
				"dirty": false,
			}
		)
	)
	_last_notice = ""
	_follow = true
	_dirty = true
	return card


## Whether unsent cards offer Retry. While a connection banner owns the fix, they only say
## "Not sent", so one problem has one action.
func set_retry_allowed(on: bool) -> void:
	_retry_allowed = on
	for card: Control in _cards:
		if is_instance_valid(card):
			card.set_retry_allowed(on)


## The card of the latest request, or null.
func current_card() -> Control:
	return _cards[-1] if not _cards.is_empty() else null


func append_delta(who: String, text: String, new_message: bool) -> void:
	if new_message or _messages.is_empty() or _messages[-1]["who"] != who:
		add_message(who, "")
	_messages[-1]["text"] += text
	_messages[-1]["dirty"] = true
	_dirty = true


func finish_response() -> void:
	pass


## A line from Joinery itself, not a chat message. Kinds: success, rejected, stopped, info,
## error. Consecutive identical notices are dropped.
func add_notice(text: String, kind := "info") -> void:
	if text.is_empty() or text + kind == _last_notice:
		return
	_last_notice = text + kind
	_log.append("Joinery  " + text)
	_hide_empty()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.accessibility_name = "Status: " + text
	var spec: Array = NOTICE_ICONS.get(kind, NOTICE_ICONS["info"])
	var picture := STYLE.glyph(spec[1])
	if not str(spec[0]).is_empty():
		STYLE.set_glyph(picture, spec[0], spec[1])
	row.add_child(picture)
	var label := UI.label(text, "JoineryCaption", true)
	row.add_child(label)
	_scale(label, "caption")
	_inset(_current_turn()).add_child(row)
	UI_MOTION.fade_in(row)
	_follow = true
	_dirty = true


## The outcome of a review or a turn ("Change accepted", "Stopped"). It is shown on
## [param card] when that card is waiting for it, otherwise as a notice.
func add_decision(text: String, kind: String, card: Control) -> void:
	if card != null and is_instance_valid(card) and card.decide(kind, text):
		if text + kind != _last_notice:
			_log.append("Joinery  " + text)
		_last_notice = text + kind
		_follow = true
		_dirty = true
		return
	add_notice(text, kind)


## A card's decision line ends its turn: after the reply and its timing line, like a notice.
func _place_outcome(row: Control, turn: VBoxContainer) -> void:
	if is_instance_valid(turn) and turn.is_inside_tree():
		_inset(turn).add_child(row)


## The card whose transaction is [param transaction_id], or null.
func card_for(transaction_id: String) -> Control:
	for card: Control in _cards:
		if is_instance_valid(card) and card.transaction_id == transaction_id:
			return card
	return null


## Show the turn's timing line ("Completed in 0:12"). It goes into the request's card, where the
## finished steps fold behind it; a turn without a card keeps it under the reply. Returns false
## when the turn has no reply yet.
func show_timing(event: Dictionary) -> bool:
	if _messages.is_empty() or _messages[-1]["who"] == "You":
		return false
	var record: Dictionary = _messages[-1]
	var seconds := float(event.get("total_ms", 0)) / 1000.0
	var outcome := str(event.get("outcome", ""))
	var clock := UI_MOTION.format_clock(seconds)
	var summary: Button = record["summary"]
	summary.text = (
		"Completed in " + clock
		if outcome == "done"
		else "Stopped after " + clock if outcome == "cancelled" else "Failed after " + clock
	)
	summary.tooltip_text = "Show timing details"
	summary.accessibility_name = "Show response timing details"
	if not summary.has_meta("_joinery_icon"):
		UI.bind_icon(summary, "disclosure_closed")
	summary.add_theme_font_override("font", UI_THEME.numeric_font(self))
	summary.visible = _show_timings
	var lines := PackedStringArray()
	if not str(event.get("model", "")).is_empty():
		var route := [str(event["model"]), str(event.get("mode", "auto")).capitalize()]
		if not str(event.get("effort", "")).is_empty():
			route.append(str(event["effort"]).capitalize())
		lines.append(" · ".join(route))
	if event.get("first_response_ms") != null:
		lines.append(
			"First response " + UI_MOTION.format_clock(float(event["first_response_ms"]) / 1000.0)
		)
	for phase: String in event.get("phases_ms", {}):
		var phase_seconds := float(event["phases_ms"][phase]) / 1000.0
		lines.append("%s %s" % [phase.capitalize(), UI_MOTION.format_clock(phase_seconds)])
	record["details"].text = "\n".join(lines)
	var card := current_card()
	if card != null and card.get_parent() == _turn:
		summary.set_meta("_joinery_card", card)
		summary.set_meta("_joinery_open", false)
		UI.bind_icon(summary, "disclosure_closed")
		card.adopt_timing(summary, record["details"])
	return true


func set_text_scale(scale: float) -> void:
	_text_scale = scale
	_rescale()


func set_show_timings(on: bool) -> void:
	_show_timings = on
	for record: Dictionary in _messages:
		if record["summary"] == null:
			continue
		var summary: Button = record["summary"]
		summary.visible = on and not summary.text.is_empty()
		if not on:
			record["details"].hide()
		var card: Variant = summary.get_meta("_joinery_card", null)
		if card is Control and is_instance_valid(card):
			if not on:
				card.set_details_open(false)
				summary.set_meta("_joinery_open", false)
				UI.bind_icon(summary, "disclosure_closed")
			card.refresh_layout()


func get_parsed_text() -> String:
	var lines := PackedStringArray()
	for record: Dictionary in _messages:
		lines.append(str(record["who"]) + "  " + str(record["text"]))
	lines.append_array(_log)
	return "\n\n".join(lines)


## Every notice shown so far, in order (including decisions shown on cards).
func notice_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for line: String in _log:
		texts.append(line.trim_prefix("Joinery  "))
	return texts


func _toggle_details(summary: Button, details: Label) -> void:
	var open := not bool(summary.get_meta("_joinery_open", details.visible))
	summary.set_meta("_joinery_open", open)
	UI.bind_icon(summary, "disclosure_open" if open else "disclosure_closed")
	var card: Variant = summary.get_meta("_joinery_card", null)
	if card is Control and is_instance_valid(card):
		# The card shows its steps and the details together, fading them in.
		card.set_details_open(open, true)
	if open:
		if not details.text.is_empty():
			UI_MOTION.fade_in(details)
	else:
		details.hide()


func _hide_empty() -> void:
	if _empty != null and _empty.visible:
		_empty.hide()


func _current_turn() -> VBoxContainer:
	if _turn == null:
		_turn = VBoxContainer.new()
		_turn.theme_type_variation = "JoineryStack"
		_body.add_child(_turn)
	return _turn


## Replies and notices sit on the shared SPACE.2 text inset; cards are boxes on the outer edge.
func _inset(turn: VBoxContainer) -> VBoxContainer:
	if turn.get_child_count() > 0 and turn.get_child(-1).has_meta("joinery_inset"):
		return turn.get_child(-1).get_child(0)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.set_meta("joinery_inset", true)
	turn.add_child(inset)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	inset.add_child(column)
	return column


func _configure_text(label: RichTextLabel) -> void:
	label.fit_content = true
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.scroll_active = false
	label.selection_enabled = true
	label.bbcode_enabled = true
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scale(label, "body")


## Keep [param control]'s text at the TYPE step [param type_name] times the text size setting.
func _scale(control: Control, type_name: String) -> void:
	_scaled.append([control, type_name])
	if is_inside_tree():
		_apply_scale(control, type_name)


func _rescale() -> void:
	var live: Array = []
	for pair: Array in _scaled:
		if is_instance_valid(pair[0]):
			_apply_scale(pair[0], pair[1])
			live.append(pair)
	_scaled = live
	_dirty = true


func _apply_scale(control: Control, type_name: String) -> void:
	var size := roundi(UI_THEME.font_size(self, type_name) * _text_scale)
	if control is RichTextLabel:
		for key: String in ["normal_font_size", "bold_font_size", "mono_font_size"]:
			control.add_theme_font_size_override(key, size)
		_semibold(control as RichTextLabel)
	else:
		control.add_theme_font_size_override("font_size", size)


## Bold is the body font emboldened to a semibold weight, so emphasis changes weight only.
func _semibold(label: RichTextLabel) -> void:
	# The transcript is in the tree even when the label is not yet, so it resolves the theme.
	var body := get_theme_font("normal_font", "RichTextLabel")
	if body == null:
		return
	var semibold := FontVariation.new()
	semibold.base_font = body
	semibold.variation_embolden = 0.35
	label.add_theme_font_override("bold_font", semibold)


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
	for record: Dictionary in _messages:
		if record["dirty"]:
			var label: RichTextLabel = record["label"]
			label.text = _format(str(record["text"]))
			record["dirty"] = false
	if _follow:
		_scroll_end.call_deferred()


func _scroll_end() -> void:
	scroll_vertical = int(get_v_scroll_bar().max_value)
