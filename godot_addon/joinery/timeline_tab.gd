@tool
extends VBoxContainer
## The Timeline tab in the editor's bottom panel: how the project changed, newest first.
##
## Every accepted Journeyman change, and every revert, is one git commit in the project's own
## repository (joinery/timeline.py). Each shows here as one line: what changed, who, how many
## files, when. The filter box narrows the list; entries load 50 at a time. Clicking one opens
## its files (timeline_entry.gd) with Restore this file as it was, which stages an ordinary
## reviewed change in the Review tab.
##
## Named Timeline because Godot's own History dock is the undo history. A project without git
## offers "Start keeping a timeline"; nothing starts without that click. A scene edit accepted
## live is not on disk until the scene is saved, so it joins the timeline on that save.
##
## The list comes from `joinery timeline list` (command_job.gd), read when the tab is shown and
## when the editor gets focus back while it is shown; it never polls.
##
## A project shared with teammates (`joinery team connect`, ADR 0071) has Pull and Push beside
## the filter, with how many changes each would move; the remote is asked when the tab is shown
## (at most once a minute). A teammate's entry names them; the last pull's entries carry a dot.
## What a pull or push did shows under the bar, with Review when a choice waits there.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const COMMAND := preload("command_job.gd")
const REVIEW_TAB := preload("review_tab.gd")
const ENTRY := preload("timeline_entry.gd")
const SELF_PATH := "res://addons/joinery/timeline_tab.gd"
const FILTER_DELAY := 0.25
const OFFER_TITLE := "Keep a timeline of this project"
const OFFER_TEXT := (
	"Every change you accept becomes an entry here, so you can see how the game changed "
	+ "and put any file back. Git keeps it, in this project's folder."
)
const PENDING_SCENES := "Scene edits you accepted join the timeline when you save the scene."
## The remote is asked again when the tab is shown this long after the last time, in seconds.
const FETCH_EVERY := 60.0

## Off in tests: nothing runs the CLI; data comes through apply_listing and the entry view.
var cli_enabled := true
## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var _plugin: EditorPlugin
var _list_view: VBoxContainer
var _entry: ENTRY
var _filter: LineEdit
var _filter_timer: Timer
var _content: VBoxContainer
var _loading: HBoxContainer
var _load_error: Label
var _offer: VBoxContainer
var _offer_title: Label
var _offer_text: Label
var _offer_button: Button
var _note: Label
var _rows: VBoxContainer
var _empty: Label
var _more: Button
## The last `timeline list` answer, without its entries: {state, message, auto_commit}.
var _status: Dictionary = {}
var _entries: Array[Dictionary] = []
var _has_more := false
var _loaded := false
## Scenes with accepted live edits not saved yet: {res path: [summaries]}.
var _unsaved: Dictionary = {}
var _jobs: Dictionary = {}
## Sharing with teammates, from the listing and `team status --fetch`: {connected, ahead,
## behind, conflicts, last_fetch}.
var _team: Dictionary = {}
var _sync: HBoxContainer
var _pull: Button
var _push: Button
var _sync_note: HBoxContainer
var _sync_text: Label
var _sync_review: Button
var _fetched_at := -1.0e9


## Add the Timeline tab to [param plugin]'s bottom panel, after Review. It removes itself when
## the plugin leaves the tree, so plugin.gd needs only this one call.
static func install(plugin: EditorPlugin) -> Control:
	var tab: Control = load(SELF_PATH).new()
	tab.set("_plugin", plugin)
	# A rail page in Joinery's layout; a bottom panel tab in the classic one.
	var rail: bool = (
		plugin.has_method("add_rail_page")
		and plugin.call("add_rail_page", "timeline", "Timeline", tab, {"editor": "History"})
	)
	if not rail:
		plugin.add_control_to_bottom_panel(tab, "Timeline")
	plugin.scene_saved.connect(tab.scene_saved)
	var edits: Variant = plugin.get("_edits")
	if edits is Object and (edits as Object).has_signal("accepted"):
		(edits as Object).connect("accepted", tab.live_edit_accepted)
	plugin.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(tab):
				if rail:
					plugin.call("remove_rail_page", tab)
				else:
					plugin.remove_control_from_bottom_panel(tab)
				tab.queue_free(),
		CONNECT_ONE_SHOT
	)
	return tab


