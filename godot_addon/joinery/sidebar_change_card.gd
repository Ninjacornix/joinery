# gdlint: disable=max-public-methods
@tool
extends VBoxContainer
## One request in the transcript, kept as a change card so the session reads as a history you
## can trace and revert. The user's bubble holds only the request text. Under it, on the
## same edges, Journeyman's side: the apps at work and a quiet timer, the live
## steps, the change when it is ready, and inline errors. A ready change shows a tiny picture,
## one line of what changed and Discard / Accept (sidebar_review_row.gd), so a small change is
## decided right here; a bigger one offers Review, which opens the Review panel. Either way the
## decision runs through the panel. The decision line ("Change accepted", with "Revert" on
## accepted file changes) is placed by the transcript at the end of the turn, after the reply
## ([signal outcome_placing]).
##
## Revert is not optimistic: the line says "Reverting…" until Joinery confirms, then
## "Reverted", or shows the error in place with Revert offered again. A change whose restore
## data is gone says "Too old to revert" instead of offering the button
## ([method set_revertible]).
##
## When asset work needs the art direction approved first, the card holds the request (the
## art gate): "Before Blender makes anything, agree how your game should look." with
## "Review the look" and "Describe my own". After approval the dock sends the same request
## again, so nothing is retyped.
##
## The status line is the turn's one header in every state: the app glyph and name, then
## "· 0:14" while it runs and "· Completed in 1:12" with its disclosure chevron when done. Steps
## always sit below it, so only its content changes as the turn moves on. The status line is
## CONTROL_H.xs tall in every state, so the gap under the bubble never changes.
##
## A turn keeps one rhythm: the bubble, the progress (status line and steps), the thumbnail and
## the error are SPACE.3 apart (JoineryStack); rows inside the progress stay SPACE.1 apart.
##
## When the turn ends the steps fold away behind the reply's timing line ("Completed in 1:12"),
## which the transcript moves into the card's status line ([method adopt_timing]); opening it
## shows the steps again with the timing details.
##
## States: sending, failed (not sent), working, gated, ready, applying, accepted, rejected,
## reverted, stopped, done.

signal retry_requested
## The request could not be sent; [param reason] says why ("" when unknown).
signal failed(reason: String)
signal revert_requested(transaction_id: String)
## The decision line is about to show; the transcript moves [param row] to the end of the turn.
## Without a listener the row is shown under the card's own steps.
signal outcome_placing(row: Control)
## "Review the look" on the art gate.
signal gate_review_requested
## "Describe my own" on the art gate.
signal gate_describe_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const ATTACHMENT_ROW := preload("sidebar_attachments.gd")
const REVIEW_ROW := preload("sidebar_review_row.gd")
## Apps recognised in step text; the card shows each one it sees once.
const APPS := ["Blender", "Krita", "GIMP", "Material Maker", "Surge XT", "SuperCollider", "Godot"]
## Shown instead of Revert once the change's restore data is gone.
const TOO_OLD := "Too old to revert"
## Step lines that only say the assistant is busy; the footer shows these, not the card.
const GENERIC_STEPS := ["Thinking…", "Preparing your request…", "Sending…"]
## Step verbs whose past tense is not "-ed".
const IRREGULAR := {
	"beginning": "began",
	"bringing": "brought",
	"building": "built",
	"choosing": "chose",
	"cutting": "cut",
	"drawing": "drew",
	"finding": "found",
	"getting": "got",
	"giving": "gave",
	"holding": "held",
	"keeping": "kept",
	"laying": "laid",
	"leaving": "left",
	"making": "made",
	"putting": "put",
	"reading": "read",
	"running": "ran",
	"seeing": "saw",
	"sending": "sent",
	"setting": "set",
	"splitting": "split",
	"taking": "took",
	"thinking": "thought",
	"writing": "wrote",
}
## Outcome kind -> [icon, tint role].
const OUTCOMES := {
	"success": ["success", "success"],
	"rejected": ["close", "text_muted"],
	"stopped": ["stop", "text_muted"],
	"reverted": ["reload", "text_muted"],
	"error": ["error", "danger"],
	"info": ["", "text_muted"],
}

