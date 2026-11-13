@tool
extends VBoxContainer
## The Share tab in the editor's bottom panel: build the game for players, see the store media,
## read the page text, and publish to itch.io.
##
## Four groups, top to bottom, each glanceable on its own:
## - Builds: a checkbox per target and Build. While it runs, one line says what is happening;
##   after, one row per target with its size, whether it started, and the playtest's verdict.
## - Store media: the screenshots, the trailer (an Ogg preview Godot can play) and the cover,
##   with Remake (a new seeded run).
## - Steam art: every store capsule and library image, composed in Krita from the
##   screenshots, each with its size and its check (size, safe area, title readability).
## - Page: the drafted share/page.md, with Edit (opens it in the user's own editor).
## - Publish to itch.io…: share_publish.gd, which names the target and channels before pushing.
##
## Everything comes from `joinery share … --json` (command_job.gd); nothing polls. Downloads
## (export templates, butler) happen only behind a button that says what is downloaded.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const COMMAND := preload("command_job.gd")
const PUBLISH := preload("share_publish.gd")
const SELF_PATH := "res://addons/joinery/share_tab.gd"
const THUMB := Vector2(176, 99)
const COVER := Vector2(126, 100)
## Steam art thumbnails share one height; their width follows each image's shape.
const STEAM_HEIGHT := 96.0
const STEAM_WIDEST := 260.0
## Check status -> status icon.
const STEAM_ICONS := {"ok": "done", "warning": "warning", "failed": "failed"}

## Off in tests: nothing runs; commands that would run are appended to [member commands].
var cli_enabled := true
## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
## Arguments of each command started (or, with the CLI off, that would have started).
var commands: Array = []
var _status: Dictionary = {}
var _checks: Dictionary = {}
var _jobs: Dictionary = {}
var _content: VBoxContainer
var _loading: HBoxContainer
var _load_error: Label
var _build_button: Button
var _templates: PanelContainer
var _progress: HBoxContainer
var _progress_text: Label
var _results: VBoxContainer
var _changes: Label
var _media_button: Button
var _media_note: Label
var _gallery: HFlowContainer
var _trailer: VideoStreamPlayer
var _trailer_box: VBoxContainer
var _trailer_text: Label
var _cover: TextureRect
var _steam_note: Label
var _steam_grid: HFlowContainer
var _steam_problems: VBoxContainer
var _steam_button: Button
## Why the last Steam art run failed, until the next one starts.
var _steam_error := ""
var _page_text: Label
var _page_note: Label
var _page_button: Button
var _publish_button: Button
var _publish_note: Label
var _dialog: PUBLISH


## Add the Share tab after Timeline; it removes itself when the plugin leaves the tree.
static func install(plugin: EditorPlugin) -> Control:
	var tab: Control = load(SELF_PATH).new()
	# A rail page in Joinery's layout; a bottom panel tab in the classic one.
	var rail: bool = (
		plugin.has_method("add_rail_page")
		and plugin.call("add_rail_page", "share", "Share", tab, {"ui": "external"})
	)
	if not rail:
		plugin.add_control_to_bottom_panel(tab, "Share")
	plugin.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(tab):
				if rail:
					plugin.call("remove_rail_page", tab)
				else:
					plugin.remove_control_from_bottom_panel(tab)
				tab.queue_free(),
		CONNECT_ONE_SHOT
	)
	return tab


func _init() -> void:
	name = "Share"
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Share your game: builds, store media, page text and publishing"
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_MOTION.rubber_band(scroll)
	add_child(scroll)
	var gutter := MarginContainer.new()
	gutter.theme_type_variation = "JoineryGutter"
	gutter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(gutter)
	_content = VBoxContainer.new()
	_content.theme_type_variation = "JoineryStack"
	_content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gutter.add_child(_content)
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	_loading.add_child(dots)
	_loading.add_child(UI.label("Reading what's built…", "JoineryCaption"))
	_content.add_child(_loading)
	_load_error = UI.label("", "JoineryError", true)
	_load_error.hide()
	_content.add_child(_load_error)
	_build_builds()
	_build_media()
	_build_steam()
	_build_page()
	_build_publish()
	_dialog = PUBLISH.new()
	_dialog.publish_requested.connect(_publish)
	_dialog.dry_run_requested.connect(_dry_run)
	add_child(_dialog)
	_render()


