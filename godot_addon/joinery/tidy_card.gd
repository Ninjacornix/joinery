@tool
extends PanelContainer
## "/tidy": what the game never uses, at a glance. One headline ("14 unused things · 38 MB"),
## the kinds in one line, then the findings grouped by kind, each with its size, Keep and
## Remove; the ones a script might still load by a path it builds sit apart under "Maybe used".
## Why each one is thought unused, and what goes with it (its .import, the textures only it
## uses, its native source, recipe and lock entry), is in the row's tooltip.
##
## Remove (all the sure ones, or one row) never deletes anything here: the removal becomes one
## change in Review, where Discard keeps everything. Keep puts the finding in `[tidy] keep`.
## "Free … of Joinery caches" clears Joinery's own caches at once and says what went.

signal remove_requested(ids: Array)
signal keep_requested(finding: Dictionary)
signal caches_requested
signal recheck_requested
signal close_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## Kinds in the order the card groups them, with their names (joinery.tidy.KINDS).
const KINDS := {
	"model": "Models",
	"texture": "Textures",
	"material": "Materials",
	"sound": "Sounds",
	"music": "Music",
	"scene": "Scenes",
	"script": "Scripts",
	"resource": "Resources",
	"font": "Fonts",
	"shader": "Shaders",
	"input": "Input actions",
	"autoload": "Autoloads",
	"node": "Nodes in scenes",
}
## Rows shown before "Show all".
const FIRST_ROWS := 5
## Of the window's height, the most the list takes before it scrolls (as the playtest card).
const MAX_LIST_SHARE := 0.3

