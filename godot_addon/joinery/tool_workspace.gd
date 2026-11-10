@tool
extends VBoxContainer
## One shared viewer for all creation tools. Desktop opening is independent of MCP readiness;
## the Python index owns provenance, launch resolution, asset kinds and which derived files
## (baked maps, exports, renders) fold under their source asset.

signal catalog_changed(tools: Array, assets: Array)
signal creator_changed(tool: String)
signal asset_selected(asset: Dictionary)
## The user asked to set up apps (an app needed to open an asset is missing).
signal setup_requested
## The user picked a part of the shown model (list or model click): a target_chip.for_part.
signal part_selected(target: Dictionary)

const COMMAND_SCRIPT := preload("res://addons/joinery/command_job.gd")
const AUDIO_SCRIPT := preload("res://addons/joinery/audio_preview.gd")
const MODEL_SCRIPT := preload("res://addons/joinery/model_preview.gd")
const LIST_SCRIPT := preload("res://addons/joinery/asset_list.gd")
const INDICATOR_SCRIPT := preload("res://addons/joinery/activity_indicator.gd")
const PARTS_SCRIPT := preload("res://addons/joinery/parts_panel.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_COMPONENTS := preload("res://addons/joinery/ui_components.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
## Section titles in list order; "other" collects files of no known kind.
const SECTIONS := {
	"model": "Models",
	"sound": "Sounds",
	"image": "Images",
	"material": "Materials",
	"scene": "Scenes",
	"other": "Other files",
}
## Readiness ("status", older names too) → [dot, wording], the same marks as the app rail.
const READINESS := {
	"ready": ["ready", "Ready"],
	"preparing": ["working", "Downloading…"],
	"needs_docker": ["off", "Needs Docker"],
	"needs_licence": ["off", "Needs your licence"],
	"unavailable": ["off", "Not available yet"],
	"installed": ["ready", "Ready"],
	"licence": ["off", "Needs your licence"],
	"missing": ["off", "Not installed"],
}
const IMAGES := ["png", "jpg", "jpeg", "webp", "bmp", "tga", "svg"]
const SOUNDS := ["wav", "ogg", "mp3"]
const MODELS := ["blend", "glb", "gltf", "tscn"]
const TEXTS := ["scd", "sc", "gd", "ptex"]
## Browser column width as a multiple of the editor font size.
const BROWSER_EM := 16.0
## Seconds between automatic refreshes while visible, and how long "Opened in …" stays.
const REFRESH_SECONDS := 2.0
const OPENED_SECONDS := 2.0

var _browser: VBoxContainer
var _filter_section: VBoxContainer
var _tools: LIST_SCRIPT
var _search: LineEdit
var _loading: HBoxContainer
var _loading_indicator: INDICATOR_SCRIPT
var _empty: Label
var _error_box: VBoxContainer
var _error_label: Label
var _assets: LIST_SCRIPT
var _header: HBoxContainer
var _title: Label
var _caption: Label
var _actions: HBoxContainer
var _open: Button
var _open_menu: MenuButton
var _open_with: PopupMenu
var _open_indicator: INDICATOR_SCRIPT
var _unavailable: HBoxContainer
var _unavailable_title: Label
var _unavailable_label: Label
var _setup_link: Button
var _parts: HFlowContainer
var _preview: VBoxContainer
var _parts_panel: PARTS_SCRIPT
var _status: Label
var _audio: AudioStreamPlayer
var _audio_panel: AUDIO_SCRIPT
var _command_job: RefCounted
var _job := ""
var _data: Dictionary = {}
var _by_path: Dictionary = {}
var _rows: Array[Dictionary] = []
var _current: Dictionary = {}
var _selected_path := ""
var _stamp := ""
var _load_error := ""
var _refresh_elapsed := 0.0
var _invalidated := false
var _queued_open := PackedStringArray()
var _pending_scene_preview := ""
var _preview_import_changed := false
var _opening := ""
var _opened := ""
var _opened_until := 0
var _loaded_once := false
## The app the preview's "isn't installed" block names; the header does not repeat it.
var _explained_app := ""


func _ready() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_THEME.watch(self)
	var page := MarginContainer.new()
	page.theme_type_variation = "JoineryPage"
	page.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(page)
	var split := HSplitContainer.new()
	page.add_child(split)
	_build_browser(split)
	_build_body(split)
	_audio = AudioStreamPlayer.new()
	add_child(_audio)
	_on_theme_rebuilt()
	_update_list_state()


func _build_browser(split: HSplitContainer) -> void:
	_browser = VBoxContainer.new()
	_browser.theme_type_variation = "JoineryStack"
	split.add_child(_browser)
	_filter_section = VBoxContainer.new()
	_filter_section.theme_type_variation = "JoineryTight"
	_browser.add_child(_filter_section)
	var heading := MarginContainer.new()
	heading.theme_type_variation = "JoineryInset"
	heading.add_child(UI_COMPONENTS.label("Created in", "JoinerySection"))
	_filter_section.add_child(heading)
	_tools = LIST_SCRIPT.new()
	_tools.fit_content = true
	_tools.item_selected.connect(func(_index: int) -> void: _filter(true))
	_filter_section.add_child(_tools)
	_search = LineEdit.new()
	_search.placeholder_text = "Find an asset"
	_search.accessibility_name = "Find an asset"
	_search.clear_button_enabled = true
	_search.text_changed.connect(func(_text: String) -> void: _filter())
	UI_COMPONENTS.bind_height(_search, "md", false)
	_browser.add_child(_search)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRow"
	_loading_indicator = INDICATOR_SCRIPT.new()
	_loading.add_child(_loading_indicator)
	_loading.add_child(UI_COMPONENTS.label("Loading assets…", "JoineryCaption"))
	_browser.add_child(_loading)
	_empty = UI_COMPONENTS.label("", "JoineryCaption", true)
	_browser.add_child(_empty)
	_error_box = VBoxContainer.new()
	_error_box.theme_type_variation = "JoineryTight"
	_error_label = UI_COMPONENTS.label("", "JoineryError", true)
	_error_box.add_child(_error_label)
	var retry := UI_COMPONENTS.secondary("Retry")
	retry.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	retry.pressed.connect(_retry)
	_error_box.add_child(retry)
	_browser.add_child(_error_box)
	_assets = LIST_SCRIPT.new()
	_assets.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_assets.item_selected.connect(_select)
	_browser.add_child(_assets)


func _build_body(split: HSplitContainer) -> void:
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryStack"
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(body)
	_header = HBoxContainer.new()
	_header.theme_type_variation = "JoineryRow"
	_header.hide()
	body.add_child(_header)
	var names := VBoxContainer.new()
	names.theme_type_variation = "JoineryTight"
	names.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	names.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_header.add_child(names)
	_title = UI_COMPONENTS.label("", "JoineryTitle")
	names.add_child(_title)
	_caption = UI_COMPONENTS.label("", "JoineryCaption")
	names.add_child(_caption)
	_actions = HBoxContainer.new()
	_actions.theme_type_variation = "JoineryRow"
	_actions.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_header.add_child(_actions)
	_open_indicator = INDICATOR_SCRIPT.new()
	_actions.add_child(_open_indicator)
	_open = UI_COMPONENTS.primary("Open")
	_open.pressed.connect(_open_option.bind(0))
	_actions.add_child(_open)
	_open_menu = MenuButton.new()
	_open_menu.theme_type_variation = "JoineryIconButton"
	_open_menu.flat = false
	_open_menu.focus_mode = Control.FOCUS_ALL
	_open_menu.tooltip_text = "Open with…"
	_open_menu.accessibility_name = "Open with"
	UI_COMPONENTS.bind_icon(_open_menu, "chevron")
	UI_COMPONENTS.bind_height(_open_menu, "md")
	_open_menu.icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_open_menu.theme_changed.connect(
		func() -> void:
			var side := float(UI_THEME.control_h(_open_menu, "md"))
			var square := _open_menu.text.is_empty()
			_open_menu.custom_minimum_size = Vector2(side if square else 0.0, side)
	)
	_open_with = _open_menu.get_popup()
	_open_with.id_pressed.connect(_open_option)
	_actions.add_child(_open_menu)
	_build_unavailable(body)
	_parts = HFlowContainer.new()
	_parts.hide()
	body.add_child(_parts)
	var stage := HBoxContainer.new()
	stage.theme_type_variation = "JoineryRow"
	stage.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(stage)
	_parts_panel = PARTS_SCRIPT.new()
	_parts_panel.part_selected.connect(part_selected.emit)
	stage.add_child(_parts_panel.map_view)
	_preview = VBoxContainer.new()
	_preview.theme_type_variation = "JoineryTight"
	_preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	stage.add_child(_preview)
	body.add_child(_parts_panel)
	_status = UI_COMPONENTS.label("", "JoineryError", true)
	_status.hide()
	body.add_child(_status)


## The compact "app isn't installed" notice under the asset title: the same title, line and
## "Open Project apps" action as the preview's empty state, for files that still preview.
func _build_unavailable(body: VBoxContainer) -> void:
	_unavailable = HBoxContainer.new()
	_unavailable.theme_type_variation = "JoineryRow"
	_unavailable.hide()
	body.add_child(_unavailable)
	var texts := VBoxContainer.new()
	texts.theme_type_variation = "JoineryTight"
	texts.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texts.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_unavailable.add_child(texts)
	_unavailable_title = UI_COMPONENTS.label("")
	texts.add_child(_unavailable_title)
	_unavailable_label = UI_COMPONENTS.label("", "JoineryCaption", true)
	texts.add_child(_unavailable_label)
	_setup_link = _setup_button()
	_unavailable.add_child(_setup_link)


func _setup_button() -> Button:
	var setup := UI_COMPONENTS.secondary("Open Project apps")
	setup.tooltip_text = "See which apps this project uses and what each one needs"
	setup.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	setup.pressed.connect(func() -> void: setup_requested.emit())
	return setup


## Title and line for a missing app (notice and empty state); a preview still works here.
static func _missing_text(app: String, extension: String, previews: bool) -> Array[String]:
	var noun := "sound" if extension in SOUNDS else "image" if extension in IMAGES else "model"
	var look := "listen" if noun == "sound" else "look at it"
	var line := "You can %s here, but changing this %s needs %s." % [look, noun, app]
	if not previews:
		line = "Install %s on this computer to see and change .%s files." % [app, extension]
	return ["%s isn't installed" % app, line]


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and _browser != null:
		_on_theme_rebuilt()


func _on_theme_rebuilt() -> void:
	_browser.custom_minimum_size.x = roundf(UI_THEME.font_size(self, "body") * BROWSER_EM)


func _exit_tree() -> void:
	if _command_job != null:
		_command_job.cancel()
		_command_job = null


func _process(delta: float) -> void:
	if not _pending_scene_preview.is_empty() and ResourceLoader.exists(_pending_scene_preview):
		var path := _pending_scene_preview
		_pending_scene_preview = ""
		_show_preview(path, _preview_import_changed)
		_preview_import_changed = false
	_refresh_elapsed += delta
	if _command_job == null:
		if not _queued_open.is_empty():
			var arguments := _queued_open
			_queued_open = PackedStringArray()
			_start_job("open", arguments)
			return
		if is_visible_in_tree() and (_invalidated or _refresh_elapsed >= REFRESH_SECONDS):
			refresh()
		return
	var result: Dictionary = _command_job.poll()
	if result.is_empty():
		return
	_command_job = null
	_refresh_elapsed = 0.0
	if _job == "open":
		_finish_open(result)
		return
	if int(result["code"]) != 0:
		var reason := PARTS_SCRIPT.first_line(str(result["error"]) if result.get("error") else "")
		if reason.is_empty():
			reason = PARTS_SCRIPT.first_line(str(result["output"]))
		_load_error = reason if not reason.is_empty() else "The workspace command failed."
		_update_list_state()
		return
	var payload: Variant = PARTS_SCRIPT.parse_json(str(result["output"]))
	if not payload is Dictionary:
		_load_error = "The workspace command did not return asset data."
		_update_list_state()
		return
	_load_error = ""
	_stamp = str(payload.get("stamp", ""))
	if not bool(payload.get("unchanged", false)) and (payload != _data or _preview_import_changed):
		_apply_data(payload)
		_preview_import_changed = false
	_update_list_state()


func invalidate() -> void:
	# Keep the last confirmed revision while requesting fresh data. Filesystem
	# notifications also cover editor/cache writes that do not change assets.
	_invalidated = true


func resources_reimported(paths: PackedStringArray) -> void:
	# A real import can change a loaded scene even when its raw file and catalog
	# metadata are unchanged (for example import settings or external textures).
	if paths.has("res://" + _selected_path):
		_preview_import_changed = true
	invalidate()


func _start_job(job: String, arguments: PackedStringArray) -> void:
	if _command_job != null:
		if job == "open":
			_queued_open = arguments
		return
	_job = job
	var engine_dir := OS.get_environment("JOINERY_HOME")
	if engine_dir.is_empty():
		engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", engine_dir, "joinery"]
	)
	args.append_array(arguments)
	args.append_array(["--project", ProjectSettings.globalize_path("res://")])
	_command_job = COMMAND_SCRIPT.new()
	var error: Error = _command_job.start("uv", args)
	if error == OK:
		return
	_command_job = null
	if job == "open":
		_finish_open({"code": int(error), "error": error_string(error), "output": ""})
	else:
		_load_error = "The workspace command could not start (%s)." % error_string(error)
		_update_list_state()


