@tool
extends Node
## Variants side by side in the sidebar (ADR 0042). Three ways to start them:
##
## - "/variants [N] <request> [| direction | …]" (2 to 4 ways, 3 by default);
## - "Try 3 ways" in the composer's options menu, on the typed message;
## - "Try other ways" on a change waiting for review: that change stays as the first way.
##
## Each request tried several ways gets one card among the agents at work (sidebar_tasks.gd)
## holding the variant strip (variant_strip.gd): the ways side by side, Use this, Mix and
## Discard all. Its ways' own task events come here instead of making a task card each. Once a
## way is kept (or all are discarded) the transcript says so in one line and the card steps out.

const STRIP := preload("variant_strip.gd")
const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
## Group the change cards call for "Try other ways".
const GROUP := &"joinery_variants"
## Seconds a decided card stays before it steps out.
const LINGER := 4.0
const USAGE := "Say what to try, for example: /variants 3 a wooden door"

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): cards go in it, and its bridge is the chat's.
var tasks: Control
var _cards: Dictionary = {}
var _strips: Dictionary = {}
## Each way's task id -> its group id.
var _members: Dictionary = {}


static func attach(sidebar: Control, list: Control) -> Node:
	var variants: Node = load("res://addons/joinery/sidebar_variants.gd").new()
	variants.set("dock", sidebar)
	variants.set("tasks", list)
	list.add_child(variants)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("variants_requested"):
		slash.connect("variants_requested", variants.start)
	var composer: Object = sidebar.get("_composer")
	if composer != null and composer.has_signal("variants"):
		composer.connect("variants", variants.start_from_composer)
	return variants


func _init() -> void:
	name = "Variants"
	add_to_group(GROUP)


## Follow [param bridge] (chat_bridge.gd): the groups' own events and the lead's change.
func follow(bridge: Object) -> void:
	if bridge == null:
		return
	if bridge.has_signal("variants_event") and not bridge.is_connected("variants_event", on_group):
		bridge.connect("variants_event", on_group)
	if bridge.has_signal("file_preview") and not bridge.is_connected("file_preview", _on_lead):
		bridge.connect("file_preview", _on_lead)


## "/variants [N] <request>": [param words] is what follows the command.
func start(words: String) -> void:
	var text := words.strip_edges()
	if text.is_empty() or text.is_valid_int():
		_say(USAGE)
		return
	_start(text, 0, "", "")


## "Try 3 ways" in the composer: the typed message, three ways.
func start_from_composer(text: String) -> void:
	if text.strip_edges().is_empty():
		_status("Write what to try first, then Try 3 ways.")
		return
	if _start(text.strip_edges(), 3, "", ""):
		var composer: Object = dock.get("_composer")
		if composer != null:
			(composer.get("prompt") as TextEdit).clear()


## "Try other ways" on a change card: the change in review is the first way.
func try_other_ways(request: String, transaction_id: String, summary := "") -> void:
	_start(request, 2, transaction_id, summary)


func _start(text: String, count: int, keep: String, summary: String) -> bool:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("start_task"):
		return false
	var composer: Object = dock.get("_composer") if dock != null else null
	var targets: Array = composer.call("target_snapshot") if composer != null else []
	var extra := {"kind": "variants_start"}
	if count > 0:
		extra["count"] = count
	if not keep.is_empty():
		extra["keep_transaction_id"] = keep
		extra["keep_summary"] = summary
	if not bridge.call("start_task", text, targets, extra):
		_status("Journeyman isn't connected.")
		return false
	_status("Trying a few ways…")
	return true


## A saved chat's variants entry as one transcript line ("" while undecided).
static func saved_line(entry: Dictionary) -> String:
	var title := str(entry.get("title", ""))
	var lead := (title + ": ") if not title.is_empty() else ""
	match str(entry.get("state", "")):
		"picked":
			var picked: Dictionary = entry.get("picked", {})
			var note := str(picked.get("direction", ""))
			var number := int(picked.get("variant", 0))
			var name := ("%d · %s" % [number, note]) if number > 0 else note
			return lead + "kept %s. The other ways were discarded." % name
		"discarded":
			return lead + "discarded every way."
	return ""


## A task event: true when it belongs to a group, which then shows it.
func take(event: Dictionary) -> bool:
	var task_id := str(event.get("task_id", ""))
	var group := str(event.get("variant_group", _members.get(task_id, "")))
	if group.is_empty():
		return false
	_members[task_id] = group
	var strip := _strip(group)
	strip.on_event(event)
	_title(strip)
	return true


