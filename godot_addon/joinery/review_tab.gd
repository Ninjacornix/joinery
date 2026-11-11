@tool
extends VBoxContainer
## The Review tab in the editor's bottom panel: every approval in one place.
##
## Waiting for you: each change that needs a decision, one line each (what it does, which app
## made it, when) with Discard and Accept on the row. Clicking the change that Journeyman just
## made opens the full before/after review (review_panel.gd, held here as the detail view);
## any other change shows its files under the row. Then the art bible's approval as one row,
## and Decided: accepted, discarded and reverted changes, newest first, with Revert while the
## change can still be put back.
##
## The list comes from `joinery transactions list` (command_job.gd), read when the tab is
## shown, on focus, when a turn ends and when a change is staged or decided; it never polls.
## Each parallel task's change is its own row, decided through the chat that runs the task.
## [signal count_changed] carries the number of things waiting, for the tab's badge.

## The number of things waiting for the user changed (the tab reads "Review 2").
signal count_changed(count: int)
## Accept or Discard on a row. The change Journeyman is showing ([param live]) is decided
## through the full review panel; any other one here through the CLI.
signal decision_requested(transaction_id: String, accepted: bool, live: bool)
## A change accepted here wrote [param paths] ([param changes] are its {path, change} rows).
signal changes_applied(transaction_id: String, paths: PackedStringArray, changes: Array)
## Revert on a decided row. With [method attach] the sidebar runs it and reports back.
signal revert_requested(transaction_id: String)
## "Open" on the art bible row: show the art direction (/art).
signal art_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const WORDS := preload("review_words.gd")
const EMPTY_WAITING := "Nothing waits for your review."
const EMPTY_DECIDED := "No decisions yet."
const TOO_OLD := "Too old to revert"
## Decided rows shown at most; older ones stay in the project history.
const DECIDED_ROWS := 50
## Outcome of a decided change -> [word, glyph, colour role].
const OUTCOMES := {
	"accepted": ["Accepted", "success", "success"],
	"rejected": ["Discarded", "close", "text_muted"],
	"reverted": ["Reverted", "reload", "text_muted"],
}
## App ids as the gateway records them -> the names people know.
const APP_NAMES := WORDS.APP_NAMES

## Off in tests: nothing runs the CLI, listings come only through [method apply_listing].
var cli_enabled := true
## True while the detail (full before/after review) is shown instead of the list.
var showing_detail := false
var _detail: Control
var _list_view: VBoxContainer
var _detail_view: VBoxContainer
var _back: Button
var _content: VBoxContainer
var _loading: HBoxContainer
var _load_error: Label
var _waiting_body: VBoxContainer
var _waiting_empty: Label
var _bible_row: HBoxContainer
## Where the bible row sits: under Waiting for you while it needs approval, otherwise first
## under Decided.
var _bible_waiting: VBoxContainer
var _bible_decided: VBoxContainer
var _bible_status: Label
var _bible_dot: Control
var _bible_open: Button
var _decided_body: VBoxContainer
var _decided_empty: Label
var _decided_more: Label
var _listing: Dictionary = {}
var _loaded := false
var _live: Dictionary = {}
## Rows busy with a decision or a revert: {id: "accepting" | "discarding" | "reverting"}.
var _busy: Dictionary = {}
## The last error per row: {id: text}.
var _errors: Dictionary = {}
## Non-live pending rows whose files are open.
var _open_files: Dictionary = {}
var _count := -1
var _jobs: Dictionary = {}
var _list_again := false
var _slash: Object
## The chat (chat_bridge.gd), and its parallel tasks' changes: decided through the chat.
var _chat: Object
var _task_ids: Dictionary = {}
var _legacy_button: Button
## True when the bottom panel opened by itself for the live review, so it closes again after.
var _opened_itself := false


