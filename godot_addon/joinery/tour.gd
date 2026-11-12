@tool
extends Control
## The first-project tour (ADR 0072): six short steps over the editor, each pointing at one
## place: the Journeyman sidebar, picking something, asking for a change, the review with its
## Accept and Discard, the Timeline to go back, and Share. It opens by itself once, on a new
## project's first open (the project was made with `.joinery/tour-pending`), after project
## setup if that shows; again from Help › Joinery Tour, Project › Tools › Joinery: Tour, or
## "/tour" in the sidebar. Skip, Done or Esc closes it, and either way it is remembered as
## seen for this project (editor project metadata, never in the project's own files).
##
## The editor stays where it is: the rest of the window dims, the place the step is about
## stays lit with an accent ring, and the card sits beside it, or over it when it is most of
## the window. It follows the Joinery theme (light and dark), the Reduce motion preference
## (every move jumps), and the window's width (the card narrows to fit, down to phone-narrow).
## A place this editor doesn't have (stock Godot) leaves the step's card in the middle.

signal finished(completed: bool)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const SELF_PATH := "res://addons/joinery/tour.gd"
## Written by `joinery new` and the new-project wizard; the first open takes it away.
const PENDING := "res://.joinery/tour-pending"
const SEEN_KEY := "tour_seen"
const MENU_ITEM := "Joinery: Tour"
const HELP_ITEM := "Joinery Tour"
const HELP_ID := 7072
## The card's width in ems of the body text; a narrower window gets the window less margins.
const CARD_EM := 22.0
## A place wider than this share of the window gets the card over it, near its bottom.
const COVERS := 0.6
## The steps, in order: what each points at, its headline and one or two plain sentences.
const STEPS: Array[Dictionary] = [
	{
		"id": "sidebar",
		"title": "Journeyman works here",
		"body":
		(
			"Say what to make or change, in your own words. Journeyman does the work in "
			+ "Godot, Blender, Krita and your other apps, then shows you the result."
		),
	},
	{
		"id": "select",
		"title": "Pick what to change",
		"body":
		(
			"Click a model in the view or a node in the scene list. Journeyman works on "
			+ "what you picked, and the message box shows it."
		),
	},
	{
		"id": "ask",
		"title": "Ask for a change",
		"body":
		(
			"Try “make the door a little wider”. A bigger request comes with a short to-do "
			+ "you can edit before you press Go."
		),
	},
	{
		"id": "review",
		"title": "Accept or Discard",
		"body":
		(
			"Every change opens here as a before and after. Nothing in your project changes "
			+ "until you press Accept; Discard leaves it as it was."
		),
	},
	{
		"id": "timeline",
		"title": "Go back any time",
		"body":
		(
			"Each change you accept is a step on the Timeline. Open one to see what it did, "
			+ "or go back to it."
		),
	},
	{
		"id": "share",
		"title": "Share your game",
		"body":
		(
			"Build it for the web or the desktop and send it to friends, or publish it on "
			+ "itch.io or Steam."
		),
	},
]
const AGAIN := "See this again with /tour, or Help › Joinery Tour."

## The editor plugin (plugin.gd); null in tests, which set [member targets] instead.
var plugin: EditorPlugin
## Step id -> Callable() returning the Control the step points at (tests, and overrides).
var targets: Dictionary = {}
## The step on screen (0-based).
var index := 0
## The lit place, in this overlay's coordinates; an empty rect dims everything.
var hole := Rect2():
	set(value):
		hole = value
		queue_redraw()
var card: PanelContainer
var count_label: Label
var title_label: Label
var body_label: Label
var again_label: Label
var back: Button
var next: Button
var skip: Button
var _help: PopupMenu
var _moves: Tween
## Counts placements, so only the newest one lands when steps change quickly.
var _placing := 0


## Put the tour in the editor: its menu entries, "/tour", and the first open of a new project.
## Called by plugin.gd with the other tabs; it lives hidden over the editor until opened.
static func install(target: EditorPlugin) -> Control:
	var tour: Control = load(SELF_PATH).new()
	tour.set("plugin", target)
	# Deferred: the editor is still adding its own children while plugins load.
	EditorInterface.get_base_control().add_child.call_deferred(tour)
	target.add_tool_menu_item(MENU_ITEM, tour.open)
	tour.call_deferred("_hook_up")
	target.tree_exiting.connect(
		func() -> void:
			target.remove_tool_menu_item(MENU_ITEM)
			if is_instance_valid(tour):
				tour.call("_unhook")
				tour.queue_free(),
		CONNECT_ONE_SHOT
	)
	return tour