## A group's own event, or the host refusing an action on it.
func on_group(event: Dictionary) -> void:
	var group := str(event.get("group_id", ""))
	if group.is_empty():
		return
	if str(event.get("kind", "")) == "notice":
		var strip := strip_for(group)
		if strip != null:
			strip.call("pick_failed")
		_say(str(event.get("text", "")), "error")
		return
	var strip := _strip(group)
	for item: Variant in event.get("ways", []):
		if item is Dictionary:
			_members[str(item.get("task_id", ""))] = group
	var before := str(strip.get("state"))
	strip.on_event(event)
	_title(strip)
	var after := str(event.get("state", ""))
	if after in ["picked", "discarded"] and before != after:
		_finished(group, strip)
	tasks.call("_sync")


## The strip of [param group] (null when there is none).
func strip_for(group: String) -> Control:
	var found: Control = _strips.get(group)
	return found if is_instance_valid(found) else null


## Every strip on screen.
func strips() -> Array[Control]:
	var found: Array[Control] = []
	for group: String in _strips:
		if strip_for(group) != null:
			found.append(strip_for(group))
	return found


## Ways still working or waiting, for the header's count.
func working_count() -> int:
	var count := 0
	for strip: Control in strips():
		count += int(strip.call("working_count"))
	return count


## True while a card shows.
func has_cards() -> bool:
	return not strips().is_empty()


## The chat's own change, when it is a group's first way ("Try other ways").
func _on_lead(event: Dictionary) -> void:
	var id := str(event.get("transaction_id", ""))
	for strip: Control in strips():
		var lead: Control = strip.call("way", "lead")
		if lead != null and str(lead.get("transaction_id")) == id:
			var preview := event.duplicate(true)
			preview["task_id"] = "lead"
			strip.on_event(preview)


func _strip(group: String) -> Control:
	var found := strip_for(group)
	if found != null:
		return found
	var card := PanelContainer.new()
	card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.on_theme_overrides(
		card,
		func() -> void:
			var source := card.get_theme_stylebox("panel", "JoineryChangeCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(card, 2)))
			card.add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	card.add_child(column)
	var head := STYLE.card_title("")
	head.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(head)
	var strip: Control = STRIP.new(group)
	column.add_child(strip)
	strip.set_meta("head", head)
	strip.connect("picked", _pick.bind(group))
	strip.connect("mix_requested", _mix.bind(group))
	strip.connect("discard_all_requested", _discard.bind(group))
	strip.connect("open_requested", _open)
	_cards[group] = card
	_strips[group] = strip
	tasks.add_child(card)
	tasks.show()
	UI_MOTION.enter(card)
	return strip


func _title(strip: Control) -> void:
	var head: Label = strip.get_meta("head")
	var count := (strip.call("ways") as Array).size()
	var title := str(strip.get("title"))
	head.text = ("%d ways · %s" % [count, title]) if not title.is_empty() else "%d ways" % count
	head.tooltip_text = title


func _pick(task_id: String, group: String) -> void:
	_send(group, {"kind": "variants_pick", "task_id": task_id})


func _mix(note: String, group: String) -> void:
	if _send(group, {"kind": "variants_mix", "note": note}):
		_status("Mixing a new way…")


func _discard(group: String) -> void:
	_send(group, {"kind": "variants_discard"})


## One action on [param group] to the host; false when Journeyman isn't connected.
func _send(group: String, line: Dictionary) -> bool:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("start_task"):
		return false
	line["group_id"] = group
	if not bridge.call("start_task", "", [], line):
		_status("Journeyman isn't connected.")
		var strip := strip_for(group)
		if strip != null:
			strip.call("pick_failed")
		return false
	return true


func _open(transaction_id: String) -> void:
	var bridge: Object = tasks.get("bridge")
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", transaction_id)


## One line in the transcript, then the card steps out.
func _finished(group: String, strip: Control) -> void:
	var title := str(strip.get("title"))
	var result := str(strip.call("result_text"))
	var line := result
	if not title.is_empty():
		line = "%s: %s" % [title, result.left(1).to_lower() + result.substr(1)]
	_say(line)
	var card: Control = _cards.get(group)
	if not is_inside_tree() or not is_instance_valid(card):
		_remove(group)
		return
	get_tree().create_timer(LINGER).timeout.connect(
		func() -> void:
			if is_instance_valid(card):
				UI_MOTION.exit(card, null, _remove.bind(group))
	)


func _remove(group: String) -> void:
	var card: Control = _cards.get(group)
	_cards.erase(group)
	_strips.erase(group)
	if is_instance_valid(card):
		card.queue_free()
	for task_id: String in _members.keys():
		if _members[task_id] == group:
			_members.erase(task_id)
	if tasks != null:
		tasks.call("_sync")
		tasks.call("sync_visible")


func _say(text: String, kind := "info") -> void:
	var transcript: Object = dock.get("_transcript") if dock != null else null
	if transcript != null and not text.is_empty():
		transcript.call("add_notice", text, kind)


func _status(text: String) -> void:
	if dock != null and dock.has_method("show_status"):
		dock.call("show_status", text, 3.0)
