extends SceneTree
## Proves each ui_checks.gd rule fires on a known defect and stays quiet on a
## clean control, so an empty visual report means something.

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _panel(parent: Node, color: Color, rect: Rect2) -> PanelContainer:
	var panel := PanelContainer.new()
	var style := StyleBoxFlat.new()
	style.bg_color = color
	panel.add_theme_stylebox_override("panel", style)
	parent.add_child(panel)
	panel.position = rect.position
	panel.size = rect.size
	return panel


func _label(parent: Node, text: String, color: Color) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_color_override("font_color", color)
	parent.add_child(label)
	return label


func _run() -> void:
	var checks: GDScript = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	root.size = Vector2i(800, 600)
	var host := Control.new()
	host.name = "Host"
	host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_child(host)

	var good := _panel(host, Color.WHITE, Rect2(10, 10, 300, 60))
	good.name = "Good"
	_label(good, "Readable", Color.BLACK)
	var good_button := Button.new()
	good_button.text = "OK"
	good_button.custom_minimum_size = Vector2(48, 32)
	good_button.position = Vector2(10, 300)
	host.add_child(good_button)

	var faint := _panel(host, Color("777777"), Rect2(10, 80, 300, 60))
	faint.name = "Faint"
	_label(faint, "Grey on grey", Color("8a8a8a"))

	var narrow := Control.new()
	narrow.name = "Narrow"
	narrow.position = Vector2(10, 150)
	narrow.size = Vector2(40, 30)
	host.add_child(narrow)
	var cut := _label(narrow, "This text is far too long to fit", Color.BLACK)
	cut.name = "Cut"
	cut.clip_text = true
	cut.size = Vector2(40, 30)

	var tiny := Button.new()
	tiny.name = "Tiny"
	tiny.text = "x"
	tiny.position = Vector2(10, 200)
	host.add_child(tiny)
	tiny.size = Vector2(12, 12)

	var box := Control.new()
	box.name = "Box"
	box.position = Vector2(400, 10)
	box.size = Vector2(50, 50)
	host.add_child(box)
	var spill := ColorRect.new()
	spill.name = "Spill"
	box.add_child(spill)
	spill.position = Vector2(0, 0)
	spill.size = Vector2(120, 20)

	# An intentional ellipsis with the full name as a tooltip is allowed; without one it is not.
	for name: String in ["Explained", "Unexplained"]:
		var holder := Control.new()
		holder.name = name
		holder.position = Vector2(10, 240 if name == "Explained" else 270)
		holder.size = Vector2(60, 24)
		if name == "Explained":
			holder.tooltip_text = "door_with_long_descriptive_name.glb"
		host.add_child(holder)
		var trimmed := _label(holder, "door_with_long_descriptive_name.glb", Color.WHITE)
		trimmed.name = "Name"
		trimmed.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		trimmed.size = Vector2(60, 24)

	# The allowlisted stock viewport button is never a contrast finding.
	var stock := _panel(host, Color("777777"), Rect2(400, 300, 200, 40))
	stock.name = "Stock"
	var perspective := Button.new()
	perspective.name = "Perspective"
	perspective.text = "Perspective"
	perspective.flat = true
	perspective.add_theme_color_override("font_color", Color("8a8a8a"))
	stock.add_child(perspective)

	for frame: int in 3:
		await process_frame
	var findings: Array[String] = checks.run("selftest", [host])
	var joined := "\n".join(findings)
	_check(joined.contains("contrast Host/Faint/"), "Low contrast is reported")
	_check(joined.contains("clipped Host/Narrow/Cut"), "Clipped text is reported")
	_check(joined.contains("target Host/Tiny"), "Small targets are reported")
	_check(joined.contains("overflow Host/Box/Spill"), "Overflow is reported")
	_check(not joined.contains("Host/Good"), "Clean controls stay quiet")
	_check(not joined.contains("Host/Explained"), "An ellipsis explained by a tooltip is allowed")
	_check(joined.contains("clipped Host/Unexplained/Name"), "An unexplained ellipsis is reported")
	_check(not joined.contains("contrast Host/Stock"), "Allowlisted stock controls stay quiet")
	_check(not joined.contains("Host/Button"), "A large enough button stays quiet")
	_check(
		is_equal_approx(snappedf(checks.contrast_ratio(Color.WHITE, Color.BLACK), 0.01), 21.0),
		"WCAG ratio of black on white is 21:1"
	)
	print("ui checks selftest: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
