@tool
extends VBoxContainer
## The Review panel: answers "is this right?" in one look. Top to bottom: the request as the
## title with a Before/After switch, one before/after view that takes the remaining height
## (review_media.gd: a split picture, a model from two angles, A/B audio, the changed lines; a
## filmstrip when there are several items), one line of facts, the checks as one quiet status,
## the reviewer's notes as one line when there are any (review_summary.gd), and the Details
## disclosure beside Discard / Accept on the last row. Details hold the files with their
## diffs, the checks that ran and every note; they stay closed unless asked for. Discard only
## throws the proposed change away: the user's files were never touched, and the panel says so.
##
## Keyboard, with focus anywhere in the review: Enter accepts, Backspace or Delete discards,
## A or B flips between before and after.
##
## Deciding is optimistic: pressing Accept or Discard collapses the review into one line
## ("Change accepted", with dots while the commit runs) at once. [method show_result] confirms
## it with a pulse and clears the panel; [method show_error] rolls the review back with the
## error above the buttons. The dock forwards its calls here and listens to the signals.

## The user chose Accept. The panel already shows the optimistic outcome.
signal accept_pressed
## The user chose Discard. The panel already shows the optimistic outcome.
signal reject_pressed
## A review is ready: bring the panel into view.
signal show_requested
## What the review shows at a glance changed (a render arrived, notes came in).
signal glance_changed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const MEDIA := preload("review_media.gd")
const DETAILS := preload("review_details.gd")
const SUMMARY := preload("review_summary.gd")
const SNAPSHOT := preload("review_snapshot.gd")
const ACCEPTED := "Change accepted"
const REJECTED := "Discarded. Your files were not touched."
const SCENE_NOTE := "The change is shown in the viewport."
## Minimum width of Accept and Discard, in em; narrow panels shrink them to their text.
const BUTTON_EM := 8.0
## Open Details grow with their rows up to this many control heights, then scroll; the
## scroll bar shows only past that.
const DETAILS_ROWS := 6
## Opacity of a disabled Discard: it keeps its fill and shape, only dimmed.
const DISABLED_ALPHA := 0.5

## True while a change waits for a decision (also while the decision is being applied).
var pending := false
var _state := "empty"
var _serial := 0
var _details_pref := false
var _header: HBoxContainer
var _title: Label
var _new_chip: PanelContainer
var _segment: HBoxContainer
var _before: Button
var _after: Button
var _stage: MarginContainer
var _body: VBoxContainer
var _media: MEDIA
var _scene_note: Label
var _summary: SUMMARY
var _details_button: Button
var _details_scroll: ScrollContainer
var _gutter: MarginContainer
var _details: DETAILS
var _spacer: Control
var _error_row: HBoxContainer
var _error_icon: TextureRect
var _error: Label
## True after a decision failed: Accept reads "Try again" until the next decision.
var _failed := false
var _footer: HBoxContainer
var _reject: Button
var _accept: Button
var _applying: HBoxContainer
var _applying_dots: INDICATOR
var _outcome: HBoxContainer
var _outcome_icon: TextureRect
var _outcome_text: Label
var _outcome_dots: INDICATOR
var _outcome_note: Label
var _outcome_role := "success"
var _empty: VBoxContainer
var _sizes_queued := false


func _init() -> void:
	name = "Review"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	# A click anywhere in the review gives it focus, so its keys work.
	focus_mode = Control.FOCUS_CLICK
	accessibility_name = "Review the proposed change"
	_build_header()
	_stage = MarginContainer.new()
	_stage.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_stage)
	_build_body()
	_build_outcome()
	_build_empty()
	_set_state("empty")


func _ready() -> void:
	UI_THEME.watch(self)
	_queue_sizes()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		_queue_sizes()
		_tint_outcome()
	elif what == NOTIFICATION_RESIZED:
		_queue_sizes()


## Called by UI_THEME.watch after a live re-theme.
func _on_theme_rebuilt() -> void:
	_queue_sizes()
	_tint_outcome()