## checking, ready, sent (the removal waits in Review) or failed.
var state := "checking"
var report: Dictionary = {}
var title_label: Label
var close: Button
var again: Button
var remove_all: Button
var caches: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _list: VBoxContainer
var _scroll: ScrollContainer
var _more: Button
var _foot: VBoxContainer
## Finding ids already sent or kept: their rows are gone.
var _gone: Dictionary = {}


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Unused content"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	title_label = STYLE.card_title("Looking for unused content")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	close = UI.icon_button("close", "Dismiss")
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(close)
	STYLE.on_theme_overrides(
		close,
		func() -> void:
			var side := float(UI_THEME.control_h(close, "sm"))
			close.custom_minimum_size = Vector2(side, side)
	)
	close.pressed.connect(func() -> void: close_pressed.emit())
	head.add_child(close)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	column.add_child(line)
	_dots = INDICATOR.new()
	_dots.set("running", true)
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label("Following the game from its main scene…", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_step.mouse_filter = Control.MOUSE_FILTER_PASS
	line.add_child(_step)
	again = UI.quiet_link("Check again")
	UI.bind_height(again, "xs")
	again.pressed.connect(func() -> void: recheck_requested.emit())
	again.hide()
	line.add_child(again)
	# The rows scroll in their own share of the window, so the card never pushes the composer.
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.hide()
	column.add_child(_scroll)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryTight"
	_list.accessibility_name = "Unused content"
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.minimum_size_changed.connect(_fit_list)
	_scroll.add_child(_list)
	_more = UI.quiet_link("")
	UI.bind_height(_more, "xs")
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.hide()
	column.add_child(_more)
	# The removal leads; Joinery's own caches are a quiet line under it, never in the way.
	_foot = VBoxContainer.new()
	_foot.theme_type_variation = "JoineryTight"
	_foot.hide()
	column.add_child(_foot)
	remove_all = UI.primary("Remove")
	remove_all.tooltip_text = "One change in Review deletes them and what goes with them"
	remove_all.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(remove_all, "sm")
	UI_MOTION.press_feedback(remove_all)
	remove_all.pressed.connect(_on_remove_all)
	_foot.add_child(remove_all)
	caches = UI.quiet("")
	caches.tooltip_text = "Joinery's own caches (review renders, maps, old results): made again"
	UI.bind_height(caches, "xs")
	caches.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(caches)
	UI_MOTION.press_feedback(caches)
	caches.pressed.connect(_on_caches)
	caches.hide()
	_foot.add_child(caches)


## The project is being read (no model, nothing changes).
func set_checking() -> void:
	state = "checking"
	_gone.clear()
	_working("Following the game from its main scene…")
	again.hide()


## `joinery tidy --json`: the headline, the kinds, the rows.
func show_report(data: Dictionary) -> void:
	report = data
	state = "ready"
	_gone.clear()
	again.show()
	_redraw(true)


func apply_failed(message: String) -> void:
	state = "failed"
	_settle("warning", message)
	again.show()


## The removal went to Review: its rows step out; Discard there keeps everything.
func set_sent(ids: Array) -> void:
	state = "sent"
	for id: Variant in ids:
		_gone[str(id)] = true
	_redraw(false)
	var count := ids.size()
	title_label.text = (
		"%d unused thing%s wait%s in Review"
		% [count, "" if count == 1 else "s", "s" if count == 1 else ""]
	)
	title_label.tooltip_text = title_label.text
	_settle("done", "Not deleted yet")
	_step.tooltip_text = "Accept the removal in Review to delete them; Discard keeps everything."


## A Keep or Remove that didn't go through: its rows come back, with why.
func restore(ids: Array, message: String) -> void:
	for id: Variant in ids:
		_gone.erase(str(id))
	state = "ready"
	_redraw(false)
	_settle("warning", message)


## Joinery's caches were cleared: say what went.
func show_caches(message: String) -> void:
	report["cache_size"] = 0
	caches.hide()
	_settle("done", message)
	UI_MOTION.pulse(_step)


## "14 unused things · 38 MB", "Nothing unused".
func headline() -> String:
	var sure := _visible(true)
	if sure.is_empty():
		var maybe := _visible(false)
		if not maybe.is_empty():
			return "Nothing surely unused · %d maybe" % maybe.size()
		return "Nothing unused"
	var total := 0
	for item: Dictionary in sure:
		total += int(item.get("size", 0))
	if _gone.is_empty():
		total = maxi(total, int(report.get("size", 0)))
	var words := "%d unused thing%s" % [sure.size(), "" if sure.size() == 1 else "s"]
	return words + (" · " + human_size(total) if total > 0 else "")


## "3 models · 2 textures · 1 input action", from what is still on the card.
func kinds_line() -> String:
	var counts := {}
	for item: Dictionary in _visible(true):
		var kind := str(item.get("kind", ""))
		counts[kind] = int(counts.get(kind, 0)) + 1
	var words := PackedStringArray()
	for kind: String in KINDS:
		if counts.has(kind):
			var label := str(KINDS[kind]).to_lower()
			if int(counts[kind]) == 1:
				label = _singular(label)
			words.append("%d %s" % [int(counts[kind]), label])
	return " · ".join(words)


## Bytes as a person reads them: "38 MB", "640 KB", "12 bytes".
static func human_size(size: int) -> String:
	if size >= 1 << 30:
		return "%.1f GB" % (size / float(1 << 30))
	if size >= 10 << 20:
		return "%d MB" % roundi(size / float(1 << 20))
	if size >= 1 << 20:
		return "%.1f MB" % (size / float(1 << 20))
	if size >= 1 << 10:
		return "%d KB" % roundi(size / float(1 << 10))
	return "%d byte%s" % [size, "" if size == 1 else "s"]


## The finding rows on the card (not the group headings).
func finding_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _list.get_children():
		if child.has_meta("finding") and (child as Control).visible:
			if not child.is_queued_for_deletion():
				rows.append(child as Control)
	return rows


func step_text() -> String:
	return _step.text


func _visible(sure: bool) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for item: Variant in report.get("findings", []):
		var entry := item as Dictionary
		if _gone.has(str(entry.get("id", ""))):
			continue
		if (str(entry.get("confidence", "")) == "unused") == sure:
			found.append(entry)
	return found


func _redraw(stagger: bool) -> void:
	title_label.text = headline()
	title_label.tooltip_text = title_label.text
	var sure := _visible(true)
	var maybe := _visible(false)
	_settle("done", _read_line())
	_step.tooltip_text = (
		(
			"Followed the game from its main scene, autoloads and settings: %s.\n"
			% (kinds_line() if not sure.is_empty() else "nothing unused")
		)
		+ "Files only tests load, and what a game feature brings, are left out."
	)
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	var shown: Array = []
	var count := 0
	var groups: Array = []
	for kind: String in KINDS:
		var items: Array[Dictionary] = []
		for item: Dictionary in sure:
			if str(item.get("kind", "")) == kind:
				items.append(item)
		if not items.is_empty():
			groups.append([str(KINDS[kind]), items, ""])
	if not maybe.is_empty():
		groups.append(
			[
				"Maybe used",
				maybe,
				"A script builds a path that could load these. Check before removing one."
			]
		)
	for group: Array in groups:
		var items: Array = group[1]
		var heading := _heading(str(group[0]), items, str(group[2]))
		heading.visible = count < FIRST_ROWS
		_list.add_child(heading)
		for item: Dictionary in items:
			var row := _row(item)
			row.visible = count < FIRST_ROWS
			_list.add_child(row)
			if row.visible:
				shown.append(row)
			count += 1
	_scroll.visible = count > 0
	_more.text = "Show all %d" % count
	_more.visible = count > FIRST_ROWS
	_foot.visible = not sure.is_empty() or int(report.get("cache_size", 0)) > 0
	remove_all.visible = not sure.is_empty()
	remove_all.text = "Remove %d" % sure.size() if sure.size() > 1 else "Remove it"
	remove_all.accessibility_name = "Remove %s" % headline()
	var cache_size := int(report.get("cache_size", 0))
	caches.visible = cache_size > 0
	caches.text = "Free %s of Joinery caches" % human_size(cache_size)
	if stagger:
		UI_MOTION.stagger(shown)


func _read_line() -> String:
	var read: Dictionary = report.get("read", {})
	if read.is_empty():
		return "the project"
	return "%d scenes, %d scripts" % [int(read.get("scenes", 0)), int(read.get("scripts", 0))]


func _heading(title: String, items: Array, tip: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var words := UI.label(title, "JoinerySection")
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	row.add_child(words)
	var total := 0
	for item: Variant in items:
		total += int((item as Dictionary).get("size", 0))
	if total > 0:
		var size := UI.label(human_size(total), "JoineryNumeric")
		size.mouse_filter = Control.MOUSE_FILTER_PASS
		row.add_child(size)
	row.tooltip_text = tip
	words.tooltip_text = tip
	return row


## One finding: its name and size; opened, why it is thought unused, what goes with it, and
## Keep and Remove. Closed rows keep the card a list to scan, not a wall of buttons.
func _row(item: Dictionary) -> VBoxContainer:
	var row := VBoxContainer.new()
	row.theme_type_variation = "JoineryFlush"
	row.set_meta("finding", str(item.get("id", "")))
	var title := str(item.get("title", ""))
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	line.mouse_filter = Control.MOUSE_FILTER_PASS
	row.add_child(line)
	UI.bind_height(line, "xs", false)
	var open := UI.disclosure(title)
	open.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	open.clip_text = true
	open.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	UI.bind_height(open, "xs")
	_rows_tight(open)
	open.tooltip_text = tooltip(item)
	open.accessibility_name = "%s: %s" % [title, str(item.get("why", ""))]
	line.add_child(open)
	var size := int(item.get("size", 0))
	if size > 0:
		var amount := UI.label(human_size(size), "JoineryNumeric")
		amount.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		amount.mouse_filter = Control.MOUSE_FILTER_PASS
		line.add_child(amount)
	var detail := VBoxContainer.new()
	detail.theme_type_variation = "JoineryTight"
	detail.hide()
	row.add_child(detail)
	var why := UI.label(str(item.get("why", "")), "JoineryCaption", true)
	detail.add_child(why)
	var goes: Array = item.get("goes_with", [])
	if not goes.is_empty():
		var names := PackedStringArray()
		for companion: Variant in goes.slice(0, 4):
			names.append(str((companion as Dictionary).get("path", "")).get_file())
		var tail := " and %d more" % (goes.size() - 4) if goes.size() > 4 else ""
		detail.add_child(
			UI.label("Goes with it: " + ", ".join(names) + tail, "JoineryCaption", true)
		)
	var actions := HBoxContainer.new()
	actions.theme_type_variation = "JoineryRow"
	actions.alignment = BoxContainer.ALIGNMENT_END
	detail.add_child(actions)
	var keep := UI.quiet("Keep")
	keep.tooltip_text = "Leave it alone: tidy won't list it again"
	keep.accessibility_name = "Keep " + title
	UI.bind_height(keep, "sm")
	UI_MOTION.press_feedback(keep)
	keep.pressed.connect(_on_keep.bind(item, row))
	actions.add_child(keep)
	var remove := UI.secondary("Remove")
	remove.tooltip_text = "Remove just this, as a change in Review"
	remove.accessibility_name = "Remove " + title
	UI.bind_height(remove, "sm")
	UI_MOTION.press_feedback(remove)
	remove.pressed.connect(_on_remove_one.bind(item, row))
	actions.add_child(remove)
	open.toggled.connect(
		func(on: bool) -> void:
			if on:
				UI_MOTION.reveal(detail)
			else:
				UI_MOTION.exit(detail)
	)
	return row


## Why it is thought unused, the evidence, and what goes with it.
static func tooltip(item: Dictionary) -> String:
	var lines := PackedStringArray([str(item.get("why", ""))])
	for line: Variant in item.get("evidence", []):
		lines.append(str(line))
	var goes: Array = item.get("goes_with", [])
	if not goes.is_empty():
		var names := PackedStringArray()
		for companion: Variant in goes.slice(0, 6):
			var entry := companion as Dictionary
			names.append("%s (%s)" % [str(entry.get("path", "")).get_file(), entry.get("what", "")])
		var tail := " and %d more" % (goes.size() - 6) if goes.size() > 6 else ""
		lines.append("Goes with it: " + ", ".join(names) + tail)
	for edit: Variant in item.get("edits", []):
		lines.append("Edits " + str(edit))
	return "\n".join(lines)


func _on_remove_all() -> void:
	var ids: Array = []
	for item: Dictionary in _visible(true):
		ids.append(str(item.get("id", "")))
	if not ids.is_empty():
		remove_requested.emit(ids)


func _on_remove_one(item: Dictionary, row: Control) -> void:
	_step_out(row)
	remove_requested.emit([str(item.get("id", ""))])


func _on_keep(item: Dictionary, row: Control) -> void:
	_gone[str(item.get("id", ""))] = true
	_step_out(row)
	title_label.text = headline()
	keep_requested.emit(item)


func _on_caches() -> void:
	caches.disabled = true
	caches_requested.emit()


func _step_out(row: Control) -> void:
	UI_MOTION.exit(row)


## A row's disclosure as tall as the row (CONTROL_H.xs), not a full md button: its vertical
## padding goes, so a list of findings reads as one evenly spaced list.
static func _rows_tight(button: Button) -> void:
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state, button.theme_type_variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				box.content_margin_top = 0.0
				box.content_margin_bottom = 0.0
				button.add_theme_stylebox_override(state, box)
	)


func _fit_list() -> void:
	if _scroll == null or not is_inside_tree():
		return
	var limit := get_viewport_rect().size.y * MAX_LIST_SHARE
	_scroll.custom_minimum_size.y = minf(_list.get_combined_minimum_size().y, limit)


func _notification(what: int) -> void:
	if what == NOTIFICATION_ENTER_TREE:
		_fit_list.call_deferred()


func _show_all() -> void:
	var shown: Array = []
	for child: Node in _list.get_children():
		if child is Control and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _working(words: String) -> void:
	_dots.show()
	_dots.set("running", true)
	_status.hide()
	_step.text = words
	_step.tooltip_text = words
	_foot.hide()
	_scroll.hide()
	_more.hide()


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	if not words.begins_with("Checked"):
		_step.tooltip_text = words
	caches.disabled = false


static func _singular(label: String) -> String:
	if label == "nodes in scenes":
		return "node in a scene"
	if label == "music":
		return label
	return label.trim_suffix("s")
