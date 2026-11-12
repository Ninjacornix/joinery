@tool
extends PanelContainer
## The playtest card (ADR 0038), above the footer with the other agents at work. While the
## bot plays it reads "Playing… 0:42" with the run's length and its own Stop; once the run
## is over it shows what was found at a glance: one count per severity, then each finding
## as a row that opens to its detail, the place in the code, the seed and a screenshot,
## with "Fix this" to put a ready request in the composer. Under the counts, how much of the
## level the bot reached ("Explored 34% of the level") over a top-down map with the places
## nobody reached marked (playtest_map.gd). The playtester's written report goes to the
## transcript (sidebar_tasks.gd), not here.

signal stop_pressed
signal close_pressed
## "Fix this" on a finding: [param request] is the request to pre-fill.
signal fix_requested(request: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const MAP := preload("playtest_map.gd")
const SEVERITIES: Array[String] = ["critical", "high", "medium", "low", "note"]
## Severity -> status dot state: bugs in the danger colour, the rest quiet.
const DOTS := {
	"critical": "attention",
	"high": "attention",
	"medium": "working",
	"low": "off",
	"note": "off",
}
const STEPS := {
	"starting": "Starting the playtester…",
	"playing": "Playing…",
	"writing": "Writing the report…",
}
## Findings shown before "Show all".
const FIRST_ITEMS := 4
## The findings scroll past this share of the window's height, so the composer stays put.
const MAX_LIST_SHARE := 0.3

var playtest_id := ""
## starting, playing, writing, done, failed or stopped.
var state := "starting"
var report: Dictionary = {}
var title_label: Label
var stop: Button
var close: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _timer: Label
var _counts: HBoxContainer
var _items: VBoxContainer
var _scroll: ScrollContainer
var _body: VBoxContainer
var _more: Button
var _coverage: VBoxContainer
var _coverage_text: Label
var _map: Control
var _seconds := 0.0


func _init(identifier := "") -> void:
	playtest_id = identifier
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Playtest"
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
	title_label = UI.label("Playtest")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title_label)
	_timer = UI.label("", "JoineryNumeric")
	_timer.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_timer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_timer.accessibility_name = "Time played"
	_timer.hide()
	head.add_child(_timer)
	stop = _small_icon("stop", "Stop the playtest")
	stop.pressed.connect(_on_stop)
	head.add_child(stop)
	close = _small_icon("close", "Dismiss the playtest")
	close.pressed.connect(func() -> void: close_pressed.emit())
	close.hide()
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
	_step = UI.label(STEPS["starting"], "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(_step)
	_counts = HBoxContainer.new()
	_counts.theme_type_variation = "JoineryRow"
	_counts.accessibility_name = "Findings by severity"
	_counts.hide()
	column.add_child(_counts)
	# The map and the findings scroll together, so the card never grows past its share.
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.hide()
	column.add_child(_scroll)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryTight"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.minimum_size_changed.connect(_fit_list)
	_scroll.add_child(_body)
	_coverage = VBoxContainer.new()
	_coverage.theme_type_variation = "JoineryTight"
	_coverage.accessibility_name = "How much of the level was reached"
	_coverage.hide()
	_body.add_child(_coverage)
	var legend := HBoxContainer.new()
	legend.theme_type_variation = "JoineryRowTight"
	_coverage.add_child(legend)
	_coverage_text = UI.label("", "JoineryCaption")
	_coverage_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	legend.add_child(_coverage_text)
	# The key sits with the words it explains, not at the far edge.
	var gap := Control.new()
	STYLE.on_theme(gap, func() -> void: gap.custom_minimum_size.x = float(UI_THEME.space(gap, 1)))
	legend.add_child(gap)
	legend.add_child(_swatch("danger", MAP.MISSED_ALPHA))
	var key := UI.label("not reached", "JoineryCaption")
	key.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	legend.add_child(key)
	_map = MAP.new()
	_coverage.add_child(_map)
	_items = VBoxContainer.new()
	_items.theme_type_variation = "JoineryTight"
	_items.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.add_child(_items)
	_more = UI.link("")
	UI.bind_height(_more, "xs")
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.hide()
	column.add_child(_more)


## The host's playtest event: {state, elapsed, seconds, report, message}.
func apply(event: Dictionary) -> void:
	var next := str(event.get("state", state))
	_seconds = float(event.get("seconds", _seconds))
	if next == "playing":
		_set_elapsed(float(event.get("elapsed", 0.0)))
	if event.get("report") is Dictionary and report.is_empty():
		report = event["report"]
		_show_report()
	if next == state:
		return
	state = next
	match state:
		"starting", "playing", "writing":
			_step.text = STEPS[state]
			if state == "writing":
				_timer.hide()
		"done":
			var found := int(_total())
			_settle(
				"done" if _serious() == 0 else "warning",
				(
					"Nothing went wrong in %s of play." % _clock(_played())
					if found == 0
					else "Played %s, seed %s." % [_clock(_played()), str(report.get("seed", ""))]
				)
			)
			UI_MOTION.pulse(self)
		"failed":
			_settle("failed", str(event.get("message", "The playtest didn't run.")))
		"stopped":
			_settle("stopped", "Stopped")


## Still running: its Stop is live.
func working() -> bool:
	return state in ["starting", "playing", "writing"]


func step_text() -> String:
	return _step.text


## Optimistic: "Stopping…" at once; the host confirms with "stopped".
func mark_stopping() -> bool:
	if not working() or stop.disabled:
		return false
	stop.disabled = true
	_step.text = "Stopping…"
	return true


## The rows showing findings (the visible ones).
func item_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _items.get_children():
		if child is Control and (child as Control).visible:
			rows.append(child as Control)
	return rows


## The coverage line as shown ("" without one): "Explored 34% of the level".
func coverage_text() -> String:
	return _coverage_text.text if _coverage.visible else ""


## Words of the counts row ("1 high", "2 low", …).
func count_words() -> PackedStringArray:
	var words := PackedStringArray()
	for child: Node in _counts.get_children():
		var label := child.get_node_or_null(^"Text") as Label
		if label != null:
			words.append(label.text)
	return words


## The request "Fix this" pre-fills for [param item].
static func fix_request(item: Dictionary) -> String:
	var text := "Fix this playtest finding: %s" % str(item.get("title", "a problem"))
	if item.get("t") != null:
		text += " at %s of play" % _clock(float(item["t"]))
	text += ". %s" % str(item.get("detail", ""))
	if not str(item.get("where", "")).is_empty():
		text += " (%s)" % str(item["where"])
	text += " Check the fix with: %s" % str(item.get("repro", ""))
	return text


func _set_elapsed(seconds: float) -> void:
	_timer.text = (
		"%s / %s" % [_clock(seconds), _clock(_seconds)] if _seconds > 0 else _clock(seconds)
	)
	_timer.tooltip_text = "Game time played"
	if not _timer.visible:
		UI_MOTION.fade_in(_timer)


func _show_report() -> void:
	for child: Node in _counts.get_children():
		child.queue_free()
	var counts: Dictionary = report.get("counts", {})
	for severity: String in SEVERITIES:
		var count := int(counts.get(severity, 0))
		if count == 0:
			continue
		var entry := UI.status("%d %s" % [count, severity], DOTS[severity])
		var words := entry.get_node(^"Text") as Label
		words.theme_type_variation = "JoineryNumeric"
		_counts.add_child(entry)
	_counts.visible = _counts.get_child_count() > 0
	if _counts.visible:
		UI_MOTION.fade_in(_counts)
	_show_coverage(report.get("coverage", {}) as Dictionary)
	var items: Array = report.get("items", [])
	var rows: Array = []
	for index: int in items.size():
		var row := _item_row(items[index] as Dictionary)
		row.visible = index < FIRST_ITEMS
		_items.add_child(row)
		if row.visible:
			rows.append(row)
	_scroll.visible = not items.is_empty() or _coverage.visible
	_fit_list()
	if items.size() > FIRST_ITEMS:
		_more.text = "Show all %d" % items.size()
		_more.show()
	UI_MOTION.stagger(rows)


## How much of the level the bot reached, over the map of what it missed.
func _show_coverage(coverage: Dictionary) -> void:
	var grid: Variant = coverage.get("grid", [])
	if coverage.is_empty() or not grid is Array or (grid as Array).is_empty():
		_coverage.hide()
		return
	var percent := float(coverage.get("percent", 0.0))
	_coverage_text.text = "Explored %s%% of the level" % _percent(percent)
	_coverage_text.tooltip_text = (
		"The bot stood in %d of the %d places on the level's floor it could map."
		% [int(coverage.get("visited", 0)), int(coverage.get("navigable", 0))]
	)
	_map.call("set_coverage", coverage)
	_coverage.show()
	UI_MOTION.fade_in(_coverage)


static func _percent(value: float) -> String:
	return (
		str(roundi(value))
		if value >= 10.0 or is_equal_approx(value, roundf(value))
		else "%.1f" % value
	)


## A small square of the map's colour [param role], for the legend.
func _swatch(role: String, alpha: float) -> Control:
	var swatch := ColorRect.new()
	swatch.mouse_filter = Control.MOUSE_FILTER_IGNORE
	swatch.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	STYLE.on_theme(
		swatch,
		func() -> void:
			var side := float(UI_THEME.dot_px(swatch))
			swatch.custom_minimum_size = Vector2(side, side)
			var colour := UI_THEME.color(swatch, role)
			colour.a = alpha
			swatch.color = colour
	)
	return swatch


func _item_row(item: Dictionary) -> VBoxContainer:
	var row := VBoxContainer.new()
	row.theme_type_variation = "JoineryTight"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	row.add_child(head)
	var dot := UI.status_dot(str(DOTS.get(str(item.get("severity", "low")), "off")))
	dot.tooltip_text = str(item.get("severity", ""))
	head.add_child(dot)
	var title := str(item.get("title", "Finding"))
	if item.get("t") != null:
		title += " · " + _clock(float(item["t"]))
	var toggle := UI.disclosure(title)
	toggle.accessibility_name = "%s, %s" % [title, str(item.get("severity", ""))]
	toggle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toggle.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	toggle.tooltip_text = str(item.get("detail", ""))
	head.add_child(toggle)
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	body.hide()
	row.add_child(body)
	body.add_child(UI.label(str(item.get("detail", "")), "JoineryCaption", true))
	var meta := PackedStringArray()
	if not str(item.get("where", "")).is_empty():
		meta.append(str(item["where"]))
	meta.append("seed %s" % str(item.get("seed", "")))
	body.add_child(UI.label(" · ".join(meta), "JoineryNumeric", true))
	var picture := _thumbnail(str(item.get("shot", "")))
	if picture != null:
		body.add_child(picture)
	var fix := UI.secondary("Fix this")
	fix.accessibility_name = "Ask Journeyman to fix: " + title
	fix.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(fix, "sm")
	UI_MOTION.press_feedback(fix)
	fix.pressed.connect(func() -> void: fix_requested.emit(fix_request(item)))
	body.add_child(fix)
	toggle.toggled.connect(
		func(open: bool) -> void:
			body.visible = open
			if open:
				UI_MOTION.reveal(body)
	)
	return row


## A screenshot of the moment, kept small: context, not an asset.
func _thumbnail(path: String) -> TextureRect:
	if path.is_empty() or not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return null
	var picture := TextureRect.new()
	picture.texture = ImageTexture.create_from_image(image)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT
	picture.accessibility_name = "Screenshot of the moment"
	var ratio := float(image.get_height()) / maxf(float(image.get_width()), 1.0)
	STYLE.on_theme(
		picture,
		func() -> void:
			var width := UI_THEME.unit(picture) * 200.0
			picture.custom_minimum_size = Vector2(width, width * ratio)
	)
	picture.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	return picture


## The list is as tall as its rows, up to a share of the window; past that it scrolls.
func _fit_list() -> void:
	if _scroll == null or not is_inside_tree():
		return
	var limit := get_viewport_rect().size.y * MAX_LIST_SHARE
	_scroll.custom_minimum_size.y = minf(_body.get_combined_minimum_size().y, limit)


func _notification(what: int) -> void:
	if what == NOTIFICATION_ENTER_TREE:
		_fit_list.call_deferred()


func _show_all() -> void:
	var shown: Array = []
	for child: Node in _items.get_children():
		if child is Control and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	_timer.hide()
	stop.hide()
	close.show()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words


func _on_stop() -> void:
	if mark_stopping():
		stop_pressed.emit()


func _serious() -> int:
	var counts: Dictionary = report.get("counts", {})
	return int(counts.get("critical", 0)) + int(counts.get("high", 0))


func _total() -> int:
	var total := 0
	for value: Variant in (report.get("counts", {}) as Dictionary).values():
		total += int(value)
	return total


func _played() -> float:
	return float(report.get("played_seconds", _seconds))


func _small_icon(icon_name: String, tooltip: String) -> Button:
	var button := UI.icon_button(icon_name, tooltip)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(button)
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "sm"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button


static func _clock(seconds: float) -> String:
	return UI_MOTION.format_clock(seconds)
