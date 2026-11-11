@tool
extends RefCounted
## Where the review on show sits among the changes waiting, for its rail's "2 of 5"
## (review_rail.gd), and stepping to the previous or next one with its arrows or Left and Right.
## The queue is the Waiting for you list's order, counting the changes that open a full review:
## the one Journeyman is showing and each parallel task's. The next review crossfades in over
## the last one (review_layout.gd's ghost).

const LAYOUT := preload("review_layout.gd")

var _tab: Control


## Serve [param tab] (review_tab.gd).
func _init(tab: Control) -> void:
	_tab = tab


## Let [param panel]'s rail go back to the list and step through the queue.
func follow(panel: Control) -> void:
	panel.connect("list_requested", func() -> void: _tab.call("show_list"))
	panel.connect("step_requested", step)


## The changes waiting that open a full review, in the list's order.
func ids() -> Array[String]:
	var found: Array[String] = []
	var tasks: Object = _tab.get("_tasks")
	for entry: Dictionary in _tab.call("_pending"):
		var id := str(entry.get("id", ""))
		if not id.is_empty() and (bool(entry.get("live", false)) or tasks.call("has", id)):
			found.append(id)
	return found


## The change on show ("" when none).
func current() -> String:
	var tasks: Object = _tab.get("_tasks")
	if tasks.call("in_view"):
		return str(tasks.get("showing"))
	return str((_tab.get("_live") as Dictionary).get("id", ""))


## Tell the panel on show where it sits.
func sync() -> void:
	var panel := _shown()
	if panel == null:
		return
	var queue := ids()
	panel.call("set_queue", queue.find(current()), queue.size())


## Show the change [param delta] places away (-1 previous, 1 next), if there is one.
func step(delta: int) -> void:
	var queue := ids()
	var at := queue.find(current())
	var to := at + delta
	if at < 0 or to < 0 or to >= queue.size():
		return
	var panel := _shown()
	if panel != null:
		LAYOUT.ghost(panel, _tab)
	_tab.call("show_task", queue[to])


func _shown() -> Control:
	var tasks: Object = _tab.get("_tasks")
	var task_panel: Control = tasks.get("panel")
	if task_panel != null and task_panel.visible:
		return task_panel
	return _tab.call("detail")