## Size the buttons once the children have seen the new theme too. The panel hears about a
## theme change before its buttons, so reading their styles at once would pin the old theme's
## box as Discard's disabled style and make Discard taller than Accept.
func _queue_sizes() -> void:
	if _sizes_queued:
		return
	_sizes_queued = true
	_apply_sizes.call_deferred()


## Review a live scene edit: the viewport before and after as one split picture when the
## editor could take both frames ([param media], or review_snapshot.gd), else a line pointing
## at the viewport. The facts read "4 scene nodes".
func show_scene_review(summary: String, count: int, media: Array = []) -> void:
	var arriving := _begin(summary)
	var frames := media if not media.is_empty() else SNAPSHOT.media()
	_media.show_media(frames)
	_details.clear_changes()
	_summary.show_review({"scene_count": count})
	_scene_note.visible = not _media.visible
	_sync_details()
	_set_details(false, false)
	_sync_sides()
	_present(arriving)


## Review file changes: [param media] items ({path, media: "image"|"audio", before?, after?})
## and the changed lines of [param diff] fill the preview; [param changes] ({path, change})
## and the diff fill Details. Details start closed unless there is nothing to preview or the
## preference is on. [method show_glance] adds the event's facts, checks and notes.
func show_file_review(
	summary: String, diff: String, media: Array = [], changes: Array = []
) -> void:
	var arriving := _begin(summary)
	_scene_note.hide()
	var listed := changes if not changes.is_empty() else DETAILS.changes_from(diff, media)
	var items := media.duplicate()
	var text := _text_item(diff, listed)
	if not text.is_empty():
		items.append(text)
	_media.show_media(items)
	_details.show_changes(diff, listed)
	_summary.show_review({"changes": listed, "diff": diff})
	_measure()
	_sync_details()
	_sync_sides()
	_set_details(_details_pref or not _media.visible, false)
	_present(arriving)


## The review event's extra fields: the agent's "facts", the "checks" that ran, the reviewer's
## "review_notes" and "reviewer_status". Missing keys keep what is shown.
## Keep the picture shorter while a row of ways (the variant strip) sits above the panel.
func set_under_ways(under: bool) -> void:
	_media.frame_max = MEDIA.FRAME_UNDER_WAYS if under else MEDIA.FRAME_MAX


func show_glance(extra: Dictionary) -> void:
	if _state == "empty":
		return
	_summary.update(extra)
	_sync_details()
	glance_changed.emit()


## The before/after renders of the change on screen arrived ({path, before?, after?, error?}).
func update_media(media: Array) -> void:
	_media.update_media(media)
	_measure()
	_sync_sides()
	glance_changed.emit()


## The review as the chat card shows it: {line, simple, thumbnail}. "simple" changes can be
## decided from the card; others open this panel.
func glance() -> Dictionary:
	if not pending:
		return {}
	return {
		"line": _summary.facts_text(),
		"simple": _summary.simple(_media.asset_count()),
		"thumbnail": _media.thumbnail(),
		"title": _title.text,
	}


## Bring the panel into view (the chat card's Review link).
func request_show() -> void:
	show_requested.emit()


## Flip the preview between before and after (A or B). Returns the side now on show.
func flip() -> String:
	var shown := _media.flip()
	_sync_sides()
	return shown


## Disable both buttons while the decision is applied; Accept shows dots and "Applying…".
## Turning it off while an optimistic outcome is showing, with no result or error, restores
## the review (the decision did not go through).
func set_applying(on: bool) -> void:
	_accept.disabled = on
	_reject.disabled = on
	var idle_text := "Try again" if _failed else "Accept"
	_accept.text = "" if on else idle_text
	_accept.accessibility_name = "Applying the change" if on else idle_text
	_accept.tooltip_text = (
		"Applying the change…"
		if on
		else ("Try keeping this change again (Enter)" if _failed else "Keep this change (Enter)")
	)
	_reject.tooltip_text = (
		"Applying the change…"
		if on
		else "Throw this change away. Your files stay as they are. (Delete)"
	)
	# A disabled Discard keeps its fill and height, only dimmed, so the pair still reads as two
	# buttons (the theme's disabled secondary has no fill).
	_reject.self_modulate.a = DISABLED_ALPHA if on else 1.0
	_applying.visible = on
	_applying_dots.running = on
	if on and _state == "deciding":
		_outcome_dots.running = true
	elif not on and _state == "deciding":
		_restore_if_undecided.call_deferred(_serial)