func _init() -> void:
	name = "Review"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Changes waiting for your review, and the ones you decided"
	_build_list()
	_detail_view = VBoxContainer.new()
	_detail_view.theme_type_variation = "JoineryTight"
	_detail_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_detail_view.hide()
	add_child(_detail_view)
	_back = UI.quiet("All approvals")
	_back.alignment = HORIZONTAL_ALIGNMENT_LEFT
	_back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_back.tooltip_text = "Back to every change waiting for you and the ones you decided"
	UI.bind_icon(_back, "back")
	_back.pressed.connect(show_list)
	_detail_view.add_child(_back)
	_render()


func _ready() -> void:
	UI_THEME.watch(self)
	set_process(false)


func _exit_tree() -> void:
	for slot: String in _jobs:
		(_jobs[slot][0] as RefCounted).call("cancel")
	_jobs.clear()


func _notification(what: int) -> void:
	match what:
		NOTIFICATION_VISIBILITY_CHANGED:
			if is_visible_in_tree():
				refresh()
		NOTIFICATION_APPLICATION_FOCUS_IN:
			# A change made in a terminal shows up when the user comes back to the editor.
			refresh()
		NOTIFICATION_THEME_CHANGED:
			_tint()


## Called by UI_THEME.watch after a live re-theme.
func _on_theme_rebuilt() -> void:
	_tint()


# --- Public ------------------------------------------------------------------------------------


## Hold [param panel] (review_panel.gd) as the detail view.
func set_detail(panel: Control) -> void:
	_detail = panel
	panel.name = "ReviewDetail"
	if panel.get_parent() != null:
		panel.get_parent().remove_child(panel)
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_detail_view.add_child(panel)


func detail() -> Control:
	return _detail


## Use the sidebar's [param slash] (slash_commands.gd) for Revert, so the chat's change card
## follows; [param legacy_button] is the bottom panel button of editors without tabs.
func attach(slash: Variant, legacy_button: Button = null) -> void:
	_legacy_button = legacy_button
	if slash is Object and (slash as Object).has_method("revert"):
		_slash = slash as Object
		if _slash.has_signal("reverted"):
			_slash.connect("reverted", revert_finished)
	_show_count()


## Make this the first bottom tab, shown, with the stable layout key "Review" (its title
## carries the badge), and read the list. On editors whose bottom panel has no tabs nothing
## moves.
func pin_first() -> void:
	refresh()
	_pin()


## Keep this the first bottom tab: a restored layout or a renamed tab can move it.
func _pin() -> void:
	var dock := get_parent() as Control
	if dock != null and dock.is_class("EditorDock"):
		dock.set("layout_key", "Review")
	var tabs := dock.get_parent() as TabContainer if dock != null else null
	if tabs == null:
		return
	var index := tabs.get_tab_idx_from_control(dock)
	if index < 0:
		return
	if index != 0:
		tabs.move_child(dock, 0)
		index = tabs.get_tab_idx_from_control(dock)
	tabs.set_tab_hidden(index, false)


## Show the full review of the change Journeyman is showing. Call it before the bottom panel
## opens on this tab: a panel that was closed opened by itself, and closes after the decision.
func show_detail() -> void:
	if _detail == null:
		return
	if not showing_detail:
		_opened_itself = not is_visible_in_tree()
	showing_detail = true
	_pin()
	var arriving := not _detail_view.visible
	_list_view.hide()
	_detail_view.show()
	if arriving:
		UI_MOTION.fade_in(_detail_view)


## Back to the list of approvals.
func show_list() -> void:
	showing_detail = false
	var arriving := not _list_view.visible
	_detail_view.hide()
	_list_view.show()
	if arriving:
		UI_MOTION.fade_in(_list_view)


## The live review was decided or dropped. Returns true when the bottom panel opened by itself
## for it and should close now; otherwise the list comes back once the result has shown.
func review_closed() -> bool:
	clear_live()
	refresh()
	var close := _opened_itself and is_visible_in_tree()
	_opened_itself = false
	if close or not showing_detail or not is_inside_tree():
		show_list()
	else:
		_list_after_result.call_deferred()
	return close


