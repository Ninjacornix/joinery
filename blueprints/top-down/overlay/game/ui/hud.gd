extends CanvasLayer
## Health, score, wave and dash cooldown while playing; a banner when a wave
## starts; the win or lose screen at the end. Built in code with the core
## menu style, so it needs no art and keeps text at the minimum size or above.

signal retry_requested
signal main_menu_requested

const HEALTH_COLOUR := Color("#ef5f7a")
const DASH_COLOUR := Color("#55d1bf")

var root: Control
var health_bar: ProgressBar
var health_label: Label
var score_label: Label
var wave_label: Label
var dash_bar: ProgressBar
var banner: Label
var end_panel: PanelContainer
var end_title: Label
var end_score_label: Label
var end_time_label: Label
var again_button: Button
var menu_button: Button

var _banner_left: float = 0.0
## What the labels show, kept so a language change can draw them again.
var _health: Vector2i = Vector2i.ZERO
var _score: int = 0
var _wave: Vector2i = Vector2i.ZERO
var _end: Dictionary = {}


func _ready() -> void:
	layer = 10
	root = Control.new()
	root.name = "Root"
	root.theme = UiStyle.build_theme()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)
	_build_stats()
	_build_banner()
	_build_end_panel()
	set_health(0, 0)
	set_score(0)
	set_wave(0, 0)
	set_dash(1.0)


func _notification(what: int) -> void:
	if what == NOTIFICATION_TRANSLATION_CHANGED and score_label != null:
		set_health(_health.x, _health.y)
		set_score(_score)
		set_wave(_wave.x, _wave.y)
		if end_panel.visible:
			_render_end()


func _process(delta: float) -> void:
	if _banner_left > 0.0:
		_banner_left -= delta
		if _banner_left <= 0.0:
			banner.visible = false


func set_health(health: int, max_health: int) -> void:
	_health = Vector2i(health, max_health)
	health_bar.max_value = maxf(float(max_health), 1.0)
	health_bar.value = float(health)
	health_label.text = tr("%d / %d") % [health, max_health]


func set_score(value: int) -> void:
	_score = value
	score_label.text = tr("Score: %d") % value


func set_wave(wave: int, total: int) -> void:
	_wave = Vector2i(wave, total)
	if wave <= 0:
		wave_label.text = tr("Get ready")
	else:
		wave_label.text = tr("Wave %d of %d") % [wave, total]


## `ratio` is 1.0 when a dash is available.
func set_dash(ratio: float) -> void:
	dash_bar.value = clampf(ratio, 0.0, 1.0)


## Shows `text` (already translated) in the middle of the screen for a while.
func show_banner(text: String, seconds: float = 1.6) -> void:
	banner.text = text
	banner.visible = true
	_banner_left = seconds


func show_end(won: bool, result: Dictionary) -> void:
	banner.visible = false
	_end = {"won": won, "result": result}
	_render_end()
	end_panel.visible = true
	again_button.grab_focus.call_deferred()


func _render_end() -> void:
	var won: bool = bool(_end.get("won", false))
	var result: Dictionary = _end.get("result", {})
	end_title.text = tr("Arena cleared!") if won else tr("You were overwhelmed")
	end_title.add_theme_color_override(&"font_color", UiStyle.ACCENT if won else HEALTH_COLOUR)
	end_score_label.text = tr("Score: %d") % int(result.get("score", 0))
	end_time_label.text = tr("Time: %s") % format_time(float(result.get("time", 0.0)))
	again_button.text = tr("Play again") if won else tr("Try again")


func hide_end() -> void:
	end_panel.visible = false


func is_end_visible() -> bool:
	return end_panel.visible


## "m:ss.c" for a time in seconds.
static func format_time(seconds: float) -> String:
	var minutes: int = floori(seconds / 60.0)
	return "%d:%04.1f" % [minutes, seconds - float(minutes) * 60.0]


