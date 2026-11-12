@tool
extends HBoxContainer
## The Review tab's "Game checks" row (ADR 0049, ADR 0050): how the game last scored for
## accessibility and performance, from the checks' saved reports, and a button each that
## opens its card in the sidebar to check again or fix. The fixes themselves are changes
## like any other, with their own rows in the Review tab. Tidy opens "/tidy" (ADR 0074).

const UI := preload("ui_components.gd")
## The checks' saved reports, inside the project.
const REPORTS := {
	"access": "res://.joinery/checks/access.json", "perf": "res://.joinery/checks/perf.json"
}
const NAMES := {"access": "Accessibility", "perf": "Performance"}
## Seconds between looks at the reports while the row shows.
const LOOK_EVERY := 3.0

var status: Label
var dot: Control
var _slash: Object
var _stamps: Dictionary = {}
var _wait := 0.0


## A row at the top of [param slot] (the Review tab's decided list), using [param slash]
## (slash_commands.gd) to open the cards.
static func attach(slot: Control, slash: Object) -> Control:
	var row: HBoxContainer = load("res://addons/joinery/review_checks.gd").new()
	row.set("_slash", slash)
	slot.add_child(row)
	return row


func _init() -> void:
	name = "GameChecks"
	theme_type_variation = "JoineryRow"
	accessibility_name = "Game checks"
	UI.bind_height(self, "md", false)
	var title := UI.label("Game checks")
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	add_child(title)
	dot = UI.status_dot("off")
	add_child(dot)
	status = UI.label("", "JoineryCaption")
	status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	status.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	status.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(status)
	for check: String in ["access", "perf"]:
		var open := UI.quiet(str(NAMES[check]))
		open.name = "Open_%s" % check
		open.tooltip_text = "Check the game's %s (/%s)" % [str(NAMES[check]).to_lower(), check]
		open.pressed.connect(
			func() -> void:
				if _slash != null and _slash.has_method("request_check"):
					_slash.call("request_check", check)
		)
		add_child(open)
	# What the game never uses: no score, so it opens its card to look.
	var tidy := UI.quiet("Tidy")
	tidy.name = "Open_tidy"
	tidy.tooltip_text = "Find what the game never uses (/tidy)"
	tidy.pressed.connect(
		func() -> void:
			if _slash != null and _slash.has_method("request_check"):
				_slash.call("request_check", "tidy")
	)
	add_child(tidy)
	refresh()


func _process(delta: float) -> void:
	if not is_visible_in_tree():
		return
	_wait -= delta
	if _wait <= 0.0:
		_wait = LOOK_EVERY
		refresh()


## Read the saved reports again when they changed.
func refresh() -> void:
	var parts := PackedStringArray()
	var worst := 100
	var any := false
	for check: String in REPORTS:
		var path := ProjectSettings.globalize_path(str(REPORTS[check]))
		var stamp := FileAccess.get_modified_time(path) if FileAccess.file_exists(path) else 0
		var entry: Dictionary = _stamps.get(check, {})
		if int(entry.get("stamp", -1)) != stamp:
			entry = {"stamp": stamp, "words": "", "score": 100}
			if stamp > 0:
				entry = _read(check, path, stamp)
			_stamps[check] = entry
		if not str(entry["words"]).is_empty():
			parts.append(str(entry["words"]))
			worst = mini(worst, int(entry["score"]))
			any = true
	status.text = " · ".join(parts) if any else "Not checked yet"
	status.tooltip_text = status.text
	dot.set("state", "off" if not any else ("ready" if worst >= 90 else "attention"))


static func _read(check: String, path: String, stamp: int) -> Dictionary:
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not parsed is Dictionary:
		return {"stamp": stamp, "words": "", "score": 100}
	var data := parsed as Dictionary
	var areas: Array = data.get("areas", [])
	if areas.is_empty():
		return {"stamp": stamp, "words": "", "score": 100}
	var total := 0
	for area: Variant in areas:
		total += int((area as Dictionary).get("score", 0))
	var score := roundi(float(total) / areas.size())
	var words := "%s %d%%" % [str(NAMES[check]), score]
	var machine := str((data.get("facts", {}) as Dictionary).get("target_label", ""))
	if check == "perf" and not machine.is_empty():
		words += " (%s)" % machine
	return {"stamp": stamp, "words": words, "score": score}