func _init() -> void:
	name = "JoineryTour"
	visible = false
	mouse_filter = Control.MOUSE_FILTER_STOP
	focus_mode = Control.FOCUS_NONE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	accessibility_name = "Tour"
	card = PanelContainer.new()
	card.name = "TourCard"
	card.theme_type_variation = "JoineryCard"
	card.mouse_filter = Control.MOUSE_FILTER_STOP
	add_child(card)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	card.add_child(column)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	column.add_child(words)
	count_label = UI.label("", "JoineryCaption")
	count_label.name = "Count"
	words.add_child(count_label)
	title_label = UI.label("", "JoineryTitle", true)
	title_label.name = "Headline"
	words.add_child(title_label)
	body_label = UI.label("", "", true)
	body_label.name = "Body"
	words.add_child(body_label)
	again_label = UI.label(AGAIN, "JoineryCaption", true)
	again_label.name = "Again"
	words.add_child(again_label)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	column.add_child(row)
	skip = UI.quiet("Skip tour")
	skip.pressed.connect(close.bind(false))
	row.add_child(skip)
	var gap := Control.new()
	gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(gap)
	back = UI.quiet("Back")
	back.pressed.connect(step_back)
	row.add_child(back)
	next = UI.primary("Next")
	next.pressed.connect(step_on)
	row.add_child(next)
	for button: Button in [skip, back, next]:
		UI.bind_height(button, "sm")
		UI_MOTION.press_feedback(button)
	UI_THEME.watch(self)
	resized.connect(_follow)


func _ready() -> void:
	count_label.add_theme_font_override("font", UI_THEME.numeric_font(self))
	card.add_theme_stylebox_override("panel", _card_box())


func _on_theme_rebuilt() -> void:
	card.add_theme_stylebox_override("panel", _card_box())
	queue_redraw()


func _card_box() -> StyleBox:
	var source := get_theme_stylebox("panel", "JoineryCard")
	var box := (source.duplicate() if source != null else StyleBoxFlat.new()) as StyleBox
	box.set_content_margin_all(float(UI_THEME.space(self, 4)))
	if box is StyleBoxFlat:
		var flat := box as StyleBoxFlat
		flat.shadow_color = Color(0, 0, 0, 0.28 if UI_THEME.is_dark(self) else 0.16)
		flat.shadow_size = UI_THEME.space(self, 3)
		flat.shadow_offset = Vector2(0, UI_THEME.space(self, 1))
	return box


# --- Opening and closing ---------------------------------------------------------------------


## Start at the first step. Does nothing while the tour is already open.
func open() -> void:
	if visible or not is_inside_tree():
		return
	index = 0
	get_parent().move_child(self, -1)
	show()
	_show_step(true)
	next.grab_focus.call_deferred()


## Skip (completed false) or Done (true): the tour closes and is remembered as seen.
func close(completed := false) -> void:
	if not visible:
		return
	mark_seen()
	UI_MOTION.exit(card)
	if _moves != null:
		_moves.kill()
	var leave := func() -> void:
		hide()
		hole = Rect2()
	if UI_MOTION.is_enabled():
		get_tree().create_timer(UI_MOTION.duration("exit")).timeout.connect(leave)
	else:
		leave.call()
	finished.emit(completed)


func step_on() -> void:
	if index >= STEPS.size() - 1:
		close(true)
		return
	index += 1
	_show_step(false)


func step_back() -> void:
	if index == 0:
		return
	index -= 1
	_show_step(false)


## Jump to step [param at] (0-based); the visual suite captures each.
func show_step(at: int) -> void:
	index = clampi(at, 0, STEPS.size() - 1)
	if not visible:
		get_parent().move_child(self, -1)
		show()
	_show_step(true)


func _input(event: InputEvent) -> void:
	if not visible or not event is InputEventKey or not event.is_pressed():
		return
	var key := (event as InputEventKey).keycode
	if key == KEY_ESCAPE:
		close(false)
	elif key == KEY_RIGHT:
		step_on()
	elif key == KEY_LEFT:
		step_back()
	else:
		return
	get_viewport().set_input_as_handled()


## True once this project's tour was finished or skipped.
static func seen() -> bool:
	if Engine.is_editor_hint():
		var settings := EditorInterface.get_editor_settings()
		return bool(settings.get_project_metadata("joinery", SEEN_KEY, false))
	var file := ConfigFile.new()
	return file.load("user://joinery_tour.cfg") == OK and bool(file.get_value("tour", "seen"))


static func mark_seen() -> void:
	if Engine.is_editor_hint():
		EditorInterface.get_editor_settings().set_project_metadata("joinery", SEEN_KEY, true)
		return
	var file := ConfigFile.new()
	file.set_value("tour", "seen", true)
	file.save("user://joinery_tour.cfg")


