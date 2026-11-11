extends SceneTree
## The asset workspace's Kits section (kits_panel.gd in tool_workspace.gd), headless: the
## Assets | Kits switch swaps the kit list and the Kits panel in; the catalogue comes from the
## real `joinery kits list` through the command pipe; each parameter gets the right control
## and a change schedules a preview; a preview shows its triangles against the budget; and a
## model made with a kit offers its settings, which open the panel in change mode.
## With JOINERY_ASSET_CAPTURE (a path prefix, from run_parts_tests.py --capture) and
## JOINERY_PARTS_THEME it also saves <prefix>-kits-make.png and <prefix>-kits-edit.png;
## JOINERY_KITS_PREVIEW names a real preview Blender drew, to show the actual product.

const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 60.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000)
	while Time.get_ticks_msec() < deadline:
		if condition.call():
			return true
		await process_frame
	return false


func _run() -> void:
	root.size = Vector2i(1100, 700)
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("kits-test-ui.cfg")
		var file := ConfigFile.new()
		file.set_value("appearance", "theme", theme)
		file.save(config)
		Engine.set_meta("joinery_ui_config", config)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var workspace: WORKSPACE = WORKSPACE.new()
	root.add_child(workspace)
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	workspace.set_process(false)
	var box := StyleBoxFlat.new()
	box.bg_color = load("res://addons/joinery/ui_theme.gd").color(workspace, "surface")
	backdrop.add_theme_stylebox_override("panel", box)
	var kits: Variant = workspace._kits
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if not python.is_empty():
		kits.cli_prefix = PackedStringArray(
			[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
		)
	kits._kits_tab.pressed.emit()
	_check(not workspace._listing.visible and kits.list.visible, "Kits swaps the list in")
	_check(not workspace._body.visible and kits.visible, "Kits swaps the panel in")
	var loaded := func() -> bool: return not kits.catalogue.is_empty()
	_check(await _until(loaded), "The real kit list answers")
	# No Blender here: stop before the first preview starts.
	kits.set_process(false)
	_check(kits.list.item_count == 7, "Seven kits: %d" % kits.list.item_count)
	_check(kits.list.get_item_text(0) == "Rock", "Rocks first")
	_check(kits._title.text == "Rock", "The first kit is chosen: " + kits._title.text)
	_check_form(kits)
	_check_preview(kits)
	await _capture("kits-make")
	kits._assets_tab.pressed.emit()
	_check(workspace._listing.visible and workspace._body.visible, "Assets comes back")
	_check(not kits.visible and not kits.list.visible, "The Kits panel steps aside")
	await _check_edit(workspace, kits)
	workspace.queue_free()
	await process_frame
	print("kits panel: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The hut's form: a slider per number, a picker for the roof, a switch for the chimney.
func _check_form(kits: Variant) -> void:
	kits._choose_by_id("hut")
	_check(kits._title.text == "Hut", kits._title.text)
	var rows: Array = kits._form.get_children()
	_check(rows.size() == 10, "A row per parameter: %d" % rows.size())
	var sliders: Array = kits._form.find_children("Slider", "HSlider", true, false)
	_check(sliders.size() == 8, "Eight numbers: %d" % sliders.size())
	var pickers: Array = kits._form.find_children("*", "OptionButton", true, false)
	_check(pickers.size() == 1 and pickers[0].item_count == 4, "One roof picker of four")
	var switches: Array = kits._form.find_children("*", "CheckButton", true, false)
	_check(switches.size() == 1, "One chimney switch")
	var windows: HSlider = null
	for slider: HSlider in sliders:
		if slider.accessibility_name == "Windows":
			windows = slider
	_check(windows != null and windows.max_value == 24.0, "Windows go up to 24")
	kits._preview_due = -1.0
	windows.value = 4
	var value: Label = windows.get_parent().find_child("Value", true, false)
	_check(value.text == "4", "The value shows: " + value.text)
	_check(kits._values["windows"] == 4 and kits._values["windows"] is int, "A whole number")
	_check(kits._preview_due > 0.0, "A change asks for a preview after a pause")
	pickers[0].select(1)
	pickers[0].item_selected.emit(1)
	_check(kits._values["roof"] == "hip", "The roof picker: " + str(kits._values["roof"]))
	_check(kits._make.text == "Make" and kits._vary.visible, "Make and Make 10 variations")
	_check(kits._name.placeholder_text == "hut", kits._name.placeholder_text)


func _check_preview(kits: Variant) -> void:
	var picture := OS.get_user_data_dir().path_join("kit-preview.png")
	var image := Image.create(64, 64, false, Image.FORMAT_RGBA8)
	image.fill(Color(0.55, 0.45, 0.35))
	image.save_png(picture)
	var data := {"image": picture, "triangles": 1234, "budget": 6000, "size_m": [4.6, 4.1, 4.17]}
	kits.show_preview(data)
	_check(kits._picture.texture != null, "The preview shows")
	var facts: String = kits._facts.text
	_check(facts == "1,234 triangles of 6,000 · 4.6 × 4.1 × 4.2 m", "Facts: " + facts)
	_show_real(kits, "make")


## For screenshots: a preview Blender really drew (JOINERY_KITS_PREVIEW, JSON by state).
func _show_real(kits: Variant, state: String) -> void:
	var drawn: Variant = JSON.parse_string(OS.get_environment("JOINERY_KITS_PREVIEW"))
	if drawn is Dictionary and (drawn as Dictionary).has(state):
		kits.show_preview(drawn[state])


## A model made with a kit: its caption names the kit, and Kit settings opens it to change.
func _check_edit(workspace: WORKSPACE, kits: Variant) -> void:
	var made := {
		"kit": "rock",
		"label": "Rock",
		"name": "rocks",
		"params": {"size": 0.8, "moss": 0.5},
		"base": {"moss": 0.4},
		"seed": 11,
		"count": 10,
		"recipe": "recipes/x.toml",
	}
	var source := _asset("art/sources/kits/rocks_03.blend", made)
	source["parts"] = ["art/environment/rocks_03.glb"]
	var model := _asset("art/environment/rocks_03.glb", made)
	model["part_of"] = source["path"]
	workspace._apply_data({"tools": [], "assets": [source, model], "issues": []})
	workspace.select_resource("res://art/sources/kits/rocks_03.blend")
	var caption: String = workspace._caption.text
	_check(caption.ends_with("Rock kit"), "The caption names the kit: " + caption)
	_check(kits.edit_button.visible and not kits.edit_button.disabled, "Kit settings offered")
	kits.edit_button.pressed.emit()
	_check(kits.visible and not workspace._body.visible, "Kit settings opens the Kits panel")
	_check(kits._title.text == "Rock · rocks_03", "Changing: " + kits._title.text)
	_check(kits._summary.text.contains("all 10 models"), kits._summary.text)
	_check(kits._make.text == "Apply changes" and kits._make.disabled, "Nothing to apply yet")
	_check(not kits._vary.visible and not kits._name.visible, "No new names when changing")
	_check(kits._values["moss"] == 0.4 and kits._seed == 11, "The set's own values")
	var moss: HSlider = null
	for slider: HSlider in kits._form.find_children("Slider", "HSlider", true, false):
		if slider.accessibility_name == "Moss":
			moss = slider
	moss.value = 0.8
	_check(not kits._make.disabled, "A change can be applied")
	moss.value = 0.4
	_check(kits._make.disabled, "Back to its values: nothing to apply")
	_show_real(kits, "edit")
	await _capture("kits-edit")
	kits._assets_tab.pressed.emit()
	source["pending"] = "0123456789abcdef0123456789abcdef"
	workspace._apply_data({"tools": [], "assets": [source, model], "issues": []})
	workspace.select_resource("res://art/sources/kits/rocks_03.blend")
	_check(kits.edit_button.disabled, "A model waiting for review can't be changed yet")


func _asset(path: String, made: Dictionary) -> Dictionary:
	return {
		"path": path,
		"tool": "blender",
		"origin": "tool-created",
		"integrity": "in sync",
		"recipe": "recipes/x.toml",
		"sources": [],
		"previews": [],
		"open_options": [],
		"kind": "model",
		"parts": [],
		"part_of": "",
		"pending": "",
		"staged": "",
		"kit": made,
	}


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png when asked (a windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.4).timeout
	await RenderingServer.frame_post_draw
	root.get_texture().get_image().save_png("%s-%s.png" % [prefix, state])
