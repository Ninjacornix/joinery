@tool
extends AcceptDialog
## "Project apps": which real apps make this project, for a project that is already open.
##
## The list comes from `joinery tools --json`, so the catalog stays the single source of
## truth, and Save goes through `joinery setup`, which validates every choice, so this screen
## can never enable an app the catalog refuses. Both run through command_job.gd and never
## block the editor. Rows share the Project Manager's "Choose your apps" design: one row per
## app (checkbox with the name, blurb, readiness as plain text), grouped, with how Joinery
## connects to each app under one collapsed "Advanced" section.

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR := preload("activity_indicator.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const GROUPS := ["engine", "3d", "2d", "audio", "other"]
const GROUP_TITLES := {
	"engine": "Engine", "3d": "3D", "2d": "2D", "audio": "Audio", "other": "Other apps"
}
const DOMAIN_GROUP := {
	"engine": "engine", "3d": "3d", "materials": "3d", "2d": "2d", "audio": "audio"
}
const DOMAIN_WORDS := {
	"engine": "the game engine",
	"3d": "3D models",
	"materials": "materials",
	"2d": "2D art",
	"audio": "sound and music",
}
## Readiness from `status` (P7 and addendum S names): caption, dot state, disables the row.
const STATUS := {
	"ready": ["Ready", "ready", false],
	"installed": ["Ready", "ready", false],
	"preparing": ["Downloading…", "working", false],
	"needs_docker": ["Needs Docker", "off", false],
	"needs_licence": ["Needs your licence", "off", false],
	"licence": ["Needs your licence", "off", false],
	"missing": ["Not found", "off", false],
	"unavailable": ["Not available yet", "off", true],
}
const RULE_ZERO := "Joinery makes assets with real apps, never AI generators."
## Size of the dialog: width in multiples of the body font, capped by these screen fractions.
const WIDTH_EM := 48
const WIDTH_SCREEN := 0.9
const HEIGHT_SCREEN := 0.8

var _body: Body
var _notice: Label
var _name_field: UI_COMPONENTS.Field
var _banner: PanelContainer
var _banner_text: Label
var _loading: HBoxContainer
var _list: VBoxContainer
var _status: Label
var _details: UI_COMPONENTS.Section
var _details_text: Label
var _save_indicator: Control
var _rows: Array[Row] = []
var _advanced: UI_COMPONENTS.Section
var _catalog_job: RefCounted
var _save_job: RefCounted
var _loaded := false


## The dialog's content root: the watched theme root. A rebuild is copied to the dialog so its
## own Save and Cancel buttons follow the same theme.
class Body:
	extends VBoxContainer
	signal rebuilt

	func _on_theme_rebuilt() -> void:
		rebuilt.emit()


## One app: checkbox with its name, blurb and readiness; its connection field lives under
## the list's Advanced section.
class Row:
	extends HBoxContainer
	var tool_id := ""
	var tool: Dictionary = {}
	var required := false
	var available := true
	## Id of a custom connector this project already saved for the app, if any.
	var saved_custom := ""
	var check: CheckBox
	var picker: OptionButton
	var note: Label
	var connector: Control
	var command: VBoxContainer
	var arguments: VBoxContainer

	## The connector option chosen in the picker.
	func option() -> Dictionary:
		return picker.get_item_metadata(picker.selected) if picker.selected >= 0 else {}

	## True when the chosen connector is the project's own (an alternative or "Use my own…").
	func is_custom() -> bool:
		var chosen := option()
		return chosen.has("custom") or chosen.get("own", false)

	## The custom tool entry `joinery setup --custom-mcps` expects (command and args added later).
	func custom_entry() -> Dictionary:
		var chosen := option()
		if chosen.has("custom"):
			return (chosen["custom"] as Dictionary).duplicate()
		var domain := str(tool.get("domains", "engine")).split("/")[0]
		var identity := tool_id + "-own"
		return {
			"id": identity,
			"name": "%s (own connector)" % str(tool.get("name", tool_id)),
			"domain": domain,
			"server": identity,
		}


func _init() -> void:
	title = "Project apps"
	ok_button_text = "Save"
	dialog_hide_on_ok = false
	confirmed.connect(_on_save)


func _ready() -> void:
	_body = Body.new()
	_body.theme_type_variation = "JoineryStack"
	add_child(_body)
	UI_THEME.watch(_body)
	theme = _body.theme
	_body.rebuilt.connect(
		func() -> void:
			theme = _body.theme
			_align_names.call_deferred()
	)
	_notice = UI_COMPONENTS.label("", "", true)
	_notice.hide()
	_body.add_child(_notice)
	_body.add_child(UI_COMPONENTS.label(RULE_ZERO, "JoineryCaption", true))
	var name_line := LineEdit.new()
	name_line.placeholder_text = "e.g. Lighthouse Keeper"
	_name_field = UI_COMPONENTS.field("Name", name_line)
	_body.add_child(_name_field)
	register_text_enter(name_line)
	_build_banner()
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	scroll.accessibility_name = "Apps for this project"
	UI_MOTION.rubber_band(scroll)
	_body.add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryStack"
	gutter.add_child(_list)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRow"
	var spinner := INDICATOR.new()
	_loading.add_child(spinner)
	var loading_text := UI_COMPONENTS.label("Loading the app list…", "JoineryCaption")
	# A trimming label has no width of its own; let it take the row.
	loading_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_loading.add_child(loading_text)
	UI_COMPONENTS.bind_height(_loading, "lg", false)
	_body.add_child(_loading)
	_details = UI_COMPONENTS.section("Details", true)
	_details_text = UI_COMPONENTS.label("", "JoineryMono", true)
	_details.body.add_child(_details_text)
	_details.hide()
	_body.add_child(_details)
	_status = UI_COMPONENTS.label("", "JoineryCaption", true)
	_status.hide()
	_body.add_child(_status)
	_style_buttons()


func _build_banner() -> void:
	_banner = PanelContainer.new()
	_banner.theme_type_variation = "JoineryBanner"
	_banner.hide()
	_body.add_child(_banner)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	_banner.add_child(row)
	_banner_text = UI_COMPONENTS.label("", "", true)
	_banner_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_banner_text)
	var retry := UI_COMPONENTS.secondary("Retry")
	retry.pressed.connect(_load_catalog)
	row.add_child(retry)


