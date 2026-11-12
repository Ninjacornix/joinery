@tool
extends PanelContainer
## One bus in the Mixer tab: its name and level on top, a live meter beside a vertical fader,
## Mute and Solo, then a reverb and a compressor, each an on/off check with one amount (the
## reverb's wet mix, the compressor's threshold). Emits [signal changed] with the bus's whole
## new state; the tab plays it and decides whether it is unsaved.

signal changed(state: Dictionary)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const BUSES := preload("mixer_buses.gd")
const TRACK := preload("music_track.gd")
## Fader height in control heights.
const FADER_ROWS := 2.5

var state: Dictionary = {}
var fader: VSlider
var mute: Button
var solo: Button
var reverb: CheckBox
var reverb_amount: HSlider
var compressor: CheckBox
var compressor_amount: HSlider
var meter: Meter
var _title: Label
var _value: Label
var _syncing := false


## A vertical level bar: the bus's peak, quiet to loud, with a hairline at 0 dB.
class Meter:
	extends Control
	var level_db := BUSES.SILENT_DB:
		set(value):
			if not is_equal_approx(value, level_db):
				level_db = value
				queue_redraw()

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		size_flags_vertical = Control.SIZE_EXPAND_FILL
		accessibility_name = "Level"

	func _notification(what: int) -> void:
		if what == NOTIFICATION_THEME_CHANGED:
			custom_minimum_size.x = UI_THEME.space(self, 2)

	func _draw() -> void:
		var radius := float(UI_THEME.radius(self, "xs"))
		var track := StyleBoxFlat.new()
		track.bg_color = UI_THEME.color(self, "surface_sunken")
		track.border_color = UI_THEME.color(self, "border_subtle")
		track.set_border_width_all(maxi(1, roundi(UI_THEME.unit(self))))
		track.set_corner_radius_all(int(radius))
		draw_style_box(track, Rect2(Vector2.ZERO, size))
		var share := clampf((level_db - BUSES.MIN_DB) / (BUSES.MAX_DB - BUSES.MIN_DB), 0.0, 1.0)
		if share > 0.0:
			var fill := StyleBoxFlat.new()
			fill.bg_color = UI_THEME.color(self, "danger" if level_db > 0.0 else "accent")
			fill.set_corner_radius_all(int(radius))
			var tall := size.y * share
			draw_style_box(fill, Rect2(0.0, size.y - tall, size.x, tall))
		var zero := size.y * (BUSES.MAX_DB / (BUSES.MAX_DB - BUSES.MIN_DB))
		draw_line(
			Vector2(0.0, zero),
			Vector2(size.x, zero),
			UI_THEME.color(self, "border_strong"),
			maxf(1.0, roundf(UI_THEME.unit(self)))
		)


func _init() -> void:
	theme_type_variation = "JoineryCard"
	size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	_title = UI.label("")
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_title)
	_value = UI.label("", "JoineryNumeric")
	_value.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	head.add_child(_value)
	var body := HBoxContainer.new()
	body.theme_type_variation = "JoineryRowTight"
	column.add_child(body)
	meter = Meter.new()
	body.add_child(meter)
	fader = VSlider.new()
	# The wheel scrolls the panel; drag or arrow keys change the level.
	fader.scrollable = false
	fader.min_value = BUSES.MIN_DB
	fader.max_value = BUSES.MAX_DB
	fader.step = 0.5
	fader.tick_count = 0
	fader.size_flags_vertical = Control.SIZE_EXPAND_FILL
	fader.value_changed.connect(func(value: float) -> void: _change("volume_db", value))
	body.add_child(fader)
	# Beside the fader: Mute and Solo, then each effect as a check over its one amount.
	var side := VBoxContainer.new()
	side.theme_type_variation = "JoineryFlush"
	side.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_child(side)
	var toggles := HBoxContainer.new()
	toggles.theme_type_variation = "JoineryRowTight"
	side.add_child(toggles)
	mute = _toggle("M", "mute")
	toggles.add_child(mute)
	solo = _toggle("S", "solo")
	toggles.add_child(solo)
	reverb = _check("Reverb", "reverb")
	side.add_child(reverb)
	reverb_amount = _amount(0.0, 1.0, 0.01, "reverb", "wet")
	side.add_child(reverb_amount)
	compressor = _check("Compress", "compressor")
	side.add_child(compressor)
	compressor_amount = _amount(BUSES.MIN_THRESHOLD, 0.0, 0.5, "compressor", "threshold")
	side.add_child(compressor_amount)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		fader.custom_minimum_size.y = UI_THEME.control_h(self, "lg") * FADER_ROWS
		custom_minimum_size.x = UI_THEME.font_size(self, "body") * 10
		fader.custom_minimum_size.x = UI_THEME.control_h(self, "sm")
		for button: Button in [mute, solo]:
			button.custom_minimum_size.x = UI_THEME.control_h(self, "sm")