## Confirm the decision: the outcome line shows [param text] (for example
## "Kept · hammer.glb"), success pulses, the panel flashes for MOTION.confirm, then clears.
## [param ok] false marks a discard or a neutral outcome.
func show_result(ok: bool, text: String) -> void:
	var serial := _next_serial()
	var showing := _outcome.visible and _state == "deciding"
	_set_state("result")
	set_applying(false)
	_outcome_text.text = text if not text.is_empty() else (ACCEPTED if ok else REJECTED)
	_outcome_note.hide()
	_outcome_dots.running = false
	_set_outcome_icon("success" if ok else "close", "success" if ok else "text_muted")
	if not showing:
		UI_MOTION.crossfade(_body, _outcome)
	UI_THEME.flash(self)
	if ok:
		UI_MOTION.pulse(_outcome_icon)
	if is_inside_tree():
		get_tree().create_timer(UI_MOTION.duration("confirm")).timeout.connect(
			_clear_if_current.bind(serial)
		)
	else:
		clear()


## Show [param text] with an error icon directly above the buttons and re-enable them;
## Accept becomes "Try again", since the change was not kept. An optimistic outcome is rolled
## back to the review first.
func show_error(text: String) -> void:
	_next_serial()
	if _state in ["deciding", "result"]:
		_set_state("review")
		_outcome_dots.running = false
		UI_MOTION.crossfade(_outcome, _body)
	_failed = not text.is_empty()
	set_applying(false)
	_set_error(text)
	if _error_row.visible:
		UI_MOTION.fade_in(_error_row)


## Decide from outside the panel (the Review tab's row buttons, the chat card): the same
## optimistic outcome and signal as pressing Accept or Discard here.
func decide(accepted: bool) -> void:
	_decide(accepted)


## Forget the review and show the empty state.
func clear() -> void:
	_next_serial()
	_set_state("empty")
	set_applying(false)
	_reset_stage()
	_media.clear_media()
	_details.clear_changes()
	_summary.clear()
	_title.text = ""
	_set_error("")


## The Details preference: open by default for every review. Without a preview they are
## always open. Applies to the review on show as well.
func set_details_open(open: bool) -> void:
	if open == _details_pref:
		return
	_details_pref = open
	if _state == "review" and _details_button.visible:
		_set_details(open or not _media.visible, true)


## The header, as the landing spot of a shared-element transition from the chat change card.
func morph_target() -> Control:
	return _header


## Shared-element entrance (motion rules 7, 9, 20): a surface shaped like the chat change
## card at [param card_rect] (global rect, same window) travels on an arc into the header,
## which fades in as it lands. Call it right after a show_* method with the card's
## get_global_rect(). Instant when motion is off.
func arrive_from(card_rect: Rect2) -> void:
	if not UI_MOTION.is_enabled() or not is_inside_tree() or not card_rect.has_area():
		return
	var serial := _serial
	_header.modulate.a = 0.0
	await get_tree().process_frame
	if serial != _serial or not _header.is_visible_in_tree():
		_header.modulate.a = 1.0
		return
	var end := _header.get_global_rect()
	var ghost := Panel.new()
	ghost.theme_type_variation = "JoineryChangeCard"
	ghost.top_level = true
	ghost.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(ghost)
	ghost.global_position = card_rect.position
	ghost.size = card_rect.size
	var bend := (end.get_center() - card_rect.get_center()).orthogonal() * 0.2
	var middle := (card_rect.get_center() + end.get_center()) / 2.0 + bend
	var travel := func(t: float) -> void:
		var a := card_rect.get_center().lerp(middle, t)
		var b := middle.lerp(end.get_center(), t)
		var size_now := card_rect.size.lerp(end.size, t)
		ghost.size = size_now
		ghost.global_position = a.lerp(b, t) - size_now / 2.0
	var tween := ghost.create_tween()
	tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
	tween.tween_method(travel, 0.0, 1.0, UI_MOTION.duration("base") * 1.5)
	# On landing the ghost hands over to the header: overlap, never a blank frame.
	tween.tween_property(ghost, "modulate:a", 0.0, UI_MOTION.duration("fast"))
	tween.parallel().tween_property(_header, "modulate:a", 1.0, UI_MOTION.duration("fast"))
	tween.tween_callback(ghost.queue_free)


