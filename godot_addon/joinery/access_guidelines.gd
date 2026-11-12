@tool
extends VBoxContainer
## The accessibility card's findings by Xbox Accessibility Guideline (ADR 0067), under a
## disclosure: "Xbox Accessibility Guidelines · 9 of 23 checked". Open, it lists what failed
## first (each guideline opens to its findings in their own words), then what Joinery
## measured and found fine, then what it checks but didn't measure this time, and last one
## line naming the guidelines a person has to judge. The report's facts carry the list
## (joinery/access_xag.py); the card only draws it.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## Status -> [order, dot state, trailing words].
const STATUS := {
	"fails": [0, "attention", ""],
	"passes": [1, "ready", "passes"],
	"not_run": [2, "off", "not measured"],
	"not_checked": [3, "off", ""],
}

var toggle: Button
var _count: Label
var _body: VBoxContainer
var _rows: VBoxContainer
var _left: Label
var _xag: Dictionary = {}


func _init() -> void:
	name = "Guidelines"
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Findings by Xbox Accessibility Guideline"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	add_child(head)
	toggle = UI.disclosure("Xbox Accessibility Guidelines")
	toggle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toggle.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	UI.bind_height(toggle, "xs")
	head.add_child(toggle)
	_count = UI.label("", "JoineryNumeric")
	_count.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	head.add_child(_count)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryTight"
	_body.hide()
	add_child(_body)
	_rows = VBoxContainer.new()
	_rows.theme_type_variation = "JoineryTight"
	_body.add_child(_rows)
	_left = UI.label("", "JoineryCaption", true)
	_body.add_child(_left)
	toggle.toggled.connect(set_open)
	hide()


## Show the report's [code]facts.xag[/code], its [param findings] filed under each guideline.
func show_xag(xag: Dictionary, findings: Array) -> void:
	_xag = xag
	for child: Node in _rows.get_children():
		child.queue_free()
	var guidelines: Array = xag.get("guidelines", [])
	visible = not guidelines.is_empty()
	if not visible:
		return
	_count.text = "%d of %d checked" % [int(xag.get("checked", 0)), int(xag.get("total", 0))]
	_count.tooltip_text = (
		(
			"Joinery measures %d of the %d Xbox Accessibility Guidelines (version %s); "
			+ "a person judges the rest."
		)
		% [int(xag.get("checked", 0)), int(xag.get("total", 0)), str(xag.get("version", ""))]
	)
	var by_id := {}
	for item: Variant in findings:
		by_id[str((item as Dictionary).get("id", ""))] = item
	var ordered := guidelines.duplicate()
	ordered.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool:
			var x := int(STATUS.get(str(a.get("status")), [9])[0])
			var y := int(STATUS.get(str(b.get("status")), [9])[0])
			return x < y if x != y else str(a.get("id")) < str(b.get("id"))
	)
	var left := PackedStringArray()
	for entry: Variant in ordered:
		var guideline := entry as Dictionary
		var status := str(guideline.get("status", "not_checked"))
		if status == "not_checked":
			left.append(_title(guideline))
			continue
		var filed: Array = []
		for id: Variant in guideline.get("findings", []):
			if by_id.has(str(id)):
				filed.append(by_id[str(id)])
		_rows.add_child(_row(guideline, status, filed))
	_left.text = "A person judges the other %d: %s." % [left.size(), ", ".join(left)]
	_left.visible = not left.is_empty()


## Open or close the list.
func set_open(open: bool) -> void:
	toggle.set_pressed_no_signal(open)
	_body.visible = open
	if open:
		UI_MOTION.reveal(_body)
		UI_MOTION.stagger(_rows.get_children())


## Each listed guideline as shown: "102 Contrast · 2 problems", "101 Text display · passes".
func row_words() -> PackedStringArray:
	var words := PackedStringArray()
	for row: Node in _rows.get_children():
		if row.is_queued_for_deletion():
			continue
		words.append(str(row.get_meta("words", "")))
	return words


## The guidelines left to a person, as the last line says them.
func left_text() -> String:
	return _left.text if _left.visible else ""


func coverage_text() -> String:
	return _count.text


static func _title(guideline: Dictionary) -> String:
	return "%s %s" % [str(guideline.get("id", "")), str(guideline.get("name", ""))]


func _row(guideline: Dictionary, status: String, filed: Array) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	line.mouse_filter = Control.MOUSE_FILTER_PASS
	UI.bind_height(line, "xs", false)
	block.add_child(line)
	var spec: Array = STATUS.get(status, STATUS["not_checked"])
	var dot := UI.status_dot(str(spec[1]))
	line.add_child(dot)
	var title := _title(guideline)
	var trailing := str(spec[2])
	if status == "fails":
		trailing = "%d %s" % [filed.size(), "problem" if filed.size() == 1 else "problems"]
	var checks: Array = guideline.get("checks", [])
	var what := "Joinery checks: " + ", ".join(PackedStringArray(checks))
	var name_control: Control
	if status == "fails":
		var open := UI.disclosure(title)
		open.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		open.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		UI.bind_height(open, "xs")
		open.accessibility_name = "%s, %s" % [title, trailing]
		name_control = open
	else:
		var words := UI.label(title, "JoineryCaption")
		words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		words.mouse_filter = Control.MOUSE_FILTER_PASS
		name_control = words
	name_control.tooltip_text = what
	line.tooltip_text = what
	line.add_child(name_control)
	var tail := UI.label(trailing, "JoineryCaption")
	tail.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	tail.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	line.add_child(tail)
	block.set_meta("words", "%s · %s" % [title, trailing])
	if status == "fails":
		# The findings line up under the guideline's name, past its dot.
		var indent := MarginContainer.new()
		indent.hide()
		block.add_child(indent)
		STYLE.on_theme_overrides(
			indent,
			func() -> void:
				var left := UI_THEME.dot_px(indent) + UI_THEME.space(indent, 1)
				indent.add_theme_constant_override("margin_left", left)
		)
		var details := VBoxContainer.new()
		details.theme_type_variation = "JoineryTight"
		indent.add_child(details)
		for item: Variant in filed:
			var finding := item as Dictionary
			var shown := str(finding.get("message", ""))
			var file := str(finding.get("path", "")).get_file()
			if not file.is_empty() and file != "project.godot":
				shown = "%s: %s" % [file, shown]
			var words := UI.label(shown, "JoineryCaption", true)
			words.tooltip_text = shown
			details.add_child(words)
		(name_control as Button).toggled.connect(
			func(on: bool) -> void:
				indent.visible = on
				if on:
					UI_MOTION.reveal(indent)
		)
	return block
