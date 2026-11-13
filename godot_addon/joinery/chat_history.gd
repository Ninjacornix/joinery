# gdlint: disable=max-public-methods
@tool
extends ScrollContainer
## The conversation. Each request is a change card (sidebar_change_card.gd); replies are plain
## text on the dock surface; Joinery's own lines are quiet notices. Turns are SPACE.5 apart;
## inside a turn the request card and the reply are SPACE.3 apart (JoineryStack) in every
## state, and notices sit SPACE.2 below the reply.
## Message rows keep their plain source text; streaming redraws at most once a frame.
##
## The transcript's content spans the sidebar's gutter to gutter (UI_THEME.GUTTER), sharing
## the left and right edges of the header and composer. Its scrollbar is always reserved and
## sits in the dock's right gutter, past that edge, so bubbles keep one right edge with or
## without a bar. The bottom edge fades instead of cutting messages. Bold text is the body
## family at a semibold weight and the same size, never a separate display font.
##
## Short notes ("Focus mode on") are a transient caption over the bottom inset
## ([method show_caption]). They take no layout height: the dock's rows stay put, and while a
## caption shows the conversation gets that much room at its end, so the newest line scrolls
## clear of it instead of hiding under it.
## Offers ([method add_offer]) are one quiet line with their actions; a feature plan
## ([method add_plan]) is a card that changes nothing until the user presses Apply.
## Messages queued while Journeyman works wait in [member queue] (sidebar_queue.gd), after
## the last turn, and stay at the end as the conversation grows.
## A reply's right-click menu has "Remember this" (chat_remember.gd).