var request := ""
var targets: Array = []
var state := "sending"
var transaction_id := ""
## The RichTextLabel holding the request text (selectable).
var text_label: RichTextLabel
var _bubble: PanelContainer
var _activity: MarginContainer
var _column: VBoxContainer
## The status line, the failure line, the steps and the timing details: one tight group.
var _progress: VBoxContainer
var _status: HBoxContainer
var _apps: HBoxContainer
## " · " between the apps and the timer or timing line ("Blender · 0:14").
var _joiner: Label
var _timer: Label
var _sending: Control
var _failure: HBoxContainer
var _failure_text: Label
var _retry: Button
var _reason := ""
var _retry_allowed := true
var _steps: VBoxContainer
## The ready change: picture, one line, Discard / Accept or Review.
var _ready_row: REVIEW_ROW
var _outcome: HBoxContainer
var _outcome_icon: TextureRect
var _outcome_text: Label
var _revert: Button
var _reverting: HBoxContainer
var _too_old: Label
## False once `joinery transactions list` says the restore data is gone.
var _revertible := true
var _gate: VBoxContainer
var _gate_text: Label
var _error: Label
var _summary: Button
var _details: Label
var _seen_apps: Array[String] = []
var _last_step := ""
var _previous_outcome: Array = []


func _init(text := "", context: Array = []) -> void:
	request = text
	targets = context.duplicate(true)
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_bubble = PanelContainer.new()
	_bubble.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.boxed(_bubble, "JoineryChangeCard")
	add_child(_bubble)
	text_label = RichTextLabel.new()
	text_label.fit_content = true
	text_label.scroll_active = false
	text_label.selection_enabled = true
	text_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text_label.accessibility_name = "Your request"
	text_label.text = request
	# Attached images sit under the text, as small thumbnails.
	var images := ATTACHMENT_ROW.strip(targets)
	if images == null:
		_bubble.add_child(text_label)
	else:
		var stack := VBoxContainer.new()
		stack.theme_type_variation = "JoineryTight"
		stack.add_child(text_label)
		stack.add_child(images)
		_bubble.add_child(stack)
	# The theme's paragraph and line spacing both land under the last line; drop the first and
	# match the second above the text, so the request sits in the middle of its bubble.
	STYLE.on_theme_overrides(
		text_label,
		func() -> void:
			text_label.add_theme_constant_override("paragraph_separation", 0)
			var lead := text_label.get_theme_constant("line_separation")
			var box := StyleBoxEmpty.new()
			box.content_margin_top = lead
			text_label.add_theme_stylebox_override("normal", box)
			text_label.add_theme_stylebox_override("focus", box)
	)
	# The assistant's side shares the bubble's edges, SPACE.3 under it.
	_activity = MarginContainer.new()
	add_child(_activity)
	_column = VBoxContainer.new()
	_column.theme_type_variation = "JoineryStack"
	_activity.add_child(_column)
	_progress = VBoxContainer.new()
	_progress.theme_type_variation = "JoineryTight"
	_column.add_child(_progress)
	_status = _row(_progress)
	_status.theme_type_variation = "JoineryRow"
	UI.bind_height(_status, "xs", false)
	_sending = INDICATOR.new()
	_sending.set("running", true)
	_sending.tooltip_text = "Sending"
	_status.add_child(_sending)
	_apps = HBoxContainer.new()
	_apps.theme_type_variation = "JoineryRowTight"
	_apps.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_apps.hide()
	_status.add_child(_apps)
	_joiner = UI.label("·", "JoineryCaption")
	_joiner.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_joiner.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_joiner.hide()
	_status.add_child(_joiner)
	_timer = UI.label("", "JoineryNumeric")
	_timer.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_timer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_timer.accessibility_name = "Time spent on this request"
	_timer.hide()
	_status.add_child(_timer)
	_failure = _row(_progress)
	_failure.add_child(_icon("error", "danger"))
	_failure_text = UI.label("Not sent", "JoineryCaption", true)
	_failure.add_child(_failure_text)
	_retry = UI.link("Retry")
	_retry.accessibility_name = "Send this request again"
	_retry.pressed.connect(func() -> void: retry_requested.emit())
	UI.bind_height(_retry, "xs")
	_failure.add_child(_retry)
	_failure.hide()
	_steps = VBoxContainer.new()
	_steps.theme_type_variation = "JoineryTight"
	_steps.hide()
	_progress.add_child(_steps)
	_ready_row = REVIEW_ROW.new()
	_ready_row.other_ways_pressed.connect(_try_other_ways)
	_column.add_child(_ready_row)
	_error = UI.label("", "JoineryError", true)
	_error.hide()
	_column.add_child(_error)
	# The decision line lives outside the card until the transcript places it.
	_outcome = HBoxContainer.new()
	_outcome.theme_type_variation = "JoineryRowTight"
	_outcome.accessibility_name = "Decision"
	_outcome_icon = STYLE.glyph()
	_outcome.add_child(_outcome_icon)
	_outcome_text = UI.label("", "JoineryCaption")
	_outcome_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_outcome.add_child(_outcome_text)
	_revert = UI.quiet("Revert")
	_revert.accessibility_name = "Revert this accepted change"
	_revert.tooltip_text = "Put back the files from before this change"
	_revert.pressed.connect(_on_revert)
	UI.bind_height(_revert, "xs")
	_outcome.add_child(_revert)
	_reverting = HBoxContainer.new()
	_reverting.theme_type_variation = "JoineryRowTight"
	_reverting.accessibility_name = "Reverting this change"
	var dots := INDICATOR.new()
	dots.set("running", true)
	_reverting.add_child(dots)
	var reverting_text := UI.label("Reverting…", "JoineryCaption")
	reverting_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_reverting.add_child(reverting_text)
	_reverting.hide()
	_outcome.add_child(_reverting)
	_too_old = UI.label(TOO_OLD, "JoineryCaption")
	_too_old.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_too_old.tooltip_text = "Its restore data was cleared. Ask Journeyman to change it back."
	_too_old.mouse_filter = Control.MOUSE_FILTER_PASS
	_too_old.hide()
	_outcome.add_child(_too_old)
	_outcome.hide()
	_set_sending_look(true)


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE and is_instance_valid(_outcome):
		if _outcome.get_parent() == null:
			_outcome.free()


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[text_label, "body"], [_outcome_text, "caption"], [_failure_text, "caption"]]