func _init() -> void:
	name = "Timeline"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Timeline: how this project changed, newest first"
	_build_list()
	_entry = ENTRY.new()
	_entry.hide()
	_entry.back.connect(show_list)
	_entry.file_requested.connect(_read_file)
	_entry.restore_requested.connect(_restore)
	_entry.open_requested.connect(_open_file)
	_entry.review_requested.connect(_show_review)
	add_child(_entry)
	_filter_timer = Timer.new()
	_filter_timer.one_shot = true
	_filter_timer.wait_time = FILTER_DELAY
	_filter_timer.timeout.connect(refresh)
	add_child(_filter_timer)
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
			if is_visible_in_tree() and not _entry.visible:
				refresh()
		NOTIFICATION_APPLICATION_FOCUS_IN:
			# A commit made in a terminal shows up when the user comes back to the editor.
			if is_visible_in_tree() and not _entry.visible:
				refresh()


# --- Public ------------------------------------------------------------------------------------


## Read the first page again (`joinery timeline list`), with the filter text.
func refresh() -> void:
	if not cli_enabled:
		return
	_cancel("list")
	var args := ["timeline", "list", "--project", _project(), "--json"]
	if not _filter.text.strip_edges().is_empty():
		args.append_array(["--filter", _filter.text.strip_edges()])
	if not _start("list", args, apply_listing.bind(false)):
		_show_load_error()


## Show a page as `joinery timeline list` prints it: {state, message, auto_commit, entries:
## [{commit, short, subject, author, time, files}], more}. [param append] adds an older page.
func apply_listing(data: Dictionary, append := false) -> void:
	if data.has("error") or not data.has("state"):
		_show_load_error(str(data.get("error", "")))
		return
	_loaded = true
	_load_error.hide()
	_status = {
		"state": str(data.get("state", "")),
		"message": str(data.get("message", "")),
		"auto_commit": bool(data.get("auto_commit", true)),
	}
	var team: Variant = data.get("team", {})
	if team is Dictionary and not append:
		apply_team(team)
	if not append:
		_entries.clear()
	for entry: Variant in data.get("entries", []):
		if entry is Dictionary:
			_entries.append(entry)
	_has_more = bool(data.get("more", false))
	_render(append)


## Show where sharing stands ({connected, ahead, behind, conflicts}); asks the remote when
## it has not been asked for a minute.
func apply_team(team: Dictionary) -> void:
	_team = team
	_render_sync()
	var now := Time.get_ticks_msec() / 1000.0
	if cli_enabled and bool(team.get("connected", false)) and now - _fetched_at > FETCH_EVERY:
		_fetched_at = now
		_start("team", ["team", "status", "--project", _project(), "--fetch", "--json"], _on_team)


## What a pull or push answered: its sentence, or why it failed. [param review] shows Review.
func show_sync_result(text: String, failed := false, review := false) -> void:
	_sync_text.text = text
	_sync_text.theme_type_variation = "JoineryError" if failed else "JoineryCaption"
	_sync_review.visible = review
	_sync_note.visible = not text.is_empty()
	if not failed and not text.is_empty():
		UI_MOTION.pulse(_sync_note)


## Back from an entry to the list.
func show_list() -> void:
	var arriving := not _list_view.visible
	_entry.hide()
	_list_view.show()
	if arriving:
		UI_MOTION.fade_in(_list_view)


## Open [param summary] (a list entry) and read its files.
func open_entry(summary: Dictionary) -> void:
	_entry.set_loading(summary)
	_list_view.hide()
	UI_MOTION.fade_in(_entry)
	if cli_enabled:
		var commit := str(summary.get("commit", ""))
		var args := ["timeline", "show", "--project", _project(), "--commit", commit, "--json"]
		_start("show", args, _on_shown)


## The entry view, for tests and the visual suite.
func entry_view() -> ENTRY:
	return _entry


## A live scene edit was accepted (scene_edits.gd): it reaches the timeline on save.
func live_edit_accepted(scene_path: String, summary: String) -> void:
	var summaries: Array = _unsaved.get_or_add(scene_path, [])
	summaries.append(summary)
	_render()