func refresh() -> void:
	if _command_job != null:
		return
	var since := "" if _invalidated else _stamp
	_refresh_elapsed = 0.0
	_invalidated = false
	# The CLI defaults to an empty stamp. Godot's native Windows argv builder
	# drops empty arguments, so omit this optional flag until a stamp exists.
	var arguments := PackedStringArray(["workspace"])
	if not since.is_empty():
		arguments.append_array(["--since", since])
	_start_job("refresh", arguments)


func _retry() -> void:
	_load_error = ""
	_update_list_state()
	invalidate()
	refresh()


func _apply_data(data: Dictionary) -> void:
	var selected_tool := _tool_id()
	_data = data
	_by_path.clear()
	for asset: Dictionary in data.get("assets", []):
		_by_path[str(asset["path"])] = asset
	var top := _top_level()
	var entries: Array[Dictionary] = [{"id": "", "label": "All apps", "tool": {}}]
	for tool: Dictionary in data.get("tools", []):
		entries.append({"id": str(tool["id"]), "label": str(tool["name"]), "tool": tool})
	entries.append({"id": "__imports", "label": "Imported", "tool": {}})
	entries.append({"id": "__unassigned", "label": "Unassigned", "tool": {}})
	var shown: Array[Dictionary] = []
	for entry: Dictionary in entries:
		var count := 0
		for asset: Dictionary in top:
			if _matches_tool(asset, entry["id"]):
				count += 1
		entry["count"] = count
		var tool: Dictionary = entry["tool"]
		if entry["id"] == "" or count > 0 or bool(tool.get("enabled", false)):
			shown.append(entry)
	# Same rows as before: update counts in place so they roll instead of being rebuilt.
	var same := _tools.item_count == shown.size()
	for index: int in shown.size() if same else 0:
		same = same and str(_tools.get_item_metadata(index)) == shown[index]["id"]
	if not same:
		_tools.clear()
		for entry: Dictionary in shown:
			_tools.set_item_metadata(_tools.add_item(entry["label"]), entry["id"])
	var chosen := 0
	for index: int in shown.size():
		_tools.set_item_count(index, shown[index]["count"])
		if not (shown[index]["tool"] as Dictionary).is_empty():
			_show_readiness(index, shown[index]["tool"])
		if shown[index]["id"] == selected_tool:
			chosen = index
	_tools.select(chosen)
	_filter(not _loaded_once)
	_loaded_once = true
	catalog_changed.emit(data.get("tools", []), top)
	creator_changed.emit(_tool_id())


