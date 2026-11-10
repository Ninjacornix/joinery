extends SceneTree
## The bottom panel's Review tab, headless: loading and empty states, pending rows with Accept
## and Discard, the change on screen opening its full review, files on demand for the others,
## decided rows newest first with Revert only where it still works, the art bible row, and the
## count for the tab's badge.

const TAB := preload("res://addons/joinery/review_tab.gd")
const PANEL := preload("res://addons/joinery/review_panel.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")

var _failed := false
var _host: Control
var _counts: Array[int] = []
var _decisions: Array = []
var _reverts: Array[String] = []
var _art := 0


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for index: int in count:
		await process_frame


func _run() -> void:
	var cache := OS.get_cache_dir().path_join("joinery-review-tab-test")
	DirAccess.make_dir_recursive_absolute(cache)
	var config := cache.path_join("journeyman.cfg")
	DirAccess.remove_absolute(config)
	Engine.set_meta("joinery_ui_config", config)
	Engine.remove_meta("joinery_ui_prefs")
	UI_MOTION.set_enabled(false)
	root.size = Vector2i(900, 520)
	_host = Control.new()
	_host.size = Vector2(900, 520)
	root.add_child(_host)
	_check_wording()
	var tab: TAB = TAB.new()
	tab.cli_enabled = false
	tab.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	tab.count_changed.connect(func(count: int) -> void: _counts.append(count))
	tab.decision_requested.connect(
		func(id: String, accepted: bool, live: bool) -> void:
			_decisions.append([id, accepted, live])
	)
	tab.revert_requested.connect(func(id: String) -> void: _reverts.append(id))
	tab.art_requested.connect(func() -> void: _art += 1)
	var panel: PANEL = PANEL.new()
	tab.set_detail(panel)
	_host.add_child(tab)
	await _frames()
	_check(tab._loading.visible, "The first read shows that it is loading")
	_check(not tab._waiting_empty.visible, "No empty state before the list has been read")
	await _check_empty(tab)
	await _check_rows(tab)
	await _check_files(tab)
	await _check_discard(tab)
	await _check_revert(tab)
	await _check_bible(tab)
	await _check_live(tab, panel)
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	Engine.remove_meta("joinery_ui_prefs")
	await _frames()
	print("review tab: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_wording() -> void:
	_check(TAB.tab_title(0) == "Review", "No badge when nothing waits")
	_check(TAB.tab_title(2) == "Review 2", "The badge counts what waits")
	var now := 1_800_000_000.0
	_check(TAB.relative_time(now - 20, now) == "just now", "Seconds read just now")
	_check(TAB.relative_time(now - 300, now) == "5 min ago", "Minutes")
	_check(TAB.relative_time(now - 7200, now) == "2 h ago", "Hours")
	_check(TAB.relative_time(now - 90000, now) == "yesterday", "Yesterday")
	_check(TAB.relative_time(now - 3 * 86400, now) == "3 days ago", "Days")
	_check(not TAB.relative_time(now - 30 * 86400, now).contains("ago"), "Older ones show the date")
	_check(TAB.relative_time(0, now).is_empty(), "No time when none was recorded")
	var entry := {"tools": ["blender-ops", "blender", "godot"], "created": now - 120}
	_check(
		TAB.meta_text(entry, now) == "Blender, Godot · 2 min ago",
		"Apps by name, once each: " + TAB.meta_text(entry, now)
	)
	_check(TAB.meta_text({}, now).is_empty(), "No caption without apps or time")


func _listing(now: float) -> Dictionary:
	return {
		"transactions":
		[
			_tx("a1", "accepted", "Darken the door wood", now - 7500, now - 7300, true),
			_tx("a2", "accepted", "Add a crate", now - 900000, now - 899000, false),
			_tx("r1", "rejected", "Make the floor blue", now - 4000, now - 3600, true),
			_tx("v1", "reverted", "Widen the door", now - 200, now - 100, true),
			_tx("p1", "review", "Add online play", now - 600, 0.0, true, ["joinery"]),
			_tx("p2", "review", "Model a hammer", now - 60, 0.0, true, ["blender-ops"]),
			_tx("s1", "staging", "Still working", now - 10, 0.0, true),
		],
		"art_bible": {"state": "approved", "by": "creation"},
	}


func _tx(
	id: String,
	state: String,
	summary: String,
	created: float,
	decided: float,
	revertible: bool,
	tools: Array = []
) -> Dictionary:
	return {
		"id": id,
		"state": state,
		"summary": summary,
		"created": created,
		"decided": decided,
		"revertible": revertible,
		"tools": tools,
		"file_count": 3,
		"files":
		[
			{"path": "features/net/net.gd", "change": "created"},
			{"path": "project.godot", "change": "modified"},
		],
	}


func _rows(body: Control) -> Array[Node]:
	var rows: Array[Node] = []
	for node: Node in body.get_children():
		if not node.is_queued_for_deletion():
			rows.append(node)
	return rows


func _find(row: Node, name: String) -> Control:
	return row.find_child(name, true, false) as Control


func _check_empty(tab: TAB) -> void:
	tab.apply_listing({"transactions": [], "art_bible": {"state": "missing"}})
	await _frames()
	_check(not tab._loading.visible, "Loading ends with the list")
	_check(tab._waiting_empty.visible, "Empty waiting list says so")
	_check(tab._waiting_empty.text == "Nothing waits for your review.", "Plain empty wording")
	_check(tab._decided_empty.visible, "Empty history says so")
	_check(not tab._bible_row.visible, "No bible row without an art bible")
	_check(tab.waiting_count() == 0, "Nothing waits")


func _check_rows(tab: TAB) -> void:
	var now := Time.get_unix_time_from_system()
	tab.apply_listing(_listing(now))
	await _frames()
	_check(not tab._waiting_empty.visible, "Rows replace the empty state")
	var pending := _rows(tab._waiting_body)
	_check(pending.size() == 2, "Two changes wait (staging ones don't): %d" % pending.size())
	if pending.size() == 2:
		var first := pending[0]
		_check(str(first.get_meta("transaction_id")) == "p1", "Oldest waiting change first")
		var open := _find(first, "Open") as Button
		_check(open != null and open.text == "Add online play", "The row names the change")
		var meta := _find(first, "Meta") as Label
		_check(meta != null and meta.text == "Joinery · 10 min ago", "App and time: " + meta.text)
		var accept := _find(first, "Accept") as Button
		var discard := _find(first, "Discard") as Button
		_check(accept != null and discard != null, "Accept and Discard on the row")
		if accept != null:
			_check(accept.focus_mode == Control.FOCUS_ALL, "Accept is keyboard reachable")
			_check(accept.is_visible_in_tree(), "Accept is on screen")
		var second_meta := _find(pending[1], "Meta") as Label
		_check(second_meta.text == "Blender · 1 min ago", "Blender row: " + second_meta.text)
	_check(tab.waiting_count() == 2, "Count of waiting changes")
	_check(_counts.has(2), "The badge hears the count")
	var decided := _rows(tab._decided_body)
	var ids: Array[String] = []
	for row: Node in decided:
		ids.append(str(row.get_meta("transaction_id")))
	_check(ids == ["v1", "r1", "a1", "a2"], "Decided rows newest first: %s" % [ids])
	if decided.size() == 4:
		_check(_find(decided[0], "Meta").get("text").begins_with("Reverted"), "Reverted wording")
		_check(_find(decided[1], "Meta").get("text").begins_with("Discarded"), "Discarded wording")
		_check(_find(decided[2], "Meta").get("text") == "Accepted · 2 h ago", "Accepted and when")
		_check(_find(decided[2], "Revert") != null, "Revert offered while it still works")
		_check(_find(decided[3], "Revert") == null, "No Revert once its data is gone")
		_check(_find(decided[3], "TooOld") != null, "Too old to revert says so")
		for row: Node in [decided[0], decided[1]]:
			_check(_find(row, "Revert") == null, "Nothing to revert on a discarded or reverted row")
	_check(tab._bible_row.visible, "The art bible row shows")
	_check(tab._bible_status.text == "Approved at creation", "Approved at creation")
	_check(tab._bible_open.text == "Open", "An approved bible opens with Open")


func _check_files(tab: TAB) -> void:
	var row := _rows(tab._waiting_body)[0]
	_check(_find(row, "Files") == null, "Files stay closed until asked for")
	(_find(row, "Open") as Button).pressed.emit()
	await _frames()
	row = _rows(tab._waiting_body)[0]
	var files := _find(row, "Files") as Label
	_check(files != null, "Clicking a change shows its files")
	if files != null:
		_check(files.text.contains("+ features/net/net.gd"), "New files marked +")
		_check(files.text.contains("~ project.godot"), "Edited files marked ~")
		_check(files.text.contains("and 1 more"), "The rest are counted")
	_check(not tab.showing_detail, "A change not on screen keeps the list")
	(_find(row, "Open") as Button).pressed.emit()
	await _frames()
	_check(_find(_rows(tab._waiting_body)[0], "Files") == null, "Clicking again closes them")


func _check_discard(tab: TAB) -> void:
	var row := _rows(tab._waiting_body)[0]
	(_find(row, "Discard") as Button).pressed.emit()
	await _frames()
	_check(_decisions.has(["p1", false, false]), "Discard asks to reject that change")
	row = _rows(tab._waiting_body)[0]
	_check(_find(row, "Discard") == null, "The row stops offering a decision while it runs")
	var working := _find(row, "Working")
	_check(working != null and _label_text(working).contains("Discarding"), "It says Discarding…")
	var now := Time.get_unix_time_from_system()
	var listing := _listing(now)
	for entry: Dictionary in listing["transactions"]:
		if entry["id"] == "p1":
			entry["state"] = "rejected"
			entry["decided"] = now
	tab.apply_listing(listing)
	await _frames()
	_check(_rows(tab._waiting_body).size() == 1, "The discarded change leaves the waiting list")
	_check(
		str(_rows(tab._decided_body)[0].get_meta("transaction_id")) == "p1",
		"It heads the decided list"
	)
	_check(_counts.back() == 1, "The badge follows")


func _check_revert(tab: TAB) -> void:
	var row: Node
	for candidate: Node in _rows(tab._decided_body):
		if str(candidate.get_meta("transaction_id")) == "a1":
			row = candidate
	(_find(row, "Revert") as Button).pressed.emit()
	await _frames()
	_check(_reverts == ["a1"], "Revert asks for that change")
	tab.revert_finished("a1", false, "Cannot revert over newer manual edits.")
	await _frames()
	for candidate: Node in _rows(tab._decided_body):
		if str(candidate.get_meta("transaction_id")) == "a1":
			var error := _find(candidate, "Error") as Label
			_check(
				error != null and error.text.begins_with("Couldn't revert."), "Revert failure shown"
			)
			_check(_find(candidate, "Revert") != null, "Revert can be tried again")


func _check_bible(tab: TAB) -> void:
	var listing := _listing(Time.get_unix_time_from_system())
	listing["art_bible"] = {"state": "needs_approval"}
	tab.apply_listing(listing)
	await _frames()
	_check(tab._bible_status.text.begins_with("Needs your approval"), "Needs approval wording")
	_check(tab._bible_waiting.is_ancestor_of(tab._bible_row), "It waits with the changes")
	_check(not tab._waiting_empty.visible, "Something waits, so no empty state")
	_check(tab._bible_open.text == "Review", "A bible that needs approval offers Review")
	_check(tab.waiting_count() == 3, "The bible counts as waiting")
	tab._bible_open.pressed.emit()
	_check(_art == 1, "Review opens the art direction")
	listing["art_bible"] = {"state": "approved", "by": "you"}
	tab.apply_listing(listing)
	await _frames()
	_check(tab._bible_status.text == "Approved by you", "Approved by you")
	_check(tab._bible_decided.is_ancestor_of(tab._bible_row), "Approved, it sits with the decided")


func _check_live(tab: TAB, panel: PANEL) -> void:
	panel.show_file_review(
		"Create a low-poly hammer",
		"+ art/models/hammer.glb",
		[],
		[{"path": "a.glb", "change": "created"}]
	)
	tab.set_live({"id": "p2", "summary": "Create a low-poly hammer"})
	await _frames()
	var first := _rows(tab._waiting_body)[0]
	_check(str(first.get_meta("transaction_id")) == "p2", "The change on screen comes first")
	var meta := _find(first, "Meta") as Label
	_check(meta.text.begins_with("Blender"), "It keeps the app from the history: " + meta.text)
	(_find(first, "Open") as Button).pressed.emit()
	await _frames()
	_check(tab.showing_detail and panel.is_visible_in_tree(), "It opens the full review")
	_check(not tab._list_view.visible, "The full review replaces the list")
	tab._back.pressed.emit()
	await _frames()
	_check(not tab.showing_detail and tab._list_view.visible, "All approvals goes back")
	var accepted := [false]
	panel.accept_pressed.connect(func() -> void: accepted[0] = true)
	(_find(_rows(tab._waiting_body)[0], "Accept") as Button).pressed.emit()
	await _frames()
	_check(_decisions.has(["p2", true, true]), "Accept on the live row")
	_check(accepted[0], "It is decided through the full review, like its own Accept")
	tab.live_failed("Blender couldn't save hammer.blend.")
	await _frames()
	var row := _rows(tab._waiting_body)[0]
	_check(_find(row, "Accept") != null, "A failed decision can be taken again")
	_check((_find(row, "Error") as Label).text.contains("Blender couldn't"), "And says why")
	var close := tab.review_closed()
	await _frames()
	_check(not close, "A panel that was already open stays open")
	var waiting: Array[String] = []
	for left: Node in _rows(tab._waiting_body):
		waiting.append(str(left.get_meta("transaction_id")))
	_check(waiting == ["p1", "p2"], "Still waiting, back in the order made: %s" % [waiting])


func _label_text(node: Node) -> String:
	var text := ""
	for child: Node in node.find_children("*", "Label", true, false):
		text += (child as Label).text
	return text