# --- Keyboard ----------------------------------------------------------------------------------


func _input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or key.echo or _state != "review":
		return
	if key.ctrl_pressed or key.alt_pressed or key.meta_pressed or key.shift_pressed:
		return
	if not _has_focus_inside():
		return
	match key.keycode:
		KEY_ENTER, KEY_KP_ENTER:
			_decide(true)
		KEY_BACKSPACE, KEY_DELETE:
			_decide(false)
		KEY_A, KEY_B:
			flip()
		_:
			return
	get_viewport().set_input_as_handled()


## True when the focus is on the panel or inside it, and not in a field that takes text.
func _has_focus_inside() -> bool:
	if not is_visible_in_tree():
		return false
	var focused := get_viewport().gui_get_focus_owner()
	if focused == null or not (focused == self or is_ancestor_of(focused)):
		return false
	return not (focused is LineEdit or focused is TextEdit)


# --- Building ----------------------------------------------------------------------------------


func _build_header() -> void:
	_header = HBoxContainer.new()
	_header.theme_type_variation = "JoineryRow"
	add_child(_header)
	_title = UI.label("", "JoineryBodyLarge")
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_title.mouse_filter = Control.MOUSE_FILTER_PASS
	_header.add_child(_title)
	var chip := UI.chip("New")
	chip.label.theme_type_variation = "JoineryCaption"
	chip.tooltip_text = "This file is new; there is no earlier version to compare"
	chip.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	chip.hide()
	_new_chip = chip
	_header.add_child(_new_chip)
	_segment = HBoxContainer.new()
	_segment.theme_type_variation = "JoineryRowTight"
	_segment.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_segment.hide()
	_header.add_child(_segment)
	var group := ButtonGroup.new()
	# Neither pressed is the split picture of a texture.
	group.allow_unpress = true
	_before = _side_button("before", "Show the original (A or B flips)", group)
	_after = _side_button("after", "Show the change (A or B flips)", group)
	_after.set_pressed_no_signal(true)


func _side_button(side: String, hint: String, group: ButtonGroup) -> Button:
	var button := Button.new()
	button.text = side.capitalize()
	button.theme_type_variation = "JoinerySegment"
	button.toggle_mode = true
	button.button_group = group
	button.focus_mode = Control.FOCUS_ALL
	button.tooltip_text = hint
	button.accessibility_name = hint
	UI.bind_height(button, "sm")
	UI_MOTION.press_feedback(button)
	button.toggled.connect(
		func(on: bool) -> void:
			if on:
				_media.set_side(side)
			elif not _before.button_pressed and not _after.button_pressed:
				_media.set_side("compare")
			_sync_sides()
	)
	_segment.add_child(button)
	return button