## The assistant acknowledged the request.
func set_sent() -> void:
	if state != "sending" and state != "failed":
		return
	state = "working"
	_set_sending_look(false)
	_failure.hide()


## The request never reached the assistant: show "Not sent" with Retry.
func set_failed(reason := "") -> void:
	state = "failed"
	_set_sending_look(false)
	text_label.theme_type_variation = "JoineryFaintRichText"
	_reason = reason
	_sync_failure()
	UI_MOTION.fade_in(_failure)
	_sync_activity()
	failed.emit(reason)


## Whether Retry is offered. While the connection banner shows its own fix, the card says
## only "Not sent" and Retry waits until the assistant is back.
func set_retry_allowed(on: bool) -> void:
	_retry_allowed = on
	_sync_failure()


func _sync_failure() -> void:
	var why := _reason if _retry_allowed else ""
	_failure_text.text = "Not sent" if why.is_empty() else "Not sent. " + why
	_failure_text.tooltip_text = why
	_retry.visible = _retry_allowed


## Back to sending after Retry, or when the art gate lets a held request go.
func set_sending() -> void:
	state = "sending"
	_failure.hide()
	if _gate != null:
		_gate.hide()
	_set_sending_look(true)


## A progress line from the assistant: the previous step gets its tick, this one is current.
## Apps named in the text join the status line.
func add_step(text: String) -> void:
	set_sent()
	var line := text.strip_edges()
	for app: String in APPS:
		if line.contains(app) and not _seen_apps.has(app):
			_seen_apps.append(app)
			_apps.add_child(_app_badge(app))
			_apps.show()
	_sync_activity()
	if line.is_empty() or line == _last_step or line in GENERIC_STEPS:
		return
	_last_step = line
	_tick_current("success")
	var row := _row(_steps)
	var cell := CenterContainer.new()
	cell.set_meta("step_cell", true)
	STYLE.on_theme(
		cell,
		func() -> void:
			var side := float(UI_THEME.space(cell, 4))
			cell.custom_minimum_size = Vector2(side, side)
	)
	cell.add_child(UI.status_dot("working"))
	row.add_child(cell)
	var label := UI.label(line, "JoineryCaption")
	label.tooltip_text = line
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)
	_steps.show()
	_sync_activity()
	UI_MOTION.stagger([row])


## True while [param text] is the live step on this card, so nothing else needs to repeat it.
func shows_step(text: String) -> bool:
	return state == "working" and _steps.visible and text.strip_edges() == _last_step


## Take the reply's timing line ([param summary], a disclosure button) and its [param details]:
## the summary joins the status line after the apps, and the finished steps fold behind it.
func adopt_timing(summary: Button, details: Label) -> void:
	if _summary == summary:
		return
	_summary = summary
	_details = details
	summary.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	summary.reparent(_status, false)
	_status.move_child(summary, _joiner.get_index() + 1)
	details.reparent(_progress, false)
	_progress.move_child(details, _steps.get_index() + 1)
	set_details_open(false)