## A tool's readiness: dot, wording in the tooltip; "unavailable" disables the row.
func _show_readiness(index: int, tool: Dictionary) -> void:
	var status := str(tool.get("status", ""))
	if not READINESS.has(status):
		_tools.set_item_status(index, "")
		_tools.set_item_tooltip(index, str(tool["name"]))
		_tools.set_item_disabled(index, false)
		return
	var state: Array = READINESS[status]
	var reason := str(tool.get("status_reason", ""))
	var text := "%s · %s" % [tool["name"], state[1]]
	_tools.set_item_status(index, state[0])
	_tools.set_item_tooltip(index, text + (". " + reason if not reason.is_empty() else ""))
	_tools.set_item_disabled(index, status == "unavailable")


func _top_level() -> Array[Dictionary]:
	var top: Array[Dictionary] = []
	for asset: Dictionary in _data.get("assets", []):
		if str(asset.get("part_of", "")).is_empty():
			top.append(asset)
	return top


func _matches_tool(asset: Dictionary, tool: String) -> bool:
	if tool == "__imports":
		return str(asset.get("origin", "")) == "human-import"
	if tool == "__unassigned":
		return str(asset.get("tool", "")).is_empty() and asset.get("origin") != "human-import"
	return tool.is_empty() or str(asset.get("tool", "")) == tool


