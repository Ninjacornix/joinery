extends SceneTree
## The first-project tour: six steps in order, each lighting its place with the card beside
## it and inside the window; Back and Next move, the last step reads Done and says how to see
## it again; Skip, Done and Esc close it and remember it was seen; a place the editor lacks
## leaves the card in the middle; a narrow window narrows the card.

const TOUR := preload("res://addons/joinery/tour.gd")

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 3) -> void:
	for index: int in count:
		await process_frame


func _box(name: String, rect: Rect2) -> Control:
	var box := ColorRect.new()
	box.name = name
	box.position = rect.position
	box.size = rect.size
	root.add_child(box)
	return box


func _inside(tour: Control) -> bool:
	var card: Control = tour.get("card")
	var inside := Rect2(Vector2.ZERO, tour.size).encloses(Rect2(card.position, card.size))
	if not inside:
		print("card %s at %s in %s" % [card.size, card.position, tour.size])
	return inside


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	DirAccess.remove_absolute(ProjectSettings.globalize_path("user://joinery_tour.cfg"))
	root.size = Vector2i(1280, 800)
	await _frames()
	# A stand-in editor: the sidebar on the right, its message box, the main view, and the
	# bottom panel's buttons.
	var sidebar := _box("Sidebar", Rect2(900, 0, 380, 760))
	var prompt := _box("Prompt", Rect2(916, 640, 348, 96))
	var view := _box("View", Rect2(0, 0, 890, 560))
	var review := _box("Review", Rect2(8, 770, 80, 24))
	var timeline := _box("Timeline", Rect2(96, 770, 80, 24))
	var tour: Control = TOUR.new()
	tour.set(
		"targets",
		{
			"sidebar": func() -> Control: return sidebar,
			"select": func() -> Control: return view,
			"ask": func() -> Control: return prompt,
			"review": func() -> Control: return review,
			"timeline": func() -> Control: return timeline,
			# No Share here: the step still shows, its card in the middle.
			"share": func() -> Control: return null,
		}
	)
	root.add_child(tour)
	await _frames()
	_check(not tour.visible, "Hidden until opened")
	_check(not TOUR.seen(), "Not seen yet")
	tour.call("open")
	await _frames()
	_check(tour.visible, "Open")
	_check((tour.get("count_label") as Label).text == "1 of 6", "Step one of six")
	_check((tour.get("title_label") as Label).text == "Journeyman works here", "The sidebar first")
	_check(not (tour.get("back") as Button).visible, "No Back on the first step")
	_check((tour.get("next") as Button).text == "Next", "Next")
	var hole: Rect2 = tour.get("hole")
	_check(hole.is_equal_approx(sidebar.get_global_rect()), "The sidebar is lit: %s" % hole)
	var card: Control = tour.get("card")
	_check(card.position.x + card.size.x <= hole.position.x, "The card sits left of the sidebar")
	_check(_inside(tour), "The card is inside the window")
	_check(card.size.y > 40.0, "The card's wrapped text has its height: %s" % card.size)

	# Next, Back and the keyboard.
	var titles: Array[String] = []
	for step: int in 5:
		(tour.get("next") as Button).pressed.emit()
		await _frames()
		titles.append((tour.get("title_label") as Label).text)
		_check(_inside(tour), "Step %d's card is inside the window" % (step + 2))
	_check(
		(
			titles
			== [
				"Pick what to change",
				"Ask for a change",
				"Accept or Discard",
				"Go back any time",
				"Share your game",
			]
		),
		"Steps in order: %s" % [titles]
	)
	_check((tour.get("count_label") as Label).text == "6 of 6", "Last step")
	_check((tour.get("next") as Button).text == "Done", "The last step reads Done")
	_check((tour.get("again_label") as Label).visible, "It says how to see it again")
	_check(not (tour.get("hole") as Rect2).has_area(), "Nothing to light without Share")
	(tour.get("back") as Button).pressed.emit()
	await _frames()
	_check((tour.get("title_label") as Label).text == "Go back any time", "Back one step")
	_check(not (tour.get("again_label") as Label).visible, "Only the last step says it")
	var left := InputEventKey.new()
	left.keycode = KEY_LEFT
	left.pressed = true
	root.push_input(left)
	await _frames()
	_check((tour.get("title_label") as Label).text == "Accept or Discard", "Left goes back")
	tour.call("show_step", 1)
	await _frames()
	_check((tour.get("hole") as Rect2).is_equal_approx(view.get_global_rect()), "The view is lit")
	_check(_inside(tour), "The card over the main view stays inside the window")

	# Esc skips, and the tour is remembered.
	var closed: Array[bool] = []
	tour.connect("finished", func(done: bool) -> void: closed.append(done))
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	escape.pressed = true
	root.push_input(escape)
	await _frames()
	_check(not tour.visible, "Esc closes it")
	_check(closed == [false], "Skipped, not finished: %s" % [closed])
	_check(TOUR.seen(), "Remembered as seen")

	# Done finishes it; a narrow window narrows the card.
	root.size = Vector2i(400, 720)
	await _frames()
	tour.call("show_step", 5)
	await _frames()
	_check(card.size.x <= 400.0 - 2.0 * 16.0 + 1.0, "The card fits a narrow window: %s" % card.size)
	_check(_inside(tour), "And stays inside it")
	(tour.get("next") as Button).pressed.emit()
	await _frames()
	_check(closed == [false, true], "Done finishes it: %s" % [closed])
	_check(not tour.visible, "Closed")
	print("tour: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