func _style_buttons() -> void:
	var save := get_ok_button()
	save.theme_type_variation = "JoineryPrimaryButton"
	UI_MOTION.press_feedback(save)
	_save_indicator = INDICATOR.new()
	_save_indicator.anchor_left = 1.0
	_save_indicator.anchor_right = 1.0
	_save_indicator.anchor_top = 0.5
	_save_indicator.anchor_bottom = 0.5
	_save_indicator.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_save_indicator.grow_vertical = Control.GROW_DIRECTION_BOTH
	save.add_child(_save_indicator)
	save.theme_changed.connect(
		func() -> void:
			_save_indicator.offset_right = -UI_THEME.space(save, 3)
			_save_indicator.offset_left = _save_indicator.offset_right
	)
	var cancel := add_cancel_button("Cancel")
	cancel.theme_type_variation = "JoineryQuietButton"
	cancel.pressed.connect(_cancel_jobs)


# --- Opening ---------------------------------------------------------------------------------


## Change this project's apps from inside the editor.
func open_for_current_project() -> void:
	var fresh := not FileAccess.file_exists("res://joinery.toml")
	_open("This project has no apps chosen yet." if fresh else "")


## Straight after the Project Manager created the project (`.joinery-new`): choose its apps.
func open_for_new_project() -> void:
	_open("Project created. Choose the apps Journeyman should work in.")


## "New project" from inside a project: only the Project Manager can create one.
func open_new_project_notice() -> void:
	_open("To start a new game, choose Project › Quit to Project List, then Create.")