func _ready() -> void:
	UI_THEME.watch(self)
	set_process(false)


func _exit_tree() -> void:
	for slot: String in _jobs:
		if _jobs[slot][0] != null:
			(_jobs[slot][0] as RefCounted).call("cancel")
	_jobs.clear()


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree() and _jobs.is_empty():
		refresh()


# --- Public ------------------------------------------------------------------------------------


## Read everything again (`joinery share status`).
func refresh() -> void:
	_start("status", ["share", "status"], apply_status)


## Show `joinery share status --json`: {name, host, targets, templates, build, media, page, itch}.
func apply_status(data: Dictionary) -> void:
	_loading.hide()
	if data.has("error") or not data.has("targets"):
		_load_error.text = _why(data, "Couldn't read the Share page.")
		_load_error.show()
		return
	_load_error.hide()
	_status = data
	_render()


## One progress line from a running command ("Exporting Web").
func apply_progress(text: String) -> void:
	_progress_text.text = text + "…"
	if not _progress.visible:
		_progress.show()
		UI_MOTION.fade_in(_progress)


## The publish dialog, for tests and the visual suite.
func publish_dialog() -> PUBLISH:
	return _dialog


## Human size: "10.3 MB".
static func size_text(bytes: float) -> String:
	if bytes >= 1024.0 * 1024.0 * 1024.0:
		return "%.1f GB" % (bytes / 1024.0 / 1024.0 / 1024.0)
	if bytes >= 1024.0 * 1024.0:
		return "%.1f MB" % (bytes / 1024.0 / 1024.0)
	return "%d KB" % maxi(1, roundi(bytes / 1024.0))


## The result row's one line: "10.3 MB · started · played 0:30, nothing to report".
static func result_line(result: Dictionary) -> String:
	if not bool(result.get("ok", false)):
		return str(result.get("error", "The build failed."))
	var parts: Array[String] = [size_text(float(result.get("size", 0)))]
	var boot: Dictionary = result.get("boot", {})
	if bool(boot.get("ran", false)):
		parts.append("starts" if bool(boot.get("ok", false)) else "didn't start")
	var played: Dictionary = result.get("playtest", {})
	if not played.is_empty():
		var summary := str(played.get("summary", ""))
		var at := summary.rfind(": ")
		parts.append(summary.substr(at + 2) if at >= 0 else summary)
	return " · ".join(parts)


# --- Building ----------------------------------------------------------------------------------


func _group(title: String) -> VBoxContainer:
	var group := VBoxContainer.new()
	group.theme_type_variation = "JoineryTight"
	group.add_child(UI.label(title, "JoinerySection"))
	_content.add_child(group)
	return group


func _build_builds() -> void:
	var group := _group("Builds")
	group.name = "Builds"
	var row := HFlowContainer.new()
	row.name = "Targets"
	row.theme_type_variation = "JoineryRow"
	group.add_child(row)
	for target: Array in [
		["web", "Web"], ["windows", "Windows"], ["macos", "macOS"], ["linux", "Linux"]
	]:
		var check := CheckBox.new()
		check.text = str(target[1])
		check.button_pressed = true
		check.name = str(target[0])
		check.toggled.connect(func(_on: bool) -> void: _render())
		_checks[str(target[0])] = check
		row.add_child(check)
	_build_button = UI.primary("Build")
	_build_button.name = "Build"
	_build_button.pressed.connect(_build.bind(false))
	UI_MOTION.press_feedback(_build_button)
	row.add_child(_build_button)
	_templates = UI.notice(
		"Godot's export templates aren't on this computer.", "Download", "JoineryCard"
	)
	_templates.name = "Templates"
	(_templates.find_child("Action", true, false) as Button).pressed.connect(_build.bind(true))
	_templates.hide()
	group.add_child(_templates)
	_progress = HBoxContainer.new()
	_progress.name = "Progress"
	_progress.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.running = true
	_progress.add_child(dots)
	_progress_text = UI.label("", "JoineryCaption")
	_progress.add_child(_progress_text)
	_progress.hide()
	group.add_child(_progress)
	_results = VBoxContainer.new()
	_results.name = "Results"
	_results.theme_type_variation = "JoineryFlush"
	group.add_child(_results)
	_changes = UI.label("", "JoineryCaption", true)
	_changes.name = "Changes"
	_changes.hide()
	group.add_child(_changes)


