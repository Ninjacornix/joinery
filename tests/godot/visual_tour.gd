extends RefCounted
## The visual suite's first-project tour: each of its six steps over the real editor, then one
## step in a narrow editor window. Copied beside visual_editor_test.gd by run_visual_suite.py;
## [param suite] is that test, whose `_shot`, `_check` and `_frames` these use.

const STEPS := ["sidebar", "select", "ask", "review", "timeline", "share"]


static func tour(suite: Variant) -> void:
	var base := EditorInterface.get_base_control()
	var tour: Control = base.find_child("JoineryTour", false, false)
	suite._check(tour != null, "The tour is installed")
	if tour == null:
		return
	for index: int in STEPS.size():
		tour.call("show_step", index)
		await suite._frames(6)
		suite._check(
			Rect2(Vector2.ZERO, tour.size).encloses(
				Rect2((tour.get("card") as Control).position, (tour.get("card") as Control).size)
			),
			"Tour step %d's card is inside the window" % (index + 1)
		)
		await suite._shot("tour-" + STEPS[index], [tour.get("card")])
	var window := base.get_window()
	var was := window.size
	window.size = Vector2i(820, maxi(620, was.y))
	await suite._frames(8)
	tour.call("show_step", 0)
	await suite._frames(6)
	await suite._shot("tour-narrow", [tour.get("card")])
	window.size = was
	await suite._frames(4)
	tour.call("close", false)
	await suite._frames(4)