func _tool_id() -> String:
	var selected := _tools.get_selected_items()
	return str(_tools.get_item_metadata(selected[0])) if not selected.is_empty() else ""


func _tool_name(tool: String) -> String:
	for entry: Dictionary in _data.get("tools", []):
		if str(entry["id"]) == tool:
			return str(entry["name"])
	return ""


func use_rail_navigation() -> void:
	_filter_section.hide()


func _display_name(path: String) -> String:
	return path.get_file().get_basename().replace("_", " ").replace("-", " ").capitalize()


## The asset's kind from the index, or from its extension for older indexes.
func _kind_of(asset: Dictionary) -> String:
	var kind := str(asset.get("kind", ""))
	if SECTIONS.has(kind):
		return kind
	var extension := str(asset.get("path", "")).get_extension().to_lower()
	if extension in ["blend", "glb", "gltf"]:
		return "model"
	if extension in ["tscn", "scn"]:
		return "scene"
	if extension in IMAGES or extension in ["kra", "ora"]:
		return "image"
	if extension in SOUNDS or extension in ["scd", "sc"]:
		return "sound"
	return "material" if extension == "ptex" else "other"


## "Model" for "model": the singular of the section title.
static func _kind_name(kind: String) -> String:
	return "File" if kind == "other" else str(SECTIONS[kind]).trim_suffix("s")