signal example_chosen(text: String)
signal retry_requested(card: Control)
signal revert_requested(transaction_id: String)
signal remember_requested(text: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const CARD := preload("sidebar_change_card.gd")
const QUEUE := preload("sidebar_queue.gd")
const INDICATOR := preload("activity_indicator.gd")
const REMEMBER := preload("chat_remember.gd")
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

## Queued messages, after every turn and before the caption's room.
var queue: QUEUE
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
var _caption: PanelContainer
## Room at the end of the conversation while the caption shows.
var _caption_room: Control
var _caption_text: Label
var _caption_action: Button
var _caption_callback: Callable
var _caption_serial := 0


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
	queue = QUEUE.new()
	queue.added.connect(_on_queue_added)
	_body.add_child(queue)
	get_v_scroll_bar().value_changed.connect(_on_scroll)
	UI_MOTION.rubber_band(self)
	STYLE.on_theme_overrides(self, _apply_fade)
	STYLE.scroll_in_gutter(self)
	resized.connect(_on_resized)
	_build_caption()


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
		"Select something in your scene, then describe the change. Type\u00a0/\u00a0for commands.",
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
			"Write this in the message box" if on else "Connect Journeyman to use this"
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
	REMEMBER.offer(label, remember_requested)
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
	_add_turn(_turn)
	var card := CARD.new(text, targets)
	card.set_retry_allowed(_retry_allowed)
	_turn.add_child(card)
	card.retry_requested.connect(func() -> void: retry_requested.emit(card))
	card.outcome_placing.connect(_place_outcome.bind(_turn))
	card.review_placing.connect(_place_outcome.bind(_turn))
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


## Whether unsent cards offer Retry (not while a connection banner owns the fix).
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
	# A plain note has no icon, and no empty slot where one would be.
	if not str(spec[0]).is_empty():
		var picture := STYLE.glyph(spec[1])
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


## A card's ready row and decision line end its turn: after the reply and its timing line.
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


## Empty the conversation for another chat: every turn, reply and notice goes and the empty
## state shows again. Queued messages stay.
func clear() -> void:
	for child: Node in _body.get_children():
		if child != _empty and child != queue and child != _caption_room:
			_body.remove_child(child)
			child.queue_free()
	_messages.clear()
	_cards.clear()
	_log.clear()
	_turn = null
	_last_notice = ""
	_empty.show()
	_follow = true


func _hide_empty() -> void:
	if _empty != null and _empty.visible:
		_empty.hide()


func _current_turn() -> VBoxContainer:
	if _turn == null:
		_turn = VBoxContainer.new()
		_turn.theme_type_variation = "JoineryStack"
		_add_turn(_turn)
	return _turn


## Turns go before the queued messages and the caption's room.
func _add_turn(turn: VBoxContainer) -> void:
	_body.add_child(turn)
	if queue != null:
		_body.move_child(turn, queue.get_index())


## A message was queued: its text follows the text size and the newest line stays in sight.
func _on_queue_added(card: Control) -> void:
	_hide_empty()
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_follow = true
	_dirty = true


## The column replies and notices share in a turn. Like cards and bubbles, it spans the
## sidebar's gutter to gutter (UI_THEME.GUTTER): there is no second, inner text edge.
func _inset(turn: VBoxContainer) -> VBoxContainer:
	if turn.get_child_count() > 0 and turn.get_child(-1).has_meta("joinery_inset"):
		return turn.get_child(-1).get_child(0)
	var inset := MarginContainer.new()
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


## Bold is the body font's semibold weight, so emphasis changes weight only.
func _semibold(label: RichTextLabel) -> void:
	# The transcript is in the tree even when the label is not yet, so it resolves the theme.
	label.add_theme_font_override("bold_font", UI_THEME.medium_font(self))


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
	if _caption != null and _caption.visible:
		_place_caption()
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


# --- Transient caption ---------------------------------------------------------------------------


## A short note over the bottom inset ("Focus mode on · /focus to undo") that clears after
## [param seconds]. [param action] adds a link that runs [param callback]. It takes no layout
## height: it floats over the transcript's faded bottom edge.
func show_caption(text: String, seconds := 3.0, action := "", callback := Callable()) -> void:
	_caption_serial += 1
	var serial := _caption_serial
	_caption_text.text = text
	_caption_text.tooltip_text = text
	_caption_action.text = action
	_caption_action.visible = not action.is_empty() and callback.is_valid()
	_caption_callback = callback
	if text.is_empty():
		_hide_caption()
		return
	_place_caption()
	_body.move_child(_caption_room, -1)
	_caption_room.custom_minimum_size.y = _caption.size.y + UI_THEME.space(self, 2)
	if _follow:
		_scroll_end.call_deferred()
	if not _caption.visible:
		UI_MOTION.fade_in(_caption)
	if is_inside_tree():
		get_tree().create_timer(seconds).timeout.connect(
			func() -> void:
				if serial == _caption_serial:
					_hide_caption()
		)


## The caption's text ("" when hidden).
func caption_text() -> String:
	return _caption_text.text if _caption.visible else ""


func _build_caption() -> void:
	_caption_room = Control.new()
	_caption_room.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_body.add_child(_caption_room)
	_caption = PanelContainer.new()
	_caption.top_level = true
	_caption.z_index = 1
	_caption.mouse_filter = Control.MOUSE_FILTER_PASS
	_caption.accessibility_name = "Note"
	_caption.hide()
	STYLE.on_theme_overrides(
		_caption,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = _caption.get_theme_color("surface_raised", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(_caption, "md"))
			var pad := float(UI_THEME.space(_caption, 2))
			box.content_margin_left = pad
			box.content_margin_right = pad
			box.content_margin_top = float(UI_THEME.space(_caption, 1))
			box.content_margin_bottom = float(UI_THEME.space(_caption, 1))
			_caption.add_theme_stylebox_override("panel", box)
	)
	# Internal, so the scroll container keeps laying out only the conversation.
	add_child(_caption, false, Node.INTERNAL_MODE_BACK)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	_caption.add_child(row)
	_caption_text = UI.label("", "JoineryCaption")
	# The note keeps its full width: the caption grows to fit it, up to the transcript's.
	_caption_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_caption_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_caption_text)
	_caption_action = UI.link("")
	_caption_action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(_caption_action, "xs")
	_caption_action.pressed.connect(
		func() -> void:
			if _caption_callback.is_valid():
				_caption_callback.call()
			_hide_caption()
	)
	row.add_child(_caption_action)


## Bottom of the visible transcript, on the content's left edge, left of the scrollbar.
func _place_caption() -> void:
	var inset := float(UI_THEME.space(self, 2))
	var width := maxf(0.0, size.x - gutter())
	var wanted := _caption.get_combined_minimum_size()
	_caption.size = Vector2(minf(maxf(wanted.x, 0.0), width), wanted.y)
	var origin := get_global_rect().position
	_caption.global_position = origin + Vector2(0.0, size.y - _caption.size.y - inset)


func _hide_caption() -> void:
	_caption_serial += 1
	_caption_room.custom_minimum_size.y = 0.0
	if _caption.visible:
		UI_MOTION.exit(_caption)


# --- Offers and plans ----------------------------------------------------------------------------


## One quiet line from Joinery with its actions, as [[label, callable], …]; the first is the
## accent link. Each action runs its callable; the line stays until [method remove_offer].
func add_offer(text: String, actions: Array = []) -> Control:
	_hide_empty()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.accessibility_name = "Suggestion: " + text
	row.set_meta("joinery_offer", text)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(column)
	var label := UI.label(text, "JoineryCaption", true)
	column.add_child(label)
	_scale(label, "caption")
	if not actions.is_empty():
		# In a narrow sidebar the links flow onto the next line.
		var links := HFlowContainer.new()
		STYLE.on_theme_overrides(
			links,
			func() -> void:
				links.add_theme_constant_override("h_separation", UI_THEME.space(links, 2))
				links.add_theme_constant_override("v_separation", UI_THEME.space(links, 1))
		)
		column.add_child(links)
		for action: Array in actions:
			# The first action is the offer; the rest (Not now, Dismiss) stay quiet.
			var first := links.get_child_count() == 0
			var button := UI.link(str(action[0])) if first else UI.quiet_link(str(action[0]))
			button.accessibility_name = str(action[0])
			UI.bind_height(button, "xs")
			STYLE.hug_left(button)
			var callback: Callable = action[1]
			button.pressed.connect(func() -> void: callback.call())
			links.add_child(button)
	_inset(_current_turn()).add_child(row)
	_log.append("Joinery  " + text)
	UI_MOTION.fade_in(row)
	_follow = true
	_dirty = true
	return row