## The editor saved [param path]: commit the live edits accepted in it.
func scene_saved(path: String) -> void:
	# A preview made in an empty editor has no file yet: its first save names it.
	var key := path if _unsaved.has(path) else ""
	if not _unsaved.has(key):
		return
	var summaries: Array = _unsaved[key]
	_unsaved.erase(key)
	_render()
	if not cli_enabled or not path.begins_with("res://"):
		return
	var args := [
		"timeline",
		"record",
		"--project",
		_project(),
		"--path",
		path.trim_prefix("res://"),
		"--message",
		"\n".join(summaries),
		"--json",
	]
	_start("record:" + path, args, func(_result: Dictionary) -> void: refresh())


## "Pull 2" / "Push": a sync button's words for [param count] changes to move.
static func sync_text(word: String, count: int) -> String:
	return word if count <= 0 else "%s %d" % [word, count]


## "Ada Lovelace · 3 files · 2 h ago" for a list entry.
static func meta_text(entry: Dictionary, now: float) -> String:
	var parts: Array[String] = []
	var author := str(entry.get("author", ""))
	if not author.is_empty():
		parts.append(author)
	var files := int(entry.get("files", 0))
	parts.append("%d file%s" % [files, "" if files == 1 else "s"])
	var when := REVIEW_TAB.relative_time(float(entry.get("time", 0)), now)
	if not when.is_empty():
		parts.append(when)
	return " · ".join(parts)


# --- Building ----------------------------------------------------------------------------------


func _build_list() -> void:
	_list_view = VBoxContainer.new()
	_list_view.theme_type_variation = "JoineryTight"
	_list_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_list_view)
	var gutter_bar := MarginContainer.new()
	gutter_bar.theme_type_variation = "JoineryGutter"
	_list_view.add_child(gutter_bar)
	var bar := HBoxContainer.new()
	bar.theme_type_variation = "JoineryRow"
	gutter_bar.add_child(bar)
	_filter = LineEdit.new()
	_filter.name = "Filter"
	_filter.placeholder_text = "Filter by change, file or person"
	_filter.accessibility_name = "Filter the timeline"
	_filter.clear_button_enabled = true
	_filter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(_filter, "md")
	_filter.text_changed.connect(func(_text: String) -> void: _filter_timer.start())
	_filter.text_submitted.connect(
		func(_text: String) -> void:
			_filter_timer.stop()
			refresh()
	)
	bar.add_child(_filter)
	_build_sync(bar)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	scroll.accessibility_name = "Timeline entries"
	UI_MOTION.rubber_band(scroll)
	_list_view.add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	_content = VBoxContainer.new()
	_content.theme_type_variation = "JoineryTight"
	_content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(_content)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	_loading.add_child(dots)
	_loading.add_child(UI.label("Reading the timeline…", "JoineryCaption"))
	UI.bind_height(_loading, "md", false)
	_content.add_child(_loading)
	_load_error = UI.label("", "JoineryError", true)
	_load_error.name = "LoadError"
	_load_error.hide()
	_content.add_child(_load_error)
	_build_offer()
	_build_sync_note()
	_note = UI.label("", "JoineryCaption", true)
	_note.name = "Note"
	_note.hide()
	_content.add_child(_note)
	_rows = VBoxContainer.new()
	_rows.name = "Rows"
	_rows.theme_type_variation = "JoineryFlush"
	_content.add_child(_rows)
	_empty = UI.label("", "JoineryCaption")
	_empty.name = "Empty"
	UI.bind_height(_empty, "md", false)
	_empty.hide()
	_content.add_child(_empty)
	_more = UI.quiet("Show older entries")
	_more.name = "More"
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_more.hide()
	_more.pressed.connect(_load_more)
	_content.add_child(_more)


## The offer shown while there is no timeline: what it is, and the one button that starts it.
func _build_offer() -> void:
	_offer = VBoxContainer.new()
	_offer.name = "Offer"
	_offer.theme_type_variation = "JoineryTight"
	_offer.hide()
	_content.add_child(_offer)
	_offer_title = UI.label(OFFER_TITLE, "JoinerySection", true)
	_offer.add_child(_offer_title)
	_offer_text = UI.label(OFFER_TEXT, "JoineryCaption", true)
	_offer.add_child(_offer_text)
	_offer_button = UI.primary("Start keeping a timeline")
	_offer_button.name = "Start"
	_offer_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_offer_button.tooltip_text = "Sets up git in this project and makes the first entry"
	_offer_button.pressed.connect(_start_timeline)
	_offer.add_child(_offer_button)