## Follow Journeyman's chat ([param chat], chat_bridge.gd): its change shows as soon as it
## passes Joinery's checks; the list is read again when a turn ends or a change is decided.
func follow(chat: Object) -> void:
	_chat = chat
	var links := {
		"file_preview": show_live,
		"completed": func(_ok: bool) -> void: refresh(),
		"file_decision": func(_event: Dictionary) -> void: refresh(),
		"task_event": _on_task_event,
		"task_review": show_task,
	}
	for signal_name: String in links:
		if chat.has_signal(signal_name):
			chat.connect(signal_name, links[signal_name])


## The change Journeyman staged for review ({transaction_id, summary}, a file_preview event).
func show_live(event: Dictionary) -> void:
	var summary := str(event.get("summary", "File changes"))
	set_live({"id": str(event.get("transaction_id", "")), "summary": summary})
	refresh()


## A parallel task's change passed its checks, was decided, or its decision failed.
func _on_task_event(event: Dictionary) -> void:
	var id := str(event.get("transaction_id", ""))
	match str(event.get("kind", "")):
		"file_preview":
			_task_ids[id] = true
		"file_decision":
			_task_ids.erase(id)
			_busy.erase(id)
			if event.get("accepted", false):
				var paths := PackedStringArray()
				for change: Variant in event.get("changes", []):
					paths.append(str((change as Dictionary).get("path", "")))
				changes_applied.emit(id, paths, event.get("changes", []))
		"notice":
			if _busy.has(id):
				_busy.erase(id)
				_errors[id] = str(event.get("text", ""))
		_:
			return
	refresh()
	_render()


## Show the list with the files of the task change [param id] open under its row.
func show_task(id: String) -> void:
	_open_files[id] = true
	show_list()
	refresh()
	_render()


func _list_after_result() -> void:
	await get_tree().create_timer(UI_MOTION.duration("confirm")).timeout
	var pending: bool = _detail != null and bool(_detail.get("pending"))
	if showing_detail and not pending:
		show_list()


## The change Journeyman is showing for review: {id, summary, tools?, created?}. Its row is
## first under Waiting for you and opens the detail view.
func set_live(entry: Dictionary) -> void:
	_live = entry.duplicate()
	_live["live"] = true
	if not _live.has("created"):
		_live["created"] = Time.get_unix_time_from_system()
	_busy.erase(str(_live.get("id", "")))
	_errors.erase(str(_live.get("id", "")))
	_render()


func clear_live() -> void:
	if _live.is_empty():
		return
	_busy.erase(str(_live.get("id", "")))
	_live = {}
	_render()


## The editor's decision on the live change failed: its row takes the decision again.
func live_failed(message: String) -> void:
	var id := str(_live.get("id", ""))
	_busy.erase(id)
	if not message.is_empty():
		_errors[id] = message
	_render()


## Read the list again (`joinery transactions list`). A read already running is followed by
## one more, so nothing staged meanwhile is missed.
func refresh() -> void:
	if not cli_enabled:
		return
	if _jobs.has("list"):
		_list_again = true
		return
	var args := SLASH._base_args()
	args.append_array(["transactions", "list", "--project", SLASH._project()])
	if not _start("list", args, _on_listed):
		_show_load_error()


## Show a listing as `joinery transactions list` prints it: {transactions: [{id, state,
## summary, revertible, created, decided, tools, files, file_count}], art_bible: {state, by?}}.
func apply_listing(data: Dictionary) -> void:
	_listing = data
	_loaded = true
	_load_error.hide()
	# A row whose decision has landed in the history is no longer busy.
	for entry: Dictionary in _transactions():
		var id := str(entry.get("id", ""))
		var state := str(entry.get("state", ""))
		var busy := str(_busy.get(id, ""))
		if (
			(busy in ["accepting", "discarding"] and state != "review")
			or (busy == "reverting" and state != "accepted")
		):
			_busy.erase(id)
	_render()