func _matches_search(asset: Dictionary, query: String) -> bool:
	if query.is_empty() or query in _display_name(str(asset["path"])).to_lower():
		return true
	for part: Variant in asset.get("parts", []):
		if query in _display_name(str(part)).to_lower():
			return true
	return false


func _filter(animate := false) -> void:
	_assets.clear()
	_rows.clear()
	var tool := _tool_id()
	creator_changed.emit(tool)
	var query := _search.text.strip_edges().to_lower()
	for asset: Dictionary in _top_level():
		if _matches_tool(asset, tool) and _matches_search(asset, query):
			_rows.append(asset)
	var order := SECTIONS.keys()
	_rows.sort_custom(
		func(left: Dictionary, right: Dictionary) -> bool:
			var a := order.find(_kind_of(left))
			var b := order.find(_kind_of(right))
			if a != b:
				return a < b
			var title := _display_name(str(left["path"]))
			return title.naturalnocasecmp_to(_display_name(str(right["path"]))) < 0
	)
	var label_counts: Dictionary = {}
	for asset: Dictionary in _rows:
		var label := _display_name(str(asset["path"]))
		label_counts[label] = int(label_counts.get(label, 0)) + 1
	var section := ""
	for asset: Dictionary in _rows:
		var kind := _kind_of(asset)
		if kind != section:
			section = kind
			_assets.add_section(SECTIONS[kind])
		var path := str(asset["path"])
		var label := _display_name(path)
		if int(label_counts[label]) > 1:
			label += " · " + path.get_extension().to_lower()
		var index := _assets.add_item(label, kind if kind != "other" else "")
		_assets.set_item_tooltip(index, path)
	if animate:
		_assets.stagger()
	var found := _rows.find_custom(_holds_selection)
	if found >= 0:
		_assets.select(found)
		_show_asset(_by_path.get(_selected_path, _rows[found]), false)
	else:
		_clear_selection()
	_update_list_state()


func _holds_selection(row: Dictionary) -> bool:
	return str(row["path"]) == _selected_path or (row.get("parts", []) as Array).has(_selected_path)


func _clear_selection() -> void:
	_clear_preview()
	_current = {}
	_header.hide()
	_parts.hide()
	_status.hide()
	if not _rows.is_empty():
		_preview.add_child(UI_COMPONENTS.label("Choose an asset to preview it.", "JoineryCaption"))


## Loading, error and empty states of the asset list.
func _update_list_state() -> void:
	var loading := _data.is_empty() and _load_error.is_empty()
	_loading.visible = loading
	_loading_indicator.running = loading
	_error_box.visible = not _load_error.is_empty()
	_error_label.text = "Couldn't load assets. " + _load_error
	var message := ""
	if not loading and _load_error.is_empty() and _rows.is_empty():
		var query := _search.text.strip_edges()
		var tool := _tool_id()
		if _top_level().is_empty():
			message = "No assets yet. Ask Journeyman to make one."
		elif not query.is_empty():
			message = "No assets match “%s”." % query
		elif tool == "__imports":
			message = "No imported assets."
		elif tool == "__unassigned":
			message = "Every asset has a recorded app."
		else:
			message = "No assets from %s yet. Ask Journeyman to make one." % _tool_name(tool)
	_empty.text = message
	_empty.visible = not message.is_empty()
	_assets.visible = not _rows.is_empty()