## Pull and Push beside the filter, shown while the project is shared.
func _build_sync(bar: HBoxContainer) -> void:
	_sync = HBoxContainer.new()
	_sync.name = "Sync"
	_sync.theme_type_variation = "JoineryRowTight"
	_sync.hide()
	bar.add_child(_sync)
	_pull = UI.secondary("Pull")
	_pull.name = "Pull"
	_pull.tooltip_text = "Bring in your teammates' changes"
	_pull.pressed.connect(sync.bind("pull"))
	UI_MOTION.press_feedback(_pull)
	_sync.add_child(_pull)
	_push = UI.secondary("Push")
	_push.name = "Push"
	_push.tooltip_text = "Share the changes you accepted with your teammates"
	_push.pressed.connect(sync.bind("push"))
	UI_MOTION.press_feedback(_push)
	_sync.add_child(_push)


## What the last pull or push did, under the bar.
func _build_sync_note() -> void:
	_sync_note = HBoxContainer.new()
	_sync_note.name = "SyncNote"
	_sync_note.theme_type_variation = "JoineryRow"
	_sync_note.hide()
	_content.add_child(_sync_note)
	_sync_text = UI.label("", "JoineryCaption", true)
	_sync_text.name = "Text"
	_sync_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sync_note.add_child(_sync_text)
	_sync_review = UI.quiet("Review")
	_sync_review.name = "Review"
	_sync_review.tooltip_text = "Open the Review tab"
	_sync_review.hide()
	_sync_review.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_sync_review.pressed.connect(_show_review)
	_sync_note.add_child(_sync_review)


# --- Rendering ---------------------------------------------------------------------------------


## Pull and Push say what they would move; the one worth doing next is the primary button.
func _render_sync() -> void:
	if _sync == null:
		return
	var ready := str(_status.get("state", "")) == "ready"
	_sync.visible = ready and bool(_team.get("connected", false))
	var behind := int(_team.get("behind", 0))
	var ahead := int(_team.get("ahead", 0))
	var waiting := int(_team.get("conflicts", 0)) > 0
	var busy := str(_team.get("busy", ""))
	_pull.text = "Pulling…" if busy == "pull" else sync_text("Pull", behind)
	_push.text = "Pushing…" if busy == "push" else sync_text("Push", ahead)
	_pull.disabled = not busy.is_empty()
	_push.disabled = not busy.is_empty()
	_pull.theme_type_variation = (
		"JoineryPrimaryButton" if behind > 0 else "JoinerySecondaryButton"
	)
	_push.theme_type_variation = (
		"JoineryPrimaryButton"
		if ahead > 0 and behind == 0 and not waiting
		else "JoinerySecondaryButton"
	)
	_pull.accessibility_name = "Pull %d changes from your teammates" % behind
	_push.accessibility_name = "Push %d changes to your teammates" % ahead
	if waiting:
		_push.tooltip_text = "Choose between your version and your teammate's in Review first"
	else:
		_push.tooltip_text = "Share the changes you accepted with your teammates"


func _render(appended := false) -> void:
	if _rows == null:
		return
	var state := str(_status.get("state", ""))
	var ready := state == "ready"
	_loading.visible = not _loaded
	_filter.get_parent().visible = ready
	_render_offer(state)
	var notes: Array[String] = []
	if ready and not bool(_status.get("auto_commit", true)):
		notes.append("Accepted changes aren't added. Turn this on in Journeyman settings.")
	if not _unsaved.is_empty():
		notes.append(PENDING_SCENES)
	_note.text = "\n".join(notes)
	_note.visible = not notes.is_empty()
	_render_sync()
	var first_new := 0 if not appended else _rows.get_child_count()
	if not appended:
		for child: Node in _rows.get_children():
			_rows.remove_child(child)
			child.queue_free()
	var now := Time.get_unix_time_from_system()
	for index: int in range(first_new, _entries.size()):
		_rows.add_child(_row(_entries[index], now))
	if appended:
		UI_MOTION.stagger(_rows.get_children().slice(first_new))
	var filtered := not _filter.text.strip_edges().is_empty()
	_empty.visible = ready and _entries.is_empty()
	_empty.text = (
		"Nothing matches “%s”." % _filter.text.strip_edges()
		if filtered
		else "No entries yet. Accept a change and it shows here."
	)
	_more.visible = ready and _has_more


