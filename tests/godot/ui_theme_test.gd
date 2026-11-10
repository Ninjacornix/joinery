extends SceneTree
## Design tokens, theme, live re-theming, icons, motion and building blocks, headless.

const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
const UI := preload("res://addons/joinery/ui_components.gd")
const INDICATOR := preload("res://addons/joinery/activity_indicator.gd")
const SETTINGS := preload("res://addons/joinery/journeyman_settings.gd")
const TEXT_ROLES := ["text", "text_muted", "text_faint", "accent", "success", "danger"]
const SURFACES := ["surface", "surface_sunken", "surface_raised"]
const LABELS := [
	"JoineryCaption",
	"JoineryFaint",
	"JoineryBodyLarge",
	"JoineryTitle",
	"JoineryDisplay",
	"JoineryError",
	"JoineryNumeric",
	"JoinerySection",
]
const BUTTONS := [
	"JoineryPrimaryButton",
	"JoinerySecondaryButton",
	"JoineryLinkButton",
	"JoineryQuietButton",
	"JoineryRowButton",
	"JoineryIconButton",
	"JoinerySendButton",
	"JoineryStopButton",
]
const PANELS := ["JoineryCard", "JoineryCardSelected", "JoineryBanner"]

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var config := OS.get_cache_dir().path_join("joinery-ui-theme-test/journeyman.cfg")
	DirAccess.remove_absolute(config)
	Engine.set_meta("joinery_ui_config", config)
	Engine.remove_meta("joinery_ui_prefs")
	_host = Control.new()
	_host.size = Vector2(480, 640)
	root.add_child(_host)
	_check(not UI_MOTION.is_enabled(), "Motion is off headless unless a test asks for it")
	_check_contrast()
	_check_tokens()
	_check_build()
	_check_icons()
	await _check_watch(config)
	await _check_motion()
	await _check_components()
	await _check_settings()
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	Engine.remove_meta("joinery_ui_prefs")
	await process_frame
	print("ui theme: ok" if not _failed else "ui theme: failed")
	quit(1 if _failed else 0)


static func _contrast(a: Color, b: Color) -> float:
	var la := _luminance(a)
	var lb := _luminance(b)
	return (maxf(la, lb) + 0.05) / (minf(la, lb) + 0.05)


static func _luminance(value: Color) -> float:
	var channels: Array[float] = []
	for channel: float in [value.r, value.g, value.b]:
		channels.append(
			channel / 12.92 if channel <= 0.04045 else pow((channel + 0.055) / 1.055, 2.4)
		)
	return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


func _check_contrast() -> void:
	for mode: String in ["dark", "light"]:
		_check(UI_THEME.set_pref("theme", mode) == OK, "Save the theme preference")
		for accent: String in UI_THEME.ACCENTS:
			_check(UI_THEME.set_pref("accent", accent) == OK, "Save the accent preference")
			var colors := UI_THEME.palette(_host)
			var where := "%s/%s" % [mode, accent]
			for role: String in TEXT_ROLES:
				for surface: String in SURFACES:
					var ratio := _contrast(colors[role], colors[surface])
					_check(ratio >= 4.5, "%s %s on %s is %.2f:1" % [where, role, surface, ratio])
			for fill: String in ["accent", "accent_hover", "accent_pressed"]:
				var ratio := _contrast(colors["on_accent"], colors[fill])
				_check(ratio >= 4.5, "%s on_accent on %s is %.2f:1" % [where, fill, ratio])
			for fill: String in ["accent_tint", "danger_tint"]:
				var ratio := _contrast(colors["text"], colors[fill])
				_check(ratio >= 4.5, "%s text on %s is %.2f:1" % [where, fill, ratio])
			for surface: String in SURFACES:
				var ratio := _contrast(colors["border_strong"], colors[surface])
				_check(ratio >= 3.0, "%s input border on %s is %.2f:1" % [where, surface, ratio])
			for alias: String in ["secondary", "inset", "raised", "border", "focus", "primary"]:
				_check(not colors.has(alias), "The old palette name %s is gone" % alias)
	_check(UI_THEME.set_pref("theme", "match") == OK, "Restore the theme preference")
	_check(UI_THEME.set_pref("accent", "brass") == OK, "Restore the accent preference")