func _build_media() -> void:
	var group := _group("Store media")
	group.name = "Media"
	_media_note = UI.label("", "JoineryCaption", true)
	group.add_child(_media_note)
	var shelf := HFlowContainer.new()
	shelf.theme_type_variation = "JoineryRow"
	group.add_child(shelf)
	_trailer_box = VBoxContainer.new()
	_trailer_box.theme_type_variation = "JoineryFlush"
	_trailer_box.name = "Trailer"
	shelf.add_child(_trailer_box)
	_trailer = VideoStreamPlayer.new()
	_trailer.custom_minimum_size = THUMB * 1.5
	_trailer.expand = true
	_trailer.loop = true
	_trailer.volume_db = -80.0
	_trailer.accessibility_name = "Trailer preview"
	_trailer_box.add_child(_trailer)
	_trailer_text = UI.label("", "JoineryNumeric")
	_trailer_box.add_child(_trailer_text)
	_cover = TextureRect.new()
	_cover.name = "Cover"
	_cover.custom_minimum_size = COVER * 1.5
	_cover.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_cover.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_cover.tooltip_text = "Cover, 630 × 500"
	shelf.add_child(_cover)
	_gallery = HFlowContainer.new()
	_gallery.name = "Screenshots"
	_gallery.theme_type_variation = "JoineryRowTight"
	group.add_child(_gallery)
	_media_button = UI.secondary("Make media")
	_media_button.name = "Remake"
	_media_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_media_button.tooltip_text = "Plays a new seeded run for screenshots, a trailer and a cover"
	_media_button.pressed.connect(_make_media)
	UI_MOTION.press_feedback(_media_button)
	group.add_child(_media_button)


func _build_steam() -> void:
	var group := _group("Steam art")
	group.name = "Steam"
	_steam_note = UI.label("", "JoineryCaption", true)
	group.add_child(_steam_note)
	_steam_grid = HFlowContainer.new()
	_steam_grid.name = "SteamImages"
	_steam_grid.theme_type_variation = "JoineryRow"
	group.add_child(_steam_grid)
	_steam_problems = VBoxContainer.new()
	_steam_problems.name = "SteamProblems"
	_steam_problems.theme_type_variation = "JoineryFlush"
	group.add_child(_steam_problems)
	_steam_button = UI.secondary("Make Steam art")
	_steam_button.name = "SteamMake"
	_steam_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_steam_button.tooltip_text = "Composes every capsule and library image in Krita"
	_steam_button.pressed.connect(_make_steam)
	UI_MOTION.press_feedback(_steam_button)
	group.add_child(_steam_button)


func _build_page() -> void:
	var group := _group("Page text")
	group.name = "Page"
	var card := UI.card()
	group.add_child(card)
	_page_text = UI.label("", "", true)
	_page_text.name = "Text"
	_page_text.max_lines_visible = 14
	_page_text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	card.add_child(_page_text)
	_page_note = UI.label("", "JoineryCaption", true)
	_page_note.hide()
	group.add_child(_page_note)
	_page_button = UI.secondary("Edit")
	_page_button.name = "Edit"
	_page_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_page_button.pressed.connect(_edit_page)
	UI_MOTION.press_feedback(_page_button)
	group.add_child(_page_button)


func _build_publish() -> void:
	var group := _group("Publish")
	group.name = "Publish"
	_publish_note = UI.label("", "JoineryCaption", true)
	group.add_child(_publish_note)
	_publish_button = UI.primary("Publish to itch.io…")
	_publish_button.name = "PublishButton"
	_publish_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_publish_button.pressed.connect(_open_publish)
	UI_MOTION.press_feedback(_publish_button)
	group.add_child(_publish_button)