func _open(notice: String) -> void:
	_notice.text = notice
	_notice.visible = not notice.is_empty()
	(_name_field.control as LineEdit).text = str(
		ProjectSettings.get_setting("application/config/name", "")
	)
	_name_field.set_error("")
	_show_status("", false)
	_details.hide()
	_set_saving(false)
	_load_catalog()
	var screen := get_tree().root.get_visible_rect().size
	var width := mini(UI_THEME.font_size(_body, "body") * WIDTH_EM, roundi(screen.x * WIDTH_SCREEN))
	popup_centered(Vector2i(width, roundi(screen.y * HEIGHT_SCREEN)))


# --- Catalog ---------------------------------------------------------------------------------


func _load_catalog() -> void:
	if _catalog_job != null:
		_catalog_job.cancel()
	_loaded = false
	_banner.hide()
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	_rows.clear()
	_loading.show()
	(_loading.get_child(0) as Control).set("running", true)
	_update_save()
	_catalog_job = COMMAND_SCRIPT.new()
	var args := _cli(["tools", "--json", "--project", _project()])
	if _catalog_job.start("uv", args) != OK:
		_catalog_job = null
		_catalog_failed("uv isn't available")


func _catalog_done(result: Dictionary) -> void:
	_catalog_job = null
	var json := JSON.new()
	var data: Variant = null
	if int(result.get("code", 1)) == 0 and json.parse(str(result.get("output", ""))) == OK:
		data = json.data
	if not data is Dictionary or not data.get("tools") is Array:
		var reason := _first_line(
			str(result.get("error", "")) + "\n" + str(result.get("output", ""))
		)
		_catalog_failed(reason if not reason.is_empty() else "its reply wasn't readable")
		return
	_loading.hide()
	(_loading.get_child(0) as Control).set("running", false)
	_build_rows(data)
	_loaded = true
	_update_save()


func _catalog_failed(reason: String) -> void:
	_loaded = false
	_loading.hide()
	(_loading.get_child(0) as Control).set("running", false)
	_banner_text.text = (
		"Joinery couldn't load its app list (%s). Try again, or check the Joinery install." % reason
	)
	UI_MOTION.enter(_banner)
	_update_save()


func _build_rows(data: Dictionary) -> void:
	var saved: Array[String] = []
	for entry: Variant in data.get("custom", []):
		if entry is Dictionary:
			saved.append(str((entry as Dictionary).get("id", "")))
	var groups := {}
	for entry: Variant in data["tools"]:
		if not entry is Dictionary:
			continue
		var group := _group_of(entry)
		if not groups.has(group):
			groups[group] = []
		(groups[group] as Array).append(entry)
	var shown: Array = []
	for group: String in GROUPS:
		if not groups.has(group):
			continue
		var section := UI_COMPONENTS.section(str(GROUP_TITLES[group]))
		_list.add_child(section)
		shown.append(section)
		for tool: Dictionary in groups[group]:
			var row := _row(tool, saved)
			section.body.add_child(row)
			_rows.append(row)
	_advanced = UI_COMPONENTS.section("Advanced", true)
	_advanced.header.tooltip_text = "Choose how Joinery connects to each app."
	_advanced.body.theme_type_variation = "JoineryStack"
	_list.add_child(_advanced)
	for row: Row in _rows:
		_advanced.body.add_child(row.connector)
	_align_names()
	UI_MOTION.stagger(shown)


## Every blurb starts at one x: each name column is as wide as the widest name, so the
## list reads as two columns instead of a ragged middle edge.
func _align_names() -> void:
	var widest := 0.0
	for row: Row in _rows:
		row.check.custom_minimum_size.x = 0
		widest = maxf(widest, row.check.get_combined_minimum_size().x)
	for row: Row in _rows:
		row.check.custom_minimum_size.x = widest


func _group_of(tool: Dictionary) -> String:
	var group := str(tool.get("group", ""))
	if group in GROUPS:
		return group
	var domain := str(tool.get("domains", "")).split("/")[0]
	return str(DOMAIN_GROUP.get(domain, "other"))