func _check_tokens() -> void:
	# The nesting table: composer, command popup, provider card.
	_check(UI_THEME.nested(16, 8) == 8, "Composer children use radius 8")
	_check(UI_THEME.nested(12, 4) == 8, "Command popup rows use radius 8")
	_check(UI_THEME.nested(12, 12) == 4, "Provider icon tiles use radius 4")
	var size := _host.get_theme_default_font_size()
	var unit := maxf(size / 14.0, 1.0)
	_check(is_equal_approx(UI_THEME.unit(_host), unit), "Unit follows the font size")
	_check(UI_THEME.space(_host, 3) == roundi(12 * unit), "Spacing scales with the unit")
	_check(UI_THEME.radius(_host, "md") == roundi(8 * unit), "Radii scale with the unit")
	_check(UI_THEME.control_h(_host, "md") == roundi(size * 2.5), "Control heights follow the font")
	_check(UI_THEME.font_size(_host, "caption") == roundi(size * 0.85), "Caption is 0.85 em")
	_check(UI_THEME.MOTION["exit"] <= UI_THEME.MOTION["enter"] * 0.8 + 0.001, "Exits are shorter")
	_check(UI_THEME.MOTION["menu"] >= 0.15 and UI_THEME.MOTION["menu"] <= 0.25, "Menus 150–250 ms")
	var file := ConfigFile.new()
	file.load(Engine.get_meta("joinery_ui_config"))
	_check(file.get_value("appearance", "theme", "") == "match", "Theme is saved in [appearance]")
	_check(UI_THEME.set_pref("text_size", "Large") == OK, "Save the text size")
	file.load(Engine.get_meta("joinery_ui_config"))
	_check(
		file.get_value("journeyman", "text_size", "") == "Large", "Text size stays in [journeyman]"
	)
	_check(UI_THEME.set_pref("text_size", "Normal") == OK, "Restore the text size")
	var numeric := UI_THEME.numeric_font(_host) as FontVariation
	var tag := TextServerManager.get_primary_interface().name_to_tag("tnum")
	_check(
		numeric != null and numeric.opentype_features.get(tag) == 1, "Numbers use tabular digits"
	)