## The number of things waiting: pending changes, and the art bible when it needs approval.
func waiting_count() -> int:
	var count := _pending().size()
	if str(_bible().get("state", "")) == "needs_approval":
		count += 1
	return count


## The tab title for [param count] things waiting: "Review" or "Review 2".
static func tab_title(count: int) -> String:
	return "Review" if count <= 0 else "Review %d" % count


## A revert started from this tab finished.
func revert_finished(transaction_id: String, ok: bool, message := "") -> void:
	_busy.erase(transaction_id)
	if ok:
		_errors.erase(transaction_id)
	else:
		_errors[transaction_id] = (
			"Couldn't revert. " + message if not message.is_empty() else "Couldn't revert."
		)
	_render()
	refresh()


## "Blender · 2 min ago": the apps that made a change and when, from [param entry].
static func meta_text(entry: Dictionary, now: float) -> String:
	var parts: Array[String] = []
	var apps := app_names(entry.get("tools", []))
	if not apps.is_empty():
		parts.append(", ".join(apps))
	var when := relative_time(float(entry.get("created", 0.0)), now)
	if not when.is_empty():
		parts.append(when)
	return " · ".join(parts)


## Display names for recorded app ids, without repeats.
static func app_names(tools: Variant) -> Array[String]:
	var names: Array[String] = []
	if not tools is Array:
		return names
	for tool: Variant in tools:
		var id := str(tool)
		var app := str(APP_NAMES.get(id, id.replace("-", " ").capitalize()))
		if not app.is_empty() and not names.has(app):
			names.append(app)
	return names


## "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then the date ("12 Oct").
static func relative_time(stamp: float, now: float) -> String:
	return WORDS.relative_time(stamp, now)


# --- Building ----------------------------------------------------------------------------------


func _build_list() -> void:
	_list_view = VBoxContainer.new()
	_list_view.theme_type_variation = "JoineryTight"
	_list_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_list_view)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	scroll.accessibility_name = "Approvals"
	UI_MOTION.rubber_band(scroll)
	_list_view.add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	_content = VBoxContainer.new()
	_content.theme_type_variation = "JoineryStack"
	_content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(_content)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	_loading.add_child(dots)
	_loading.add_child(UI.label("Reading your changes…", "JoineryCaption"))
	UI.bind_height(_loading, "md", false)
	_content.add_child(_loading)
	_load_error = UI.label(
		"Couldn't read the list of changes. Details are in Output.", "JoineryCaption", true
	)
	_load_error.hide()
	_content.add_child(_load_error)

	var waiting := UI.section("Waiting for you")
	_content.add_child(waiting)
	_bible_waiting = _slot(waiting)
	_waiting_body = waiting.body
	_waiting_empty = UI.label(EMPTY_WAITING, "JoineryCaption")
	_waiting_empty.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(_waiting_empty, "md", false)
	waiting.add_child(_waiting_empty)

	_bible_row = HBoxContainer.new()
	_bible_row.theme_type_variation = "JoineryRow"
	_bible_row.hide()
	UI.bind_height(_bible_row, "md", false)
	var bible_title := UI.label("Art bible")
	bible_title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_bible_row.add_child(bible_title)
	_bible_dot = UI.status_dot("off")
	_bible_row.add_child(_bible_dot)
	_bible_status = UI.label("", "JoineryCaption")
	_bible_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_bible_status.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_bible_row.add_child(_bible_status)
	_bible_open = UI.quiet("Open")
	_bible_open.tooltip_text = "Open the art direction (/art)"
	_bible_open.accessibility_name = "Open the art bible"
	_bible_open.pressed.connect(func() -> void: art_requested.emit())
	_bible_row.add_child(_bible_open)

	var decided := UI.section("Decided")
	_content.add_child(decided)
	_bible_decided = _slot(decided)
	_bible_decided.add_child(_bible_row)
	_decided_body = decided.body
	_decided_empty = UI.label(EMPTY_DECIDED, "JoineryCaption")
	UI.bind_height(_decided_empty, "md", false)
	decided.add_child(_decided_empty)
	_decided_more = UI.label(
		"Showing the latest %d. Older changes stay in the project history." % DECIDED_ROWS,
		"JoineryFaint",
		true
	)
	_decided_more.hide()
	decided.add_child(_decided_more)