## "3D models and materials" from the catalog blurb, or from the app's domains.
func _blurb_of(tool: Dictionary) -> String:
	var blurb := str(tool.get("blurb", "")).strip_edges()
	if not blurb.is_empty():
		return blurb
	var words: Array[String] = []
	for domain: String in str(tool.get("domains", "")).split("/", false):
		if DOMAIN_WORDS.has(domain):
			words.append(str(DOMAIN_WORDS[domain]))
	var text := " and ".join(words)
	return text.left(1).to_upper() + text.substr(1)


## Readiness for the chip: {text, dot, disables, reason}, or {} when the catalog says nothing.
func _status_of(tool: Dictionary) -> Dictionary:
	var key := str(tool.get("status", ""))
	if not STATUS.has(key):
		key = ""
		if not tool.get("available", true):
			key = "unavailable"
		elif tool.get("paid", false):
			key = "needs_licence"
	if key.is_empty():
		return {}
	var spec: Array = STATUS[key]
	return {
		"key": key,
		"text": spec[0],
		"dot": spec[1],
		"disables": spec[2],
		"reason": str(tool.get("status_reason", "")),
	}


func _row(tool: Dictionary, saved: Array[String]) -> Row:
	var row := Row.new()
	row.theme_type_variation = "JoineryRow"
	row.tool = tool
	row.tool_id = str(tool.get("id", ""))
	row.required = bool(tool.get("required", false))
	var status := _status_of(tool)
	row.available = bool(tool.get("available", true)) and not status.get("disables", false)
	var display := str(tool.get("name", row.tool_id))
	# One row, one control tall: the checkbox carries the name, the blurb follows in
	# secondary text, readiness is plain text at the trailing edge.
	UI_COMPONENTS.bind_height(row, "md", false)
	row.check = CheckBox.new()
	row.check.text = display
	row.check.focus_mode = Control.FOCUS_ALL
	row.check.accessibility_name = display
	row.check.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_COMPONENTS.bind_height(row.check, "md", false)
	row.check.toggled.connect(func(_on: bool) -> void: _clear_row_errors(row))
	row.add_child(row.check)
	var blurb := UI_COMPONENTS.label(_blurb_of(tool), "JoineryCaption")
	blurb.tooltip_text = blurb.text
	blurb.mouse_filter = Control.MOUSE_FILTER_PASS
	blurb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	blurb.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(blurb)
	if row.required:
		# Locked on, drawn exactly like a chosen app (an enabled, checked box that ignores
		# the pointer); the caption at the trailing edge, where readiness sits, says why.
		var always := UI_COMPONENTS.label("Always included", "JoineryCaption")
		always.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		always.clip_text = false
		always.size_flags_horizontal = Control.SIZE_SHRINK_END
		always.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(always)
		row.check.mouse_filter = Control.MOUSE_FILTER_IGNORE
		row.check.toggled.connect(
			func(on: bool) -> void:
				if not on:
					row.check.set_pressed_no_signal(true)
		)
	else:
		_add_status(row, tool, status)
	_add_connector(row, saved)
	var on := bool(tool.get("enabled", false)) or row.required or not row.saved_custom.is_empty()
	row.check.button_pressed = on and (row.available or row.is_custom() or row.required)
	_sync_row(row, false)
	if row.required:
		row.check.tooltip_text = "Every project uses Godot."
		row.check.focus_mode = Control.FOCUS_NONE
	elif not row.available:
		row.check.tooltip_text = str(status.get("reason", "Not available yet."))
	return row


func _add_status(parent: Control, tool: Dictionary, status: Dictionary) -> void:
	if status.is_empty():
		return
	# Plain secondary text with a state dot; nothing that looks pressable.
	var state := HBoxContainer.new()
	state.theme_type_variation = "JoineryRowTight"
	state.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	state.add_child(UI_COMPONENTS.status_dot(str(status["dot"])))
	var text := UI_COMPONENTS.label(str(status["text"]), "JoineryCaption")
	var reason := str(status.get("reason", ""))
	text.tooltip_text = reason if not reason.is_empty() else text.text
	text.mouse_filter = Control.MOUSE_FILTER_PASS
	state.add_child(text)
	parent.add_child(state)
	var url: Variant = tool.get("get_url")
	if status["key"] == "missing" and url is String and not (url as String).is_empty():
		var link := UI_COMPONENTS.link("Get it")
		link.tooltip_text = url
		link.pressed.connect(func() -> void: OS.shell_open(str(url)))
		parent.add_child(link)