func select_resource(path: String) -> void:
	var selected := path.trim_prefix("res://").split("::")[0]
	# The native filesystem dock can repeat its selection while rescanning. An
	# already-rendered model keeps its camera; pending imports still retry below.
	if (
		selected == _selected_path
		and _current.get("path") == selected
		and _preview.get_child_count() > 0
		and _preview.get_child(0) is SubViewportContainer
	):
		return
	if selected != _selected_path:
		_preview_import_changed = false
	_selected_path = selected
	if _data.is_empty():
		return
	_tools.select(0)
	_filter()


func select_tool(tool: String) -> void:
	for index: int in _tools.item_count:
		if str(_tools.get_item_metadata(index)) == tool:
			_tools.select(index)
			_search.clear()
			_filter(true)
			if not _rows.is_empty() and _current.is_empty():
				_assets.select(0)
				_select(0)
			elif not _current.is_empty():
				asset_selected.emit(_current.duplicate(true))
			return


func _select(index: int, notify: bool = true) -> void:
	_show_asset(_rows[index], notify)


## Show [param asset] (a top-level asset or one of its parts) in the header and preview.
func _show_asset(asset: Dictionary, notify: bool) -> void:
	var path := str(asset["path"])
	if path != _selected_path:
		_preview_import_changed = false
	_current = asset
	_selected_path = path
	var owner := _owner_of(asset)
	_title.text = _display_name(str(owner["path"]))
	_title.tooltip_text = str(owner["path"])
	_caption.text = _describe(owner)
	_header.show()
	_status.hide()
	_show_parts(owner, path)
	_update_open()
	_show_preview("res://" + path, _preview_import_changed)
	_preview_import_changed = false
	_parts_panel.show_for(owner, _kind_of(owner) == "model")
	if notify:
		asset_selected.emit(_current.duplicate(true))


func _owner_of(asset: Dictionary) -> Dictionary:
	var owner := str(asset.get("part_of", ""))
	return _by_path.get(owner, asset) if not owner.is_empty() else asset


## "Model · made in Blender", "Image · imported".
func _describe(asset: Dictionary) -> String:
	var words: Array[String] = [_kind_name(_kind_of(asset))]
	var tool := _tool_name(str(asset.get("tool", "")))
	if str(asset.get("origin", "")) == "human-import":
		words.append("imported")
	elif not tool.is_empty():
		words.append("made in " + tool)
	return " · ".join(words)


## Parts: a segment per file (the source, then each derived part), so each can be previewed
## and opened on its own.
func _show_parts(owner: Dictionary, viewing: String) -> void:
	for child: Node in _parts.get_children():
		_parts.remove_child(child)
		child.queue_free()
	var parts: Array = owner.get("parts", [])
	_parts.visible = not parts.is_empty()
	if parts.is_empty():
		return
	var heading := UI_COMPONENTS.label("Parts", "JoineryCaption")
	heading.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_parts.add_child(heading)
	var owner_path := str(owner["path"])
	for item: Variant in [owner_path] + parts:
		var path := str(item)
		var segment := Button.new()
		segment.theme_type_variation = "JoinerySegment"
		segment.toggle_mode = true
		segment.focus_mode = Control.FOCUS_ALL
		segment.text = "Source" if path == owner_path else _part_name(owner_path, path)
		segment.tooltip_text = path
		segment.accessibility_name = segment.text
		segment.set_pressed_no_signal(path == viewing)
		segment.pressed.connect(_view_part.bind(path))
		UI_COMPONENTS.bind_height(segment, "sm")
		UI_MOTION.press_feedback(segment)
		_parts.add_child(segment)


func _part_name(owner: String, part: String) -> String:
	var base := owner.get_file().get_basename()
	var name := part.get_file().get_basename().trim_prefix(base).lstrip("_-. ")
	if name.is_empty():
		return _kind_name(_kind_of(_by_path.get(part, {"path": part})))
	return name.replace("_", " ").replace("-", " ").capitalize()


func _view_part(path: String) -> void:
	if _by_path.has(path) and path != _selected_path:
		_show_asset(_by_path[path], true)
	else:
		_show_parts(_owner_of(_current), _selected_path)


