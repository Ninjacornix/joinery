@tool
extends RefCounted
## Each parallel task's change gets the same review as the main request's (ADR 0036): its row in
## the Review tab, or its card's Review link, opens the before/after panel with the renders,
## Details, Discard and Accept, decided through the chat that runs the task.
##
## The Review tab shows one panel at a time: the main request's (review_tab.gd's detail) or this
## one, holding one task's change. Opening another task's row swaps what it shows. A task never
## opens the panel by itself; the main request's review does, and takes the view back.

const PANEL := preload("review_panel.gd")
const UI_MOTION := preload("ui_motion.gd")

## The panel a task's change shows in (review_panel.gd), inside the tab's detail view.
var panel: Control
## The transaction of the task change on show ("" for none).
var showing := ""
var _tab: Control
var _chat: Object
## Each task change waiting for review: {transaction id: its file_preview}.
var _previews: Dictionary = {}


## Live inside [param tab] (review_tab.gd), next to its own panel in [param view].
func _init(tab: Control, view: Control) -> void:
	_tab = tab
	panel = PANEL.new()
	panel.name = "TaskReview"
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.hide()
	view.add_child(panel)
	panel.connect("accept_pressed", _decided.bind(true))
	panel.connect("reject_pressed", _decided.bind(false))


## Decide through [param chat] (chat_bridge.gd), which runs the tasks.
func follow(chat: Object) -> void:
	_chat = chat


## True when [param id] is a task's change.
func has(id: String) -> bool:
	return _previews.has(id)


## The panel shows the task change [param id]; false when it isn't one.
func open(id: String) -> bool:
	var preview: Dictionary = _previews.get(id, {})
	if preview.is_empty():
		return false
	if showing != id or not bool(panel.get("pending")):
		showing = id
		(
			panel
			. call(
				"show_file_review",
				str(preview.get("summary", "File changes")),
				str(preview.get("diff", "")),
				preview.get("media", []),
				preview.get("changes", []),
			)
		)
	return true


## The task panel is the one in view.
func in_view() -> bool:
	return panel.visible and not showing.is_empty()


## Accept or Discard on the task's row: through the panel when it shows that change, so both
## say the same; otherwise straight through the chat. False when [param id] isn't a task's.
func decide(id: String, accepted: bool) -> bool:
	if not has(id):
		return false
	if showing == id and panel.is_visible_in_tree() and bool(panel.get("pending")):
		panel.call("decide", accepted)
	elif _chat != null:
		_chat.call("decide_files", id, accepted)
	return true


func _decided(accepted: bool) -> void:
	if showing.is_empty() or _chat == null:
		return
	_tab.call("mark_busy", showing, accepted)
	_chat.call("decide_files", showing, accepted)


## One event from a task (chat_bridge.gd's task_event). Returns the transaction it was about
## when the Review tab should read its list again.
func on_event(event: Dictionary) -> String:
	var id := str(event.get("transaction_id", ""))
	match str(event.get("kind", "")):
		"file_preview":
			_previews[id] = event.duplicate(true)
		"review_media":
			if _previews.has(id):
				_previews[id]["media"] = event.get("media", [])
				_previews[id]["media_pending"] = false
			if showing == id:
				panel.call("update_media", event.get("media", []))
			return ""
		"file_decision":
			_previews.erase(id)
			if showing == id:
				var accepted := bool(event.get("accepted", false))
				panel.call("show_result", accepted, str(event.get("message", "")))
				_leave_after_result(id)
		"notice":
			if showing == id and bool(panel.get("pending")):
				panel.call("show_error", str(event.get("text", "")))
		_:
			return ""
	return id


## After the decision has shown: back to the main request's review if one waits, else the list.
func _leave_after_result(id: String) -> void:
	if not _tab.is_inside_tree():
		_leave(id)
		return
	await _tab.get_tree().create_timer(UI_MOTION.duration("confirm")).timeout
	_leave(id)


func _leave(id: String) -> void:
	if showing != id:
		return
	showing = ""
	if not panel.visible:
		return
	var main: Control = _tab.call("detail")
	if main != null and bool(main.get("pending")):
		_tab.call("show_detail")
	else:
		_tab.call("show_list")