# --- Rendering ---------------------------------------------------------------------------------


func _render() -> void:
	if _results == null:
		return
	_render_builds()
	_render_media()
	_render_steam()
	_render_page()
	var built := _built()
	_publish_button.disabled = built.is_empty() or _jobs.has("itch")
	_publish_note.text = (
		"Build the game first; then publish the builds to your itch.io page."
		if built.is_empty()
		else "Pushes %s with butler, using your own itch.io login." % _names(built)
	)


func _render_builds() -> void:
	var busy := _jobs.has("build")
	var templates: Dictionary = _status.get("templates", {})
	_templates.visible = (
		not _status.is_empty() and not bool(templates.get("ready", true)) and not busy
	)
	var notice := _templates.find_child("Text", true, false) as Label
	notice.text = (
		"Godot's export templates aren't on this computer. Download them (%s GB) to build."
		% str(templates.get("download_gb", "1.3"))
	)
	(_templates.find_child("Action", true, false) as Button).text = "Download and build"
	_build_button.disabled = busy or _chosen().is_empty() or _templates.visible
	_build_button.text = "Building…" if busy else "Build"
	if not busy:
		_progress.hide()
	for child: Node in _results.get_children():
		_results.remove_child(child)
		child.queue_free()
	var report: Dictionary = _status.get("build", {}) if _status.get("build") is Dictionary else {}
	var targets: Dictionary = report.get("targets", {})
	for id: String in targets:
		_results.add_child(_result_row(id, targets[id]))
	var changes: Array = report.get("changes", [])
	_changes.text = "Joinery set up: " + "; ".join(changes) + "." if not changes.is_empty() else ""
	_changes.visible = not changes.is_empty()


func _result_row(id: String, result: Dictionary) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = id
	row.theme_type_variation = "JoineryRow"
	UI.bind_height(row, "md", false)
	var ok := bool(result.get("ok", false))
	var warnings: Array = result.get("warnings", [])
	var played: Dictionary = result.get("playtest", {})
	var kind := "done"
	if not ok or bool(result.get("boot", {}).get("ok", true)) == false:
		kind = "failed"
	elif not warnings.is_empty() or str(played.get("status", "played")) != "played":
		kind = "warning"
	row.add_child(UI.status_icon(kind))
	var title := UI.label(str(result.get("label", id)))
	title.custom_minimum_size.x = UI_THEME.space(self, 6) * 2
	row.add_child(title)
	var line := UI.label(result_line(result), "JoineryNumeric" if ok else "JoineryError")
	line.name = "Line"
	line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	var why: Array[String] = []
	why.append(str(result.get("boot", {}).get("detail", "")))
	for warning: Variant in warnings:
		why.append(str(warning))
	line.tooltip_text = "\n".join(why.filter(func(t: String) -> bool: return not t.is_empty()))
	row.add_child(line)
	return row


func _render_media() -> void:
	var media: Dictionary = _status.get("media", {})
	var shots: Array = media.get("screenshots", [])
	var trailer: Dictionary = media.get("trailer", {})
	var cover := str(media.get("cover", "")) if media.get("cover") != null else ""
	var busy := _jobs.has("media")
	var has_any := not shots.is_empty() or not cover.is_empty() or trailer.get("mp4") != null
	_media_button.text = "Making…" if busy else ("Remake" if has_any else "Make media")
	_media_button.disabled = busy or _status.is_empty()
	_media_note.text = (
		"Screenshots, a trailer and a cover, all from a seeded run of the game."
		if not has_any
		else "Seed %s. Files are in share/media." % str(media.get("seed", ""))
	)
	for child: Node in _gallery.get_children():
		_gallery.remove_child(child)
		child.queue_free()
	for path: Variant in shots:
		var picture := _picture(_absolute(str(path)), THUMB)
		if picture != null:
			_gallery.add_child(picture)
	_cover.texture = _texture(_absolute(cover)) if not cover.is_empty() else null
	_cover.visible = _cover.texture != null
	var preview := str(trailer.get("preview", "")) if trailer.get("preview") != null else ""
	_trailer_box.visible = not preview.is_empty()
	if not preview.is_empty() and FileAccess.file_exists(_absolute(preview)):
		var stream := VideoStreamTheora.new()
		stream.file = _absolute(preview)
		if _trailer.stream == null or (_trailer.stream as VideoStream).file != stream.file:
			_trailer.stream = stream
			if is_visible_in_tree() and not DisplayServer.get_name() == "headless":
				_trailer.play()
	var seconds := float(trailer.get("seconds", 0.0)) if trailer.get("seconds") != null else 0.0
	var sound := "with sound" if str(trailer.get("sound", "")).begins_with("With") else "silent"
	_trailer_text.text = "Trailer · %d s · %s" % [roundi(seconds), sound]