## The open action: "Open in Blender" (primary) plus an "Open with…" menu when more than one
## app can open the file. A missing app is explained under the title with a way to set it up.
func _update_open() -> void:
	_open_with.clear()
	var options: Array = _current.get("open_options", [])
	for index: int in options.size():
		var option: Dictionary = options[index]
		_open_with.add_item(str(option["name"]), index)
		_open_with.set_item_metadata(index, option)
		var available := bool(option["available"])
		_open_with.set_item_disabled(index, not available)
		_open_with.set_item_tooltip(
			index, str(option["path"]) if available else _unavailable_text(option)
		)
	_open_menu.visible = options.size() > 1
	_set_menu_label(false)
	_unavailable.hide()
	_open.visible = not options.is_empty()
	if options.is_empty():
		return
	var primary: Dictionary = options[0]
	var app := str(primary["name"])
	if not bool(primary["available"]):
		_open.hide()
		var missing := _source_missing(primary)
		var extension := str(_current.get("path", "")).get_extension().to_lower()
		var lines := _missing_text(app, extension, true)
		_unavailable_title.text = "The source file is missing" if missing else lines[0]
		_unavailable_label.text = "" if missing else lines[1]
		_unavailable_label.visible = not missing
		# Project apps can't bring back a missing source file.
		_setup_link.visible = not missing
		# The preview's empty state already says the app is missing: say it once.
		_unavailable.visible = _explained_app != app or _source_missing(primary)
		_set_menu_label(options.any(func(item: Dictionary) -> bool: return item["available"]))
		return
	_open.tooltip_text = str(primary["path"])
	if _opening == app:
		return
	_open.disabled = false
	var opened := _opened == app and Time.get_ticks_msec() < _opened_until
	_open.text = ("Opened in " if opened else "Open in ") + app


## The "Open with" menu is a chevron beside "Open in …", or, when the creating app is missing
## but another app can open the file, a labelled secondary button.
func _set_menu_label(labelled: bool) -> void:
	var text := "Open with…" if labelled else ""
	if _open_menu.text == text:
		return
	_open_menu.text = text
	_open_menu.theme_type_variation = (
		"JoinerySecondaryButton" if labelled else "JoineryIconButton"
	)


func _unavailable_text(option: Dictionary) -> String:
	if _source_missing(option):
		return "The source file is missing."
	return "Install %s on this computer to open it here." % option["name"]


static func _source_missing(option: Dictionary) -> bool:
	return str(option.get("reason", "")).begins_with("Source missing")


func _open_option(index: int) -> void:
	var options: Array = _current.get("open_options", [])
	if _current.is_empty() or index < 0 or index >= options.size():
		return
	var option: Dictionary = options[index]
	if not bool(option["available"]) or not _opening.is_empty():
		return
	_opening = str(option["name"])
	_status.hide()
	if bool(options[0]["available"]):
		_open.disabled = true
		_open.text = "Opening %s…" % _opening
	_open_indicator.running = true
	var arguments := ["open", str(_current["path"]), "--tool", str(option["tool"])]
	_start_job("open", PackedStringArray(arguments))


func _finish_open(result: Dictionary) -> void:
	var app := _opening
	_opening = ""
	_open_indicator.running = false
	if int(result["code"]) == 0:
		_opened = app
		_opened_until = Time.get_ticks_msec() + roundi(OPENED_SECONDS * 1000.0)
		_update_open()
		get_tree().create_timer(OPENED_SECONDS).timeout.connect(_end_opened.bind(app))
		invalidate()
		return
	_update_open()
	var reason := PARTS_SCRIPT.first_line(str(result.get("error", "")))
	if reason.is_empty():
		reason = PARTS_SCRIPT.first_line(str(result.get("output", "")))
	_status.text = "Couldn't open %s. %s" % [app, reason]
	_status.show()


func _end_opened(app: String) -> void:
	if _opened == app and Time.get_ticks_msec() >= _opened_until:
		_opened = ""
		if is_inside_tree():
			_update_open()


func _clear_preview() -> void:
	_pending_scene_preview = ""
	_audio.stop()
	_audio.stream_paused = false
	_audio.stream = null
	_audio_panel = null
	_audio.pitch_scale = 1.0
	_audio.volume_db = 0
	if not _explained_app.is_empty():
		# The header takes over saying the app is missing again.
		_explained_app = ""
		if not _current.is_empty():
			_update_open()
	for child: Node in _preview.get_children():
		_preview.remove_child(child)
		child.queue_free()


