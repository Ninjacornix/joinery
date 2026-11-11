@tool
extends RefCounted
## Each parallel task's change gets the same review as the main request's (ADR 0036): its row in
## the Review tab, or its card's Review link, opens the before/after panel with the renders,
## Details, Discard and Accept, decided through the chat that runs the task.
##
## The Review tab shows one panel at a time: the main request's (review_tab.gd's detail) or this
## one, holding one task's change. Opening another task's row swaps what it shows. A task never
## opens the panel by itself; the main request's review does, and takes the view back.
##
## A way of a request tried several ways (ADR 0042) shows the variant strip in its panel's rail
## (variant_strip.gd, dense): every way side by side, Use this, Mix and Discard all; a way's
## picture swaps the panel to that way.

const PANEL := preload("review_panel.gd")
const STRIP := preload("variant_strip.gd")
const UI_MOTION := preload("ui_motion.gd")

## The panel a task's change shows in (review_panel.gd), inside the tab's detail view.
var panel: Control
## The transaction of the task change on show ("" for none).
var showing := ""
## The variant strip in the rail of the panel on show while a way is on show (null otherwise).
var strip: Control
var _tab: Control
var _chat: Object
## Each task change waiting for review: {transaction id: its file_preview}.
var _previews: Dictionary = {}
var _view: Control
## Variant groups as last heard: {group id: its event}, and each way's group.
var _groups: Dictionary = {}
var _way_group: Dictionary = {}


## Live inside [param tab] (review_tab.gd), next to its own panel in [param view].
func _init(tab: Control, view: Control) -> void:
	_tab = tab
	_view = view
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
	if chat != null and chat.has_signal("variants_event"):
		chat.connect("variants_event", _on_group)


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
		panel.call("show_glance", preview)
	_show_strip(str(_way_group.get(str(preview.get("task_id", "")), "")), id)
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
	var group := str(event.get("variant_group", ""))
	if not group.is_empty():
		_way_group[str(event.get("task_id", ""))] = group
	if strip != null and strip.call("has_way", str(event.get("task_id", ""))):
		strip.call("on_event", event)
	match str(event.get("kind", "")):
		"file_preview":
			_previews[id] = event.duplicate(true)
		"review_media":
			if _previews.has(id):
				_previews[id]["media"] = event.get("media", [])
				_previews[id]["media_pending"] = false
			if showing == id:
				panel.call("update_media", event.get("media", []))
				panel.call("show_glance", event)
			return ""
		"review_notes":
			for key: String in ["review_notes", "reviewer_status", "reviewer"]:
				if _previews.has(id) and event.has(key):
					_previews[id][key] = event[key]
			if showing == id:
				panel.call("show_glance", event)
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


## The variant strip of [param group] above the panel, [param id] being the way on show; none
## when the change isn't a way of one.
func _show_strip(group: String, id: String) -> void:
	if group.is_empty() or not _groups.has(group):
		_drop_strip()
		return
	if strip == null or str(strip.get("group_id")) != group:
		_drop_strip()
		strip = STRIP.new(group)
		strip.set("compact", true)
		strip.set("dense", true)
		strip.name = "VariantStrip"
		strip.connect("open_requested", _open_way)
		strip.connect("picked", _pick.bind(group))
		strip.connect("mix_requested", _mix.bind(group))
		strip.connect("discard_all_requested", _discard.bind(group))
		_place_strip(panel)
		strip.call("show_group", _groups[group])
		for each: String in _previews:
			if strip.call("has_way", str(_previews[each].get("task_id", ""))):
				strip.call("on_event", _previews[each])
	_place_strip(panel)
	strip.visible = true
	for way: Control in strip.call("ways"):
		way.set("chosen", str(way.get("transaction_id")) == id)
		way.call("_restyle")


## Put the strip in the rail of [param host] (a review_panel.gd).
func _place_strip(host: Control) -> void:
	if strip == null or host == null or not host.has_method("rail_slot"):
		return
	var slot: Control = host.call("rail_slot")
	if strip.get_parent() == slot:
		return
	if strip.get_parent() != null:
		strip.reparent(slot, false)
	else:
		slot.add_child(strip)


func _drop_strip() -> void:
	if strip != null:
		strip.queue_free()
		strip = null


func _on_group(event: Dictionary) -> void:
	var group := str(event.get("group_id", ""))
	if str(event.get("kind", "")) == "variants":
		_groups[group] = event.duplicate(true)
		for way: Variant in event.get("ways", []):
			if way is Dictionary:
				_way_group[str(way.get("task_id", ""))] = group
	if strip != null and str(strip.get("group_id")) == group:
		if str(event.get("kind", "")) == "notice":
			strip.call("pick_failed")
		else:
			strip.call("on_event", event)


## A way's picture: a task's change in this panel, the "As made" way in the main review.
func _open_way(id: String) -> void:
	_tab.call("show_task", id)


## The main review shows [param id], the "As made" way of the strip's request: the strip stays
## above it, that way ringed.
func over_main(id: String) -> void:
	if strip == null:
		return
	var ways: Array = strip.call("ways")
	if not ways.any(func(way: Control) -> bool: return str(way.get("transaction_id")) == id):
		return
	_place_strip(_tab.call("detail"))
	strip.visible = true
	for way: Control in ways:
		way.set("chosen", str(way.get("transaction_id")) == id)
		way.call("_restyle")


func _pick(task_id: String, group: String) -> void:
	if _chat != null:
		_chat.call(
			"start_task", "", [], {"kind": "variants_pick", "group_id": group, "task_id": task_id}
		)


func _mix(note: String, group: String) -> void:
	if _chat != null:
		_chat.call("start_task", "", [], {"kind": "variants_mix", "group_id": group, "note": note})


func _discard(group: String) -> void:
	if _chat != null:
		_chat.call("start_task", "", [], {"kind": "variants_discard", "group_id": group})


func _leave(id: String) -> void:
	if showing != id:
		return
	showing = ""
	_drop_strip()
	if not panel.visible:
		return
	var main: Control = _tab.call("detail")
	if main != null and bool(main.get("pending")):
		_tab.call("show_detail")
	else:
		_tab.call("show_list")