func _render_steam() -> void:
	var steam: Dictionary = _status.get("steam", {}) if _status.get("steam") is Dictionary else {}
	var made: Array = []
	for item: Variant in steam.get("images", []):
		if (item as Dictionary).get("path") != null:
			made.append(item)
	var busy := _jobs.has("steam")
	var ready := bool(steam.get("ready", false))
	_steam_button.text = (
		"Making…" if busy else ("Remake Steam art" if not made.is_empty() else "Make Steam art")
	)
	_steam_button.disabled = busy or not ready
	if not _steam_error.is_empty():
		_steam_note.text = _steam_error
	elif not ready:
		_steam_note.text = "Make the screenshots first: every Steam image is composed from them."
	else:
		_steam_note.text = (
			"Every capsule and library image, composed in Krita from the screenshots. Sizes as "
			+ (
				"Steamworks documented them on %s; verify against Steamworks before uploading."
				% str(steam.get("checked", ""))
			)
		)
	for child: Node in _steam_grid.get_children() + _steam_problems.get_children():
		child.get_parent().remove_child(child)
		child.queue_free()
	_steam_grid.visible = not made.is_empty()
	for item: Variant in made:
		var entry := item as Dictionary
		_steam_grid.add_child(_steam_tile(entry))
		var status := str(entry.get("status", ""))
		for problem: Variant in entry.get("problems", []):
			var row := HBoxContainer.new()
			row.theme_type_variation = "JoineryRowTight"
			row.add_child(UI.status_icon(str(STEAM_ICONS.get(status, "warning"))))
			row.add_child(
				UI.label("%s: %s" % [entry.get("label", ""), problem], "JoineryCaption", true)
			)
			_steam_problems.add_child(row)
	# Steam asks for store screenshots of at least 1920 × 1080.
	var small_shots := str(steam.get("screenshots_warning", ""))
	if ready and not small_shots.is_empty():
		var shots_row := HBoxContainer.new()
		shots_row.theme_type_variation = "JoineryRowTight"
		shots_row.add_child(UI.status_icon("warning"))
		shots_row.add_child(UI.label("Screenshots: " + small_shots, "JoineryCaption", true))
		_steam_problems.add_child(shots_row)
	_steam_problems.visible = _steam_problems.get_child_count() > 0


## One Steam image: the picture at a shared height on the sunken media ground (the logo is
## transparent), then its check and name, then its size.
func _steam_tile(entry: Dictionary) -> VBoxContainer:
	var tile := VBoxContainer.new()
	tile.name = str(entry.get("id", "image"))
	tile.theme_type_variation = "JoineryTight"
	var size: Array = entry.get("size", [1, 1])
	var aspect := float(size[0]) / maxf(1.0, float(size[1]))
	var frame := PanelContainer.new()
	frame.theme_type_variation = "JoineryMediaFrame"
	frame.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	tile.add_child(frame)
	var picture := TextureRect.new()
	picture.texture = _texture(_absolute(str(entry.get("path", ""))))
	picture.custom_minimum_size = Vector2(minf(STEAM_WIDEST, STEAM_HEIGHT * aspect), STEAM_HEIGHT)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.tooltip_text = "%s, %d × %d" % [entry.get("label", ""), int(size[0]), int(size[1])]
	frame.add_child(picture)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	tile.add_child(line)
	var status := str(entry.get("status", ""))
	if STEAM_ICONS.has(status):
		var icon := UI.status_icon(str(STEAM_ICONS[status]))
		var problems: Array = entry.get("problems", [])
		icon.tooltip_text = "Passes its checks" if problems.is_empty() else "\n".join(problems)
		line.add_child(icon)
	line.add_child(UI.label(str(entry.get("label", "")), "JoineryCaption"))
	# The size on its own line keeps each tile about as wide as its picture.
	tile.add_child(UI.label("%d × %d" % [int(size[0]), int(size[1])], "JoineryNumeric"))
	return tile