func _show_preview(path: String, reimported: bool = false) -> void:
	_clear_preview()
	var cache_mode := (
		ResourceLoader.CACHE_MODE_REPLACE if reimported else ResourceLoader.CACHE_MODE_REUSE
	)
	if not FileAccess.file_exists(path):
		_note("This file is missing.")
		return
	path = _preview_path(path)
	var extension := path.get_extension().to_lower()
	var image := PARTS_SCRIPT.load_image(path)
	if image != null:
		_show_image(image)
		return
	if extension in SOUNDS and ResourceLoader.exists(path):
		var stream := ResourceLoader.load(path, "", cache_mode) as AudioStream
		if stream != null:
			_show_audio(stream)
			return
	if extension in MODELS and ResourceLoader.exists(path):
		var scene := ResourceLoader.load(path, "", cache_mode) as PackedScene
		if scene != null:
			_show_scene(scene)
			return
	if extension in TEXTS and FileAccess.file_exists(path):
		_show_source(FileAccess.get_file_as_string(path))
		return
	if extension in ["glb", "gltf"] and not ResourceLoader.exists(path):
		_pending_scene_preview = path
		_note("Importing model…", true)
		return
	_show_no_preview(extension)


## The preview's empty state for a file it can't show: how to see it, or, when the app that
## opens it is missing, that the app isn't installed.
func _show_no_preview(extension: String) -> void:
	var options: Array = _current.get("open_options", [])
	if options.is_empty():
		_empty_state("No preview for .%s files" % extension, "")
		return
	var primary: Dictionary = options[0]
	var app := str(primary["name"])
	if bool(primary["available"]) or _source_missing(primary):
		_empty_state("No preview for .%s files" % extension, "Open it in %s to see it." % app)
		return
	# The app is missing: one block says so and offers the one next step, instead of telling
	# the user to open the file in an app they don't have.
	_explained_app = app
	_unavailable.hide()
	var lines := _missing_text(app, extension, false)
	_empty_state(lines[0], lines[1], _setup_button())


## A centred empty state in the preview: the asset kind's icon, a [param title], an optional
## [param line] saying what to do, and an optional [param action] button.
func _empty_state(title: String, line: String, action: Button = null) -> void:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	block.alignment = BoxContainer.ALIGNMENT_CENTER
	block.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(block)
	var icon := TextureRect.new()
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var kind := _kind_of(_current) if not _current.is_empty() else "other"
	UI_COMPONENTS.bind_icon(icon, kind if UI_THEME.ICONS.has(kind) else "warning", "texture")
	icon.theme_changed.connect(
		func() -> void: icon.self_modulate = UI_THEME.color(icon, "text_faint")
	)
	block.add_child(icon)
	var heading := UI_COMPONENTS.label(title, "JoineryBodyLarge")
	heading.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	block.add_child(heading)
	if not line.is_empty():
		var hint := UI_COMPONENTS.label(line, "JoineryCaption", true)
		hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		block.add_child(hint)
	if action != null:
		action.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		block.add_child(action)


func _note(text: String, busy := false) -> void:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	if busy:
		var indicator: INDICATOR_SCRIPT = INDICATOR_SCRIPT.new()
		indicator.running = true
		row.add_child(indicator)
	row.add_child(UI_COMPONENTS.label(text, "JoineryCaption", true))
	_preview.add_child(row)


func _preview_path(path: String) -> String:
	var extension := path.get_extension().to_lower()
	var native := extension in MODELS
	if native and ResourceLoader.exists(path):
		return path
	if not native and extension in IMAGES + SOUNDS + TEXTS + ["kra", "ora"]:
		return path
	for relative: String in _current.get("previews", []):
		var preview := "res://" + relative
		if FileAccess.file_exists(preview):
			return preview
	return path


func _show_image(image: Image) -> void:
	var view := TextureRect.new()
	view.texture = ImageTexture.create_from_image(image)
	view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	view.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(view)


func _show_source(text: String) -> void:
	var code := CodeEdit.new()
	code.editable = false
	code.text = text
	code.gutters_draw_line_numbers = false
	code.size_flags_vertical = Control.SIZE_EXPAND_FILL
	code.accessibility_name = "Source"
	_preview.add_child(code)


func _show_audio(stream: AudioStream) -> void:
	_audio_panel = AUDIO_SCRIPT.new()
	_audio_panel.player = _audio
	_audio_panel.set_stream(stream)
	_audio_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_preview.add_child(_audio_panel)


func _show_scene(scene: PackedScene) -> void:
	var viewer: MODEL_SCRIPT = MODEL_SCRIPT.new()
	_preview.add_child(viewer)
	viewer.show_scene(scene)
	_parts_panel.attach(viewer)


## Draw the eye to the selected asset: its row and its preview pulse once.
func flash_selected() -> void:
	for index: int in _assets.get_selected_items():
		UI_THEME.flash(_assets._rows[index])
	UI_THEME.flash(_preview)