## A slot for the bible row in [param section], between its title and its rows.
func _slot(section: Control) -> VBoxContainer:
	var slot := VBoxContainer.new()
	slot.theme_type_variation = "JoineryFlush"
	section.add_child(slot)
	section.move_child(slot, 1)
	return slot


# --- Rendering ---------------------------------------------------------------------------------


func _transactions() -> Array[Dictionary]:
	var rows: Array[Dictionary] = []
	for entry: Variant in _listing.get("transactions", []):
		if entry is Dictionary:
			rows.append(entry)
	return rows


func _bible() -> Dictionary:
	var bible: Variant = _listing.get("art_bible", {})
	return bible if bible is Dictionary else {}


## Pending changes, the live one first, then oldest first (the order they were made).
func _pending() -> Array[Dictionary]:
	var rows: Array[Dictionary] = []
	var live_id := str(_live.get("id", ""))
	var live_row := _live.duplicate()
	for entry: Dictionary in _transactions():
		if str(entry.get("state", "")) != "review":
			continue
		if str(entry.get("id", "")) == live_id:
			# The history knows its apps and files; the editor knows it is on screen.
			live_row = entry.duplicate()
			live_row.merge(_live, true)
			var live_tools: Array = _live.get("tools", [])
			if live_tools.is_empty():
				live_row["tools"] = entry.get("tools", [])
			continue
		rows.append(entry)
	rows.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool:
			return float(a.get("created", 0.0)) < float(b.get("created", 0.0))
	)
	if not live_row.is_empty():
		rows.push_front(live_row)
	return rows


## Accepted, discarded and reverted changes, newest decision first.
func _decided() -> Array[Dictionary]:
	var rows: Array[Dictionary] = []
	for entry: Dictionary in _transactions():
		if OUTCOMES.has(str(entry.get("state", ""))):
			rows.append(entry)
	var when := func(entry: Dictionary) -> float:
		var decided := float(entry.get("decided", 0.0))
		return decided if decided > 0.0 else float(entry.get("created", 0.0))
	rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return when.call(a) > when.call(b))
	return rows


func _render() -> void:
	if _content == null:
		return
	var focus_key := _focused_key()
	var now := Time.get_unix_time_from_system()
	var pending := _pending()
	_loading.visible = not _loaded and _live.is_empty()
	for child: Node in _waiting_body.get_children():
		_waiting_body.remove_child(child)
		child.queue_free()
	for entry: Dictionary in pending:
		_waiting_body.add_child(_pending_row(entry, now))
	_render_bible()
	_waiting_empty.visible = pending.is_empty() and _loaded and not _bible_waiting_now()
	for child: Node in _decided_body.get_children():
		_decided_body.remove_child(child)
		child.queue_free()
	var decided := _decided()
	for index: int in mini(decided.size(), DECIDED_ROWS):
		_decided_body.add_child(_decided_row(decided[index], now))
	_decided_empty.visible = (
		_loaded and decided.is_empty() and not (_bible_row.visible and not _bible_waiting_now())
	)
	_decided_more.visible = decided.size() > DECIDED_ROWS
	_tint()
	_restore_focus(focus_key)
	var count := waiting_count()
	if count != _count:
		_count = count
		_show_count()
		count_changed.emit(count)


## The tab reads "Review 2" while two things wait.
func _show_count() -> void:
	_pin()
	var title := tab_title(_count)
	var dock := get_parent() as Control
	if dock != null and dock.is_class("EditorDock"):
		dock.set("title", title)
	elif dock != null and dock.get_parent() is TabContainer:
		var tabs := dock.get_parent() as TabContainer
		var index := tabs.get_tab_idx_from_control(dock)
		if index >= 0:
			tabs.set_tab_title(index, title)
	if _legacy_button != null:
		_legacy_button.text = title


