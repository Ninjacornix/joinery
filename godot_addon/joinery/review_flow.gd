@tool
extends Node
## Puts Journeyman's change in front of the user when Joinery says it passed its checks.
##
## The chat host sends file_preview when a submitted change passes, turn_state as the turn
## moves (working, submitting, in_review, decided, idle) and review_media when the before and
## after renders arrive. A live scene edit shows in the viewport at once, but its Accept and
## Discard wait here until the change is in review; a turn that drops its change drops the
## preview too.

## Reasons a turn ends without its change ("idle" with one of these): the preview goes.
const DROPPED := ["failed", "cancelled", "stopped", "needs_user"]

## The plugin (plugin.gd): its review panel, sidebar and live preview.
var _plugin: Object
var _live_summary := ""
var _live_waiting := false


## Follow [param chat] (chat_bridge.gd) for [param plugin].
func setup(plugin: Object, chat: Node) -> void:
	_plugin = plugin
	var links := {
		"file_preview": _on_file_preview,
		"turn_state": _on_turn_state,
		"review_media": _on_review_media,
	}
	for signal_name: String in links:
		if chat.has_signal(signal_name):
			chat.connect(signal_name, links[signal_name])


## A live edit is on screen in the viewport ([param summary] names it). Its review opens once
## the change is in review.
func live_ready(summary: String) -> void:
	_live_summary = summary
	_live_waiting = true


## True while a live edit waits for its change to reach review.
func live_waiting() -> bool:
	return _live_waiting


func _on_turn_state(event: Dictionary) -> void:
	if not _live_waiting:
		return
	var state := str(event.get("state", ""))
	if state == "in_review":
		_live_waiting = false
		var edits: Variant = _plugin.get("_edits")
		var pending := edits is Object and bool((edits as Object).call("pending"))
		# A file review shows the live edit with it; otherwise the scene review opens.
		if pending and (_plugin.get("_file_review") as Dictionary).is_empty():
			_plugin.call("_open_review", _live_summary)
	elif state == "idle" and str(event.get("reason", "")) in DROPPED:
		_live_waiting = false
		_plugin.call("_reject_scene_edit", "", "info")


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
	_plugin.call("_show_edit_review_panel")


## The renders finished: the review panel and the request's card take the pictures.
func _on_review_media(event: Dictionary) -> void:
	var review: Dictionary = _plugin.get("_file_review")
	if review.get("transaction_id") != event.get("transaction_id"):
		return
	var media: Array = event.get("media", [])
	review["media"] = media
	var tab: Variant = _plugin.get("_review_tab")
	var panel: Variant = (tab as Object).call("detail") if tab is Object else null
	if panel is Object and (panel as Object).has_method("update_media"):
		(panel as Object).call("update_media", media)
	var dock: Variant = _plugin.get("_dock")
	var card: Variant = (
		(dock as Object).call("_card")
		if dock is Object and (dock as Object).has_method("_card")
		else null
	)
	if card is Object and (card as Object).get("state") == "ready":
		(card as Object).call("set_ready", media)
