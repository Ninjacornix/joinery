@tool
extends PanelContainer
## "Make it match" (ADR 0045): the project against its art bible, at a glance. One headline
## that says how much drifts, one row per area with its score ("Palette 72%"), the top
## findings (what was measured, what the bible asks for), and the call to action: Fix all, or
## Fix on one area. The fixes run as parallel tasks with their own cards; each change is
## reviewed in the Review tab. While they run the card counts what is ready to review, and
## after each accept the project is checked again so the scores rise in place.
##
## The same card shows the accessibility and performance checks (ADR 0049, ADR 0050) with
## [member kind] "access" or "perf": their own headline, the finding's own words on each
## row, and for performance the target machine to judge against. The accessibility card also
## files its findings by Xbox Accessibility Guideline, with which guidelines Joinery checks
## (access_guidelines.gd, ADR 0067).

signal fix_requested(areas: Array)
signal recheck_requested
signal stop_pressed
signal close_pressed
signal review_pressed
## Performance only: the target machine was changed ("steam-deck").
signal target_changed(target: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const GUIDELINES := preload("access_guidelines.gd")
## Severity -> status dot state.
const DOTS := {"high": "attention", "medium": "working", "low": "off"}
## Findings shown before "Show all".
const FIRST_FINDINGS := 3
## Below this card width, in em, it shows one finding fewer.
const NARROW_EM := 23.0
const FINAL := ["accepted", "discarded", "done", "failed", "stopped"]
## Per kind: [title while checking, step while checking, accessible name].
const WORDS := {
	"match":
	["Checking against the art bible", "Measuring every asset, scene and theme…", "Make it match"],
	"access":
	["Checking accessibility", "Playing the game and reading its menus…", "Accessibility check"],
	"perf": ["Measuring performance", "Playing the game to measure it…", "Performance check"],
}
const TARGETS := {
	"high": "High-end PC",
	"mid": "Mid-range PC",
	"low": "Low-end PC",
	"steam-deck": "Steam Deck",
	"web": "Web browser",
}


## A thin score bar: the share that matches, filled in the accent colour (success at 100%).
class ScoreBar:
	extends Control
	var shown := 0.0:
		set(value):
			shown = value
			queue_redraw()

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		size_flags_vertical = Control.SIZE_SHRINK_CENTER
		STYLE.on_theme(
			self,
			func() -> void:
				var unit := UI_THEME.unit(self)
				custom_minimum_size = Vector2(roundf(unit * 56.0), maxf(3.0, roundf(unit * 4.0)))
		)

	func set_score(value: float) -> void:
		UI_MOTION.tween_to(self, self, "shown", clampf(value / 100.0, 0.0, 1.0), "base")

	func _draw() -> void:
		var height := size.y
		var radius := height / 2.0
		var track := UI_THEME.color(self, "border_subtle")
		draw_rect(Rect2(Vector2.ZERO, size), track)
		if shown <= 0.0:
			return
		var role := "success" if shown >= 0.999 else "accent"
		draw_rect(
			Rect2(0, 0, maxf(radius * 2.0, size.x * shown), height), UI_THEME.color(self, role)
		)


## "match", "access" or "perf".
var kind := "match"
## checking, ready, fixing, done or failed.
var state := "checking"
## Performance only: the target machine to judge against.
var target_picker: OptionButton
var audit: Dictionary = {}
var title_label: Label
var close: Button
var stop: Button
var fix_all: Button
## Accessibility only: the findings by Xbox Accessibility Guideline.
var guidelines: Control
var _dots: Control
var _status: TextureRect
var _step: Label
var _review: Button
var _again: Button
var _areas: VBoxContainer
var _area_rows: Dictionary = {}
var _findings: VBoxContainer
var _more: Button
## The user pressed Show all: every finding stays shown whatever the width.
var _all_shown := false
var _foot: HFlowContainer
var _needs: Label
var _kept: Label
var _run: Array = []


func _init(card_kind := "match") -> void:
	kind = card_kind if WORDS.has(card_kind) else "match"
	var words: Array = WORDS[kind]
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = str(words[2])
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryChangeCard")
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
	title_label = STYLE.card_title(str(words[0]))
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	if kind == "perf":
		target_picker = _target_picker()
		head.add_child(target_picker)
	stop = _small_icon("stop", "Stop making it match" if kind == "match" else "Stop the fixes")
	stop.pressed.connect(_on_stop)
	stop.hide()
	head.add_child(stop)
	close = _small_icon("close", "Dismiss")
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
	_step = UI.label(str(words[1]), "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	line.add_child(_step)
	_review = UI.link("Review")
	_review.accessibility_name = "Review the changes in the Review tab"
	UI.bind_height(_review, "xs")
	_review.pressed.connect(func() -> void: review_pressed.emit())
	_review.hide()
	line.add_child(_review)
	_again = UI.quiet_link("Check again")
	UI.bind_height(_again, "xs")
	_again.pressed.connect(func() -> void: recheck_requested.emit())
	_again.hide()
	line.add_child(_again)
	_areas = VBoxContainer.new()
	_areas.theme_type_variation = "JoineryTight"
	_areas.accessibility_name = "Scores by area"
	_areas.hide()
	column.add_child(_areas)
	_findings = VBoxContainer.new()
	_findings.theme_type_variation = "JoineryTight"
	_findings.accessibility_name = "What drifts"
	_findings.hide()
	column.add_child(_findings)
	_more = UI.quiet_link("")
	UI.bind_height(_more, "xs")
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.hide()
	if kind == "access":
		# Show all and the guidelines share a line, so the closed card is no taller; in a
		# narrow sidebar the guidelines flow onto the next.
		var links := HFlowContainer.new()
		STYLE.on_theme_overrides(
			links,
			func() -> void:
				links.add_theme_constant_override("h_separation", UI_THEME.space(links, 2))
				links.add_theme_constant_override("v_separation", UI_THEME.space(links, 1))
		)
		column.add_child(links)
		links.add_child(_more)
		guidelines = GUIDELINES.new()
		links.add_child(guidelines.get("head"))
		column.add_child(guidelines)
		guidelines.connect("opened", _by_guideline)
	else:
		column.add_child(_more)
	# Fix all and its two counts on one line; in a narrow sidebar the counts flow onto the next.
	_foot = HFlowContainer.new()
	STYLE.on_theme_overrides(
		_foot,
		func() -> void:
			_foot.add_theme_constant_override("h_separation", UI_THEME.space(_foot, 2))
			_foot.add_theme_constant_override("v_separation", UI_THEME.space(_foot, 1))
	)
	_foot.hide()
	column.add_child(_foot)
	fix_all = UI.primary("Fix all")
	UI.bind_height(fix_all, "sm")
	UI_MOTION.press_feedback(fix_all)
	fix_all.pressed.connect(func() -> void: fix_requested.emit([]))
	_foot.add_child(fix_all)
	_needs = UI.label("", "JoineryCaption")
	_needs.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_needs.mouse_filter = Control.MOUSE_FILTER_PASS
	_foot.add_child(_needs)
	_kept = UI.label("", "JoineryCaption")
	_kept.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_kept.mouse_filter = Control.MOUSE_FILTER_PASS
	_foot.add_child(_kept)


## Measuring again: the scores stay while the dots run.
func set_checking() -> void:
	state = "checking"
	_working(str(WORDS[kind][1]))
	_again.hide()
	_review.hide()
	fix_all.disabled = true


## `joinery match --json`: headline, scores, top findings and what Fix all would do.
func show_audit(data: Dictionary) -> void:
	var before := _scores()
	audit = data
	state = "ready"
	stop.hide()
	fix_all.disabled = false
	var findings: Array = data.get("findings", [])
	title_label.text = headline(data) if kind == "match" else check_headline(kind, data)
	title_label.tooltip_text = title_label.text
	var fixable := _fixable(findings)
	var serious := 0
	for item: Variant in findings:
		if str((item as Dictionary).get("severity", "")) == "high":
			serious += 1
	show_target(str((data.get("facts", {}) as Dictionary).get("target", "")))
	var errors: Array = data.get("errors", [])
	if not errors.is_empty():
		_settle("warning", "%d couldn't be measured. Details are in Output." % errors.size())
	elif findings.is_empty():
		_settle(
			"done", "Checked against the art bible's rules" if kind == "match" else facts_line(data)
		)
	else:
		_settle("warning" if serious > 0 else "done", severity_line(findings))
	_again.visible = true
	_review.visible = _ready_count() > 0
	_fill_areas(data.get("areas", []), before)
	_fill_findings(findings)
	if guidelines != null:
		guidelines.call("show_xag", (data.get("facts", {}) as Dictionary).get("xag", {}), findings)
		if guidelines.visible:
			_by_guideline(true)
	fix_all.text = "Fix %d %s" % [fixable, "thing" if fixable == 1 else "things"]
	fix_all.visible = fixable > 0
	var needs := findings.size() - fixable
	_needs.text = "%d need you" % needs if needs > 0 else ""
	_needs.tooltip_text = _needs_detail(findings)
	_needs.visible = needs > 0
	var kept: Array = data.get("excluded", [])
	_kept.text = "%d kept as they are" % kept.size() if not kept.is_empty() else ""
	_kept.tooltip_text = "Final or excluded in joinery.toml [match]:\n" + "\n".join(kept)
	_kept.visible = not kept.is_empty()
	_foot.visible = fix_all.visible or _needs.visible or _kept.visible
	var after := _scores()
	if not before.is_empty() and _total(after) > _total(before):
		UI_MOTION.pulse(self)


## The chat host's match event: {state: auditing|fixing|done|failed|stopped, tasks, message}.
func apply_run(event: Dictionary) -> void:
	_run = event.get("tasks", _run)
	var next := str(event.get("state", ""))
	if event.get("audit") is Dictionary and audit.is_empty():
		show_audit(event["audit"])
	match next:
		"auditing":
			state = "fixing"
			_working("Planning the changes…")
			_quiet_details()
		"fixing":
			state = "fixing"
			_working(fixing_line())
			_quiet_details()
			_review.visible = _ready_count() > 0
		"done", "stopped", "failed":
			state = "done"
			stop.hide()
			close.show()
			var failed := 0
			for row: Variant in _run:
				if str((row as Dictionary).get("state", "")) in ["failed", "stopped"]:
					failed += 1
			var words := str(event.get("message", ""))
			if words.is_empty():
				words = done_line()
			_settle("warning" if failed > 0 or next != "done" else "done", words)
			_review.visible = _ready_count() > 0
			_again.visible = true
			_findings.visible = _findings.get_child_count() > 0
			_areas.visible = not _area_rows.is_empty()
			if guidelines != null:
				var xag: Dictionary = (audit.get("facts", {}) as Dictionary).get("xag", {})
				(guidelines.get("head") as Control).visible = not xag.is_empty()
			for id: String in _area_rows:
				_show_fix(_area_rows[id]["fix"] as Button, false)
			UI_MOTION.pulse(self)


## A task of this run changed state (sidebar_tasks.gd's task events).
func task_changed(task_id: String, task_state: String) -> bool:
	for row: Variant in _run:
		var entry := row as Dictionary
		if str(entry.get("task_id", "")) == task_id:
			entry["state"] = task_state
			if state == "fixing":
				_working(fixing_line())
			elif state in ["done", "ready"]:
				_step.text = done_line()
			_review.visible = _ready_count() > 0
			return true
	return false


## The headline: what drifts, in one line.
static func headline(data: Dictionary) -> String:
	if not bool(data.get("rules", true)):
		return "The art bible has no checked rules yet"
	var count := (data.get("findings", []) as Array).size()
	if count == 0:
		return (
			"Everything matches the art bible"
			if not (data.get("areas", []) as Array).is_empty()
			else "Nothing to check against the art bible yet"
		)
	return "%d %s from the art bible" % [count, "thing drifts" if count == 1 else "things drift"]


## The headline of an accessibility or performance check.
static func check_headline(check: String, data: Dictionary) -> String:
	var count := (data.get("findings", []) as Array).size()
	var facts: Dictionary = data.get("facts", {})
	if check == "perf":
		var machine := str(facts.get("target_label", "the target"))
		if count == 0:
			return "Runs within a %s's budget" % machine
		return "%d %s on a %s" % [count, "problem" if count == 1 else "problems", machine]
	if count == 0:
		return "No accessibility problems found"
	return "%d accessibility %s" % [count, "problem" if count == 1 else "problems"]


## What a clean check measured: "9.3 ms a frame here · 134 draw calls", "102 controls…".
static func facts_line(data: Dictionary) -> String:
	var facts: Dictionary = data.get("facts", {})
	var measured: Dictionary = facts.get("measured", {})
	if measured.is_empty():
		return (
			"Checked %d controls on %d screens"
			% [int(facts.get("controls", 0)), (facts.get("pages", []) as Array).size() + 1]
		)
	var windowed := bool(measured.get("windowed", false))
	var frame: Dictionary = measured.get("frame_ms" if windowed else "cpu_frame_ms", {})
	var line := "p95 %.1f ms a frame here" % float(frame.get("p95", 0.0))
	if windowed:
		line += " · %d draw calls" % int(measured.get("draw_calls", 0))
	return line


## "3 high · 5 medium": how serious what drifts is, at a glance.
static func severity_line(findings: Array) -> String:
	var counts := {"high": 0, "medium": 0, "low": 0}
	for item: Variant in findings:
		var severity := str((item as Dictionary).get("severity", "low"))
		counts[severity] = int(counts.get(severity, 0)) + 1
	var words := PackedStringArray()
	for severity: String in ["high", "medium", "low"]:
		if int(counts[severity]) > 0:
			words.append("%d %s" % [int(counts[severity]), severity])
	return " · ".join(words)


## "Making 5 changes… 2 ready to review".
func fixing_line() -> String:
	if _run.is_empty():
		return "Planning the changes…"
	var ready := _ready_count()
	var line := "Making %d %s…" % [_run.size(), "change" if _run.size() == 1 else "changes"]
	if ready > 0:
		line += " %d ready to review" % ready
	return line


func done_line() -> String:
	if _run.is_empty():
		return "Nothing Joinery can fix by itself."
	var ready := _ready_count()
	var accepted := 0
	var failed := 0
	for row: Variant in _run:
		var task_state := str((row as Dictionary).get("state", ""))
		accepted += int(task_state == "accepted")
		failed += int(task_state in ["failed", "stopped"])
	var parts := PackedStringArray()
	if ready > 0:
		parts.append("%d ready to review" % ready)
	if accepted > 0:
		parts.append("%d accepted" % accepted)
	if failed > 0:
		parts.append("%d didn't finish" % failed)
	return ", ".join(parts).capitalize().left(1) + ", ".join(parts).substr(1)


## Each area's words as shown ("Palette 72%"), for checks.
func area_words() -> PackedStringArray:
	var words := PackedStringArray()
	for id: String in _area_rows:
		var row: Dictionary = _area_rows[id]
		words.append("%s %s" % [(row["label"] as Label).text, (row["number"] as Label).text])
	return words


## The visible finding rows.
func finding_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _findings.get_children():
		if child is Control and (child as Control).visible:
			rows.append(child as Control)
	return rows


func area_fix(id: String) -> Button:
	return (_area_rows.get(id, {}) as Dictionary).get("fix") as Button


func step_text() -> String:
	return _step.text


func _fill_areas(areas: Array, before: Dictionary) -> void:
	var seen := {}
	var added: Array = []
	for area: Variant in areas:
		var entry := area as Dictionary
		var id := str(entry.get("id", ""))
		seen[id] = true
		if not _area_rows.has(id):
			_area_rows[id] = _area_row(entry)
			_areas.add_child(_area_rows[id]["row"])
			added.append(_area_rows[id]["row"])
		var row: Dictionary = _area_rows[id]
		var score := int(entry.get("score", 0))
		UI_MOTION.roll_number(row["number"] as Label, score, "%d%%")
		(row["bar"] as ScoreBar).set_score(score)
		_show_fix(row["fix"] as Button, int(entry.get("fixable", 0)) > 0)
		(row["row"] as Control).tooltip_text = (
			"%d of %d match"
			% [
				int(entry.get("checked", 0)) - int(entry.get("failing", 0)),
				int(entry.get("checked", 0))
			]
		)
		if before.has(id) and score > int(before[id]):
			UI_MOTION.flash(row["number"] as Label)
	for id: String in _area_rows.keys():
		if not seen.has(id):
			(_area_rows[id]["row"] as Control).queue_free()
			_area_rows.erase(id)
	_areas.visible = not _area_rows.is_empty()
	UI_MOTION.stagger(added)


func _area_row(entry: Dictionary) -> Dictionary:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	UI.bind_height(row, "xs", false)
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var label := UI.label(str(entry.get("label", "")))
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(label)
	var bar := ScoreBar.new()
	row.add_child(bar)
	var number := UI.label("", "JoineryNumeric")
	number.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	number.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	number.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	STYLE.on_theme(
		number, func() -> void: number.custom_minimum_size.x = roundf(UI_THEME.unit(number) * 38.0)
	)
	row.add_child(number)
	# A quiet link as tall as a caption line (UI.quiet_link is a full control height), never
	# narrower than it is tall, so the rows stay compact and every Fix is a full target.
	var fix := Button.new()
	fix.text = "Fix"
	fix.theme_type_variation = "JoineryQuietLink"
	fix.accessibility_name = "Fix %s" % str(entry.get("label", ""))
	fix.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(fix)
	STYLE.on_theme_overrides(
		fix,
		func() -> void:
			var side := float(UI_THEME.control_h(fix, "xs"))
			fix.custom_minimum_size = Vector2(side, side)
			for box_state: String in ["normal", "hover", "pressed", "disabled", "focus"]:
				fix.remove_theme_stylebox_override(box_state)
				var source := fix.get_theme_stylebox(box_state, "JoineryQuietLink")
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				box.content_margin_top = 0.0
				box.content_margin_bottom = 0.0
				fix.add_theme_stylebox_override(box_state, box)
	)
	var id := str(entry.get("id", ""))
	fix.pressed.connect(func() -> void: fix_requested.emit([id]))
	row.add_child(fix)
	# Fix all is the card's one call to action; an area's own Fix shows when its row is pointed at.
	STYLE.reveal_on_hover(row, fix)
	return {"row": row, "label": label, "bar": bar, "number": number, "fix": fix}


func _fill_findings(findings: Array) -> void:
	for child: Node in _findings.get_children():
		child.queue_free()
	_all_shown = false
	var shown: Array = []
	var first := _first_findings()
	for index: int in findings.size():
		var row := _finding_row(findings[index] as Dictionary)
		row.visible = index < first
		_findings.add_child(row)
		if row.visible:
			shown.append(row)
	_findings.visible = not findings.is_empty()
	_more.text = "Show all %d" % findings.size()
	_more.visible = findings.size() > first
	UI_MOTION.stagger(shown)


## How many findings the closed card shows: one fewer in a narrow sidebar, where the card's
## lines wrap and it would otherwise outgrow a short window.
func _first_findings() -> int:
	var narrow := size.x > 0.0 and size.x < NARROW_EM * UI_THEME.font_size(self, "body")
	return FIRST_FINDINGS - 1 if narrow else FIRST_FINDINGS


func _finding_row(item: Dictionary) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var severity := str(item.get("severity", "low"))
	var dot := UI.status_dot(str(DOTS.get(severity, "off")))
	row.add_child(dot)
	var name := str(item.get("path", "")).get_file()
	var shown := "%s  %s → %s" % [name, str(item.get("measured", "")), str(item.get("target", ""))]
	if kind != "match":
		# A check's finding reads best in its own words: what was seen, and where.
		shown = str(item.get("message", ""))
		if name != "project.godot":
			shown = "%s: %s" % [name, shown]
	var words := UI.label(shown, "JoineryCaption")
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	var fix: Variant = item.get("fix")
	var how := (
		str((fix as Dictionary).get("label", ""))
		if fix is Dictionary
		else "Needs you: " + str(item.get("needs_you", ""))
	)
	words.tooltip_text = (
		"%s\n%s\n%s" % [str(item.get("path", "")), str(item.get("message", "")), how]
	)
	row.tooltip_text = words.tooltip_text
	row.accessibility_name = "%s, %s: %s" % [name, severity, str(item.get("message", ""))]
	row.add_child(words)
	return row


## The guidelines open in place of the scores and findings, and close back to them.
func _by_guideline(open: bool) -> void:
	_areas.visible = not open and not _area_rows.is_empty()
	_findings.visible = not open and _findings.get_child_count() > 0
	var hidden_rows := false
	for child: Node in _findings.get_children():
		hidden_rows = hidden_rows or not (child as Control).visible
	_more.visible = not open and hidden_rows


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED:
		_fit_first()


## Shows the first findings for the card's width now (it settles after the rows are made).
func _fit_first() -> void:
	if _findings == null or _all_shown:
		return
	var rows: Array = []
	for child: Node in _findings.get_children():
		if child is Control and not child.is_queued_for_deletion():
			rows.append(child)
	var first := _first_findings()
	for index: int in rows.size():
		(rows[index] as Control).visible = index < first
	_more.visible = rows.size() > first


func _show_all() -> void:
	_all_shown = true
	var shown: Array = []
	for child: Node in _findings.get_children():
		if child is Control and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _fixable(findings: Array) -> int:
	var count := 0
	for item: Variant in findings:
		if (item as Dictionary).get("fix") is Dictionary:
			count += 1
	return count


func _needs_detail(findings: Array) -> String:
	var lines := PackedStringArray()
	for item: Variant in findings:
		var entry := item as Dictionary
		if not entry.get("fix") is Dictionary:
			lines.append("%s: %s" % [str(entry.get("path", "")), str(entry.get("needs_you", ""))])
	return "\n".join(lines)


func _ready_count() -> int:
	var count := 0
	for row: Variant in _run:
		if str((row as Dictionary).get("state", "")) == "in_review":
			count += 1
	return count


func _scores() -> Dictionary:
	var scores := {}
	for area: Variant in audit.get("areas", []):
		scores[str((area as Dictionary).get("id", ""))] = int((area as Dictionary).get("score", 0))
	return scores


func _total(scores: Dictionary) -> int:
	var total := 0
	for value: Variant in scores.values():
		total += int(value)
	return total


## While the changes run: the details step back; the task cards say what happens.
func _quiet_details() -> void:
	if guidelines != null:
		# Closed first: closing brings the scores back, which step back just below.
		guidelines.call("set_open", false)
		(guidelines.get("head") as Control).hide()
	_foot.hide()
	_areas.hide()
	_findings.hide()
	_more.hide()
	_again.hide()
	stop.show()
	close.hide()
	for id: String in _area_rows:
		_show_fix(_area_rows[id]["fix"] as Button, false)


## A Fix link that isn't offered keeps its place, so the scores stay in one column.
func _show_fix(fix: Button, on: bool) -> void:
	fix.disabled = not on
	fix.modulate.a = 1.0 if on else 0.0
	fix.mouse_filter = Control.MOUSE_FILTER_STOP if on else Control.MOUSE_FILTER_IGNORE
	fix.focus_mode = Control.FOCUS_ALL if on else Control.FOCUS_NONE


func _working(words: String) -> void:
	_dots.show()
	_dots.set("running", true)
	_status.hide()
	_step.text = words
	_step.tooltip_text = words


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words


func _on_stop() -> void:
	if stop.disabled:
		return
	stop.disabled = true
	_step.text = "Stopping…"
	stop_pressed.emit()


func _target_picker() -> OptionButton:
	var picker := OptionButton.new()
	picker.accessibility_name = "Target machine"
	picker.tooltip_text = "Judge frame times and budgets for this machine"
	picker.flat = true
	picker.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	picker.fit_to_longest_item = false
	for index: int in TARGETS.size():
		var id: String = TARGETS.keys()[index]
		picker.add_item(str(TARGETS[id]), index)
		picker.set_item_metadata(index, id)
	UI.bind_height(picker, "xs")
	picker.item_selected.connect(
		func(index: int) -> void: target_changed.emit(str(picker.get_item_metadata(index)))
	)
	return picker


## Show [param target] as the picked machine, without asking to measure again.
func show_target(target: String) -> void:
	if target_picker == null:
		return
	for index: int in target_picker.item_count:
		if str(target_picker.get_item_metadata(index)) == target:
			target_picker.select(index)


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