func _build_body() -> void:
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryStack"
	_body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_stage.add_child(_body)
	_media = MEDIA.new()
	_media.item_changed.connect(_sync_sides)
	_media.side_changed.connect(_sync_sides)
	_media.hide()
	_body.add_child(_media)
	_scene_note = UI.label(SCENE_NOTE, "JoineryCaption", true)
	_scene_note.hide()
	_body.add_child(_scene_note)
	_summary = SUMMARY.new()
	_body.add_child(_summary)
	_details_scroll = ScrollContainer.new()
	_details_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_details_scroll.size_flags_vertical = Control.SIZE_FILL
	_details_scroll.follow_focus = true
	_details_scroll.accessibility_name = "Details"
	_details_scroll.hide()
	UI_MOTION.rubber_band(_details_scroll)
	_body.add_child(_details_scroll)
	# The gutter keeps the scroll bar off the line counts; without a scroll bar it is dropped
	# so the counts share the right edge of the buttons (see _fit_details).
	_gutter = MarginContainer.new()
	_gutter.theme_type_variation = "JoineryGutter"
	_gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_gutter.minimum_size_changed.connect(_fit_details)
	_details_scroll.add_child(_gutter)
	var details_box := VBoxContainer.new()
	details_box.theme_type_variation = "JoineryStack"
	_gutter.add_child(details_box)
	details_box.add_child(_summary.info())
	_details = DETAILS.new()
	details_box.add_child(_details)
	# Keeps the buttons at the bottom when there is neither a preview nor open details.
	_spacer = Control.new()
	_spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_spacer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_body.add_child(_spacer)
	_error_row = HBoxContainer.new()
	_error_row.theme_type_variation = "JoineryRowTight"
	_error_row.hide()
	_body.add_child(_error_row)
	_error_icon = TextureRect.new()
	_error_icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	_error_icon.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	_error_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI.bind_icon(_error_icon, "error", "texture")
	_error_row.add_child(_error_icon)
	_error = UI.label("", "JoineryError", true)
	_error.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_error.hide()
	_error_row.add_child(_error)
	_build_footer()


func _build_footer() -> void:
	_footer = HBoxContainer.new()
	_footer.theme_type_variation = "JoineryStack"
	_body.add_child(_footer)
	# A flat disclosure, not a tab-like button: no fill open or closed, muted ink that
	# brightens on hover, and no padding, so its chevron sits on the content edge.
	_details_button = UI.quiet("Details")
	_details_button.theme_type_variation = "JoinerySectionButton"
	_details_button.toggle_mode = true
	_details_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	_details_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_details_button.accessibility_name = "Show or hide the files, checks and notes"
	_details_button.tooltip_text = "Files, checks and notes"
	UI.bind_icon(_details_button, "disclosure_closed")
	UI.bind_height(_details_button)
	_details_button.toggled.connect(func(open: bool) -> void: _set_details(open, true))
	_footer.add_child(_details_button)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_footer.add_child(push)
	_reject = UI.secondary("Discard")
	_reject.accessibility_name = "Discard this change"
	_reject.pressed.connect(_decide.bind(false))
	_accept = UI.primary("Accept")
	_accept.pressed.connect(_decide.bind(true))
	# Follow the editor's OK/Cancel order: the primary action sits on the trailing edge
	# unless the platform puts OK first.
	var ordered := [_accept, _reject] if DisplayServer.get_swap_cancel_ok() else [_reject, _accept]
	for button: Button in ordered:
		_footer.add_child(button)
	_applying = HBoxContainer.new()
	_applying.theme_type_variation = "JoineryRowTight"
	_applying.alignment = BoxContainer.ALIGNMENT_CENTER
	_applying.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_applying.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_applying.hide()
	_accept.add_child(_applying)
	_applying_dots = INDICATOR.new()
	_applying.add_child(_applying_dots)
	var label := _fixed(UI.label("Applying…"))
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_applying.add_child(label)
	set_applying(false)


func _build_outcome() -> void:
	_outcome = HBoxContainer.new()
	_outcome.theme_type_variation = "JoineryRow"
	_outcome.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	_outcome.hide()
	_stage.add_child(_outcome)
	_outcome_icon = TextureRect.new()
	_outcome_icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	_outcome_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_outcome_icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_icon(_outcome_icon, "success", "texture")
	_outcome.add_child(_outcome_icon)
	_outcome_text = _fixed(UI.label("", "JoineryBodyLarge"))
	_outcome.add_child(_outcome_text)
	_outcome_dots = INDICATOR.new()
	_outcome.add_child(_outcome_dots)
	_outcome_note = _fixed(UI.label("", "JoineryCaption"))
	_outcome.add_child(_outcome_note)
	UI.bind_height(_outcome, "md", false)


