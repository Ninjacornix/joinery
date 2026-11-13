@tool
extends AcceptDialog
## Joinery updates in the editor (ADR 0083): Help ▸ Check for Joinery updates…, Project ▸
## Tools ▸ Joinery: Check for updates…, and a quiet check once a day when this Joinery is an
## installed release. A newer version opens this dialog with its release notes; nothing is
## downloaded until Download and install is pressed. `joinery update --install` checks the
## archive's SHA-256 and Sigstore signature before unpacking it beside the install, and the
## swap happens when Joinery exits. Restart now closes the editor the normal way (it asks
## about unsaved scenes) and Joinery opens this project again on the new version. Go back to
## <version> is the same swap with the version kept from the last update.
##
## The fork's Project Manager loads this same script from JOINERY_HOME for its "Check for
## updates" link (godot_patches), setting [member engine_home]; there is no project there.

signal label_changed(text: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const INDICATOR := preload("activity_indicator.gd")
const DOCUMENT := preload("art_review_dialog.gd")
const SELF_PATH := "res://addons/joinery/update_dialog.gd"
const MENU_ITEM := "Joinery: Check for updates…"
const HELP_ITEM := "Check for Joinery updates…"
const HELP_ID := 7083
## Size in ems of body text, as the other document dialogs.
const SIZE_EM := Vector2(34, 30)

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
## The Joinery folder, when the Project Manager opens this (no JOINERY_HOME guess needed).
var engine_home := ""
## The open project, reopened on the new version; empty in the Project Manager.
var project_path := ""
## checking, current, available, unsigned, offline, source, working, ready, failed.
var state := "checking"
var result: Dictionary = {}
var page: VBoxContainer
var title_label: Label
var caption: Label
var status_row: HBoxContainer
var status_label: Label
var notes: RichTextLabel
var notes_scroll: ScrollContainer
var prereleases: UI.SwitchRow
var back_button: Button
var later_button: Button
var _dots: Control
var _job: RefCounted
var _job_kind := ""
var _streamed := PackedStringArray()
var _quiet := false
var _help: PopupMenu


static func install(plugin: EditorPlugin) -> AcceptDialog:
	var dialog: AcceptDialog = load(SELF_PATH).new()
	dialog.set("project_path", ProjectSettings.globalize_path("res://").rstrip("/"))
	EditorInterface.get_base_control().add_child.call_deferred(dialog)
	# The Joinery menu where the plugin has one (it falls back to Project > Tools itself).
	var command := plugin.has_method("add_command")
	if command:
		plugin.call("add_command", "Check for Updates…", dialog.open)
	else:
		plugin.add_tool_menu_item(MENU_ITEM, dialog.open)
	dialog.call_deferred("_hook_help")
	dialog.call_deferred("check_daily")
	plugin.tree_exiting.connect(
		func() -> void:
			if not command:
				plugin.remove_tool_menu_item(MENU_ITEM)
			if is_instance_valid(dialog):
				dialog.call("_unhook_help")
				dialog.queue_free(),
		CONNECT_ONE_SHOT
	)
	return dialog


func _init() -> void:
	name = "JoineryUpdates"
	title = "Joinery updates"
	exclusive = false
	visible = false
	dialog_hide_on_ok = false
	# Sized in ems at popup (the notes scroll): a wrapping line measured before its first
	# layout would otherwise grow the window to a word per line.
	wrap_controls = false
	page = VBoxContainer.new()
	page.theme_type_variation = "JoineryStack"
	add_child(page)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	page.add_child(head)
	title_label = UI.label("Checking for updates", "JoineryTitle", true)
	head.add_child(title_label)
	caption = UI.label("", "JoineryCaption", true)
	head.add_child(caption)
	status_row = HBoxContainer.new()
	status_row.theme_type_variation = "JoineryRow"
	page.add_child(status_row)
	_dots = INDICATOR.new()
	status_row.add_child(_dots)
	status_label = UI.label("", "JoineryCaption", true)
	status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status_row.add_child(status_label)
	notes_scroll = ScrollContainer.new()
	notes_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	notes_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	UI_MOTION.rubber_band(notes_scroll)
	page.add_child(notes_scroll)
	notes = RichTextLabel.new()
	notes.name = "Notes"
	notes.bbcode_enabled = true
	notes.fit_content = true
	notes.scroll_active = false
	notes.selection_enabled = true
	notes.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	notes.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	notes_scroll.add_child(notes)
	prereleases = UI.switch_row(
		"Include pre-releases", "Alpha, beta and release candidate versions are offered too."
	)
	prereleases.toggled.connect(_on_prereleases)
	page.add_child(prereleases)
	back_button = UI.link("Go back")
	back_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back_button.pressed.connect(_on_back)
	page.add_child(back_button)
	later_button = add_cancel_button("Not now")
	later_button.theme_type_variation = "JoinerySecondaryButton"
	get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	confirmed.connect(_on_primary)
	canceled.connect(_on_later)
	set_process(false)


func _ready() -> void:
	theme = UI_THEME.build(get_parent() if get_parent() is Control else null)
	UI.dialog_footer(self)
	_paint()
	# A floor under the page's width: wrapping lines are never measured a word per line.
	page.custom_minimum_size.x = (SIZE_EM.x - 4) * UI_THEME.font_size(self, "body")
	page.minimum_size_changed.connect(_fit_page, CONNECT_DEFERRED)
	show_state("checking")


## Help ▸ Check for updates: always asks GitHub now.
func open() -> void:
	_quiet = false
	_popup()
	show_state("checking")
	_run("check", ["update", "--check", "--force", "--json"])


## Once a day at most (the CLI keeps the date): only an installed Joinery asks, and only a new,
## not dismissed, signed version opens the dialog.
func check_daily() -> void:
	_quiet = true
	_run("check", ["update", "--check", "--json"])


## Show a `joinery update --check --json` answer, or another command's, in the dialog.
func apply(data: Dictionary) -> void:
	result = data.duplicate(true)
	prereleases.set_on(bool(data.get("prereleases", false)))
	if data.has("error"):
		show_state("offline" if bool(data.get("offline", false)) else "failed")
	elif not str(data.get("pending", "")).is_empty():
		show_state("ready")
	elif bool(data.get("offline", false)):
		show_state("offline")
	elif bool(data.get("available", false)):
		var signed := bool((data.get("release", {}) as Dictionary).get("signed", false))
		if not bool(data.get("can_install", false)):
			show_state("source")
		else:
			show_state("available" if signed else "unsigned")
	else:
		show_state("current")


func show_state(which: String) -> void:
	state = which
	var release: Dictionary = result.get("release", {})
	var version := str(release.get("version", ""))
	var current := str(result.get("current", ""))
	var previous := str(result.get("previous", ""))
	var ok := get_ok_button()
	ok.disabled = false
	later_button.visible = true
	later_button.text = "Not now"
	_dots.set("running", which in ["checking", "working"])
	status_row.visible = which in ["checking", "working", "offline", "failed", "source", "unsigned"]
	notes_scroll.visible = which in ["available", "unsigned", "source"] and not version.is_empty()
	prereleases.visible = which in ["current", "available", "unsigned", "offline"]
	back_button.visible = not previous.is_empty() and which in ["current", "available", "offline"]
	back_button.text = "Go back to %s" % previous
	caption.text = "You have Joinery %s." % current if not current.is_empty() else ""
	match which:
		"checking":
			title_label.text = "Checking for updates"
			status_label.text = "Asking GitHub for the latest release…"
			ok.text = "Close"
			ok.disabled = true
			later_button.visible = false
		"current":
			title_label.text = "Joinery %s is up to date" % current
			caption.text = "Joinery checks once a day."
			ok.text = "Close"
			later_button.visible = false
		"available", "unsigned", "source":
			title_label.text = "Joinery %s is available" % version
			if bool(release.get("prerelease", false)):
				caption.text += " This is a pre-release."
			notes.text = DOCUMENT.to_bbcode(_without_heading(str(release.get("notes", ""))))
			ok.text = "Download and install"
			if which == "unsigned":
				status_label.text = "This release has no signature, so it can't be installed from here."
				ok.text = "Open the release page"
			elif which == "source":
				status_label.text = "This Joinery runs from a source checkout. Update it with git pull."
				ok.text = "Close"
				later_button.visible = false
		"offline":
			title_label.text = "Couldn't check for updates"
			status_label.text = str(
				result.get("message", "You're offline. Joinery will check again later.")
			)
			ok.text = "Try again"
			later_button.text = "Close"
		"working":
			title_label.text = "Getting ready"
			if not version.is_empty():
				title_label.text = "Installing Joinery %s" % version
			ok.text = "Download and install"
			ok.disabled = true
			later_button.text = "Cancel"
		"ready":
			var pending := str(result.get("pending", result.get("version", version)))
			title_label.text = "Joinery %s is ready" % pending
			caption.text = (
				"Restart Joinery to finish. This project opens again on the new version, and its"
				+ " Joinery addon is updated. %s stays available to go back to." % current
			)
			ok.text = "Restart now"
			later_button.text = "Later"
		"failed":
			title_label.text = "The update didn't install"
			status_label.text = str(
				result.get("error", result.get("message", "Something went wrong."))
			)
			ok.text = "Try again"
			later_button.text = "Close"
	if status_row.visible:
		UI_MOTION.fade_in(status_row)


## Release notes start "## Joinery 0.6.0", which the dialog's title already says.
static func _without_heading(markdown: String) -> String:
	var text := markdown.strip_edges()
	if text.begins_with("#") and text.get_slice("\n", 0).contains("Joinery"):
		text = text.substr(text.get_slice("\n", 0).length()).strip_edges()
	return text


func _on_primary() -> void:
	match state:
		"available":
			show_state("working")
			status_row.visible = true
			status_label.text = "Downloading…"
			_run("install", ["update", "--install", "--progress", "--json"] + _context())
		"unsigned":
			OS.shell_open(str((result.get("release", {}) as Dictionary).get("url", "")))
			hide()
		"ready":
			_run("restart", ["update", "--restart", "--json"])
		"offline", "failed":
			open()
		_:
			hide()


func _on_later() -> void:
	if state == "working" and _job != null:
		_job.call("cancel")
		_job = null
		_run("cancel", ["update", "--cancel", "--json"])
	elif state in ["available", "unsigned"]:
		var version := str((result.get("release", {}) as Dictionary).get("version", ""))
		_run("dismiss", ["update", "--dismiss", version, "--json"])


func _on_back() -> void:
	show_state("working")
	status_row.visible = true
	status_label.text = "Getting %s ready…" % str(result.get("previous", ""))
	_run("install", ["update", "--rollback", "--json"] + _context())


func _on_prereleases(on: bool) -> void:
	_run("prereleases", ["update", "--prereleases", "on" if on else "off", "--json"])


## This editor's process and project: the swap waits for one and reopens the other.
func _context() -> Array:
	var context := ["--after-exit", str(OS.get_process_id())]
	if not project_path.is_empty():
		context += ["--project", project_path]
	return context


func _run(kind: String, args: Array) -> void:
	if _job != null and kind != "prereleases" and kind != "dismiss" and kind != "cancel":
		_job.call("cancel")
	var prefix := cli_prefix
	if prefix.is_empty() and not engine_home.is_empty():
		# No trailing separator: on Windows Godot quotes "C:\Program Files\Joinery\" and the
		# last backslash would escape the closing quote.
		var home := engine_home.rstrip("/\\")
		prefix = PackedStringArray(["uv", "run", "--frozen", "--quiet", "--project", home])
		prefix.append("joinery")
	_streamed = PackedStringArray()
	_job = COMMAND.joinery(prefix, args)
	_job_kind = kind if _job != null else ""
	set_process(_job != null)
	if _job == null and not _quiet:
		apply({"error": "Couldn't start Joinery's command line (uv)."})


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	if _job_kind == "install":
		# Streamed lines leave the job's output: keep them, the answer is the last one.
		for line: String in _job.call("take_lines"):
			_progress(line)
			_streamed.append(line)
	var done: Dictionary = _job.call("poll")
	if done.is_empty():
		return
	_job = null
	set_process(false)
	var kind := _job_kind
	_job_kind = ""
	var data: Dictionary = {}
	var lines := _streamed + str(done.get("output", "")).split("\n", false)
	_streamed = PackedStringArray()
	for line: String in lines:
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary and not (parsed as Dictionary).has("progress"):
			data = parsed
	if data.is_empty() and int(done.get("code", 1)) != 0:
		data = {"error": "Joinery couldn't check for updates. Details are in Output."}
		print("[Joinery] update\n", done.get("output", ""), "\n", done.get("error", ""))
	_finished(kind, data)


func _progress(line: String) -> void:
	var parsed: Variant = JSON.parse_string(line)
	if not parsed is Dictionary or not (parsed as Dictionary).has("progress"):
		return
	var step := str(parsed.get("stage", ""))
	var total := int((result.get("release", {}) as Dictionary).get("size", 0))
	if step == "download" and total > 0:
		var done_mb := float(parsed["progress"]) * total / 1048576.0
		status_label.text = "Downloading %d of %d MB" % [roundi(done_mb), roundi(total / 1048576.0)]
	elif step == "verify":
		status_label.text = "Checking the SHA-256 and the signature…"
	elif step == "unpack":
		status_label.text = "Unpacking beside this version…"


func _finished(kind: String, data: Dictionary) -> void:
	match kind:
		"check":
			var release: Dictionary = data.get("release", {})
			var fresh := (
				bool(data.get("available", false))
				and bool(data.get("can_install", false))
				and str(data.get("dismissed", "")) != str(release.get("version", ""))
			)
			var waiting := not str(data.get("pending", "")).is_empty()
			var quiet := _quiet
			_quiet = false
			apply(data)
			_announce()
			# A quiet check opens the dialog only for a new version; a pending restart (the user
			# chose Later) and anything else just update the link's text.
			if not quiet or (fresh and not waiting):
				_popup()
		"install":
			if data.has("error"):
				result.merge(data, true)
				show_state("failed")
			elif str(data.get("state", "")) == "current":
				result["available"] = false
				show_state("current")
			else:
				result["pending"] = str(data.get("version", ""))
				show_state("ready")
				_announce()
				UI_MOTION.pulse(title_label)
		"restart":
			if data.has("error"):
				result.merge(data, true)
				show_state("failed")
				return
			hide()
			# The editor's own close asks about unsaved scenes; the swap waits for it.
			get_tree().root.propagate_notification(NOTIFICATION_WM_CLOSE_REQUEST)
		"prereleases":
			open()
		"cancel", "dismiss":
			pass


## Tell a "Check for updates" link what to say: the new version, or the restart it waits for.
func _announce() -> void:
	var pending := str(result.get("pending", ""))
	var version := str((result.get("release", {}) as Dictionary).get("version", ""))
	if not pending.is_empty():
		label_changed.emit("Restart to finish updating")
	elif state in ["available", "unsigned"] and not version.is_empty():
		label_changed.emit("Joinery %s is available" % version)
	else:
		label_changed.emit("Check for updates")


func _popup() -> void:
	if not is_inside_tree():
		return
	if not visible:
		var body := float(UI_THEME.font_size(self, "body"))
		popup_centered(Vector2i(roundi(SIZE_EM.x * body), roundi(SIZE_EM.y * body)))
	grab_focus()


## Give the page the dialog's height again once its lines are laid out. While hidden its
## containers don't sort, so wrapping lines first measure a word per line; the dialog sizes
## the page only on popup and resize, and would keep that height (and its rows off-screen).
func _fit_page() -> void:
	if not visible:
		return
	var footer := get_ok_button().get_parent() as Control
	var gap := float(get_theme_constant("buttons_separation", "AcceptDialog"))
	var height := footer.position.y - gap - page.position.y
	page.size = Vector2(page.size.x, maxf(height, page.get_combined_minimum_size().y))


func _paint() -> void:
	var box := StyleBoxFlat.new()
	box.bg_color = get_theme_color("surface", "Joinery")
	var pad := float(UI_THEME.space(get_ok_button(), 4))
	box.content_margin_left = pad
	box.content_margin_right = pad
	box.content_margin_top = pad
	box.content_margin_bottom = pad
	add_theme_stylebox_override("panel", box)


func _hook_help() -> void:
	for found: Node in EditorInterface.get_base_control().find_children(
		"*", "PopupMenu", true, false
	):
		var menu := found as PopupMenu
		if menu.name == "Help" or menu.title == "Help":
			_help = menu
			break
	if _help != null and _help.get_item_index(HELP_ID) < 0:
		_help.add_item(HELP_ITEM, HELP_ID)
		_help.id_pressed.connect(_on_help)


func _unhook_help() -> void:
	if _help != null and is_instance_valid(_help):
		var at := _help.get_item_index(HELP_ID)
		if at >= 0:
			_help.remove_item(at)
		if _help.id_pressed.is_connected(_on_help):
			_help.id_pressed.disconnect(_on_help)


func _on_help(id: int) -> void:
	if id == HELP_ID:
		open()