## Show or fold the finished steps and timing details under the summary; [param animate]
## fades the steps in.
func set_details_open(open: bool, animate := false) -> void:
	_steps.visible = open and _steps.get_child_count() > 0
	if _steps.visible and animate:
		UI_MOTION.fade_in(_steps)
	if _details != null:
		_details.visible = open and not _details.text.is_empty()
	_sync_activity()


## Re-check which parts show, after the transcript hid or showed the summary.
func refresh_layout() -> void:
	_sync_activity()


## Seconds spent so far; the timer appears after three seconds. Each second only its text
## changes: a ticking clock that moves every second pulls the eye from the work.
func set_elapsed(seconds: int) -> void:
	if seconds < 3 or state != "working":
		return
	_timer.text = UI_MOTION.format_clock(seconds)
	if not _timer.visible:
		UI_MOTION.fade_in(_timer)
		_sync_activity()


## The turn ended. [param outcome] is "done", "cancelled" or "failed". A request held by the
## art gate stays held.
func finish(outcome: String) -> void:
	if state == "sending":
		set_failed()
		return
	if state == "gated":
		_timer.hide()
		_tick_current("success")
		_sync_activity()
		return
	_timer.hide()
	_tick_current(
		"success" if outcome == "done" else "stopped" if outcome == "cancelled" else "error"
	)
	if state == "working":
		state = "done"
	_sync_activity()


## A change is waiting for a decision. [param media] is the review's media list (its first
## "after" image is the picture until the panel has one); [param review] is the Review panel
## (review_panel.gd) that decides it, kept from an earlier call when null.
func set_ready(media: Array = [], review: Object = null) -> void:
	set_sent()
	_timer.hide()
	_tick_current("success")
	state = "ready"
	_error.hide()
	_outcome.hide()
	_ready_row.show_for(media, review)
	_sync_activity()


## "Try other ways": a few more versions of this request beside this change (sidebar_variants.gd).
func _try_other_ways() -> void:
	if is_inside_tree() and not transaction_id.is_empty():
		get_tree().call_group(
			&"joinery_variants", &"try_other_ways", request, transaction_id, _ready_row.line_text()
		)


## The ready change's row in this card (picture, line, Discard / Accept).
func review_row() -> REVIEW_ROW:
	return _ready_row


## Show the decision. [param kind]: "success", "rejected", "stopped", "error" or "info".
## With [param optimistic], the previous look is kept so [method roll_back] can restore it.
func set_outcome(kind: String, text: String, optimistic := false) -> void:
	set_sent()
	_previous_outcome = [state, _ready_row.visible] if optimistic else []
	_timer.hide()
	_ready_row.hide()
	_error.hide()
	_tick_current("success" if kind == "success" else "stopped")
	state = {"success": "accepted", "rejected": "rejected", "stopped": "stopped"}.get(kind, "done")
	var spec: Array = OUTCOMES.get(kind, OUTCOMES["info"])
	_outcome_icon.visible = not str(spec[0]).is_empty()
	if _outcome_icon.visible:
		STYLE.set_glyph(_outcome_icon, spec[0], spec[1])
	_outcome_text.text = text
	# Revert only once the accept is confirmed: an accept still on its way may yet roll back.
	_sync_revert(kind == "success" and not optimistic)
	_place_outcome()
	_outcome.show()
	_sync_activity()
	if kind == "success":
		UI_MOTION.pulse(_outcome)
	else:
		UI_MOTION.fade_in(_outcome)


## Undo an optimistic outcome after the action failed, and say why under the card.
func roll_back(message := "") -> void:
	if _previous_outcome.is_empty():
		return
	state = str(_previous_outcome[0])
	_ready_row.visible = bool(_previous_outcome[1])
	_previous_outcome = []
	_outcome.hide()
	show_error(message)


## An inline error under the card ("" hides it).
func show_error(message: String) -> void:
	_error.text = message
	if message.is_empty():
		_error.hide()
	else:
		UI_MOTION.fade_in(_error)
	_sync_activity()


## True while the card is waiting for a decision in the Review panel.
func awaiting_decision() -> bool:
	return state in ["ready", "applying"]


