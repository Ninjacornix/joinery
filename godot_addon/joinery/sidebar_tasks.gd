@tool
extends VBoxContainer
## Agents working beside Journeyman (parallel tasks, ADR 0036), between the transcript and the
## footer: one card each (sidebar_task_card.gd) with its title, apps, step, timer and its own
## Stop. It also runs the rest of the sidebar's side of parallel work:
##
## - "Run alongside" in the composer while Journeyman works starts the typed message as a task;
## - the header says "3 agents working" while two or more agents work;
## - Stop on Journeyman's own request asks, in one quiet line, whether to stop the tasks too;
## - a finished task leaves one line in the transcript ("Lamp: change accepted.") and its card
##   steps out; its change is decided in the Review tab like any other.
##
## It hears the host through the chat bridge's task_event ([method on_event]).

const CARD := preload("sidebar_task_card.gd")
const DEBUG := preload("sidebar_debug.gd")
const UI_MOTION := preload("ui_motion.gd")
## Seconds a finished task's card stays before it steps out.
const LINGER := 4.0
## Final state -> how its transcript line ends.
const ENDINGS := {
	"accepted": "change accepted.",
	"discarded": "change discarded.",
	"done": "done, nothing needed changing.",
	"failed": "didn't finish.",
	"stopped": "stopped.",
}

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The chat bridge (chat_bridge.gd), once attached.
var bridge: Object
var _cards: Dictionary = {}
var _main_busy := false
var _offer: Control


static func attach(sidebar: Control, content: Control, before: Control) -> VBoxContainer:
	var tasks: VBoxContainer = load("res://addons/joinery/sidebar_tasks.gd").new()
	tasks.dock = sidebar
	content.add_child(tasks)
	content.move_child(tasks, before.get_index())
	var composer: Object = sidebar.get("_composer")
	if composer != null and composer.has_signal("alongside"):
		composer.connect("alongside", tasks.run_alongside)
	# "/debug" and the offer after a new error in Output start a debugging task.
	DEBUG.attach(sidebar, tasks)
	return tasks


func _init() -> void:
	name = "Tasks"
	theme_type_variation = "JoineryTight"
	accessibility_name = "Agents working alongside"
	hide()


## Every task card, oldest first.
func cards() -> Array[Control]:
	var found: Array[Control] = []
	for card: Control in _cards.values():
		if is_instance_valid(card):
			found.append(card)
	return found


func card(task_id: String) -> Control:
	var found: Control = _cards.get(task_id)
	return found if is_instance_valid(found) else null


## Tasks still working.
func working_count() -> int:
	var count := 0
	for each: Control in cards():
		if each.call("working"):
			count += 1
	return count


## Journeyman's own request started or ended: the header count and "Run alongside" follow it.
func set_main_busy(busy: bool) -> void:
	_main_busy = busy
	if not busy and is_instance_valid(_offer) and working_count() == 0:
		_drop_offer()
	_sync()


## One event from a task (chat_bridge.gd's task_event).
func on_event(event: Dictionary) -> void:
	var id := str(event.get("task_id", ""))
	match str(event.get("kind", "")):
		"task":
			var each := card(id)
			if each == null:
				each = _add(id, str(event.get("title", "Task")))
			var before := str(each.get("state"))
			each.call("apply", event)
			var after := str(each.get("state"))
			if after != before and ENDINGS.has(after):
				_finished(each, event)
		"progress":
			var each := card(id)
			if each != null:
				each.call("set_step", str(event.get("text", "")))
	_sync()


## "Run alongside": [param text] starts now as its own task.
func run_alongside(text: String) -> void:
	var composer: Object = dock.get("_composer")
	if bridge == null or not bridge.has_method("start_task"):
		return
	var targets: Array = composer.call("target_snapshot") if composer != null else []
	if not bridge.call("start_task", text, targets):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	if composer != null:
		(composer.get("prompt") as TextEdit).clear()
		var images: Object = composer.get("attachments")
		if images != null:
			images.call("clear")
	dock.call("show_status", "Started alongside", 2.0)


## Stop on Journeyman's own request: when tasks still work, ask once whether to stop them too.
func offer_stop_all() -> void:
	var count := working_count()
	if count == 0 or is_instance_valid(_offer):
		return
	var transcript: Object = dock.get("_transcript")
	if transcript == null:
		return
	var text := (
		"A task is still working. Stop it too?"
		if count == 1
		else "%d tasks are still working. Stop them too?" % count
	)
	_offer = transcript.call(
		"add_offer", text, [["Stop all", _stop_all], ["Keep them", _drop_offer]]
	)


func _stop_all() -> void:
	_drop_offer()
	for each: Control in cards():
		each.call("mark_stopping")
	if bridge != null and bridge.has_method("cancel_tasks"):
		bridge.call("cancel_tasks")


func _drop_offer() -> void:
	var transcript: Object = dock.get("_transcript")
	if transcript != null and is_instance_valid(_offer):
		transcript.call("remove_offer", _offer)
	_offer = null


func _add(id: String, title: String) -> Control:
	var each: Control = CARD.new(id, title)
	each.connect("stop_pressed", _on_stop.bind(id))
	each.connect("review_pressed", _on_review.bind(each))
	_cards[id] = each
	add_child(each)
	if not visible:
		show()
	UI_MOTION.enter(each)
	return each


func _on_stop(id: String) -> void:
	if bridge != null and bridge.has_method("cancel_task"):
		bridge.call("cancel_task", id)


func _on_review(each: Control) -> void:
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", str(each.get("transaction_id")))


## A task is over: one line in the transcript, then its card steps out.
func _finished(each: Control, event: Dictionary) -> void:
	var state := str(each.get("state"))
	var transcript: Object = dock.get("_transcript")
	if transcript != null:
		var kind := "error" if state == "failed" else "info"
		var line := "%s: %s" % [str(each.get("title")), ENDINGS[state]]
		var message := str(event.get("message", ""))
		if (state == "failed" or str(each.get("role")) == "debugger") and not message.is_empty():
			line = "%s: %s" % [str(each.get("title")), message]
		transcript.call("add_notice", line, kind)
	if not is_inside_tree():
		_remove(str(each.get("task_id")))
		return
	get_tree().create_timer(LINGER).timeout.connect(
		func() -> void:
			if is_instance_valid(each) and not each.call("working"):
				UI_MOTION.exit(each, null, _remove.bind(str(each.get("task_id"))))
	)


func _remove(id: String) -> void:
	var each: Control = _cards.get(id)
	_cards.erase(id)
	if is_instance_valid(each):
		each.queue_free()
	if _cards.is_empty():
		hide()
	_sync()


func _sync() -> void:
	var working := working_count()
	var header: Object = dock.get("_header") if dock != null else null
	if header != null and header.has_method("show_agents"):
		header.call("show_agents", working + (1 if _main_busy else 0))
	var composer: Object = dock.get("_composer") if dock != null else null
	if composer != null and composer.has_method("set_alongside"):
		var limit := int(bridge.get("parallel")) if bridge != null else 1
		composer.call("set_alongside", _main_busy and working < limit - 1)
	if working == 0 and is_instance_valid(_offer):
		_drop_offer()