func _bible_waiting_now() -> bool:
	return str(_bible().get("state", "")) == "needs_approval"


func _render_bible() -> void:
	var state := str(_bible().get("state", "missing"))
	_bible_row.visible = _loaded and state != "missing"
	var slot := _bible_waiting if state == "needs_approval" else _bible_decided
	if _bible_row.get_parent() != slot:
		_bible_row.reparent(slot, false)
	match state:
		"approved":
			var at_creation := str(_bible().get("by", "")) == "creation"
			_bible_status.text = "Approved at creation" if at_creation else "Approved by you"
			_bible_dot.set("state", "ready")
			_bible_open.theme_type_variation = "JoineryQuietButton"
			_bible_open.text = "Open"
		"needs_approval":
			_bible_status.text = "Needs your approval before new art or audio"
			_bible_dot.set("state", "attention")
			_bible_open.theme_type_variation = "JoinerySecondaryButton"
			_bible_open.text = "Review"
	_bible_open.accessibility_name = _bible_open.text + " the art bible"
	_bible_dot.tooltip_text = _bible_status.text


func _pending_row(entry: Dictionary, now: float) -> VBoxContainer:
	var id := str(entry.get("id", ""))
	var live := bool(entry.get("live", false))
	var summary := str(entry.get("summary", "")).strip_edges()
	if summary.is_empty():
		summary = "Project changes"
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryFlush"
	box.set_meta("transaction_id", id)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	UI.bind_height(line, "md", false)
	box.add_child(line)
	var open := UI.row_button(summary, "disclosure_closed")
	open.name = "Open"
	open.set_meta("focus_key", id + ":open")
	open.tooltip_text = (
		"Show the before and after" if live else "Show the files this change touches"
	)
	open.accessibility_name = summary + (", show the review" if live else ", show its files")
	if not live and _open_files.has(id):
		UI.bind_icon(open, "disclosure_open")
	# The chevron leads the summary, like every disclosure, so it lines up with the glyphs of
	# the decided rows instead of floating beside the caption.
	open.icon_alignment = HORIZONTAL_ALIGNMENT_LEFT
	open.pressed.connect(_on_open.bind(id, live))
	line.add_child(open)
	var meta := UI.label(meta_text(entry, now), "JoineryNumeric")
	meta.name = "Meta"
	meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(meta)
	var busy := str(_busy.get(id, ""))
	if not busy.is_empty():
		var working := HBoxContainer.new()
		working.theme_type_variation = "JoineryRowTight"
		working.name = "Working"
		var dots := INDICATOR.new()
		dots.running = true
		working.add_child(dots)
		working.add_child(
			UI.label("Accepting…" if busy == "accepting" else "Discarding…", "JoineryCaption")
		)
		working.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.add_child(working)
	else:
		var discard := UI.secondary("Discard")
		discard.name = "Discard"
		discard.set_meta("focus_key", id + ":discard")
		discard.tooltip_text = "Throw this change away. Your files stay as they are."
		discard.accessibility_name = "Discard " + summary
		discard.pressed.connect(_decide.bind(id, false, live))
		var accept := UI.primary("Accept")
		accept.name = "Accept"
		accept.set_meta("focus_key", id + ":accept")
		accept.tooltip_text = "Keep this change"
		accept.accessibility_name = "Accept " + summary
		accept.pressed.connect(_decide.bind(id, true, live))
		var ordered := (
			[accept, discard] if DisplayServer.get_swap_cancel_ok() else [discard, accept]
		)
		for button: Button in ordered:
			line.add_child(button)
	_add_error(box, id)
	if not live and _open_files.has(id):
		box.add_child(_files(entry))
	return box