func _check_build() -> void:
	var theme := UI_THEME.build(_host)
	for type_name: String in LABELS:
		_check(
			theme.get_type_variation_base(type_name) == "Label", type_name + " is a Label variation"
		)
	for type_name: String in BUTTONS:
		_check(
			theme.get_type_variation_base(type_name) == "Button",
			type_name + " is a Button variation"
		)
	for type_name: String in PANELS:
		_check(
			theme.get_type_variation_base(type_name) == "PanelContainer", type_name + " is a panel"
		)
	_check(theme.get_type_variation_base("JoineryPicker") == "OptionButton", "Picker variation")
	_check(theme.get_type_variation_base("JoinerySwitch") == "CheckButton", "Switch variation")
	_check(theme.get_stylebox("normal", "RichTextLabel") is StyleBoxEmpty, "Replies have no inset")
	_check(theme.get_stylebox("normal", "Label") is StyleBoxEmpty, "Labels have no inset")
	var colors := UI_THEME.palette(_host)
	_check(theme.get_color("caret_color", "TextEdit") == colors["accent"], "One caret colour")
	_check(theme.get_color("caret_color", "LineEdit") == colors["accent"], "One caret colour")
	_check(
		theme.get_color("font_placeholder_color", "LineEdit") == colors["text_faint"],
		"Placeholders use text_faint"
	)
	var send: StyleBoxFlat = theme.get_stylebox("normal", "JoinerySendButton")
	var primary: StyleBoxFlat = theme.get_stylebox("normal", "JoineryPrimaryButton")
	_check(send.bg_color == primary.bg_color, "Send and primary share the accent fill")
	var ring: StyleBoxFlat = theme.get_stylebox("focus", "Button")
	var button: StyleBoxFlat = theme.get_stylebox("normal", "Button")
	_check(ring.border_color == colors["accent"], "Focus rings use the accent")
	_check(
		ring.corner_radius_top_left == button.corner_radius_top_left + int(ring.expand_margin_left),
		"Focus ring corners stay concentric with the button"
	)
	var size := _host.get_theme_default_font_size()
	var checked := theme.get_icon("checked", "CheckButton")
	_check(
		checked.get_size() == Vector2(roundi(2.0 * size), roundi(1.1 * size)),
		"Switch track is 2.0 × 1.1 em"
	)
	_check(theme.get_stylebox("normal", "CheckButton") is StyleBoxEmpty, "Switch has no box")
	_check(theme.has_icon("frame_3", "JoinerySwitch"), "Switch travel frames are built")
	# One control height: inputs, buttons and pickers in a row share top and bottom edges.
	var holder := VBoxContainer.new()
	holder.theme = theme
	_host.add_child(holder)
	var expected := float(UI_THEME.control_h(_host, "md"))
	var controls: Array[Control] = [LineEdit.new(), Button.new(), OptionButton.new()]
	for variation: String in [
		"JoineryPrimaryButton", "JoinerySecondaryButton", "JoineryIconButton"
	]:
		var other := Button.new()
		other.theme_type_variation = variation
		if variation == "JoineryIconButton":
			other.icon = UI_THEME.icon(_host, "close")
		else:
			other.text = "Accept"
		controls.append(other)
	var check := CheckButton.new()
	check.text = "Show reply times"
	controls.append(check)
	(controls[1] as Button).text = "Accept"
	(controls[2] as OptionButton).add_item("Auto")
	for control: Control in controls:
		holder.add_child(control)
		var height := control.get_combined_minimum_size().y
		_check(
			absf(height - expected) <= 1.0,
			"%s is %.1f px tall, expected %.1f" % [control.get_class(), height, expected]
		)
	holder.queue_free()


func _check_icons() -> void:
	for name: String in UI_THEME.ICONS:
		var texture := UI_THEME.icon(_host, name)
		_check(texture != null and texture.get_width() > 0, "Icon %s resolves headless" % name)
	var tinted := UI_THEME.icon(_host, "chevron", Color.RED)
	_check(
		tinted != null and tinted != UI_THEME.icon(_host, "chevron"), "Tinted icons are separate"
	)


func _check_watch(config: String) -> void:
	var script := GDScript.new()
	script.source_code = (
		"extends Control\nvar rebuilt := 0\n"
		+ "func _on_theme_rebuilt() -> void:\n\trebuilt += 1\n"
	)
	script.reload()
	var watched: Control = script.new()
	_host.add_child(watched)
	UI_THEME.watch(watched)
	_check(watched.theme != null, "watch() themes a root that has none")
	var before := watched.theme.get_color("accent", "Joinery")
	_check(UI_THEME.set_pref("accent", "violet") == OK, "Change the accent")
	await process_frame
	_check(watched.theme.get_color("accent", "Joinery") != before, "Accent applies live")
	_check(int(watched.get("rebuilt")) == 1, "The root hears about the rebuild once")
	var dark := watched.theme.get_color("surface", "Joinery")
	_check(UI_THEME.set_pref("theme", "light") == OK, "Change the theme")
	await process_frame
	await process_frame
	_check(watched.theme.get_color("surface", "Joinery") != dark, "Theme applies live")
	_check(int(watched.get("rebuilt")) == 2, "Re-theming does not loop on theme_changed")
	_check(UI_THEME.set_pref("reduce_motion", "on") == OK, "Reduce motion saves")
	await process_frame
	_check(int(watched.get("rebuilt")) == 2, "Unrelated preferences do not rebuild the theme")
	_check(UI_THEME.set_pref("reduce_motion", "off") == OK, "Reduce motion turns off")
	_check(UI_THEME.set_pref("theme", "match") == OK, "Restore the theme")
	_check(UI_THEME.set_pref("accent", "brass") == OK, "Restore the accent")
	var file := ConfigFile.new()
	_check(file.load(config) == OK, "Preferences are written to journeyman.cfg")
	watched.queue_free()
	await process_frame


