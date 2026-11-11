extends RefCounted
## The checks behind accessibility_test.gd. Loaded once the game is running, so this
## file can use the autoloads. Works on every blueprint: the hold-or-toggle and camera
## shake checks run only where the game has a held action or a shaking player.

var tree: SceneTree
var h: PlayHarness


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	h = harness


func run() -> void:
	await h.boot()
	h.expect(Game.current_level() != null, "the level starts")
	await _check_page()
	await _check_text_size()
	_check_high_contrast()
	_check_colour_filter()
	await _check_reduced_motion()
	await _check_toggle()
	_check_game_speed()
	await _check_subtitles()
	await _check_names()
	Menus.close_all()


func _check_page() -> void:
	var labels: Array[String] = []
	for page: Dictionary in Menus.settings_pages():
		labels.append(str(page["label"]))
	h.expect(labels.count("Accessibility") == 1, "one Accessibility page in Settings")
	Menus.open_settings()
	Menus.call(&"_open_settings_page", &"accessibility")
	await h.process_frames(2)
	var page := Menus.current_page()
	for key: String in [
		"text_scale",
		"high_contrast",
		"colour_filter",
		"screen_shake",
		"reduced_motion",
		"reduce_flashes",
		"subtitles",
		"subtitle_size",
		"game_speed"
	]:
		var control := page.find_child("access_%s" % key, true, false) as Control
		h.expect(control != null, "the page has %s" % key)
		if control != null:
			h.expect(not str(control.accessibility_name).is_empty(), "%s has a name" % key)
	var remap := page.find_child("access_remap", true, false) as Button
	var has_controls := false
	for entry: Dictionary in Menus.settings_pages():
		has_controls = has_controls or StringName(entry["id"]) == &"controls"
	h.expect((remap != null) == has_controls, "Remap controls shows with the Controls page")
	if remap != null:
		remap.pressed.emit()
		await h.process_frames(2)
		h.expect(Menus.current_page().name == "Settings_controls", "Remap opens Controls")
	Menus.close_all()


func _check_text_size() -> void:
	var root := Menus.get_node(^"Root") as Control
	var base: int = UiStyle.build_theme().default_font_size
	var hud := Label.new()
	hud.text = "HUD"
	hud.add_theme_font_size_override(&"font_size", 20)
	tree.current_scene.add_child(hud)
	await h.process_frames(2)
	Settings.set_value("access", "text_scale", 1.5)
	h.expect(root.theme.default_font_size == roundi(base * 1.5), "menu text grows with text size")
	h.expect(hud.get_theme_font_size(&"font_size") == 30, "HUD text grows with text size")
	var late := Label.new()
	late.add_theme_font_size_override(&"font_size", 20)
	tree.current_scene.add_child(late)
	await h.process_frames(2)
	h.expect(late.get_theme_font_size(&"font_size") == 30, "HUD text added later grows too")
	Settings.set_value("access", "text_scale", 1.0)
	h.expect(root.theme.default_font_size == base, "text size back to normal")
	h.expect(hud.get_theme_font_size(&"font_size") == 20, "HUD text back to normal")
	hud.queue_free()
	late.queue_free()


func _check_high_contrast() -> void:
	var root := Menus.get_node(^"Root") as Control
	Settings.set_value("access", "high_contrast", true)
	var panel := root.theme.get_stylebox(&"panel", &"PanelContainer") as StyleBoxFlat
	h.expect(panel.bg_color == Color.BLACK, "high contrast panels are black")
	h.expect(root.theme.get_color(&"font_color", &"HintLabel") == Color.WHITE, "hints are white")
	Settings.set_value("access", "high_contrast", false)
	panel = root.theme.get_stylebox(&"panel", &"PanelContainer") as StyleBoxFlat
	h.expect(panel.bg_color == UiStyle.PANEL, "panels come back")