func _render_offer(state: String) -> void:
	_offer.visible = _loaded and state in ["none", "empty", "no-git", "nested"]
	_offer_button.visible = state in ["none", "empty"]
	_offer_button.disabled = _jobs.has("start")
	match state:
		"no-git":
			_offer_title.text = "Git isn't installed"
			_offer_text.text = str(_status.get("message", ""))
		"nested":
			_offer_title.text = "This project is part of a bigger repository"
			_offer_text.text = str(_status.get("message", ""))
		_:
			_offer_title.text = OFFER_TITLE
			_offer_text.text = OFFER_TEXT


func _row(entry: Dictionary, now: float) -> HBoxContainer:
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.set_meta("commit", str(entry.get("commit", "")))
	UI.bind_height(line, "md", false)
	var subject := str(entry.get("subject", "")).strip_edges()
	var open := UI.row_button(subject if not subject.is_empty() else "Project changes", "")
	open.name = "Open"
	open.tooltip_text = subject
	open.accessibility_name = subject + ", show its files"
	open.pressed.connect(open_entry.bind(entry))
	UI_MOTION.press_feedback(open)
	line.add_child(open)
	var meta := UI.label(meta_text(entry, now), "JoineryNumeric")
	meta.name = "Meta"
	meta.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(meta)
	if bool(entry.get("pulled", false)):
		# Arrived with the last pull: a dot before who and when, said in words for readers.
		var dot := UI.status_dot("working")
		dot.name = "Pulled"
		dot.tooltip_text = "New from your last pull"
		line.add_child(dot)
		line.move_child(dot, meta.get_index())
		open.accessibility_name = subject + ", new from your last pull, show its files"
	return line


func _show_load_error(why := "") -> void:
	_loaded = true
	_load_error.text = _why({"error": why}, "Couldn't read the timeline.")
	_render()
	_load_error.show()


# --- Actions -----------------------------------------------------------------------------------


## Pull or push ([param action]): the button says so at once, the list follows the result.
func sync(action: String) -> void:
	if not cli_enabled or _jobs.has("sync"):
		return
	_team["busy"] = action
	_render_sync()
	var args := ["team", action, "--project", _project(), "--json"]
	if not _start("sync", args, _on_synced.bind(action)):
		_team.erase("busy")
		_render_sync()
		show_sync_result("Couldn't start Joinery. Is uv installed?", true)


func _on_synced(data: Dictionary, action: String) -> void:
	_team.erase("busy")
	if data.has("error"):
		_render_sync()
		show_sync_result(_why(data, "Couldn't %s." % action), true)
		return
	var waiting := not str(data.get("review_id", "")).is_empty()
	waiting = waiting or not (data.get("documents", []) as Array).is_empty()
	waiting = waiting or not (data.get("reviews", {}).get("conflicting", []) as Array).is_empty()
	show_sync_result(str(data.get("message", "")), false, waiting)
	_fetched_at = -1.0e9
	refresh()
	if waiting:
		var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
		if review is Object and (review as Object).has_method("refresh"):
			(review as Object).call("refresh")


func _on_team(data: Dictionary) -> void:
	if data.has("error") or not data.has("connected"):
		return
	var conflicts: Variant = data.get("conflicts", [])
	apply_team(
		{
			"connected": bool(data.get("connected", false)),
			"ahead": int(data.get("ahead", 0)),
			"behind": int(data.get("behind", 0)),
			"conflicts": (conflicts as Array).size() if conflicts is Array else 0,
		}
	)
	if data.has("fetch_error"):
		show_sync_result(str(data["fetch_error"]), true)