## The app's connection as a field (app name above the picker), shown under Advanced.
func _add_connector(row: Row, saved: Array[String]) -> void:
	var app_name := str(row.tool.get("name", row.tool_id))
	row.picker = OptionButton.new()
	row.picker.fit_to_longest_item = false
	row.picker.clip_text = true
	row.picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	row.picker.accessibility_name = "Connection for " + app_name
	row.picker.theme_changed.connect(style_as_field.bind(row.picker))
	var field := UI_COMPONENTS.field(app_name, row.picker)
	row.connector = field
	var selected := 0
	for entry: Variant in row.tool.get("mcp_options", []):
		if not entry is Dictionary:
			continue
		var option := entry as Dictionary
		var index := row.picker.item_count
		var raw := str(option.get("label", ""))
		row.picker.add_item(connection_name(option))
		row.picker.set_item_metadata(index, option)
		var why := str(option.get("reason", ""))
		row.picker.set_item_tooltip(index, raw if why.is_empty() else raw + "\n" + why)
		row.picker.set_item_disabled(index, not option.get("usable", true))
		var custom: Dictionary = option.get("custom", {})
		if saved.has(str(custom.get("id", "-"))):
			selected = index
			row.saved_custom = str(custom["id"])
	var own := row.picker.item_count
	row.picker.add_item("Use my own…")
	row.picker.set_item_metadata(own, {"own": true})
	if saved.has(row.tool_id + "-own"):
		selected = own
		row.saved_custom = row.tool_id + "-own"
	row.picker.select(selected)
	row.picker.item_selected.connect(func(_index: int) -> void: _sync_row(row, true))
	row.note = field.help
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	field.add_child(body)
	var command := LineEdit.new()
	command.placeholder_text = "e.g. /usr/local/bin/my-connector"
	row.command = UI_COMPONENTS.field("Command", command)
	body.add_child(row.command)
	var arguments := LineEdit.new()
	arguments.placeholder_text = "e.g. --stdio"
	row.arguments = UI_COMPONENTS.field(
		"Arguments", arguments, "Separate with spaces. Quote an argument that contains spaces."
	)
	body.add_child(row.arguments)
	for line: LineEdit in [command, arguments]:
		line.text_changed.connect(func(_text: String) -> void: _clear_row_errors(row))
		# A long path scrolls inside the field; the tooltip has all of it.
		line.text_changed.connect(func(text: String) -> void: line.tooltip_text = text)
		register_text_enter(line)


## A connection's name for people ("hi-godot/godot-ai" reads "Godot AI (community)"); the
## raw id stays in the item tooltip.
static func connection_name(option: Dictionary) -> String:
	var shown := str(option.get("display_name", ""))
	if shown.is_empty():
		var raw := str(option.get("label", ""))
		var words: Array[String] = []
		for word: String in (
			raw.get_slice("/", raw.get_slice_count("/") - 1).replace("_", "-").split("-", false)
		):
			var upper := word.to_lower() in ["ai", "mcp", "api", "gimp", "midi", "ui"]
			words.append(word.to_upper() if upper else word.left(1).to_upper() + word.substr(1))
		shown = " ".join(words) if not words.is_empty() else raw
	match str(option.get("state", "")):
		"chosen":
			return "%s (recommended)" % shown
		"candidate":
			return "%s (not reviewed)" % shown
		"alternative":
			return "%s (community)" % shown
	return shown