func _decided_row(entry: Dictionary, now: float) -> VBoxContainer:
	var id := str(entry.get("id", ""))
	var state := str(entry.get("state", ""))
	var outcome: Array = OUTCOMES[state]
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryFlush"
	box.set_meta("transaction_id", id)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	UI.bind_height(line, "md", false)
	box.add_child(line)
	var glyph := TextureRect.new()
	glyph.name = "Glyph"
	glyph.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	glyph.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	glyph.mouse_filter = Control.MOUSE_FILTER_IGNORE
	glyph.set_meta("role", str(outcome[2]))
	UI.bind_icon(glyph, str(outcome[1]), "texture")
	line.add_child(glyph)
	var summary := str(entry.get("summary", "")).strip_edges()
	var title := UI.label(summary if not summary.is_empty() else "Project changes")
	title.name = "Summary"
	title.tooltip_text = title.text
	title.mouse_filter = Control.MOUSE_FILTER_PASS
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(title)
	var decided := float(entry.get("decided", 0.0))
	var when := relative_time(decided if decided > 0.0 else float(entry.get("created", 0.0)), now)
	var words := str(outcome[0]) + (" · " + when if not when.is_empty() else "")
	var meta := UI.label(words, "JoineryNumeric")
	meta.name = "Meta"
	meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(meta)
	if state == "accepted":
		if str(_busy.get(id, "")) == "reverting":
			var working := HBoxContainer.new()
			working.theme_type_variation = "JoineryRowTight"
			working.name = "Working"
			var dots := INDICATOR.new()
			dots.running = true
			working.add_child(dots)
			working.add_child(UI.label("Reverting…", "JoineryCaption"))
			working.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			line.add_child(working)
		elif bool(entry.get("revertible", true)):
			var revert := UI.quiet("Revert")
			revert.name = "Revert"
			revert.set_meta("focus_key", id + ":revert")
			revert.tooltip_text = "Put back the files from before this change"
			revert.accessibility_name = "Revert " + title.text
			revert.pressed.connect(_on_revert.bind(id))
			line.add_child(revert)
		else:
			var old := UI.label(TOO_OLD, "JoineryFaint")
			old.name = "TooOld"
			old.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
			old.tooltip_text = "Its restore data was cleared. Ask Journeyman to change it back."
			old.mouse_filter = Control.MOUSE_FILTER_PASS
			old.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			line.add_child(old)
	_add_error(box, id)
	return box


func _add_error(box: VBoxContainer, id: String) -> void:
	var text := str(_errors.get(id, ""))
	if text.is_empty():
		return
	var error := UI.label(text, "JoineryError", true)
	error.name = "Error"
	box.add_child(error)


## The files a change touches, one per line ("+ art/models/hammer.glb"), under its row.
func _files(entry: Dictionary) -> Label:
	var lines: Array[String] = []
	var marks := {"created": "+", "deleted": "−", "modified": "~"}
	for file: Variant in entry.get("files", []):
		if file is Dictionary:
			lines.append(
				"%s %s" % [marks.get(str(file.get("change", "")), "~"), str(file.get("path", ""))]
			)
	var more := int(entry.get("file_count", lines.size())) - lines.size()
	if more > 0:
		lines.append("and %d more" % more)
	if lines.is_empty():
		lines.append("No file list for this change.")
	var label := UI.label("\n".join(lines), "JoineryCaption", true)
	label.name = "Files"
	return label


func _tint() -> void:
	if not is_inside_tree():
		return
	for glyph: Node in find_children("Glyph", "TextureRect", true, false):
		var rect := glyph as TextureRect
		rect.self_modulate = UI_THEME.color(rect, str(rect.get_meta("role", "text_muted")))


func _focused_key() -> String:
	var viewport := get_viewport() if is_inside_tree() else null
	var owner_control := viewport.gui_get_focus_owner() if viewport != null else null
	if owner_control != null and is_ancestor_of(owner_control):
		return str(owner_control.get_meta("focus_key", ""))
	return ""