## Settle a decision reported by the editor. An optimistic outcome of the same kind is kept
## (no second animation); otherwise the outcome is shown now. Returns false when the card is
## not waiting for a decision, or a stop arrives after the work finished.
func decide(kind: String, text: String) -> bool:
	var target: String = {"success": "accepted", "rejected": "rejected", "stopped": "stopped"}.get(
		kind, "done"
	)
	if not _previous_outcome.is_empty() and state == target:
		_previous_outcome = []
		_outcome_text.text = text
		_sync_revert(kind == "success")
		return true
	var open := awaiting_decision() or not _previous_outcome.is_empty()
	if kind == "stopped":
		open = state in ["sending", "working"]
	if not open:
		return false
	set_outcome(kind, text)
	return true


## The revert finished. On success the line reads "Reverted"; on failure it stays accepted,
## offers Revert again and shows why in place.
func revert_finished(ok: bool, message := "") -> void:
	_reverting.hide()
	if ok:
		state = "reverted"
		_outcome_text.text = "Reverted"
		STYLE.set_glyph(_outcome_icon, "reload", "text_muted")
		_outcome_icon.show()
		_revert.hide()
		_too_old.hide()
		UI_MOTION.pulse(_outcome)
		return
	_sync_revert(true)
	show_error("Couldn't revert. " + message if not message.is_empty() else "Couldn't revert.")


## Whether this change can still be reverted (`revertible` from `joinery transactions
## list`). A change that can't shows "Too old to revert" instead of the button.
func set_revertible(on: bool) -> void:
	_revertible = on
	if state == "accepted" and not _reverting.visible:
		_sync_revert(true)


## True while a revert of this change is running.
func reverting() -> bool:
	return _reverting.visible


## Hold the request until the art direction is approved. [param app] names the app that was
## about to make something ("Blender"), or "" for the one the card saw at work.
func show_art_gate(app := "") -> void:
	set_sent()
	state = "gated"
	_timer.hide()
	_tick_current("success")
	if _gate == null:
		_build_gate()
	var who := app if not app.is_empty() else _gate_app()
	_gate_text.text = "Before %s makes anything, agree how your game should look." % who
	_column.move_child(_gate, _column.get_child_count() - 1)
	UI_MOTION.fade_in(_gate)
	_sync_activity()


## True while the art gate holds this request.
func gated() -> bool:
	return state == "gated"


## The art gate's sentence ("" when there is no gate).
func gate_text() -> String:
	return _gate_text.text if _gate != null and _gate.visible else ""


func _build_gate() -> void:
	_gate = VBoxContainer.new()
	_gate.theme_type_variation = "JoineryRow"
	_gate.accessibility_name = "Art direction needed"
	_gate.hide()
	_column.add_child(_gate)
	_gate_text = UI.label("", "", true)
	_gate.add_child(_gate_text)
	var actions := HBoxContainer.new()
	actions.theme_type_variation = "JoineryRow"
	_gate.add_child(actions)
	var review := UI.primary("Review the look")
	review.accessibility_name = "Review the art direction"
	review.pressed.connect(func() -> void: gate_review_requested.emit())
	actions.add_child(review)
	var own := UI.link("Describe my own")
	own.accessibility_name = "Describe how your game should look"
	own.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	own.pressed.connect(func() -> void: gate_describe_requested.emit())
	actions.add_child(own)
	var note := UI.label("Your request will continue after you approve.", "JoineryCaption", true)
	_gate.add_child(note)


## The app this request was using, else Journeyman itself.
func _gate_app() -> String:
	for app: String in _seen_apps:
		if app != "Godot":
			return app
	return "Journeyman"


## Revert, "Reverting…" or "Too old to revert" on the accepted line.
func _sync_revert(accepted: bool) -> void:
	var file_change := accepted and not transaction_id.is_empty()
	_revert.visible = file_change and _revertible
	_revert.disabled = false
	_too_old.visible = file_change and not _revertible
	_reverting.hide()


func _on_revert() -> void:
	# Not optimistic: the line says "Reverting…" until Joinery confirms (revert_finished).
	if state != "accepted" or _reverting.visible:
		return
	_revert.hide()
	_error.hide()
	_reverting.show()
	UI_MOTION.fade_in(_reverting)
	_sync_activity()
	revert_requested.emit(transaction_id)


func _set_sending_look(on: bool) -> void:
	_sending.set("running", on)
	# Faint through the theme, not alpha, so the pending text keeps its contrast.
	text_label.theme_type_variation = "JoineryFaintRichText" if on else ""
	_sync_activity()