func _check_motion() -> void:
	var themed := Control.new()
	themed.theme = UI_THEME.build(_host)
	_host.add_child(themed)
	var panel := PanelContainer.new()
	themed.add_child(panel)
	panel.hide()
	UI_MOTION.enter(panel)
	_check(panel.visible and panel.modulate.a == 1.0, "With motion off, enter shows at once")
	UI_MOTION.exit(panel)
	_check(not panel.visible, "With motion off, exit hides at once")
	var label := Label.new()
	themed.add_child(label)
	UI_MOTION.roll_number(label, 75, UI_MOTION.format_clock)
	_check(label.text == "1:15", "Clocks read m:ss")
	UI_MOTION.roll_number(label, 3, "%d files")
	_check(label.text == "3 files", "Counts use the format")
	UI_MOTION.set_enabled(true)
	_check(UI_MOTION.is_enabled(), "Tests can turn motion on")
	UI_MOTION.enter(panel, label)
	_check(panel.modulate.a < 1.0 and panel.scale.x < 1.0, "Popovers start small and clear")
	UI_MOTION.roll_number(label, 12, "%d files")
	_check(label.text == "12 files" and label.visible_characters == 0, "Numbers roll")
	await create_timer(UI_THEME.MOTION["base"] + 0.1).timeout
	_check(panel.modulate.a == 1.0 and panel.scale == Vector2.ONE, "Entrances settle")
	_check(label.visible_characters == -1, "The rolled number settles to plain text")
	UI_MOTION.exit(panel, label)
	await create_timer(UI_THEME.MOTION["menu"] + 0.1).timeout
	_check(not panel.visible and panel.scale == Vector2.ONE, "Exits hide and reset")
	var check := UI.switch("Reduce motion")
	themed.add_child(check)
	check.button_pressed = true
	await process_frame
	_check(check.has_theme_icon_override("checked"), "The knob travels through frames")
	await create_timer(UI_THEME.MOTION["base"] + 0.1).timeout
	_check(not check.has_theme_icon_override("checked"), "The knob settles on the theme icon")
	_check(UI_THEME.set_pref("reduce_motion", "on") == OK, "Reduce motion")
	_check(not UI_MOTION.is_enabled(), "Reduce motion turns every animation off")
	_check(UI_THEME.set_pref("reduce_motion", "off") == OK, "Restore motion")
	UI_MOTION.set_enabled(false)
	themed.queue_free()
	await process_frame


func _check_components() -> void:
	var themed := VBoxContainer.new()
	themed.theme = UI_THEME.build(_host)
	_host.add_child(themed)
	var name_field := UI.field("Name", LineEdit.new(), "Shown in the editor title")
	themed.add_child(name_field)
	_check(not name_field.error.visible, "Errors start hidden")
	name_field.set_error("Write a project name")
	_check(name_field.error.visible, "set_error shows the message")
	_check(
		name_field.control.theme_type_variation == "JoineryLineEditError",
		"The field border turns red"
	)
	name_field.set_error("")
	_check(name_field.control.theme_type_variation == "", "Clearing restores the field")
	var md := float(UI_THEME.control_h(_host, "md"))
	var primary := UI.primary("Create project")
	var secondary := UI.secondary("Back")
	var picker := UI.picker(["Auto", "Fast"])
	var icon := UI.icon_button("close", "Close")
	var row := UI.row_button("Art direction")
	for control: Control in [primary, secondary, picker, icon, row]:
		themed.add_child(control)
		_check(control.custom_minimum_size.y == md, "%s is CONTROL_H.md" % control.get_class())
	_check(icon.icon != null and icon.custom_minimum_size.x == md, "Icon buttons are square")
	_check(row.icon != null, "Row buttons carry a chevron")
	var section := UI.section("Troubleshooting", true)
	themed.add_child(section)
	_check(not section.body.visible, "Collapsible sections start closed")
	(section.header as Button).button_pressed = true
	_check(section.body.visible and section.open, "The disclosure opens the section")
	var tag := UI.chip("hammer.glb", "model", true)
	themed.add_child(tag)
	var removed: Array[bool] = []
	tag.removed.connect(func() -> void: removed.append(true))
	tag.remove.pressed.emit()
	_check(removed.size() == 1, "Removing a chip reports it")
	var dot := UI.status_dot("ready")
	themed.add_child(dot)
	dot.state = "attention"
	_check(dot.custom_minimum_size.x > 0, "Status dots have a size")
	var indicator: Control = INDICATOR.new()
	themed.add_child(indicator)
	_check(not indicator.visible and not indicator.is_processing(), "Idle indicator is still")
	indicator.set("running", true)
	_check(indicator.visible and indicator.custom_minimum_size.y > 0, "Running indicator shows")
	indicator.set("running", false)
	_check(not indicator.visible and not indicator.is_processing(), "Stopped indicator hides")
	themed.queue_free()
	await process_frame