func _build_empty() -> void:
	_empty = VBoxContainer.new()
	_empty.theme_type_variation = "JoineryTight"
	add_child(_empty)
	_empty.add_child(UI.label("No change to review", "JoineryBodyLarge"))
	_empty.add_child(
		UI.label(
			"Ask Journeyman for a change. It shows here so you can accept or discard it.",
			"JoineryCaption",
			true
		)
	)


# --- State -------------------------------------------------------------------------------------


func _set_state(state: String) -> void:
	_state = state
	pending = state in ["review", "deciding"]
	var idle := state == "empty"
	_empty.visible = idle
	_header.visible = not idle
	_stage.visible = not idle


func _next_serial() -> int:
	_serial += 1
	return _serial


## Reset to a fresh review. Returns true when the panel was not already showing one, so the
## caller animates the entrance.
func _begin(summary: String) -> bool:
	var arriving := _state in ["empty", "result"]
	_next_serial()
	_set_state("review")
	_reset_stage()
	_failed = false
	set_applying(false)
	_set_error("")
	_title.text = summary
	_title.tooltip_text = summary
	return arriving


func _reset_stage() -> void:
	for node: Control in [_outcome, _body, _header]:
		UI_MOTION.stop(node)
	_outcome.hide()
	_outcome_dots.running = false
	_body.show()


func _present(arriving: bool) -> void:
	show()
	if arriving:
		UI_MOTION.fade_in(self)
		UI_MOTION.reveal(self, "down")
	# Keys work at once unless the user is busy elsewhere (typing in the chat).
	if is_visible_in_tree() and get_viewport().gui_get_focus_owner() == null:
		grab_focus()
	show_requested.emit()
	glance_changed.emit()


func _decide(accepted: bool) -> void:
	if _state != "review" or _accept.disabled:
		return
	_next_serial()
	_set_error("")
	_failed = false
	_media.stop_audio()
	_set_state("deciding")
	set_applying(true)
	_outcome_text.text = ACCEPTED if accepted else REJECTED
	_outcome_note.text = "Saving…" if accepted else "Discarding…"
	_outcome_note.show()
	_outcome_dots.running = true
	_set_outcome_icon("success" if accepted else "close", "success" if accepted else "text_muted")
	UI_MOTION.crossfade(_body, _outcome)
	(accept_pressed if accepted else reject_pressed).emit()


func _restore_if_undecided(serial: int) -> void:
	if serial != _serial or _state != "deciding" or _accept.disabled:
		return
	_set_state("review")
	_outcome_dots.running = false
	UI_MOTION.crossfade(_outcome, _body)


func _clear_if_current(serial: int) -> void:
	if serial == _serial and _state == "result":
		clear()


## A text item for the preview: the changed lines of the text files in [param diff], named
## "2 text files" (or the one file), shown with its file type on the filmstrip.
static func _text_item(diff: String, changes: Array) -> Dictionary:
	var files := DETAILS.split_diff(diff).keys().filter(
		func(path: String) -> bool: return path != DETAILS.LOOSE_GROUP
	)
	var lines := 0
	for section: Variant in DETAILS.split_diff(diff).values():
		var counts := DETAILS.line_counts(section as Array)
		lines += counts[0] + counts[1]
	if lines == 0:
		return {}
	var name := (
		str(files[0]).get_file() if files.size() == 1 else DETAILS.plural(files.size(), "text file")
	)
	if files.is_empty():
		name = DETAILS.counts_text(changes) if not changes.is_empty() else "Text changes"
	var extension := str(files[0]).get_extension() if files.size() == 1 else ""
	return {
		"path": name,
		"media": "text",
		"diff": diff,
		"word": "." + extension if not extension.is_empty() else "Aa",
	}


## Take what the media can measure into the facts line.
func _measure() -> void:
	_summary.set_measured(_media.measured_facts())


## Details show when there is anything behind them: files, checks or notes.
func _sync_details() -> void:
	_details_button.visible = _details.has_content() or _summary.has_info()
	if not _details_button.visible:
		_details_scroll.hide()