## The decision line goes where the transcript puts it (after the reply), or under the steps.
func _place_outcome() -> void:
	if _outcome.get_parent() != null:
		return
	outcome_placing.emit(_outcome)
	if _outcome.get_parent() == null:
		_column.add_child(_outcome)


## The assistant's side shows only when something in it does; the status line likewise.
func _sync_activity() -> void:
	var timed := _summary != null and _summary.visible
	_joiner.visible = _apps.visible and (timed or _timer.visible)
	_status.visible = _sending.visible or _apps.visible or _timer.visible or timed
	_progress.visible = _any_visible(_progress)
	_activity.visible = _any_visible(_column)


static func _any_visible(parent: Node) -> bool:
	for child: Node in parent.get_children():
		if child is CanvasItem and (child as CanvasItem).visible:
			return true
	return false


func _tick_current(kind: String) -> void:
	if _steps.get_child_count() == 0:
		return
	var row := _steps.get_child(-1)
	var cell := row.get_child(0) as CenterContainer
	if cell == null or cell.get_child_count() == 0 or not cell.get_child(0) is UI.StatusDot:
		return
	var dot := cell.get_child(0)
	cell.remove_child(dot)
	dot.queue_free()
	var spec: Array = OUTCOMES.get(kind, OUTCOMES["success"])
	cell.add_child(_icon(spec[0], spec[1]))
	# A finished step reads as done: past tense on success, never a trailing ellipsis.
	var label := row.get_child(1) as Label
	if label != null:
		label.text = past_tense(label.text) if kind == "success" else _settled(label.text)
		label.tooltip_text = label.text


## "Exporting the model…" -> "Exported the model". Lines that don't start with a verb in
## "-ing" only lose their trailing ellipsis.
static func past_tense(text: String) -> String:
	var line := _settled(text)
	var space := line.find(" ")
	var word := line if space < 0 else line.substr(0, space)
	var lower := word.to_lower()
	var stem := lower.trim_suffix("ing")
	if stem == lower or stem.length() < 2 or not RegEx.create_from_string("[aeiouy]").search(stem):
		return line
	var done: String = IRREGULAR.get(lower, "")
	if done.is_empty():
		if stem.ends_with("y") and not "aeiou".contains(stem[-2]):
			done = stem.left(-1) + "ied"
		elif stem.ends_with("e"):
			done = stem + "d"
		else:
			done = stem + "ed"
	if word[0] != lower[0]:
		done = done[0].to_upper() + done.substr(1)
	return done + line.substr(word.length())


static func _settled(text: String) -> String:
	var line := text.strip_edges()
	for tail: String in ["…", "..."]:
		line = line.trim_suffix(tail)
	return line.strip_edges()


func _icon(icon_name: String, role: String) -> TextureRect:
	var picture := STYLE.glyph(role)
	STYLE.set_glyph(picture, icon_name, role)
	return picture


func _app_badge(app: String) -> Control:
	# The app's icon at text size with its name beside it: the header of the steps below.
	var badge := HBoxContainer.new()
	badge.theme_type_variation = "JoineryRowTight"
	badge.accessibility_name = app
	var slug := app.to_lower().replace(" ", "_")
	var base := (get_script() as Script).resource_path.get_base_dir()
	for name: String in [slug, slug.replace("_", "-"), slug.replace("_", "")]:
		if UI_THEME.has_app_logo(name):
			# The app's official logo, in its own colours (no tint role).
			var logo := STYLE.glyph("")
			STYLE.on_theme(logo, func() -> void: logo.texture = UI_THEME.app_logo(logo, name))
			badge.add_child(logo)
			break
		var path := base.path_join("icons/tools/%s.svg" % name)
		if FileAccess.file_exists(path):
			var picture := STYLE.glyph("text_muted")
			STYLE.on_theme(
				picture,
				func() -> void:
					var image := Image.new()
					var svg := FileAccess.get_file_as_string(path)
					if image.load_svg_from_string(svg, UI_THEME.unit(picture)) == OK:
						picture.texture = ImageTexture.create_from_image(image)
			)
			badge.add_child(picture)
			break
	# The name keeps its full width: it is the glyph's meaning, never cut to an ellipsis.
	var label := UI.label(app, "JoineryCaption")
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.clip_text = false
	label.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	badge.add_child(label)
	return badge


func _row(parent: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	parent.add_child(row)
	return row