func _check_settings() -> void:
	var script := GDScript.new()
	script.source_code = (
		"extends PanelContainer\nsignal reconnect_requested\nvar applied: Array = []\n"
		+ 'var ran: Array = []\nvar connection_state := "error"\n'
		+ "func apply_preferences(values: Dictionary) -> void:\n\tapplied.append(values)\n"
		+ "func run_command(entry: String) -> void:\n\tran.append(entry)\n"
	)
	script.reload()
	var dock: Control = script.new()
	dock.size = Vector2(420, 700)
	_host.add_child(dock)
	var content := VBoxContainer.new()
	dock.add_child(content)
	var header := HBoxContainer.new()
	content.add_child(header)
	var chat := Label.new()
	chat.text = "chat"
	content.add_child(chat)
	var page: ScrollContainer = SETTINGS.attach(dock, header, content)
	await process_frame
	page.set_open(true)
	_check(page.visible and not header.visible and not chat.visible, "Settings replace the chat")
	var texts := _texts(page)
	for expected: String in [
		"Settings", "Appearance", "Theme", "Accent", "Text size", "Reduce motion"
	]:
		_check(expected in texts, "Settings show " + expected)
	for gone: String in ["Editor", "Start new requests in", "Saved for you in"]:
		_check(
			not texts.any(func(text: String) -> bool: return text.begins_with(gone)),
			gone + " is gone"
		)
	_check(
		texts.all(func(text: String) -> bool: return text != text.to_upper() or text.length() < 4),
		"No all-caps labels"
	)
	var text_picker: OptionButton = page.get("_text_picker")
	text_picker.select(2)
	text_picker.item_selected.emit(2)
	var applied: Array = dock.get("applied")
	_check(
		not applied.is_empty() and is_equal_approx(float(applied[-1].get("text_scale", 0)), 1.15),
		"Text size applies through apply_preferences"
	)
	_check(UI_THEME.prefs()["text_size"] == "Large", "Text size is saved")
	var reconnect: Button = page.get("_reconnect")
	_check(reconnect.visible, "Reconnect shows while not connected")
	var deadline := Time.get_ticks_msec() + 30000
	var history: Label = page.get("_history_size")
	while page.get("_history_job") != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not history.text.is_empty(), "History shows a size or Unavailable")
	var art: Button = null
	for button: Node in page.find_children("*", "Button", true, false):
		if (button as Button).text == "Art direction":
			art = button
	_check(art != null, "Art direction row exists")
	if art != null:
		art.pressed.emit()
	_check((dock.get("ran") as Array).has("/art"), "Rows run their command through the dock")
	page.set_open(false)
	_check(header.visible and chat.visible and not page.visible, "Back restores the chat")
	_check(UI_THEME.set_pref("text_size", "Normal") == OK, "Restore the text size")
	dock.queue_free()
	await process_frame


func _texts(node: Node) -> Array[String]:
	var result: Array[String] = []
	for child: Node in node.find_children("*", "", true, false):
		if child is Label:
			result.append((child as Label).text)
		elif child is Button:
			result.append((child as Button).text)
	return result