func _render_page() -> void:
	var page := str(_status.get("page", "")) if _status.get("page") != null else ""
	_page_text.text = _plain(page) if not page.is_empty() else "No page text yet."
	_page_button.text = "Edit" if not page.is_empty() else "Draft page text"
	_page_button.disabled = _jobs.has("page") or _status.is_empty()
	var gaps := page.count("_Write this part._")
	_page_note.text = (
		"%d part%s still to write." % [gaps, "" if gaps == 1 else "s"] if gaps > 0 else ""
	)
	_page_note.visible = gaps > 0


## Markdown without its markup, for a glance: no comments, # marks or table rules.
static func _plain(markdown: String) -> String:
	var lines: Array[String] = []
	var comment := false
	for raw: String in markdown.split("\n"):
		var line := raw.strip_edges()
		if line.begins_with("<!--"):
			comment = not line.ends_with("-->")
			continue
		if comment:
			comment = not line.ends_with("-->")
			continue
		if line.begins_with("|---"):
			continue
		line = line.trim_prefix("# ").trim_prefix("## ")
		if line.begins_with("|"):
			line = " · ".join(
				Array(line.trim_prefix("|").trim_suffix("|").split("|")).map(
					func(cell: String) -> String: return cell.strip_edges()
				)
			)
		lines.append(line)
	return "\n".join(lines).strip_edges()


func _picture(path: String, box: Vector2) -> TextureRect:
	var texture := _texture(path)
	if texture == null:
		return null
	var picture := TextureRect.new()
	picture.texture = texture
	picture.custom_minimum_size = box
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.tooltip_text = path.get_file()
	return picture


static func _texture(path: String) -> Texture2D:
	if path.is_empty() or not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	return ImageTexture.create_from_image(image) if image != null else null


# --- Actions -----------------------------------------------------------------------------------


func _chosen() -> Array[String]:
	var chosen: Array[String] = []
	for id: String in _checks:
		if (_checks[id] as CheckBox).button_pressed:
			chosen.append(id)
	return chosen


func _built() -> Array[String]:
	var built: Array[String] = []
	var report: Dictionary = _status.get("build", {}) if _status.get("build") is Dictionary else {}
	var targets: Dictionary = report.get("targets", {})
	for id: String in targets:
		if bool((targets[id] as Dictionary).get("ok", false)):
			built.append(id)
	return built


func _names(ids: Array[String]) -> String:
	var labels: Array[String] = []
	for id: String in ids:
		labels.append((_checks[id] as CheckBox).text if _checks.has(id) else id)
	if labels.size() <= 1:
		return "".join(labels)
	return ", ".join(labels.slice(0, -1)) + " and " + labels[-1]


func _build(download: bool) -> void:
	var args := ["share", "build", "--targets", ",".join(_chosen())]
	if download:
		args.append("--download-templates")
	if _start("build", args, _on_built):
		apply_progress("Starting")
	_render()


func _on_built(data: Dictionary) -> void:
	if data.has("needs"):
		_status["templates"] = {"ready": false}
	if data.has("error"):
		_load_error.text = _why(data, "The build failed.")
		_load_error.show()
	refresh()


func _make_media() -> void:
	var args := ["share", "media"]
	if not (_status.get("media", {}) as Dictionary).get("screenshots", []).is_empty():
		args.append("--new-seed")
	if _start("media", args, _after_media):
		apply_progress("Playing a seeded run")
	_render()


