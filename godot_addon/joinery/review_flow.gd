@tool
extends Node
## Puts Journeyman's change in front of the user when Joinery says it passed its checks.
##
## The chat host sends file_preview when a submitted change passes, turn_state as the turn
## moves (working, submitting, in_review, decided, idle) and review_media when the before and
## after renders arrive. A live scene edit shows in the viewport at once, but its Accept and
## Discard wait here until the change is in review; a turn that drops its change drops the
## preview too.
##
## Once Journeyman's own request is idle, a parallel task may make the live edit (ADR 0036): its
## preview is followed through that task's turn instead, and its review never opens over a
## file review of the main request; it opens after it, or from the task card's Review link.

## Reasons a turn ends without its change ("idle" with one of these): the preview goes.
const DROPPED := ["failed", "cancelled", "stopped", "needs_user"]

## The plugin (plugin.gd): its review panel, sidebar and live preview.
var _plugin: Object
var _chat: Node
var _live_summary := ""
var _live_waiting := false
## The task whose live edit is on screen ("" for Journeyman's own request).
var _live_owner := ""


## Follow [param chat] (chat_bridge.gd) for [param plugin].
func setup(plugin: Object, chat: Node) -> void:
	_plugin = plugin
	_chat = chat
	var links := {
		"file_preview": _on_file_preview,
		"turn_state": _on_turn_state,
		"review_media": _on_review_media,
		"task_event": _on_task_event,
		"task_review": _on_task_review,
		"review_notes": _on_review_notes,
	}
	for signal_name: String in links:
		if chat.has_signal(signal_name):
			chat.connect(signal_name, links[signal_name])
	var panel := _panel()
	if panel != null and panel.has_signal("revise_requested"):
		panel.connect("revise_requested", _on_revise)


## A live edit is on screen in the viewport ([param summary] names it; [param owner] is the
## task that made it, "" for Journeyman's own request). Its review opens once the change is in
## review.
func live_ready(summary: String, owner := "") -> void:
	_live_summary = summary
	_live_waiting = true
	_live_owner = owner


## True when the live preview on screen, if any, belongs to Journeyman's own request, so it is
## decided together with that request's files.
func owns_live() -> bool:
	return _live_owner.is_empty() or not _pending()


## Open a task's live edit for review, unless the main request's file review is on screen.
func reopen_live() -> void:
	if _live_owner.is_empty() or _live_waiting or not _pending():
		return
	if (_plugin.get("_file_review") as Dictionary).is_empty():
		_plugin.call("_open_review", _live_summary)


## True while a live edit waits for its change to reach review.
func live_waiting() -> bool:
	return _live_waiting


func _on_turn_state(event: Dictionary) -> void:
	if str(event.get("state", "")) == "in_review" and event.get("done") is Array:
		_show_done(event["done"])
	if _live_owner.is_empty():
		_follow(event)


## The request's card lists what was done for each part of it (submit_for_review's done).
func _show_done(items: Array) -> void:
	var dock: Variant = _plugin.get("_dock")
	var card: Variant = (dock as Object).call("_card") if dock is Object else null
	if card is Object and (card as Object).has_method("set_done"):
		(card as Object).call("set_done", items)


## A task's events: the turn of the task whose live edit is on screen moves its review.
func _on_task_event(event: Dictionary) -> void:
	if str(event.get("kind", "")) != "turn_state" or _live_owner.is_empty():
		return
	if str(event.get("task_id", "")) == _live_owner:
		_follow(event)


## A task card's Review link; a live edit has no transaction ("").
func _on_task_review(transaction_id: String) -> void:
	if transaction_id.is_empty():
		reopen_live()


func _follow(event: Dictionary) -> void:
	if not _live_waiting:
		return
	var state := str(event.get("state", ""))
	if state == "in_review":
		_live_waiting = false
		# A file review shows the live edit with it; otherwise the scene review opens.
		if _pending() and (_plugin.get("_file_review") as Dictionary).is_empty():
			_plugin.call("_open_review", _live_summary)
	elif state == "idle" and str(event.get("reason", "")) in DROPPED:
		_live_waiting = false
		if owns_live() or (_plugin.get("_file_review") as Dictionary).is_empty():
			_plugin.call("_reject_scene_edit", "", "info")
		else:
			# Only the task's preview goes; the main request's files stay in review.
			(_plugin.get("_edits") as Object).call("reject")
			_chat.call("finish_live_review", false)


func _pending() -> bool:
	var edits: Variant = _plugin.get("_edits")
	return edits is Object and bool((edits as Object).call("pending"))


func _on_file_preview(event: Dictionary) -> void:
	_plugin.set("_file_review", event.duplicate(true))
	(
		_plugin
		. call(
			"_call_dock",
			"show_file_review",
			[
				str(event.get("summary", "File changes")),
				str(event.get("diff", "")),
				event.get("media", []),
				event.get("changes", []),
			]
		)
	)
	_glance(event)
	_plugin.call("_show_edit_review_panel")


## The review panel (review_panel.gd) held by the Review tab, or null.
func _panel() -> Object:
	var tab: Variant = _plugin.get("_review_tab")
	var panel: Variant = (tab as Object).call("detail") if tab is Object else null
	return panel as Object if panel is Object else null


## The event's facts, checks and reviewer notes join the review on show.
func _glance(event: Dictionary) -> void:
	var panel := _panel()
	if panel != null and panel.has_method("show_glance"):
		panel.call("show_glance", event)


## Ask for changes, from the review: the chat's message box takes the focus with the changed
## asset as its target, so the user says what to change about this change.
func _on_revise(path: String) -> void:
	var dock: Variant = _plugin.get("_dock")
	if not dock is Object:
		return
	if not path.is_empty():
		(dock as Object).call("set_resource_target", "res://" + path.trim_prefix("res://"))
	var prompt: Variant = (dock as Object).get("_prompt")
	if prompt is Control and (prompt as Control).is_visible_in_tree():
		(prompt as Control).grab_focus()


## The reviewer's notes arrived after the review opened.
func _on_review_notes(event: Dictionary) -> void:
	var review: Dictionary = _plugin.get("_file_review")
	if review.get("transaction_id") != event.get("transaction_id"):
		return
	for key: String in ["review_notes", "reviewer_status", "reviewer"]:
		if event.has(key):
			review[key] = event[key]
	_glance(event)


## The renders finished: the review panel and the request's card take the pictures.
func _on_review_media(event: Dictionary) -> void:
	var review: Dictionary = _plugin.get("_file_review")
	if review.get("transaction_id") != event.get("transaction_id"):
		return
	var media: Array = event.get("media", [])
	review["media"] = media
	var panel := _panel()
	if panel != null and panel.has_method("update_media"):
		panel.call("update_media", media)
	_glance(event)
	var dock: Variant = _plugin.get("_dock")
	var card: Variant = (
		(dock as Object).call("_card")
		if dock is Object and (dock as Object).has_method("_card")
		else null
	)
	if card is Object and (card as Object).get("state") == "ready":
		(card as Object).call("set_ready", media)