# --- One step --------------------------------------------------------------------------------


func _show_step(first: bool) -> void:
	var step := STEPS[index]
	var last := index == STEPS.size() - 1
	count_label.text = "%d of %d" % [index + 1, STEPS.size()]
	title_label.text = str(step["title"])
	body_label.text = str(step["body"])
	again_label.visible = last
	back.visible = index > 0
	next.text = "Done" if last else "Next"
	next.accessibility_name = "Finish the tour" if last else "Next step"
	card.accessibility_name = "Tour, step %d of %d: %s" % [index + 1, STEPS.size(), step["title"]]
	_place(first)


## The lit place for the step on screen, in this overlay's coordinates (empty: none).
func target_rect() -> Rect2:
	var found: Variant = _target(str(STEPS[index]["id"]))
	var rect := Rect2()
	if found is Rect2:
		rect = found
	elif found is Control and (found as Control).is_visible_in_tree():
		rect = (found as Control).get_global_rect()
	if not rect.has_area():
		return Rect2()
	rect.position -= get_global_rect().position
	return rect.intersection(Rect2(Vector2.ZERO, size))


func _place(first: bool) -> void:
	var lit := target_rect()
	var margin := float(UI_THEME.space(self, 4))
	var width := minf(CARD_EM * UI_THEME.font_size(self, "body"), size.x - margin * 2.0)
	card.custom_minimum_size = Vector2(maxf(width, 0.0), 0)
	card.size = Vector2(width, card.size.y)
	if first:
		card.modulate.a = 0.0
	# Wrapped text has its height once the card has laid out at its width.
	_placing += 1
	var ticket := _placing
	for frame: int in 2:
		await get_tree().process_frame
	if ticket != _placing or not visible:
		return
	card.reset_size()
	var spot := _spot(lit, card.size, margin)
	var animate := UI_MOTION.is_enabled() and not first
	if _moves != null:
		_moves.kill()
	if animate:
		# Moves ease in and out (motion rule 9); the old place and the new overlap.
		_moves = create_tween().set_parallel(true)
		_moves.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
		_moves.tween_property(self, "hole", lit, UI_MOTION.duration("base"))
		_moves.tween_property(card, "position", spot, UI_MOTION.duration("base"))
	else:
		hole = lit
		card.position = spot
	# A first placement a quicker step overtook still needs its entrance.
	if first or is_zero_approx(card.modulate.a):
		UI_MOTION.enter(card)


## Where the card goes: beside the place (left, right, below, above, the first that fits),
## over it near its bottom when the place is most of the window, else the window's middle.
func _spot(lit: Rect2, card_size: Vector2, margin: float) -> Vector2:
	var area := Rect2(Vector2.ONE * margin, size - Vector2.ONE * margin * 2.0)
	var middle := (size - card_size) / 2.0
	if not lit.has_area():
		return middle.max(Vector2.ZERO)
	var gap := float(UI_THEME.space(self, 3)) + _ring_pad()
	var centre := lit.get_center()
	var y := clampf(centre.y - card_size.y / 2.0, area.position.y, area.end.y - card_size.y)
	var x := clampf(centre.x - card_size.x / 2.0, area.position.x, area.end.x - card_size.x)
	if lit.size.x > size.x * COVERS:
		return Vector2(x, maxf(area.position.y, lit.end.y - card_size.y - margin))
	var tries: Array[Vector2] = [
		Vector2(lit.position.x - gap - card_size.x, y),
		Vector2(lit.end.x + gap, y),
		Vector2(x, lit.end.y + gap),
		Vector2(x, lit.position.y - gap - card_size.y),
	]
	for spot: Vector2 in tries:
		if area.encloses(Rect2(spot, card_size)):
			return spot
	return Vector2(
		x, clampf(middle.y, area.position.y, maxf(area.position.y, area.end.y - card_size.y))
	)


func _ring_pad() -> float:
	return float(UI_THEME.space(self, 1))


func _follow() -> void:
	if visible:
		_place(true)


