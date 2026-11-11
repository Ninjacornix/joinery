@tool
extends PanelContainer
## Details as a drawer: a raised sheet that slides over the review's stage from the rail's side
## (where its trigger is) and back, so opening it never moves the picture or the actions. It
## holds the files with their diffs, the checks that ran and every note, and scrolls when they
## are long. Half the stage wide (at least WIDTH_EM), the whole stage when there is nothing else
## to show. The slide eases out on the way in and is 20% shorter, easing in, on the way out;
## Reduce motion makes it instant.

## The user closed it (its × or Escape).
signal close_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
## Share of the stage's width the drawer takes, and its least width in em.
const WIDTH_SHARE := 0.5
const WIDTH_EM := 24.0

## True while open (or opening).
var open := false
## True when the drawer is the whole stage (nothing to preview); it then has no close button.
var fill := false
## The rows of the drawer: the panel adds the checks, notes and files here.
var body: VBoxContainer
var scroll: ScrollContainer
var gutter: MarginContainer
var _close: Button
var _host: Control
var _tween: Tween


func _init() -> void:
	name = "Details"
	theme_type_variation = "JoineryDrawer"
	mouse_filter = Control.MOUSE_FILTER_STOP
	accessibility_name = "Details: files, checks and notes"
	hide()
	var page := MarginContainer.new()
	page.theme_type_variation = "JoineryPage"
	add_child(page)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	page.add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	var title := UI.label("Details", "JoinerySection")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title)
	_close = UI.icon_button("close", "Close details (Esc)")
	_close.pressed.connect(func() -> void: close_requested.emit())
	head.add_child(_close)
	scroll = ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	scroll.accessibility_name = "Details"
	UI_MOTION.rubber_band(scroll)
	column.add_child(scroll)
	# The gutter keeps the scroll bar off the line counts.
	gutter = MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	body = VBoxContainer.new()
	body.theme_type_variation = "JoineryStack"
	gutter.add_child(body)


## Live over [param host] (the stage), sized to it.
func attach(host: Control) -> void:
	_host = host
	host.add_child(self)
	host.resized.connect(_place)


## Open or close; [param animate] slides it.
func set_open(value: bool, animate: bool) -> void:
	_close.visible = not fill
	if value == open and (visible == value):
		_place()
		return
	open = value
	_stop()
	if open:
		show()
		_place()
		scroll.scroll_vertical = 0
		if animate and UI_MOTION.is_enabled() and is_inside_tree():
			var target := position.x
			position.x = _away()
			_tween = create_tween()
			_tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)
			_tween.tween_property(self, "position:x", target, UI_MOTION.duration("enter"))
		return
	if animate and visible and UI_MOTION.is_enabled() and is_inside_tree():
		_tween = create_tween()
		_tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
		_tween.tween_property(self, "position:x", _away(), UI_MOTION.duration("exit"))
		_tween.tween_callback(hide)
		return
	hide()


## The close button (hidden while the drawer fills the stage).
func close_button() -> Button:
	return _close


func _stop() -> void:
	if _tween != null and _tween.is_valid():
		_tween.kill()
	_tween = null


## Where the drawer rests off the stage: past its trailing edge.
func _away() -> float:
	if _host == null:
		return 0.0
	return -size.x if is_layout_rtl() else _host.size.x


func _place() -> void:
	if _host == null or not is_inside_tree():
		return
	var room := _host.size
	var width := room.x
	if not fill:
		var least := WIDTH_EM * float(UI_THEME.font_size(self, "body"))
		width = minf(room.x, maxf(room.x * WIDTH_SHARE, least))
	size = Vector2(width, room.y)
	# A stage that changes size mid-slide ends the slide: the drawer goes where it belongs.
	_stop()
	if not open:
		position = Vector2(_away(), 0.0)
		hide()
		return
	position = Vector2(0.0 if is_layout_rtl() else room.x - size.x, 0.0)