func _set_details(open: bool, animate: bool) -> void:
	var shown := open and _details_button.visible
	_details_button.set_pressed_no_signal(shown)
	UI.bind_icon(_details_button, "disclosure_open" if shown else "disclosure_closed")
	_details_scroll.visible = shown
	# Open details take the height of their rows (see _fit_details); the preview keeps the
	# rest, so it stays the largest thing in the panel.
	_fit_details()
	_spacer.visible = not _media.visible
	if shown:
		_details_scroll.scroll_vertical = 0
		if animate:
			UI_MOTION.reveal(_gutter)
			UI_MOTION.stagger(_details.rows())


func _sync_sides() -> void:
	var pairs := _media.visible and _media.current_paired() and _media.current_kind() != "text"
	_segment.visible = pairs
	_new_chip.visible = (
		_media.visible and not pairs and _media.current_is_new() and _media.current_kind() != "text"
	)
	_after.set_pressed_no_signal(_media.side == "after")
	_before.set_pressed_no_signal(_media.side == "before")


## A short label that keeps its full width in a row (no ellipsis collapsing it to "…").
static func _fixed(label: Label) -> Label:
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return label


func _set_error(text: String) -> void:
	_error.text = text
	_error.visible = not text.is_empty()
	_error_row.visible = _error.visible


func _set_outcome_icon(icon_name: String, role: String) -> void:
	_outcome_role = role
	UI.bind_icon(_outcome_icon, icon_name, "texture")
	_tint_outcome()


func _tint_outcome() -> void:
	if _outcome_icon != null and is_inside_tree():
		_outcome_icon.self_modulate = get_theme_color(_outcome_role, "Joinery")
		_error_icon.self_modulate = get_theme_color("danger", "Joinery")


## Accept and Discard share one width: 8em, and wide enough for "Applying…"; a narrow panel
## shrinks them towards that text so Details, Discard and Accept stay on one row.
func _apply_sizes() -> void:
	_sizes_queued = false
	if _accept == null or not is_inside_tree():
		return
	var font := _accept.get_theme_font("font")
	var font_size := _accept.get_theme_font_size("font_size")
	var box := _accept.get_theme_stylebox("normal")
	var busy := font.get_string_size("Applying…", HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
	busy += _applying_dots.get_combined_minimum_size().x + UI_THEME.space(self, 1)
	busy += box.get_margin(SIDE_LEFT) + box.get_margin(SIDE_RIGHT)
	var width := maxf(BUTTON_EM * UI_THEME.font_size(self, "body"), busy)
	if size.x > 0.0:
		var gaps := _footer.get_theme_constant("separation") * 3.0
		var room := (size.x - _details_button.get_combined_minimum_size().x - gaps) / 2.0
		width = maxf(busy, minf(width, room))
	width = ceilf(width)
	var height := UI_THEME.control_h(self, "md")
	for button: Button in [_accept, _reject]:
		button.custom_minimum_size = Vector2(width, height)
	# Discard keeps its normal fill and ink when disabled; set_applying dims it instead.
	_reject.add_theme_stylebox_override("disabled", _reject.get_theme_stylebox("normal"))
	_reject.add_theme_color_override("font_disabled_color", _reject.get_theme_color("font_color"))
	_fit_details()
	custom_minimum_size.y = UI_THEME.control_h(self, "md") * 7


## Size the open Details to their content, capped at DETAILS_ROWS control heights; only
## content past the cap scrolls, so a short list never shows a scroll bar.
func _fit_details() -> void:
	if _gutter == null:
		return
	var content := _gutter.get_combined_minimum_size().y
	var cap := UI_THEME.control_h(self, "md") * DETAILS_ROWS if is_inside_tree() else content
	var scrolls: bool = content > cap
	if scrolls and _gutter.has_theme_constant_override("margin_right"):
		_gutter.remove_theme_constant_override("margin_right")
	elif not scrolls and _gutter.get_theme_constant("margin_right") != 0:
		_gutter.add_theme_constant_override("margin_right", 0)
	var height := ceilf(minf(content, cap))
	if not is_equal_approx(_details_scroll.custom_minimum_size.y, height):
		_details_scroll.custom_minimum_size.y = height