func _check_colour_filter() -> void:
	var rect: ColorRect = Accessibility.filter_rect
	h.expect(not rect.visible, "no colour filter by default")
	Settings.set_value("access", "colour_filter", "deuteranopia")
	var material := rect.material as ShaderMaterial
	h.expect(rect.visible, "the colour filter shows")
	h.expect(int(material.get_shader_parameter("mode")) == 2, "the deuteranopia filter is on")
	h.expect(
		(material.shader as Shader).code.contains("hint_screen_texture"), "it filters the screen"
	)
	Settings.set_value("access", "colour_filter", "none")
	h.expect(not rect.visible, "the colour filter turns off")


func _check_reduced_motion() -> void:
	Settings.set_value("access", "screen_shake", 0.8)
	Settings.set_value("access", "reduced_motion", true)
	h.expect(Settings.screen_shake() == 0.0, "reduced motion turns screen shake off")
	h.expect(Accessibility.flashes_reduced(), "reduced motion reduces flashes")
	var player: Node = tree.get_first_node_in_group(&"player")
	if player != null and player.has_method(&"shake") and "_shake" in player:
		player.call(&"shake", 1.0)
		await h.physics_frames(2)
		h.expect(float(player.get(&"_shake")) == 0.0, "the camera doesn't shake")
	Settings.set_value("access", "reduced_motion", false)
	h.expect_near(Settings.screen_shake(), 0.8, 0.001, "screen shake comes back")
	Settings.set_value("access", "reduce_flashes", true)
	h.expect(Accessibility.flashes_reduced(), "flashes can be reduced alone")
	Settings.set_value("access", "reduce_flashes", false)
	Settings.set_value("access", "screen_shake", 1.0)


func _check_toggle() -> void:
	var held: Array[StringName] = Accessibility.hold_actions()
	if held.is_empty():
		return
	var action: StringName = held[0]
	Settings.set_value("access", "toggle_actions", true)
	await h.tap(action)
	h.expect(Input.is_action_pressed(action), "%s stays on after a tap" % action)
	await h.physics_frames(5)
	h.expect(Input.is_action_pressed(action), "%s is still on" % action)
	await h.tap(action)
	h.expect(not Input.is_action_pressed(action), "a second tap turns %s off" % action)
	Settings.set_value("access", "toggle_actions", false)
	h.press(action)
	await h.physics_frames(1)
	h.release(action)
	await h.physics_frames(1)
	h.expect(not Input.is_action_pressed(action), "%s is held again with toggle off" % action)


func _check_game_speed() -> void:
	Settings.set_value("access", "game_speed", 0.5)
	h.expect_near(Engine.time_scale, 0.5, 0.001, "game speed slows the game")
	Settings.set_value("access", "game_speed", 1.0)
	h.expect_near(Engine.time_scale, 1.0, 0.001, "game speed back to normal")


func _check_subtitles() -> void:
	Subtitles.say("Over here!", 0.5, "Guard")
	h.expect(Subtitles.showing() == "Guard: Over here!", "a subtitle shows")
	var size := Subtitles.label.get_theme_font_size(&"font_size")
	Settings.set_value("access", "subtitle_size", 2.0)
	h.expect(Subtitles.label.get_theme_font_size(&"font_size") > size, "subtitles grow")
	Settings.set_value("access", "subtitle_size", 1.0)
	await h.process_frames(60)
	h.expect(Subtitles.showing().is_empty(), "the subtitle goes after its time")
	Settings.set_value("access", "subtitles", false)
	Subtitles.caption("[door creaks]")
	h.expect(Subtitles.showing().is_empty(), "subtitles off shows nothing")
	Settings.set_value("access", "subtitles", true)


func _check_names() -> void:
	Menus.open_settings()
	Menus.call(&"_open_settings_page", &"audio")
	await h.process_frames(2)
	var slider := Menus.current_page().find_child("audio_master", true, false) as Control
	h.expect(slider != null, "the audio page has its master slider")
	if slider != null:
		h.expect(str(slider.accessibility_name) == "Master volume", "the slider is named")
	Menus.close_all()
