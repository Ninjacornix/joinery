@tool
extends Control
## A quiet ring in the sidebar header that fills with the context Journeyman's conversation
## holds. Hovering shows a card: context used of the model's window, this session's tokens
## (input with the cached part, output) and the model. It shows only what the provider reports
## (Codex token usage, Claude Code's stream-json usage, ACP agents that send usage) and stays
## hidden while nothing is reported. It never polls: [method show_usage] is fed by the chat
## bridge's usage events.
##
## The ring is muted; it turns the danger colour from 90% full, when the conversation is close
## to the model's limit. Fill changes ease out over UI_MOTION's "base" duration.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
## From this share of the window the ring warns.
const NEAR_FULL := 0.9

## The latest usage event ({} = none reported).
var usage: Dictionary = {}
var _shown := 0.0
var _tween: Tween


func _init() -> void:
	hide()
	mouse_filter = Control.MOUSE_FILTER_STOP
	focus_mode = Control.FOCUS_NONE
	size_flags_vertical = Control.SIZE_SHRINK_CENTER
	accessibility_name = "Context used"
	STYLE.on_theme(
		self,
		func() -> void:
			var side := float(UI_THEME.control_h(self, "sm"))
			custom_minimum_size = Vector2(side, side)
			queue_redraw()
	)


## Show [param event] (chat_bridge.gd's usage event); {} or an event with nothing reported
## hides the ring.
func show_usage(event: Dictionary) -> void:
	usage = event.duplicate()
	var reported := (
		_count("context_used") > 0 or _count("input_tokens") > 0 or _count("output_tokens") > 0
	)
	if not reported:
		usage = {}
		hide()
		return
	tooltip_text = summary()
	var target := fraction()
	if not visible:
		_shown = target
		UI_MOTION.enter(self)
		queue_redraw()
		return
	if _tween != null and _tween.is_valid():
		_tween.kill()
	if not UI_MOTION.is_enabled() or not is_inside_tree():
		_shown = target
		queue_redraw()
		return
	_tween = create_tween().set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
	_tween.tween_method(_set_shown, _shown, target, UI_MOTION.duration("base"))


## The share of the context window in use, 0 when the window is not reported.
func fraction() -> float:
	var window := _count("context_window")
	return clampf(float(_count("context_used")) / window, 0.0, 1.0) if window > 0 else 0.0


## One line for the tooltip trigger and screen readers ("Context 23% used").
func summary() -> String:
	if _count("context_window") > 0:
		return "Context %d%% used" % roundi(fraction() * 100.0)
	if _count("context_used") > 0:
		return "Context %s tokens" % compact(_count("context_used"))
	return "Tokens this session"


## The hover card: context, this session's tokens and the model, as label and value rows.
func card() -> Control:
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	# A tooltip lives in its own window: carry the sidebar's theme along.
	var owner_control := get_parent_control()
	while owner_control != null and owner_control.theme == null:
		owner_control = owner_control.get_parent_control()
	if owner_control != null:
		column.theme = owner_control.theme
	column.add_child(_text(summary(), ""))
	var rows := GridContainer.new()
	rows.columns = 2
	rows.add_theme_constant_override("h_separation", UI_THEME.space(self, 4))
	column.add_child(rows)
	if _count("context_used") > 0:
		var context := compact(_count("context_used"))
		if _count("context_window") > 0:
			context += " of " + compact(_count("context_window"))
		_row(rows, "Context", context)
	if _count("input_tokens") > 0:
		var input := compact(_count("input_tokens"))
		if _count("cached_tokens") > 0:
			input += " (%s cached)" % compact(_count("cached_tokens"))
		_row(rows, "Input this session", input)
	if _count("output_tokens") > 0:
		_row(rows, "Output this session", compact(_count("output_tokens")))
	var model := str(usage.get("model_name", ""))
	if model.is_empty():
		model = str(usage.get("model", ""))
	if not model.is_empty():
		_row(rows, "Model", model)
	return column


func _make_custom_tooltip(_for_text: String) -> Object:
	return card()


## 1234 -> "1.2k", 272000 -> "272k", 1000000 -> "1M".
static func compact(count: int) -> String:
	if count >= 1_000_000:
		return _trim("%.1f" % (count / 1_000_000.0)) + "M"
	if count >= 10_000:
		return "%dk" % roundi(count / 1000.0)
	if count >= 1000:
		return _trim("%.1f" % (count / 1000.0)) + "k"
	return str(count)


static func _trim(number: String) -> String:
	return number.trim_suffix(".0")


func _row(grid: GridContainer, name: String, value: String) -> void:
	grid.add_child(_text(name, "JoineryCaption"))
	var figure := _text(value, "JoineryNumeric")
	figure.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	figure.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.add_child(figure)


## A card label at its full width: the card sizes to its text, nothing ellipsizes.
static func _text(value: String, variation: String) -> Label:
	var label := Label.new()
	label.text = value
	label.theme_type_variation = variation
	return label


func _count(key: String) -> int:
	return maxi(0, int(usage.get(key, 0)))


func _set_shown(value: float) -> void:
	_shown = value
	queue_redraw()


func _draw() -> void:
	var unit := UI_THEME.unit(self)
	var width := maxf(1.5, roundf(unit * 2.0))
	var radius := minf(size.x, size.y) * 0.32
	var center := size / 2.0
	# A full, clearly drawn track: the ring reads as a gauge filling up, never as a spinner.
	var track := UI_THEME.color(self, "border_strong")
	track.a *= 0.6
	draw_arc(center, radius, 0.0, TAU, 48, track, width, true)
	if _shown <= 0.0:
		return
	var role := "danger" if _shown >= NEAR_FULL else "text"
	var start := -PI / 2.0
	draw_arc(
		center, radius, start, start + TAU * _shown, 48, UI_THEME.color(self, role), width, true
	)