func _restore_focus(key: String) -> void:
	if key.is_empty():
		return
	var candidates: Array[Node] = _content.find_children("*", "Button", true, false)
	for node: Node in candidates:
		if str(node.get_meta("focus_key", "")) == key and not node.is_queued_for_deletion():
			(node as Control).grab_focus.call_deferred()
			return
	# The focused button went away (its row was decided): keep focus in the list.
	for node: Node in candidates:
		if not node.is_queued_for_deletion() and (node as Control).focus_mode != FOCUS_NONE:
			(node as Control).grab_focus.call_deferred()
			return


# --- Actions -----------------------------------------------------------------------------------


func _on_open(id: String, live: bool) -> void:
	if live:
		show_detail()
		return
	if _open_files.has(id):
		_open_files.erase(id)
	else:
		_open_files[id] = true
	_render()


func _decide(id: String, accepted: bool, live: bool) -> void:
	if _busy.has(id):
		return
	# Optimistic: the row says what is happening at once.
	_busy[id] = "accepting" if accepted else "discarding"
	_errors.erase(id)
	_render()
	decision_requested.emit(id, accepted, live)
	if _task_ids.has(id) and _chat != null:
		_chat.call("decide_files", id, accepted)
		return
	if live:
		# Decided exactly as the full review's own buttons would.
		if _detail != null and _detail.has_method("decide"):
			_detail.call("decide", accepted)
		return
	if not cli_enabled:
		return
	var args := SLASH._base_args()
	args.append_array(
		[
			"transactions",
			"accept" if accepted else "reject",
			"--project",
			SLASH._project(),
			"--id",
			id
		]
	)
	if not _start("decide:" + id, args, _on_decided.bind(id, accepted)):
		_busy.erase(id)
		_errors[id] = "Couldn't start Joinery. Is uv installed?"
		_render()


func _on_revert(id: String) -> void:
	if _busy.has(id):
		return
	_busy[id] = "reverting"
	_errors.erase(id)
	_render()
	revert_requested.emit(id)
	if _slash != null:
		_slash.call("revert", id)


# --- CLI ---------------------------------------------------------------------------------------


func _start(slot: String, args: PackedStringArray, callback: Callable) -> bool:
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.call("start", "uv", args) != OK:
		return false
	_jobs[slot] = [job, callback]
	set_process(true)
	return true


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		if not _jobs.has(slot):
			continue
		var result: Dictionary = (_jobs[slot][0] as RefCounted).call("poll")
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		callback.call(result)
	if _jobs.is_empty():
		set_process(false)


func _on_listed(result: Dictionary) -> void:
	var data := SLASH.parse_json(str(result.get("output", "")))
	if int(result.get("code", 1)) != 0 or not data.has("transactions"):
		print("[Joinery] transactions list\n", result.get("output", ""), result.get("error", ""))
		_show_load_error()
	else:
		apply_listing(data)
	if _list_again:
		_list_again = false
		refresh()


func _show_load_error() -> void:
	_loaded = true
	_render()
	_load_error.show()
	_loading.hide()


func _on_decided(result: Dictionary, id: String, accepted: bool) -> void:
	var output := str(result.get("output", ""))
	var data := SLASH.parse_json(output)
	if int(result.get("code", 1)) != 0:
		_busy.erase(id)
		var why := str(data.get("error", "")) if data.has("error") else ""
		_errors[id] = (
			why if not why.is_empty() else "It couldn't be decided. Details are in Output."
		)
		if why.is_empty():
			print("[Joinery] transactions\n", output, "\n", result.get("error", ""))
		_render()
		refresh()
		return
	if accepted:
		var paths := PackedStringArray()
		var changes: Array = data.get("changes", [])
		for change: Variant in changes:
			if change is Dictionary:
				paths.append(str(change.get("path", "")))
		changes_applied.emit(id, paths, changes)
		if _slash != null and _slash.has_method("check_revertible"):
			# The chat's change cards learn which changes can still be reverted.
			_slash.call("check_revertible")
	elif not str(data.get("warning", "")).is_empty():
		# A scene still uses a file only the discarded change would have added.
		_errors[id] = str(data["warning"])
	refresh()