func _load_more() -> void:
	if not cli_enabled or _jobs.has("list"):
		return
	var args := [
		"timeline", "list", "--project", _project(), "--json", "--skip", str(_entries.size())
	]
	if not _filter.text.strip_edges().is_empty():
		args.append_array(["--filter", _filter.text.strip_edges()])
	_start("list", args, apply_listing.bind(true))


func _start_timeline() -> void:
	if not cli_enabled or _jobs.has("start"):
		return
	if _start("start", ["timeline", "start", "--project", _project(), "--json"], _on_started):
		_render()


func _on_started(data: Dictionary) -> void:
	if data.has("error"):
		_show_load_error(_why(data, "Couldn't start the timeline."))
		return
	refresh()
	UI_MOTION.pulse(_content)


func _on_shown(data: Dictionary) -> void:
	if data.has("error"):
		var why := _why(data, "Couldn't read this entry.")
		_entry.show_entry({"subject": why, "files": []})
		return
	_entry.show_entry(data)


## The readable reason in a failed result, or [param fallback] pointing at Output.
static func _why(data: Dictionary, fallback: String) -> String:
	var text := str(data.get("error", ""))
	return text if not text.is_empty() else fallback + " Details are in Output."


func _read_file(commit: String, path: String) -> void:
	if not cli_enabled:
		return
	var args := [
		"timeline", "show", "--project", _project(), "--commit", commit, "--path", path, "--json"
	]
	_start("file:" + path, args, _on_file.bind(path))


func _on_file(data: Dictionary, path: String) -> void:
	if data.has("error"):
		_entry.file_failed(path, _why(data, "Couldn't read this file."))
	else:
		_entry.apply_file(data)


## Restore stages a reviewed change: nothing on disk changes until it is accepted in Review.
func _restore(commit: String, path: String) -> void:
	if not cli_enabled or _jobs.has("restore:" + path):
		return
	_entry.set_restore(path, "busy")
	var args := [
		"timeline", "restore", "--project", _project(), "--commit", commit, "--path", path, "--json"
	]
	if not _start("restore:" + path, args, _on_restored.bind(path)):
		_entry.set_restore(path, "failed", "Couldn't start Joinery. Is uv installed?")


func _on_restored(data: Dictionary, path: String) -> void:
	if data.has("error"):
		_entry.set_restore(path, "failed", _why(data, "Couldn't restore it."))
		return
	_entry.set_restore(path, "done")
	var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
	if review is Object and (review as Object).has_method("refresh"):
		(review as Object).call("refresh")


func _show_review() -> void:
	var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
	if review is Control:
		_plugin.make_bottom_panel_item_visible(review as Control)


func _open_file(path: String) -> void:
	var resource := "res://" + path
	if not FileAccess.file_exists(resource) or not Engine.is_editor_hint():
		return
	match path.get_extension().to_lower():
		"tscn", "scn":
			EditorInterface.open_scene_from_path(resource)
		"gd", "gdshader", "cs", "tres", "res", "md", "txt", "cfg", "json", "toml":
			var loaded := load(resource)
			if loaded != null:
				EditorInterface.edit_resource(loaded)
			else:
				EditorInterface.select_file(resource)
		_:
			EditorInterface.select_file(resource)


# --- CLI ---------------------------------------------------------------------------------------


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


func _start(slot: String, args: Array, callback: Callable) -> bool:
	_cancel(slot)
	var job: RefCounted = COMMAND.joinery(cli_prefix, args)
	if job == null:
		return false
	_jobs[slot] = [job, callback]
	set_process(true)
	return true


func _cancel(slot: String) -> void:
	if _jobs.has(slot):
		(_jobs[slot][0] as RefCounted).call("cancel")
		_jobs.erase(slot)


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		if not _jobs.has(slot):
			continue
		var result: Dictionary = (_jobs[slot][0] as RefCounted).call("poll")
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		var output := str(result.get("output", ""))
		var data := _parse(output)
		if int(result.get("code", 1)) != 0 and not data.has("error"):
			print("[Joinery] timeline\n", output, "\n", result.get("error", ""))
			data = {"error": ""}
		callback.call(data)
	if _jobs.is_empty():
		set_process(false)


static func _parse(output: String) -> Dictionary:
	var lines := output.strip_edges().split("\n", false)
	lines.reverse()
	for line: String in lines:
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			return parsed
	return {}