func _after_media(data: Dictionary) -> void:
	if data.has("error"):
		_media_note.text = _why(data, "Couldn't make the media.")
		return
	refresh()
	UI_MOTION.pulse(_gallery)


func _make_steam() -> void:
	_steam_error = ""
	if _start("steam", ["share", "steam-art"], _after_steam):
		apply_progress("Composing the Steam art in Krita")
	_render()


func _after_steam(data: Dictionary) -> void:
	if data.has("error"):
		_steam_error = _why(data, "Couldn't make the Steam art.")
		_render_steam()
		return
	refresh()
	UI_MOTION.pulse(_steam_grid)


func _edit_page() -> void:
	if _status.get("page") == null:
		_start("page", ["share", "page"], func(_data: Dictionary) -> void: refresh())
		_render()
		return
	if cli_enabled:
		OS.shell_open(_absolute("share/page.md"))


func _open_publish() -> void:
	var itch: Dictionary = _status.get("itch", {})
	_dialog.open(self, _built(), itch)


func _dry_run(target: String, channels: Array[String]) -> void:
	var args := ["share", "itch", "--target", target, "--channel", ",".join(channels), "--dry-run"]
	_start("dry-run", args, _dialog.apply_dry_run)


func _publish(target: String, channels: Array[String], install: bool) -> void:
	var args := ["share", "itch", "--target", target, "--channel", ",".join(channels), "--yes"]
	if install:
		args.append("--install-butler")
	if _start("itch", args, _on_published):
		apply_progress("Publishing to itch.io")
	_render()


func _on_published(data: Dictionary) -> void:
	_dialog.apply_result(data)
	refresh()


# --- CLI ---------------------------------------------------------------------------------------


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


func _absolute(path: String) -> String:
	return path if path.is_absolute_path() else _project().path_join(path)


## The readable reason in a failed result, or [param fallback] pointing at Output.
static func _why(data: Dictionary, fallback: String) -> String:
	var text := str(data.get("error", ""))
	return text if not text.is_empty() else fallback + " Details are in Output."


func _start(slot: String, args: Array, callback: Callable) -> bool:
	var full := args.duplicate()
	full.append_array(["--project", _project(), "--json"])
	commands.append(full)
	if not cli_enabled:
		_jobs[slot] = [null, callback]
		return true
	_cancel(slot)
	var job: RefCounted = COMMAND.joinery(cli_prefix, full)
	if job == null:
		return false
	_jobs[slot] = [job, callback]
	set_process(true)
	return true


## Finish a job started with the CLI off (tests): its callback gets [param data].
func finish_job(slot: String, data: Dictionary) -> void:
	if not _jobs.has(slot):
		return
	var callback: Callable = _jobs[slot][1]
	_jobs.erase(slot)
	callback.call(data)
	_render()


func _cancel(slot: String) -> void:
	if _jobs.has(slot) and _jobs[slot][0] != null:
		(_jobs[slot][0] as RefCounted).call("cancel")
	_jobs.erase(slot)


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		if not _jobs.has(slot) or _jobs[slot][0] == null:
			continue
		var job: RefCounted = _jobs[slot][0]
		for line: String in job.call("take_lines"):
			_line(slot, line)
		var result: Dictionary = job.call("poll")
		if result.is_empty():
			continue
		for line: String in str(result.get("output", "")).split("\n", false):
			_line(slot, line)
		var data: Dictionary = _jobs[slot][2] if _jobs[slot].size() > 2 else {}
		if int(result.get("code", 1)) != 0 and data.is_empty():
			print("[Joinery] share\n", result.get("error", ""))
			data = {"error": ""}
		finish_job(slot, data)
	if _jobs.values().all(func(job: Array) -> bool: return job[0] == null):
		set_process(false)


func _line(slot: String, line: String) -> void:
	var parsed: Variant = JSON.parse_string(line)
	if not parsed is Dictionary:
		return
	var data: Dictionary = parsed
	if data.has("progress"):
		apply_progress(str(data["progress"]))
		return
	if _jobs.has(slot):
		var entry: Array = _jobs[slot]
		if entry.size() > 2:
			entry[2] = data
		else:
			entry.append(data)