## Show [param bus] ({name, volume_db, mute, solo, …} from mixer_buses.gd) without emitting.
func show_state(bus: Dictionary) -> void:
	state = bus.duplicate(true)
	name = str(state.get("name", "Bus"))
	_syncing = true
	_title.text = name
	accessibility_name = name + " bus"
	fader.value = clampf(float(state.get("volume_db", 0.0)), BUSES.MIN_DB, BUSES.MAX_DB)
	fader.accessibility_name = name + " level"
	fader.tooltip_text = "%s level. Drag to mix; it plays at once." % name
	mute.set_pressed_no_signal(bool(state.get("mute", false)))
	solo.set_pressed_no_signal(bool(state.get("solo", false)))
	_show_effect(reverb, reverb_amount, state.get("reverb"), "wet")
	_show_effect(compressor, compressor_amount, state.get("compressor"), "threshold")
	_syncing = false
	_sync_words()


## The bus's peak now, for the meter (the tab polls while it is visible).
func set_level(db: float) -> void:
	meter.level_db = db


func _toggle(text: String, key: String) -> Button:
	var button := Button.new()
	button.text = text
	button.theme_type_variation = "JoinerySegment"
	button.toggle_mode = true
	button.focus_mode = Control.FOCUS_ALL
	UI.bind_height(button, "sm", false)
	UI_MOTION.press_feedback(button)
	button.toggled.connect(func(on: bool) -> void: _change(key, on))
	return button


func _check(text: String, key: String) -> CheckBox:
	var check := CheckBox.new()
	check.text = text
	check.focus_mode = Control.FOCUS_ALL
	check.toggled.connect(func(on: bool) -> void: _set_effect(key, "enabled", on))
	return check


func _amount(low: float, high: float, step: float, key: String, field: String) -> HSlider:
	var slider := HSlider.new()
	slider.scrollable = false
	slider.min_value = low
	slider.max_value = high
	slider.step = step
	slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(slider, "sm")
	slider.value_changed.connect(func(value: float) -> void: _set_effect(key, field, value))
	return slider


func _show_effect(check: CheckBox, slider: HSlider, effect: Variant, field: String) -> void:
	var on := effect is Dictionary and bool(effect.get("enabled", false))
	check.set_pressed_no_signal(on)
	if effect is Dictionary:
		slider.set_value_no_signal(float(effect.get(field, slider.value)))
	elif field == "wet":
		slider.set_value_no_signal(BUSES.ROOM["wet"])
	else:
		slider.set_value_no_signal(BUSES.DUCKING["threshold"])
	slider.editable = on
	slider.modulate.a = 1.0 if on else 0.5


func _change(key: String, value: Variant) -> void:
	if _syncing or state.is_empty():
		return
	state[key] = snappedf(float(value), 0.1) if key == "volume_db" else value
	_sync_words()
	changed.emit(state.duplicate(true))


func _set_effect(key: String, field: String, value: Variant) -> void:
	if _syncing or state.is_empty():
		return
	var effect: Variant = state.get(key)
	if not effect is Dictionary:
		effect = (
			{"enabled": false, "wet": BUSES.ROOM["wet"]}
			if key == "reverb"
			else {"enabled": false, "threshold": BUSES.DUCKING["threshold"], "sidechain": ""}
		)
		state[key] = effect
	effect[field] = value
	_show_effect(
		reverb if key == "reverb" else compressor,
		reverb_amount if key == "reverb" else compressor_amount,
		effect,
		"wet" if key == "reverb" else "threshold"
	)
	_sync_words()
	changed.emit(state.duplicate(true))


func _sync_words() -> void:
	var db := float(state.get("volume_db", 0.0))
	_value.text = "Off" if db <= BUSES.MIN_DB else TRACK.db_text(db)
	var room: Variant = state.get("reverb")
	var wet := float(room.get("wet", 0.0)) if room is Dictionary else BUSES.ROOM["wet"]
	reverb.tooltip_text = "Reverb: %d%% wet" % roundi(wet * 100.0)
	reverb_amount.tooltip_text = reverb.tooltip_text
	reverb_amount.accessibility_name = "%s reverb amount" % name
	var squeeze: Variant = state.get("compressor")
	var threshold := (
		float(squeeze.get("threshold", 0.0))
		if squeeze is Dictionary
		else float(BUSES.DUCKING["threshold"])
	)
	compressor.tooltip_text = "Compressor: threshold %s" % TRACK.db_text(threshold)
	compressor_amount.tooltip_text = compressor.tooltip_text
	compressor_amount.accessibility_name = "%s compressor threshold" % name
	mute.tooltip_text = "Mute: silence the %s bus" % name
	solo.tooltip_text = "Solo: hear only the %s bus" % name
	mute.accessibility_name = mute.tooltip_text
	solo.accessibility_name = solo.tooltip_text