func _build_stats() -> void:
	var stats: PanelContainer = PanelContainer.new()
	stats.name = "Stats"
	var style: StyleBoxFlat = UiStyle.box(Color(UiStyle.PANEL, 0.88), UiStyle.px(12), UiStyle.px(18))
	stats.add_theme_stylebox_override(&"panel", style)
	stats.position = Vector2(UiStyle.px(28), UiStyle.px(28))
	stats.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var column: VBoxContainer = VBoxContainer.new()
	column.add_theme_constant_override(&"separation", UiStyle.px(8))
	stats.add_child(column)

	health_bar = _bar(HEALTH_COLOUR)
	health_bar.name = "HealthBar"
	health_label = _dynamic_label()
	column.add_child(_bar_row("Health", health_bar, health_label))

	dash_bar = _bar(DASH_COLOUR)
	dash_bar.name = "DashBar"
	dash_bar.max_value = 1.0
	dash_bar.step = 0.01
	column.add_child(_bar_row("Dash", dash_bar, null))

	score_label = _dynamic_label()
	score_label.name = "Score"
	column.add_child(score_label)
	wave_label = _dynamic_label()
	wave_label.name = "Wave"
	column.add_child(wave_label)
	root.add_child(stats)


func _build_banner() -> void:
	banner = Label.new()
	banner.name = "Banner"
	banner.theme_type_variation = &"TitleLabel"
	banner.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	banner.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	banner.add_theme_color_override(&"font_outline_color", Color(0.0, 0.0, 0.0, 0.85))
	banner.add_theme_constant_override(&"outline_size", UiStyle.px(10))
	banner.set_anchors_and_offsets_preset(Control.PRESET_CENTER_TOP)
	banner.grow_horizontal = Control.GROW_DIRECTION_BOTH
	banner.offset_top = UiStyle.px(150)
	banner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	banner.visible = false
	root.add_child(banner)


func _build_end_panel() -> void:
	end_panel = PanelContainer.new()
	end_panel.name = "EndPanel"
	end_panel.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	end_panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	end_panel.grow_vertical = Control.GROW_DIRECTION_BOTH
	end_panel.visible = false
	var column: VBoxContainer = VBoxContainer.new()
	end_title = UiStyle.title("Arena cleared!")
	end_score_label = _dynamic_label()
	end_score_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	end_time_label = _dynamic_label()
	end_time_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	again_button = UiStyle.button("Play again")
	again_button.name = "Again"
	menu_button = UiStyle.button("Main menu")
	menu_button.name = "MainMenu"
	again_button.pressed.connect(retry_requested.emit)
	menu_button.pressed.connect(main_menu_requested.emit)
	# Title and retry text are set with tr() when the panel opens.
	end_title.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	again_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	for node: Control in [end_title, end_score_label, end_time_label, again_button, menu_button]:
		column.add_child(node)
	end_panel.add_child(column)
	root.add_child(end_panel)


func _bar(colour: Color) -> ProgressBar:
	var bar: ProgressBar = ProgressBar.new()
	bar.show_percentage = false
	bar.custom_minimum_size = Vector2(UiStyle.px(280), UiStyle.px(26))
	bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	bar.add_theme_stylebox_override(&"background", UiStyle.box(UiStyle.BUTTON, UiStyle.px(6), 0))
	bar.add_theme_stylebox_override(&"fill", UiStyle.box(colour, UiStyle.px(6), 0))
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return bar


func _bar_row(text: String, bar: ProgressBar, value_label: Label) -> HBoxContainer:
	var line: HBoxContainer = HBoxContainer.new()
	line.add_theme_constant_override(&"separation", UiStyle.px(14))
	var label: Label = Label.new()
	label.text = text
	label.custom_minimum_size = Vector2(UiStyle.px(110), 0)
	line.add_child(label)
	line.add_child(bar)
	if value_label != null:
		line.add_child(value_label)
	return line


func _dynamic_label() -> Label:
	var label: Label = Label.new()
	label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	return label