func _draw() -> void:
	var dim := Color(0, 0, 0, 0.5 if UI_THEME.is_dark(self) else 0.32)
	if not hole.has_area():
		draw_rect(Rect2(Vector2.ZERO, size), dim)
		return
	var lit := hole.grow(_ring_pad())
	var corner := UI_THEME.radius(self, "md")
	# Four bands around the lit place, then its corners outside the ring's curve, so the dim
	# stops exactly at the ring.
	draw_rect(Rect2(0, 0, size.x, lit.position.y), dim)
	draw_rect(Rect2(0, lit.end.y, size.x, size.y - lit.end.y), dim)
	draw_rect(Rect2(0, lit.position.y, lit.position.x, lit.size.y), dim)
	draw_rect(Rect2(lit.end.x, lit.position.y, size.x - lit.end.x, lit.size.y), dim)
	var bend := minf(float(corner), minf(lit.size.x, lit.size.y) / 2.0)
	if bend > 0.0:
		for at: Vector2 in [
			lit.position,
			Vector2(lit.end.x, lit.position.y),
			lit.end,
			Vector2(lit.position.x, lit.end.y)
		]:
			var inward := Vector2(
				bend if is_equal_approx(at.x, lit.position.x) else -bend,
				bend if is_equal_approx(at.y, lit.position.y) else -bend
			)
			var centre := at + inward
			var points := PackedVector2Array([at])
			for step: int in 9:
				var angle := step / 8.0 * PI / 2.0
				points.append(centre + Vector2(-inward.x * cos(angle), -inward.y * sin(angle)))
			draw_colored_polygon(points, dim)
	var ring := StyleBoxFlat.new()
	ring.draw_center = false
	ring.border_color = UI_THEME.color(self, "accent")
	ring.set_border_width_all(maxi(2, roundi(2 * UI_THEME.unit(self))))
	ring.set_corner_radius_all(corner)
	draw_style_box(ring, lit)


# --- Where each step points ------------------------------------------------------------------


## What step [param id] points at: a Control, a global Rect2 (a bottom panel tab) or null.
func _target(id: String) -> Variant:
	var found: Variant = null
	if targets.has(id):
		found = (targets[id] as Callable).call()
	elif plugin != null:
		var dock: Control = plugin.get("_dock")
		match id:
			"sidebar":
				found = dock
			"select":
				found = EditorInterface.get_editor_main_screen()
			"ask":
				found = dock.get("_prompt") if dock != null else null
			"review":
				found = plugin.get("_review_button")
				if not (found is Control and (found as Control).is_visible_in_tree()):
					found = _bottom_tab("Review")
			"timeline", "share":
				found = _bottom_tab(id.capitalize())
	return found if found is Control or found is Rect2 else null


## The bottom panel's tab titled [param title] as a global rect (the bottom panel is a tab
## container), or its button where the editor has buttons instead; null when neither shows.
func _bottom_tab(title: String) -> Variant:
	var base := EditorInterface.get_base_control()
	for found: Node in base.find_children("*", "TabContainer", true, false):
		var tabs := found as TabContainer
		var bar := tabs.get_tab_bar()
		if bar == null or not bar.is_visible_in_tree():
			continue
		for at: int in tabs.get_tab_count():
			if tabs.get_tab_title(at) == title:
				var rect := bar.get_tab_rect(at)
				rect.position += bar.get_global_rect().position
				return rect
	for found: Node in base.find_children("*", "Button", true, false):
		var button := found as Button
		if button.text == title and button.is_visible_in_tree() and not is_ancestor_of(button):
			return button
	return null


# --- Hooking up ------------------------------------------------------------------------------


func _hook_up() -> void:
	if plugin == null:
		return
	var dock: Control = plugin.get("_dock")
	var slash: Object = dock.get("_slash") if dock != null else null
	if slash != null and slash.has_signal("tour_requested"):
		slash.connect("tour_requested", open)
	_help = _help_menu()
	if _help != null:
		_help.add_separator()
		_help.add_item(HELP_ITEM, HELP_ID)
		_help.id_pressed.connect(_on_help)
	if FileAccess.file_exists(PENDING):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(PENDING))
		if not seen():
			_first_open()


func _unhook() -> void:
	if _help != null and is_instance_valid(_help):
		var at := _help.get_item_index(HELP_ID)
		if at >= 0:
			_help.remove_item(at)
			# The separator added before it.
			if at > 0 and _help.is_item_separator(at - 1):
				_help.remove_item(at - 1)
		if _help.id_pressed.is_connected(_on_help):
			_help.id_pressed.disconnect(_on_help)


func _on_help(id: int) -> void:
	if id == HELP_ID:
		open()


## The editor's Help menu, when this editor has one where Godot puts it.
func _help_menu() -> PopupMenu:
	for found: Node in EditorInterface.get_base_control().find_children(
		"*", "PopupMenu", true, false
	):
		var menu := found as PopupMenu
		if menu.name == "Help" or menu.title == "Help":
			return menu
	return null


## A new project's first open: once the editor has settled and project setup (if it shows)
## is closed.
func _first_open() -> void:
	await get_tree().create_timer(1.5).timeout
	var setup: Variant = plugin.get("_setup") if plugin != null else null
	while setup is Window and is_instance_valid(setup) and (setup as Window).visible:
		await (setup as Window).visibility_changed
	open()
