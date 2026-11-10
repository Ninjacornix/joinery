@tool
extends AcceptDialog
## "Project apps": which real apps make this project, how ready each one is, and its game
## features (project_features.gd). The list comes from `joinery tools --json` (the catalog is
## the one source of truth) and Save goes through `joinery setup`, which validates every
## choice. Readiness comes from `joinery status --catalog --background --json` (ADR 0030),
## asked again while it is still checking. Each row: checkbox and name, blurb, dot and wording
## with at most one action (Start Docker, Rebuild, Check again, Get it), and the reason below.
## "Older versions" (project_history.gd) goes with Save. Connections sit under one collapsed
## "Advanced" section. Every command is a command_job.gd job, so the editor never blocks.

## Saved with a new history policy: past changes may no longer revert.
signal history_changed

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR := preload("activity_indicator.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const FEATURES := preload("project_features.gd")
const HISTORY := preload("project_history.gd")
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
const RULE_ZERO := "Joinery makes assets with real apps, never AI generators."
const DOCKER_HOWTO := "Open Docker Desktop and wait until it says it's running, then check again."
## Seconds between readiness questions while something is still being checked or built.
const POLL_SECONDS := 2.0
## Size of the dialog: width in multiples of the body font, capped by these screen fractions.
const WIDTH_EM := 52
const WIDTH_SCREEN := 0.9
const HEIGHT_SCREEN := 0.8

## The command that runs the Joinery CLI, followed by its leading arguments. Empty: the
## engine checkout's `uv run … joinery`. Tests point it at a fixture.
var cli_prefix := PackedStringArray()
## What "Start Docker" runs besides showing how (empty: only the how-to).
var start_docker_argv := PackedStringArray(
	["open", "-a", "Docker"] if OS.get_name() == "macOS" else []
)

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
var _features: FEATURES
var _history: HISTORY
var _history_saved := false
var _catalog_job: RefCounted
var _save_job: RefCounted
var _status_job: RefCounted
var _rebuild_job: RefCounted
var _check_jobs: Dictionary = {}
var _loaded := false
var _poll_in := -1.0


## The dialog's content root: the watched theme root. A rebuild is copied to the dialog so its
## own Save and Cancel buttons follow the same theme.
class Body:
	extends VBoxContainer
	signal rebuilt

	func _on_theme_rebuilt() -> void:
		rebuilt.emit()


## One app: checkbox with its name, blurb, readiness and its one action; the reason (and the
## Docker how-to) on the line below. Its connection field lives under the Advanced section.
class Row:
	extends VBoxContainer
	var tool_id := ""
	var tool: Dictionary = {}
	var required := false
	var available := true
	## Id of a custom connector this project already saved for the app, if any.
	var saved_custom := ""
	var line: HBoxContainer
	var check: CheckBox
	var state: UI_COMPONENTS.Readiness
	var below: HBoxContainer
	var indent: Control
	var reason: Label
	var howto: PanelContainer
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
	_list.theme_type_variation = "JoinerySections"
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
	_banner = UI_COMPONENTS.notice("", "Retry")
	_banner.hide()
	_body.add_child(_banner)
	_banner_text = _banner.find_child("Text", true, false)
	(_banner.find_child("Action", true, false) as Button).pressed.connect(_load_catalog)


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
	cancel.pressed.connect(_cancel_jobs)
	UI_COMPONENTS.dialog_footer(self)


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
	var target := Vector2i(width, roundi(screen.y * HEIGHT_SCREEN))
	popup_centered(target)
	# Wrapped text is measured at zero width on the first layout, which stretches the dialog
	# past the screen; once it has its width, return to the intended size.
	await get_tree().process_frame
	if visible:
		size = Vector2i(target.x, maxi(target.y, ceili(get_contents_minimum_size().y)))
		move_to_center()


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
	_features = null
	_history = null
	_loading.show()
	(_loading.get_child(0) as Control).set("running", true)
	_update_save()
	_catalog_job = _start(["tools", "--json", "--project", _project()])
	if _catalog_job == null:
		_catalog_failed("Joinery's command line isn't available")


func _catalog_done(result: Dictionary) -> void:
	_catalog_job = null
	var data: Variant = _json(result)
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
	_query_status()
	_features.reload()


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
	_features = FEATURES.new()
	_features.cli_prefix = cli_prefix
	_features.project = _project()
	_features.applied.connect(
		func(message: String) -> void:
			_show_status(message, false)
			UI_MOTION.pulse(_status)
	)
	_list.add_child(_features)
	_history = HISTORY.new()
	_history.cli_prefix = cli_prefix
	_history.project = _project()
	_history.panel.applied.connect(_on_save)
	_list.add_child(_history)
	_history.reload()
	_advanced = UI_COMPONENTS.section("Advanced", true)
	_advanced.header.tooltip_text = "Choose how Joinery connects to each app."
	_advanced.body.theme_type_variation = "JoineryStack"
	_list.add_child(_advanced)
	for row: Row in _rows:
		_advanced.body.add_child(row.connector)
	_align_names()
	UI_MOTION.stagger(shown)


## Every blurb starts at one x: each name column is as wide as the widest name, so the
## list reads as two columns instead of a ragged middle edge. Reasons start there too.
func _align_names() -> void:
	var widest := 0.0
	for row: Row in _rows:
		row.check.custom_minimum_size.x = 0
		widest = maxf(widest, row.check.get_combined_minimum_size().x)
	for row: Row in _rows:
		row.check.custom_minimum_size.x = widest
		row.indent.custom_minimum_size.x = widest


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


func _row(tool: Dictionary, saved: Array[String]) -> Row:
	var row := Row.new()
	row.name = "App_" + str(tool.get("id", "")).replace("-", "_")
	row.theme_type_variation = "JoineryFlush"
	row.tool = tool
	row.tool_id = str(tool.get("id", ""))
	row.required = bool(tool.get("required", false))
	# Readiness never blocks choosing an app: only the catalog can refuse one.
	row.available = bool(tool.get("available", true))
	var display := str(tool.get("name", row.tool_id))
	# One line, one control tall: the checkbox carries the name, the blurb follows in
	# secondary text, readiness and its one action sit at the trailing edge.
	row.line = HBoxContainer.new()
	row.line.theme_type_variation = "JoineryRow"
	UI_COMPONENTS.bind_height(row.line, "md", false)
	row.add_child(row.line)
	row.check = CheckBox.new()
	row.check.text = display
	row.check.focus_mode = Control.FOCUS_ALL
	row.check.accessibility_name = display
	row.check.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_COMPONENTS.bind_height(row.check, "md", false)
	row.check.toggled.connect(func(_on: bool) -> void: _clear_row_errors(row))
	row.line.add_child(row.check)
	var blurb := UI_COMPONENTS.label(_blurb_of(tool), "JoineryCaption")
	blurb.tooltip_text = blurb.text
	blurb.mouse_filter = Control.MOUSE_FILTER_PASS
	blurb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	blurb.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.line.add_child(blurb)
	row.state = UI_COMPONENTS.readiness()
	row.state.action.pressed.connect(_on_action.bind(row))
	row.line.add_child(row.state)
	# The reason (and the Docker how-to) line up with the blurb.
	row.below = HBoxContainer.new()
	row.below.theme_type_variation = "JoineryRow"
	row.below.hide()
	row.add_child(row.below)
	row.indent = Control.new()
	row.indent.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.below.add_child(row.indent)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.below.add_child(words)
	row.reason = UI_COMPONENTS.label("", "JoineryCaption", true)
	row.reason.name = "Reason"
	words.add_child(row.reason)
	row.howto = _howto(row)
	words.add_child(row.howto)
	_add_connector(row, saved)
	var on := bool(tool.get("enabled", false)) or row.required or not row.saved_custom.is_empty()
	row.check.button_pressed = on and (row.available or row.is_custom() or row.required)
	_sync_row(row, false)
	if row.required:
		UI_COMPONENTS.lock_check(row.check, "Every project uses Godot.")
	elif not row.available:
		row.check.tooltip_text = str(tool.get("status_reason", "Not available yet."))
	_apply_readiness(row, tool)
	return row


## The Docker how-to under a row, with Check again: shown by "Start Docker".
func _howto(row: Row) -> PanelContainer:
	var panel := UI_COMPONENTS.notice(DOCKER_HOWTO, "Check again", "JoineryCard")
	panel.name = "HowTo"
	panel.hide()
	(panel.find_child("Action", true, false) as Button).pressed.connect(_check_again.bind(row))
	return panel


# --- Readiness -------------------------------------------------------------------------------


## Show one readiness entry (from `joinery status` or the menu) on its row.
func _apply_readiness(row: Row, entry: Dictionary) -> void:
	var key := str(entry.get("status", ""))
	var previous := str(row.state.entry.get("status", ""))
	row.state.set_entry(entry)
	if not row.state.visible:
		return
	var get_it := str(entry.get("get_url", row.tool.get("get_url", "")))
	if not (row.required or row.available) or (key == "needs_licence" and get_it.is_empty()):
		row.state.action.set_action(null)
	var reason := str(entry.get("status_reason", ""))
	if key == "ready" and not entry.get("checking", false):
		reason = _feature_gap(entry)
	row.reason.text = reason
	row.reason.visible = not reason.is_empty()
	row.howto.visible = row.howto.visible and key == "needs_docker"
	row.below.visible = row.reason.visible or row.howto.visible
	if key == "ready" and previous not in ["", "ready"] and row.is_inside_tree():
		UI_MOTION.pulse(row.state)


## A ready app that can't do everything says what is missing (Surge XT without its SoundFont).
func _feature_gap(entry: Dictionary) -> String:
	for feature: Variant in entry.get("features", []):
		if feature is Dictionary and str(feature.get("status", "ready")) != "ready":
			if not bool(feature.get("checking", false)):
				return str(feature.get("detail", ""))
	return ""


func _query_status() -> void:
	if _status_job != null:
		return
	_poll_in = -1.0
	_status_job = _start(["status", "--catalog", "--background", "--json", "--project", _project()])


func _status_done(result: Dictionary) -> void:
	_status_job = null
	var data: Variant = _json(result)
	if not data is Dictionary:
		# Keep what the menu said; ask again later.
		_poll_in = POLL_SECONDS * 5.0
		return
	var entries := {}
	for entry: Variant in data.get("tools", []):
		if entry is Dictionary:
			entries[str(entry.get("id", ""))] = entry
	var waiting := bool(data.get("checking", false)) or _rebuild_job != null
	for row: Row in _rows:
		if entries.has(row.tool_id) and not _check_jobs.has(row.tool_id):
			_apply_readiness(row, entries[row.tool_id])
		waiting = waiting or str(row.state.entry.get("status", "")) == "preparing"
	_poll_in = POLL_SECONDS if waiting else -1.0


func _on_action(row: Row) -> void:
	match row.state.action.action_id:
		"start_docker":
			_start_docker(row)
		"rebuild":
			_rebuild(row)
		"check_again":
			_check_again(row)
		"get":
			var url := str(row.state.entry.get("get_url", row.tool.get("get_url", "")))
			if not url.is_empty():
				OS.shell_open(url)


func _start_docker(row: Row) -> void:
	if not start_docker_argv.is_empty():
		OS.create_process(start_docker_argv[0], start_docker_argv.slice(1))
	row.howto.show()
	row.below.show()
	UI_MOTION.reveal(row.below)
	(row.howto.find_child("Action", true, false) as Control).grab_focus()


func _check_again(row: Row) -> void:
	if _check_jobs.has(row.tool_id):
		return
	var job := _start(
		["status", "--tool", row.tool_id, "--refresh", "--json", "--project", _project()]
	)
	if job == null:
		return
	_check_jobs[row.tool_id] = job
	row.state.set_busy(true, "Checking…")


func _check_done(tool_id: String, result: Dictionary) -> void:
	_check_jobs.erase(tool_id)
	var row := _find_row(tool_id)
	if row == null:
		return
	row.state.set_busy(false)
	var data: Variant = _json(result)
	var entry: Dictionary = {}
	if data is Dictionary:
		for item: Variant in data.get("tools", []):
			if item is Dictionary and str(item.get("id", "")) == tool_id:
				entry = item
	if entry.is_empty():
		_apply_readiness(row, row.state.entry)
		row.reason.text = "Joinery couldn't check %s just now." % row.check.text
		row.reason.show()
		row.below.show()
		return
	_apply_readiness(row, entry)


## Rebuild the offline workers (`joinery bootstrap`): readiness says Downloading… meanwhile.
func _rebuild(row: Row) -> void:
	if _rebuild_job != null:
		return
	_rebuild_job = _start(["bootstrap", "--project", _project()])
	if _rebuild_job == null:
		return
	for other: Row in _rows:
		if other.state.action.action_id == "rebuild":
			other.state.set_busy(true)
	row.reason.text = "Rebuilding the offline workers. This can take a few minutes."
	row.reason.show()
	row.below.show()
	_poll_in = POLL_SECONDS


func _rebuild_done(result: Dictionary) -> void:
	_rebuild_job = null
	for row: Row in _rows:
		row.state.set_busy(false)
		_apply_readiness(row, row.state.entry)
	if int(result.get("code", 1)) != 0:
		var reason := _first_line(
			str(result.get("error", "")) + "\n" + str(result.get("output", ""))
		)
		var why := reason if not reason.is_empty() else "no reason given"
		_show_status("Rebuild didn't finish: " + why, true)
	_query_status()


func _find_row(tool_id: String) -> Row:
	for row: Row in _rows:
		if row.tool_id == tool_id:
			return row
	return null


# --- Connections -----------------------------------------------------------------------------


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
	if not row.required:
		row.check.disabled = not custom and not row.available
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
	var history: Variant = _history.save_args() if _history != null else []
	if history == null:
		_show_status("Check the numbers under Older versions.", true)
		return
	tools.sort()
	var args := ["setup", "--project", _project(), "--tools", ",".join(tools)]
	if not customs.is_empty():
		args.append_array(["--custom-mcps", JSON.stringify(customs)])
	args.append_array(history)
	_history_saved = not (history as Array).is_empty()
	_details.hide()
	_show_status("", false)
	_save_job = _start(args)
	if _save_job == null:
		_save_failed("Joinery's command line isn't available", "")
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
	if _history_saved:
		history_changed.emit()
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


func _process(delta: float) -> void:
	_catalog_job = _poll(_catalog_job, _catalog_done)
	_save_job = _poll(_save_job, _save_done)
	_status_job = _poll(_status_job, _status_done)
	_rebuild_job = _poll(_rebuild_job, _rebuild_done)
	for tool_id: String in _check_jobs.keys():
		var result: Dictionary = (_check_jobs[tool_id] as RefCounted).call("poll")
		if not result.is_empty():
			_check_done(tool_id, result)
	if _poll_in >= 0.0 and _status_job == null and visible:
		_poll_in -= delta
		if _poll_in < 0.0:
			_query_status()


func _poll(job: RefCounted, done: Callable) -> RefCounted:
	return COMMAND_SCRIPT.finish(job, done)


func _cancel_jobs() -> void:
	var jobs: Array = [_catalog_job, _save_job, _status_job]
	jobs.append_array(_check_jobs.values())
	for job: Variant in jobs:
		if job != null:
			(job as RefCounted).call("cancel")
	# A rebuild carries on: stopping it halfway helps nobody.
	_catalog_job = null
	_save_job = null
	_status_job = null
	_check_jobs.clear()
	_poll_in = -1.0
	_set_saving(false)


func _exit_tree() -> void:
	_cancel_jobs()


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


## Start the Joinery CLI with [param arguments] as a job; null when it can't start.
func _start(arguments: Array) -> RefCounted:
	return COMMAND_SCRIPT.joinery(cli_prefix, arguments)


static func _json(result: Dictionary) -> Variant:
	var json := JSON.new()
	if json.parse(str(result.get("output", ""))) != OK:
		return null
	return json.data


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