## Make a form OptionButton look like the fields around it: the theme's LineEdit fill, edge,
## inset and focus ring, re-read on every theme change.
static func style_as_field(picker: OptionButton) -> void:
	# Each override re-sends theme_changed; the meta keeps this from re-entering.
	if picker.has_meta("_joinery_styling"):
		return
	picker.set_meta("_joinery_styling", true)
	var normal := picker.get_theme_stylebox("normal", "LineEdit")
	var states := {
		"normal": normal,
		"hover": normal,
		"pressed": normal,
		"hover_pressed": normal,
		"disabled": picker.get_theme_stylebox("read_only", "LineEdit"),
		"focus": picker.get_theme_stylebox("focus", "LineEdit"),
	}
	for state: String in states:
		picker.add_theme_stylebox_override(state, states[state])
	var ink := picker.get_theme_color("font_color", "LineEdit")
	for color: String in [
		"font_color", "font_hover_color", "font_pressed_color", "font_focus_color"
	]:
		picker.add_theme_color_override(color, ink)
	picker.remove_meta("_joinery_styling")


## Apply the chosen connector to its row: the note, the Command and Arguments fields, and
## whether the checkbox can change (Godot always on; an unavailable app only with its own).
func _sync_row(row: Row, by_user: bool) -> void:
	var option := row.option()
	var custom := row.is_custom()
	var note := ""
	match str(option.get("state", "own" if option.get("own", false) else "")):
		"alternative":
			note = "Not recommended: %s." % str(option.get("summary", "")).trim_suffix(".")
		"candidate":
			note = "Not recommended: Joinery hasn't checked this connector yet."
		"own":
			note = "Assets it makes are recorded as made by a custom tool."
	row.note.text = note
	row.note.visible = not note.is_empty()
	row.command.visible = custom
	row.arguments.visible = custom
	row.check.disabled = not row.required and not custom and not row.available
	if row.required:
		row.check.button_pressed = true
	elif custom and by_user:
		row.check.button_pressed = true
	elif not custom and not row.available:
		row.check.button_pressed = false
	_clear_row_errors(row)
	if custom and by_user:
		(row.command.get("control") as Control).grab_focus()


func _clear_row_errors(row: Row) -> void:
	row.command.call("set_error", "")
	row.arguments.call("set_error", "")


# --- Saving ----------------------------------------------------------------------------------


func _on_save() -> void:
	if _save_job != null or not _loaded:
		return
	var tools: Array[String] = []
	var customs: Array = []
	var first_problem: Control = null
	for row: Row in _rows:
		if not row.check.button_pressed:
			continue
		if not row.is_custom():
			tools.append(row.tool_id)
			continue
		var entry := _custom_choice(row)
		if entry.has("problem"):
			if first_problem == null:
				first_problem = entry["problem"]
				_advanced.set_open(true)
		elif not entry.is_empty():
			customs.append(entry)
	if first_problem != null:
		first_problem.grab_focus()
		_show_status("Check the connection under Advanced.", true)
		return
	tools.sort()
	var args := _cli(["setup", "--project", _project(), "--tools", ",".join(tools)])
	if not customs.is_empty():
		args.append_array(PackedStringArray(["--custom-mcps", JSON.stringify(customs)]))
	_details.hide()
	_show_status("", false)
	_save_job = COMMAND_SCRIPT.new()
	if _save_job.start("uv", args) != OK:
		_save_job = null
		_save_failed("uv isn't available", "")
		return
	_set_saving(true)


## The custom connector entry for a row, {} to keep an already saved one unchanged, or
## {"problem": control} when a field needs fixing (its error is shown under it).
func _custom_choice(row: Row) -> Dictionary:
	var entry := row.custom_entry()
	var command := (row.command.get("control") as LineEdit).text.strip_edges()
	var arguments := row.arguments.get("control") as LineEdit
	if command.is_empty():
		if entry["id"] == row.saved_custom and arguments.text.strip_edges().is_empty():
			return {}
		row.command.call("set_error", "Enter the command that starts this connector")
		return {"problem": row.command.get("control")}
	var parsed := split_arguments(arguments.text)
	if parsed.has("error"):
		row.arguments.call("set_error", parsed["error"])
		return {"problem": arguments}
	entry["command"] = command
	entry["args"] = parsed["args"]
	return entry


