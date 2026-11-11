@tool
extends VBoxContainer
## The Review panel: answers "is this right?" in one look. Two parts (review_layout.gd):
##
## - The stage, about 70% on the leading side at the full height: the change itself, edge to
##   edge on its own background (review_media.gd: a split picture, the model in live 3D,
##   clips, A/B audio, the changed lines; a filmstrip when there are several items), with the
##   Before | After | Both pill in its corner and an animation's transport floating over its
##   foot. Music layers sit under a track's waveform (review_music.gd).
## - The rail on the trailing side (review_rail.gd): "2 of 5" with previous and next and All
##   approvals; the request as the title; one line of facts and the checks as chips
##   (review_summary.gd); Details; a request's other ways; and, anchored at the foot, Ask for
##   changes, Discard and a large Accept.
##
## Narrow (the sidebar's width), the rail stacks under the stage. Details slide over the stage
## as a drawer (review_drawer.gd) with the files and their diffs, the checks that ran and every
## note; closed unless asked for, or when there is nothing else to show. Discard only throws
## the proposed change away: the user's files were never touched, and the panel says so.
##
## Keyboard, once the review has the focus (a click in it; it never takes the focus itself): A
## accepts, D discards, B flips between before and after, Left and Right go to the previous or
## next change waiting, Escape closes Details. With the media clicked, an animation takes Space
## (play/pause) and Left/Right (a frame) instead.
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
## All approvals: back to the list (the Review tab).
signal list_requested
## Previous (-1) or next (1) change waiting (the Review tab).
signal step_requested(step: int)
## Ask for changes: the chat should take a message about this change; [param path] is the
## changed asset ("" for a scene edit). Shown only when something listens.
signal revise_requested(path: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const MEDIA := preload("review_media.gd")
const MUSIC := preload("review_music.gd")
const DETAILS := preload("review_details.gd")
const SUMMARY := preload("review_summary.gd")
const SNAPSHOT := preload("review_snapshot.gd")
const RAIL := preload("review_rail.gd")
const DRAWER := preload("review_drawer.gd")
const LAYOUT := preload("review_layout.gd")
const ACCEPTED := "Change accepted"
const REJECTED := "Discarded. Your files were not touched."
const SCENE_NOTE := "The change is shown in the viewport."
## Least width of Discard, in em.
const BUTTON_EM := 6.0
## The stage's least height: this share of the panel's width, between STAGE_MIN and STAGE_MAX
## large control heights, so a wide bottom panel opens tall enough to judge the change.
const STAGE_SHARE := 0.5
const STAGE_MIN := 2.0
const STAGE_MAX := 10.0
const STAGE_NARROW_SHARE := 0.6
## Opacity of a disabled Discard: it keeps its fill and shape, only dimmed.
const DISABLED_ALPHA := 0.5

## True while a change waits for a decision (also while the decision is being applied).
var pending := false
var _state := "empty"
var _serial := 0
var _details_pref := false
## The rail's title block (the landing spot of the chat card's entrance).
var _header: VBoxContainer
var _title: Label
var _stage: MarginContainer
var _body: LAYOUT
var _rail: RAIL
var _stage_area: Control
var _media: MEDIA
var _music: MUSIC
var _scene_note: Label
var _summary: SUMMARY
var _details_button: Button
var _drawer: DRAWER
var _details_scroll: ScrollContainer
var _details: DETAILS
var _open_link: Button
var _revise: Button
var _revise_path := ""
var _error_row: HBoxContainer
var _error_icon: TextureRect
var _error: Label
## True after a decision failed: Accept reads "Try again" until the next decision.
var _failed := false
var _footer: VBoxContainer
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
		_size_stage()


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
	_music.clear()
	_details.clear_changes()
	_summary.show_review({"scene_count": count})
	_scene_note.visible = not _media.visible
	_open_link.visible = SNAPSHOT.can_open()
	_revise_path = ""
	_rail.sync_links()
	_sync_details()
	_set_details(false, false)
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
	_open_link.hide()
	var listed := changes if not changes.is_empty() else DETAILS.changes_from(diff, media)
	var items := media.duplicate()
	var text := _text_item(diff, listed)
	if not text.is_empty():
		items.append(text)
	_media.show_media(items)
	_music.show_change(diff, media, listed)
	_details.show_changes(diff, listed)
	_summary.show_review({"changes": listed, "diff": diff})
	_revise_path = _asset_path(media, listed)
	_rail.sync_links()
	_measure()
	_sync_details()
	_set_details(_details_pref or not _media.visible, false)
	_present(arriving)


## The review event's extra fields: the agent's "facts", the "checks" that ran, the reviewer's
## "review_notes" and "reviewer_status". Missing keys keep what is shown.
func show_glance(extra: Dictionary) -> void:
	if _state == "empty":
		return
	_summary.update(extra)
	if extra.get("facts") is Array:
		_music.set_facts(extra["facts"])
	_sync_details()
	glance_changed.emit()


## The before/after renders of the change on screen arrived ({path, before?, after?, error?}).
func update_media(media: Array) -> void:
	_media.update_media(media)
	_measure()
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


## Flip the preview between before and after (B). Returns the side now on show.
func flip() -> String:
	return _media.flip()


## Where this change sits among those waiting: [param index] (0-based) of [param total]. The
## Review tab sets it; a negative total hides the row.
func set_queue(index: int, total: int) -> void:
	_rail.set_queue(index, total)


## Where a request's ways go (variant_strip.gd), in the rail.
func rail_slot() -> Control:
	return _rail.slot


## True when Details are open.
func details_open() -> bool:
	return _drawer.open and _drawer.visible


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
		else ("Try keeping this change again (A)" if _failed else "Keep this change (A)")
	)
	_reject.tooltip_text = (
		"Applying the change…" if on else "Throw this change away. Your files stay as they are. (D)"
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
	_music.clear()
	_details.clear_changes()
	_summary.clear()
	_title.text = ""
	_set_details(false, false)
	_set_error("")


## The Details preference: open by default for every review. Without a preview they are
## always open. Applies to the review on show as well.
func set_details_open(open: bool) -> void:
	if open == _details_pref:
		return
	_details_pref = open
	if _state == "review" and _details_button.visible:
		_set_details(open or not _media.visible, true)


## The title block, as the landing spot of a shared-element transition from the chat card.
func morph_target() -> Control:
	return _header


## Shared-element entrance (motion rules 7, 9, 20): a surface shaped like the chat change
## card at [param card_rect] (global rect, same window) travels on an arc into the title,
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
	# On landing the ghost hands over to the title: overlap, never a blank frame.
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
	if _has_focus_inside() and _on_key(key.keycode):
		get_viewport().set_input_as_handled()


## Act on [param code] with the focus in the review. True when the key was used.
func _on_key(code: Key) -> bool:
	match code:
		KEY_A:
			_decide(true)
		KEY_D:
			_decide(false)
		KEY_B:
			flip()
		KEY_ESCAPE:
			if not details_open() or _drawer.fill:
				return false
			_set_details(false, true)
		KEY_LEFT, KEY_RIGHT:
			# A clicked animation steps a frame instead (review_transport.gd), the filmstrip
			# moves between its tiles.
			var focused := get_viewport().gui_get_focus_owner()
			var strip := _media.filmstrip()
			var stepping := _media.transport().visible and _media.is_ancestor_of(focused)
			stepping = stepping or focused == strip or strip.is_ancestor_of(focused)
			if stepping or not _rail.has_queue():
				return false
			step_requested.emit(-1 if code == KEY_LEFT else 1)
		_:
			return false
	return true


## A click anywhere in the review (not on a control that takes the focus itself) gives it the
## keys. Nothing else ever moves the focus here.
func _gui_input(event: InputEvent) -> void:
	var click := event as InputEventMouseButton
	if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
		if not _has_focus_inside():
			grab_focus()


## True when the focus is on the panel or inside it, and not in a field that takes text.
func _has_focus_inside() -> bool:
	if not is_visible_in_tree():
		return false
	var focused := get_viewport().gui_get_focus_owner()
	if focused == null or not (focused == self or is_ancestor_of(focused)):
		return false
	return not (focused is LineEdit or focused is TextEdit)


# --- Building ----------------------------------------------------------------------------------


func _build_body() -> void:
	_rail = RAIL.new()
	_rail.list_requested.connect(func() -> void: list_requested.emit())
	_rail.step_requested.connect(func(step: int) -> void: step_requested.emit(step))
	_stage_area = Control.new()
	_stage_area.name = "Stage"
	_stage_area.clip_contents = true
	_stage_area.mouse_filter = Control.MOUSE_FILTER_PASS
	_body = LAYOUT.new(_stage_area, _rail)
	_body.narrow_changed.connect(
		func(_narrow: bool) -> void:
			_queue_sizes()
			_size_stage.call_deferred()
	)
	_stage.add_child(_body)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	column.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	column.mouse_filter = Control.MOUSE_FILTER_PASS
	_stage_area.add_child(column)
	# Only the height: the stage's width is the layout's to give.
	column.minimum_size_changed.connect(
		func() -> void: _stage_area.custom_minimum_size.y = column.get_combined_minimum_size().y
	)
	_media = MEDIA.new()
	_media.hide()
	_media.set_room(_stage_area)
	column.add_child(_media)
	_music = MUSIC.new()
	column.add_child(_music)
	_scene_note = UI.label(SCENE_NOTE, "JoineryCaption", true)
	_scene_note.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scene_note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_scene_note.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_scene_note.hide()
	column.add_child(_scene_note)
	_drawer = DRAWER.new()
	_drawer.attach(_stage_area)
	_drawer.close_requested.connect(func() -> void: _set_details(false, true))
	_details_scroll = _drawer.scroll
	_drawer.body.add_child(_rail.summary.info())
	_details = DETAILS.new()
	_drawer.body.add_child(_details)
	_header = _rail.head
	_title = _rail.title
	_summary = _rail.summary
	_details_button = _rail.details_button
	_details_button.toggled.connect(func(open: bool) -> void: _set_details(open, true))
	_error_row = _rail.error_row
	_error_icon = _rail.error_icon
	_error = _rail.error
	_footer = _rail.actions
	_open_link = _rail.open_link
	_open_link.pressed.connect(SNAPSHOT.open_in_editor)
	_revise = _rail.revise
	_revise.pressed.connect(func() -> void: revise_requested.emit(_revise_path))
	_reject = _rail.reject
	_reject.pressed.connect(_decide.bind(false))
	_accept = _rail.accept
	_accept.pressed.connect(_decide.bind(true))
	_applying = _rail.applying
	_applying_dots = _rail.applying_dots
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
	_stage.visible = not idle


func _next_serial() -> int:
	_serial += 1
	return _serial


## Reset to a fresh review. Returns true when the panel was not already showing one, so the
## caller animates the entrance; a review replacing another crossfades over it.
func _begin(summary: String) -> bool:
	var arriving := _state in ["empty", "result"]
	if not arriving and _title.text != summary:
		LAYOUT.ghost(_body, self)
	_next_serial()
	_set_state("review")
	_reset_stage()
	_failed = false
	set_applying(false)
	_set_error("")
	_title.text = summary
	_title.tooltip_text = summary
	_revise.visible = not revise_requested.get_connections().is_empty()
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


## The changed asset Ask for changes attaches: the first previewed item, else the first file.
static func _asset_path(media: Array, changes: Array) -> String:
	for entry: Variant in media + changes:
		if entry is Dictionary and not str((entry as Dictionary).get("path", "")).is_empty():
			return str((entry as Dictionary)["path"])
	return ""


## Take what the media can measure into the facts line.
func _measure() -> void:
	_summary.set_measured(_media.measured_facts())


## Details show when there is anything behind them: files, checks or notes.
func _sync_details() -> void:
	_details_button.visible = _details.has_content() or _summary.has_info()
	_rail.sync_links()
	if not _details_button.visible and _drawer.open:
		_set_details(false, false)


func _set_details(open: bool, animate: bool) -> void:
	var shown := open and _details_button.visible
	_details_button.set_pressed_no_signal(shown)
	_details_button.text = "Hide details" if shown else "Details"
	UI.bind_icon(_details_button, "next" if shown else "previous")
	# With nothing to preview the drawer is the stage.
	_drawer.fill = not _media.visible and not _scene_note.visible
	_drawer.set_open(shown, animate)
	if shown and animate:
		UI_MOTION.stagger(_details.rows())


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


## Accept fills the rail's width beside Discard, large while the rail is beside the stage and
## standard when it is stacked under it; both stay wide enough for their text and "Applying…".
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
	var height := UI_THEME.control_h(self, "md" if _body.narrow else "lg")
	_accept.custom_minimum_size = Vector2(ceilf(busy), height)
	_reject.custom_minimum_size = Vector2(BUTTON_EM * UI_THEME.font_size(self, "body"), height)
	# Discard keeps its normal fill and ink when disabled; set_applying dims it instead.
	_reject.add_theme_stylebox_override("disabled", _reject.get_theme_stylebox("normal"))
	_reject.add_theme_color_override("font_disabled_color", _reject.get_theme_color("font_color"))
	custom_minimum_size.y = UI_THEME.control_h(self, "md") * 6
	_size_stage()


## The stage grows with the panel's width (see STAGE_SHARE). Stacked, the panel asks for the
## stage at STAGE_NARROW_SHARE of the width, the rail's actions and two lines of its title and
## facts; the layout still gives the stage its share first.
func _size_stage() -> void:
	if _stage_area == null or not is_inside_tree():
		return
	var large := float(UI_THEME.control_h(self, "lg"))
	var tall := floorf(clampf(size.x * STAGE_SHARE, large * STAGE_MIN, large * STAGE_MAX))
	if _body.narrow:
		var stage := clampf(size.x * STAGE_NARROW_SHARE, large * STAGE_MIN, large * STAGE_MAX)
		var rail := _rail.get_combined_minimum_size().y + UI_THEME.control_h(self, "md") * 2
		tall = floorf(stage + rail + UI_THEME.space(self, 4))
	if not is_equal_approx(_body.custom_minimum_size.y, tall):
		_body.custom_minimum_size.y = tall
