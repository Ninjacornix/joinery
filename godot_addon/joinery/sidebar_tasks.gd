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
##
## The playtester (ADR 0038) is an agent at work too: "/playtest" starts it, and its card
## (playtest_card.gd) sits here with the tasks while it plays. Its written report goes to the
## transcript, each of its "Fix:" lines as an "Ask Journeyman to fix" offer, and "Fix this" on
## a finding puts a ready request in the composer ([method on_playtest]).
##
## "Make it match" (ADR 0045) has its card here too (sidebar_match.gd): the project's scores
## against the art bible, while its fixes run as tasks with cards of their own. So do the
## accessibility and performance checks (ADR 0049, ADR 0050), with the same card. The design
## check has a card of its own (sidebar_design.gd).

const CARD := preload("sidebar_task_card.gd")
const DEBUG := preload("sidebar_debug.gd")
const DESIGN := preload("sidebar_design.gd")
const MATCH := preload("sidebar_match.gd")
const PLAYTEST_CARD := preload("playtest_card.gd")
const VARIANTS := preload("sidebar_variants.gd")
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
## The chat bridge (chat_bridge.gd), once attached; its playtest events come here.
var bridge: Object:
	set(value):
		bridge = value
		if bridge != null and bridge.has_signal("playtest_event"):
			if not bridge.is_connected("playtest_event", on_playtest):
				bridge.connect("playtest_event", on_playtest)
		if variants != null:
			variants.call("follow", bridge)
## The playtest card, while there is one.
var playtest: Control
## The match card (match_card.gd), while there is one.
var match_card: Control
## The accessibility and performance cards (match_card.gd), while there are.
var access_card: Control
var perf_card: Control
## The design check's card (design_card.gd), while there is one.
var design_card: Control
## Requests tried several ways (sidebar_variants.gd): their ways' events go there.
var variants: Node
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
	# "/match" and Make it match: the project against the art bible.
	MATCH.attach(sidebar, tasks)
	# "/access" and "/perf": the game's accessibility and performance.
	MATCH.attach(sidebar, tasks, "access")
	MATCH.attach(sidebar, tasks, "perf")
	# "/design": the game against its own brief.
	DESIGN.attach(sidebar, tasks)
	tasks.variants = VARIANTS.attach(sidebar, tasks)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("playtest_requested"):
		slash.connect("playtest_requested", tasks.start_playtest)
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
	if variants != null:
		count += int(variants.call("working_count"))
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
	if variants != null and variants.call("take", event):
		_sync()
		return
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


## "/playtest [focus]": the playtester starts; its card shows at once.
func start_playtest(focus: String) -> void:
	if bridge == null or not bridge.has_method("start_playtest"):
		return
	if is_instance_valid(playtest) and playtest.call("working"):
		dock.call("show_status", "A playtest is already running.", 3.0)
		return
	if not bridge.call("start_playtest", focus):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	_playtest_card("")


## One event from a playtest (chat_bridge.gd's playtest_event).
func on_playtest(event: Dictionary) -> void:
	var id := str(event.get("playtest_id", ""))
	var card := playtest
	if not is_instance_valid(card) or not str(card.get("playtest_id")) in ["", id]:
		card = _playtest_card(id)
	card.set("playtest_id", id)
	var before := str(card.get("state"))
	card.call("apply", event)
	if str(card.get("state")) != before:
		match str(card.get("state")):
			"done":
				_playtest_report(event)
			"failed":
				var transcript: Object = dock.get("_transcript")
				if transcript != null:
					transcript.call(
						"add_notice", "Playtest: " + str(card.call("step_text")), "error"
					)
	_sync()


func _playtest_card(id: String) -> Control:
	if is_instance_valid(playtest):
		playtest.queue_free()
	playtest = PLAYTEST_CARD.new(id)
	playtest.connect("stop_pressed", _stop_playtest)
	playtest.connect("close_pressed", _close_playtest)
	playtest.connect("fix_requested", prefill)
	add_child(playtest)
	show()
	UI_MOTION.enter(playtest)
	_sync()
	return playtest


## The playtester's words in the transcript, and each follow-up it proposed as an offer.
func _playtest_report(event: Dictionary) -> void:
	var transcript: Object = dock.get("_transcript")
	if transcript == null:
		return
	var summary := str(event.get("summary", ""))
	if not summary.is_empty() and str(event.get("origin", "")) != "agent":
		transcript.call("add_message", "Playtester", summary)
	var followups: Variant = event.get("followups", [])
	if followups is Array:
		for request: Variant in followups:
			var text := str(request)
			transcript.call("add_offer", text, [["Ask Journeyman to fix", prefill.bind(text)]])


## Puts [param text] in the composer, ready to send as a normal request.
func prefill(text: String) -> void:
	var composer: Object = dock.get("_composer")
	if composer == null:
		return
	var prompt := composer.get("prompt") as TextEdit
	prompt.text = text
	prompt.set_caret_line(prompt.get_line_count() - 1)
	prompt.set_caret_column(prompt.get_line(prompt.get_line_count() - 1).length())
	prompt.grab_focus()
	dock.call("show_status", "Request ready: edit it or send it", 3.0)


func _stop_playtest() -> void:
	if bridge != null and bridge.has_method("cancel_playtest"):
		bridge.call("cancel_playtest", str(playtest.get("playtest_id")))


func _close_playtest() -> void:
	var card := playtest
	playtest = null
	UI_MOTION.exit(
		card,
		null,
		func() -> void:
			card.queue_free()
			sync_visible()
	)
	_sync()


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
	sync_visible()
	_sync()


## Shown while any card is: a task's, the playtest's, the match card or a variant strip.
func sync_visible() -> void:
	visible = (
		not _cards.is_empty()
		or is_instance_valid(playtest)
		or is_instance_valid(match_card)
		or is_instance_valid(access_card)
		or is_instance_valid(perf_card)
		or is_instance_valid(design_card)
		or _has_variants()
	)


func _has_variants() -> bool:
	return variants != null and bool(variants.call("has_cards"))


func _sync() -> void:
	var working := working_count()
	var testing := is_instance_valid(playtest) and bool(playtest.call("working"))
	var header: Object = dock.get("_header") if dock != null else null
	if header != null and header.has_method("show_agents"):
		header.call("show_agents", working + (1 if _main_busy else 0) + (1 if testing else 0))
	var composer: Object = dock.get("_composer") if dock != null else null
	if composer != null and composer.has_method("set_alongside"):
		var limit := int(bridge.get("parallel")) if bridge != null else 1
		composer.call("set_alongside", _main_busy and working < limit - 1)
	if working == 0 and is_instance_valid(_offer):
		_drop_offer()