func _save_done(result: Dictionary) -> void:
	_save_job = null
	_set_saving(false)
	var output := (
		(str(result.get("output", "")) + "\n" + str(result.get("error", ""))).strip_edges()
	)
	print("[Joinery] setup exit=%d %s" % [int(result.get("code", 1)), output])
	if int(result.get("code", 1)) != 0:
		_save_failed(_first_line(str(result.get("error", "")) + "\n" + output), output)
		return
	var name_text := (_name_field.control as LineEdit).text.strip_edges()
	if not name_text.is_empty():
		ProjectSettings.set_setting("application/config/name", name_text)
		ProjectSettings.save()
	_show_status("Saved.", false)
	UI_MOTION.pulse(_status)
	if UI_MOTION.is_enabled():
		await get_tree().create_timer(UI_MOTION.duration("confirm")).timeout
	if _save_job == null:
		hide()


func _save_failed(reason: String, output: String) -> void:
	_show_status("Couldn't save: " + (reason if not reason.is_empty() else "no reason given"), true)
	_details_text.text = output
	_details.visible = not output.is_empty()
	if _details.visible:
		_details.call("set_open", false)
		UI_MOTION.fade_in(_details)


func _set_saving(on: bool) -> void:
	var save := get_ok_button()
	save.text = "Saving…" if on else "Save"
	_save_indicator.set("running", on)
	_update_save()


func _update_save() -> void:
	var save := get_ok_button()
	save.disabled = _save_job != null or not _loaded
	save.tooltip_text = (
		""
		if _loaded
		else ("Loading the app list…" if _catalog_job != null else "Load the app list first.")
	)


func _show_status(text: String, error: bool) -> void:
	_status.text = text
	_status.theme_type_variation = "JoineryError" if error else "JoineryCaption"
	_status.visible = not text.is_empty()


# --- Jobs and helpers ------------------------------------------------------------------------


func _process(_delta: float) -> void:
	if _catalog_job != null:
		var result: Dictionary = _catalog_job.poll()
		if not result.is_empty():
			_catalog_done(result)
	if _save_job != null:
		var saved: Dictionary = _save_job.poll()
		if not saved.is_empty():
			_save_done(saved)


func _cancel_jobs() -> void:
	for job: RefCounted in [_catalog_job, _save_job]:
		if job != null:
			job.call("cancel")
	_catalog_job = null
	_save_job = null
	_set_saving(false)


func _exit_tree() -> void:
	_cancel_jobs()


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


func _cli(arguments: Array) -> PackedStringArray:
	var engine := OS.get_environment("JOINERY_HOME")
	if engine.is_empty():
		engine = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var args := PackedStringArray(["run", "--frozen", "--quiet", "--project", engine, "joinery"])
	for argument: Variant in arguments:
		args.append(str(argument))
	return args


## The first line worth showing from CLI output, without "setup:"-style prefixes.
static func _first_line(text: String) -> String:
	for raw: String in text.split("\n", false):
		var line := raw.strip_edges()
		if line.is_empty() or line.begins_with("Traceback") or line.begins_with("warning:"):
			continue
		for prefix: String in ["setup:", "error:", "Error:"]:
			line = line.trim_prefix(prefix).strip_edges()
		return line.trim_suffix(".").left(160)
	return ""


## Split [param text] on spaces, keeping "quoted parts" together. A backslash escapes a
## quote, a space or another backslash; any other backslash is kept (Windows paths).
## Returns {"args": Array[String]} or {"error": text}.
static func split_arguments(text: String) -> Dictionary:
	var args: Array[String] = []
	var current := ""
	var quote := ""
	var started := false
	var index := 0
	while index < text.length():
		var ch := text[index]
		var next := text[index + 1] if index + 1 < text.length() else ""
		index += 1
		if ch == "\\" and next in ['"', "'", " ", "\\"] and quote != "'":
			current += next
			started = true
			index += 1
		elif not quote.is_empty():
			if ch == quote:
				quote = ""
			else:
				current += ch
		elif ch == '"' or ch == "'":
			quote = ch
			started = true
		elif ch == " " or ch == "\t":
			if started:
				args.append(current)
			current = ""
			started = false
		else:
			current += ch
			started = true
	if not quote.is_empty():
		return {"error": "Close the quote in the arguments"}
	if started:
		args.append(current)
	return {"args": args}