## Take an offer line away.
func remove_offer(row: Control) -> void:
	if is_instance_valid(row):
		UI_MOTION.exit(row, null, row.queue_free)


## Every offer line still shown, oldest first.
func offer_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for row: Node in _body.find_children("*", "HBoxContainer", true, false):
		if row.has_meta("joinery_offer") and not row.is_queued_for_deletion():
			texts.append(str(row.get_meta("joinery_offer")))
	return texts


## A feature plan from `joinery feature … --plan --json` as a card: what it adds, which
## settings change, and notes. Nothing changes until Apply ([signal FeaturePlan.apply_pressed]).
func add_plan(plan: Dictionary, title: String) -> FeaturePlan:
	_hide_empty()
	_turn = VBoxContainer.new()
	_turn.theme_type_variation = "JoineryStack"
	_add_turn(_turn)
	var card := FeaturePlan.new()
	card.build(plan, title)
	_turn.add_child(card)
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_log.append("Joinery  " + title)
	UI_MOTION.fade_in(card)
	_last_notice = ""
	_follow = true
	_dirty = true
	return card


## The plan for adding or removing a game feature. States: planned, applying, applied,
## cancelled, failed (Apply again).
class FeaturePlan:
	extends PanelContainer

	signal apply_pressed
	signal cancel_pressed
	signal revert_pressed(transaction_id: String)

	## Files listed before "and N more".
	const MAX_FILES := 6

	var state := "planned"
	var plan: Dictionary = {}
	var transaction_id := ""
	var title_label: Label
	var _column: VBoxContainer
	var _lines: Array[Label] = []
	var _headings: Array[Label] = []
	var _error: Label
	var _actions: HBoxContainer
	var _apply: Button
	var _cancel: Button
	var _outcome: HBoxContainer
	var _outcome_icon: TextureRect
	var _outcome_text: Label
	var _revert: Button
	var _working: Control

	func build(data: Dictionary, title: String) -> void:
		plan = data.duplicate(true)
		STYLE.boxed(self, "JoineryChangeCard")
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		accessibility_name = title
		_column = VBoxContainer.new()
		_column.theme_type_variation = "JoineryStack"
		add_child(_column)
		var head := VBoxContainer.new()
		head.theme_type_variation = "JoineryTight"
		_column.add_child(head)
		title_label = STYLE.card_title(title, true)
		head.add_child(title_label)
		_lines.append(_caption(head, "Nothing changes until you apply it."))
		var files := _files(plan.get("changes", []))
		if not files.is_empty():
			_group("Files", files)
		var settings := _settings(plan.get("config_edits", []))
		if not settings.is_empty():
			_group("Settings", settings)
		var notes: Array[String] = []
		for note: Variant in plan.get("notes", []):
			notes.append(str(note))
		if not notes.is_empty():
			_group("Notes", notes)
		_error = UI.label("", "JoineryError", true)
		_error.hide()
		_column.add_child(_error)
		var conflicts: Array = plan.get("conflicts", [])
		if not conflicts.is_empty():
			_error.text = " ".join(conflicts.map(func(c: Variant) -> String: return str(c)))
			_error.show()
		_actions = HBoxContainer.new()
		_actions.theme_type_variation = "JoineryRow"
		_actions.alignment = BoxContainer.ALIGNMENT_END
		_column.add_child(_actions)
		_working = INDICATOR.new()
		_working.hide()
		_actions.add_child(_working)
		_cancel = UI.secondary("Cancel")
		_cancel.accessibility_name = "Cancel this change"
		_cancel.pressed.connect(_on_cancel)
		_apply = UI.primary("Apply")
		_apply.accessibility_name = "Apply: " + title
		_apply.disabled = not conflicts.is_empty()
		_apply.pressed.connect(_on_apply)
		var ordered := (
			[_apply, _cancel] if DisplayServer.get_swap_cancel_ok() else [_cancel, _apply]
		)
		for button: Button in ordered:
			_actions.add_child(button)
		_outcome = HBoxContainer.new()
		_outcome.theme_type_variation = "JoineryRowTight"
		_outcome.hide()
		_column.add_child(_outcome)
		_outcome_icon = STYLE.glyph()
		_outcome.add_child(_outcome_icon)
		_outcome_text = UI.label("", "JoineryCaption", true)
		_outcome_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_outcome.add_child(_outcome_text)
		_revert = UI.quiet("Revert")
		_revert.tooltip_text = "Put back the files from before this change"
		_revert.hide()
		UI.bind_height(_revert, "xs")
		_revert.pressed.connect(func() -> void: revert_pressed.emit(transaction_id))
		_outcome.add_child(_revert)

	## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
	func scalables() -> Array:
		var pairs: Array = [[title_label, "body"], [_outcome_text, "caption"]]
		for line: Label in _lines:
			pairs.append([line, "caption"])
		for heading: Label in _headings:
			pairs.append([heading, "body"])
		return pairs

	## The visible texts of the card, top to bottom (for tests and captures).
	func texts() -> PackedStringArray:
		var found := PackedStringArray()
		for label: Node in find_children("*", "Label", true, false):
			if (label as Label).is_visible_in_tree():
				found.append((label as Label).text)
		return found

	## Apply is running: both buttons wait, with dots, until [method set_applied].
	func set_applying() -> void:
		state = "applying"
		_apply.disabled = true
		_cancel.disabled = true
		_apply.text = "Applying…"
		_working.show()
		_working.set("running", true)
		_error.hide()

	## Applied: the buttons give way to the outcome, with Revert when there is a transaction.
	func set_applied(text: String, transaction := "") -> void:
		state = "applied"
		transaction_id = transaction
		_settle("success", "success", text)
		_revert.visible = not transaction.is_empty()
		UI_MOTION.pulse(_outcome)

	## The apply failed: say why in place and offer Apply again.
	func set_failed(text: String) -> void:
		state = "failed"
		_working.set("running", false)
		_working.hide()
		_apply.text = "Apply"
		_apply.disabled = false
		_cancel.disabled = false
		_error.text = text
		UI_MOTION.fade_in(_error)

	## A revert of the applied change finished.
	func set_reverted(ok: bool, message := "") -> void:
		if ok:
			_revert.hide()
			_settle("reload", "text_muted", "Reverted")
			return
		_error.text = (
			"Couldn't revert. " + message if not message.is_empty() else "Couldn't revert."
		)
		UI_MOTION.fade_in(_error)

	func _on_apply() -> void:
		if state in ["planned", "failed"] and not _apply.disabled:
			set_applying()
			apply_pressed.emit()

	func _on_cancel() -> void:
		if state in ["planned", "failed"]:
			state = "cancelled"
			_settle("close", "text_muted", "Cancelled. Nothing changed.")
			cancel_pressed.emit()

	func _settle(icon_name: String, role: String, text: String) -> void:
		_working.set("running", false)
		_actions.hide()
		_error.hide()
		STYLE.set_glyph(_outcome_icon, icon_name, role)
		_outcome_text.text = text
		_outcome.show()

	func _group(heading: String, lines: Array[String]) -> void:
		var group := VBoxContainer.new()
		group.theme_type_variation = "JoineryTight"
		_column.add_child(group)
		var title := UI.label(heading)
		group.add_child(title)
		_headings.append(title)
		for line: String in lines:
			_lines.append(_caption(group, line))

	func _caption(parent: Control, text: String) -> Label:
		var label := UI.label(text, "JoineryCaption", true)
		parent.add_child(label)
		return label

	## Changed paths without the .uid sidecars, at most MAX_FILES then "and N more".
	static func _files(changes: Array) -> Array[String]:
		var paths: Array[String] = []
		for change: Variant in changes:
			var path := str(change.get("path", "")) if change is Dictionary else str(change)
			if not path.is_empty() and not path.ends_with(".uid"):
				paths.append(path)
		if paths.size() > MAX_FILES:
			var more := paths.size() - (MAX_FILES - 1)
			paths = paths.slice(0, MAX_FILES - 1)
			paths.append("and %d more" % more)
		return paths

	## project.godot edits as plain lines: "Adds autoload Controller", "Changes input: pause,
	## move left". Values stay out: they are engine text, not something to read here.
	static func _settings(edits: Array) -> Array[String]:
		var groups := {}
		for edit: Variant in edits:
			if not edit is Dictionary:
				continue
			var verb := (
				"Adds"
				if edit.get("before") == null
				else "Removes" if edit.get("after") == null else "Changes"
			)
			var key := "%s %s" % [verb, str(edit.get("section", "")).replace("_", " ")]
			if not groups.has(key):
				groups[key] = []
			groups[key].append(str(edit.get("key", "")).replace("_", " "))
		var lines: Array[String] = []
		for key: String in groups:
			lines.append("%s: %s" % [key, ", ".join(groups[key])])
		return lines
