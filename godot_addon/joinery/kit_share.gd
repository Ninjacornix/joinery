@tool
extends VBoxContainer
## Shared kits, at the top of the Kits section (kits_panel.gd): Import kit… and Export kit…,
## and the kits imported into this project, each with its version, author and licence, and
## Update… and Remove. A kit is a .joinerykit file the user moves between projects; only the
## user imports, exports or removes one (the shared hook refuses those commands to agents).
##
## Export kit… opens a dialog: name, version, licence (the game's by default), a description
## and what goes in (prefab scenes, kit generators, assets). `joinery kit export --plan` says
## what that brings along and how big it is, or why something can't go; Save kit… writes the
## file. Import kit… picks a file and shows its manifest (`joinery kit import --plan`): who
## made it, its licence, what it holds and, for a kit already here, what the update changes.
## Add to project, Update and Remove each leave one change waiting in the Review tab.

## An import, update or removal is waiting for review; [param result] is the CLI's JSON.
signal changed(result: Dictionary)

const COMMAND_SCRIPT := preload("res://addons/joinery/command_job.gd")
const INDICATOR_SCRIPT := preload("res://addons/joinery/activity_indicator.gd")
const PARTS_SCRIPT := preload("res://addons/joinery/parts_panel.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_COMPONENTS := preload("res://addons/joinery/ui_components.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
## Seconds after the last change in the export dialog before its contents are worked out.
const PLAN_DELAY := 0.35
const FILTER := "*.joinerykit"
## Height of the export dialog's "What goes in" list, in multiples of the font size.
const CHOICES_EM := 13.0

## The command that runs Joinery's CLI (tests use a Python interpreter); empty runs uv.
var cli_prefix := PackedStringArray()
## The last `joinery kit list` payload: installed kits and what could go in a kit.
var listing: Dictionary = {}
var import_button: Button
var export_button: Button
var picker: FileDialog
var export_dialog: ConfirmationDialog
var import_dialog: ConfirmationDialog
var export_summary: Label
var export_problem: Label
var import_body: VBoxContainer
var rows: VBoxContainer
var status: Label
var _working: HBoxContainer
var _working_label: Label
var _indicator: INDICATOR_SCRIPT
var _name: LineEdit
var _version: LineEdit
var _licence: OptionButton
var _description: LineEdit
var _custom: CheckButton
## "include:<path>" or "generator:<id>" -> its CheckBox in the export dialog.
var _checks: Dictionary = {}
var _picking := ""
## Update…: the kit the picked file should be.
var _expected := ""
var _import_file := ""
var _plan_due := -1.0
## Which dialog the running plan is for: "export" or "import".
var _plan_for := ""
var _list_job: RefCounted
var _plan_job: RefCounted
var _run_job: RefCounted


func _init() -> void:
	name = "SharedKits"
	theme_type_variation = "JoineryTight"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRow"
	add_child(head)
	var title := UI_COMPONENTS.label("Shared kits", "JoinerySection")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title)
	import_button = UI_COMPONENTS.secondary("Import kit…")
	import_button.tooltip_text = "Add a .joinerykit from another project"
	import_button.pressed.connect(pick.bind("import"))
	head.add_child(import_button)
	export_button = UI_COMPONENTS.secondary("Export kit…")
	export_button.tooltip_text = "Put scenes, generators and assets in a .joinerykit"
	export_button.pressed.connect(open_export)
	head.add_child(export_button)
	rows = VBoxContainer.new()
	rows.theme_type_variation = "JoineryTight"
	add_child(rows)
	_working = HBoxContainer.new()
	_working.theme_type_variation = "JoineryRow"
	_indicator = INDICATOR_SCRIPT.new()
	_working.add_child(_indicator)
	_working_label = UI_COMPONENTS.label("", "JoineryCaption", true)
	_working.add_child(_working_label)
	_working.hide()
	add_child(_working)
	status = UI_COMPONENTS.label("", "JoineryCaption", true)
	status.hide()
	add_child(status)
	picker = FileDialog.new()
	picker.access = FileDialog.ACCESS_FILESYSTEM
	picker.file_selected.connect(_picked)
	add_child(picker)


## Ask the CLI for the imported kits and what this project could share.
func refresh() -> void:
	if _list_job == null:
		_list_job = _start(PackedStringArray(["kit", "list"]))


## Show [param data] (the `joinery kit list` payload): one row per imported kit.
func set_listing(data: Dictionary) -> void:
	listing = data
	for child: Node in rows.get_children():
		rows.remove_child(child)
		child.queue_free()
	for kit: Dictionary in data.get("installed", []):
		rows.add_child(_kit_row(kit))
	rows.visible = rows.get_child_count() > 0
	UI_MOTION.stagger(rows.get_children())


## An imported kit: its name and version, then who made it, its licence and what it holds.
func _kit_row(kit: Dictionary) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.name = "Kit"
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryFlush"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(words)
	var title := "%s %s" % [kit.get("name", ""), kit.get("version", "")]
	words.add_child(UI_COMPONENTS.label(title))
	var facts: Array[String] = [str(kit.get("author", "")), str(kit.get("licence", ""))]
	facts.append(str(kit.get("holds", "")))
	var caption := UI_COMPONENTS.label(" · ".join(facts), "JoineryCaption", true)
	caption.name = "Facts"
	words.add_child(caption)
	var used: Array = kit.get("used_by", [])
	if not used.is_empty():
		var where := UI_COMPONENTS.label(
			"Used by " + ", ".join(used.map(func(p: Variant) -> String: return str(p).get_file())),
			"JoineryCaption",
			true
		)
		where.name = "UsedBy"
		words.add_child(where)
	var update := UI_COMPONENTS.quiet("Update…")
	update.name = "Update"
	update.tooltip_text = "Pick a newer .joinerykit of this kit"
	update.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	update.pressed.connect(pick.bind("update", str(kit.get("name", ""))))
	row.add_child(update)
	var remove := UI_COMPONENTS.quiet("Remove")
	remove.name = "Remove"
	remove.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	remove.disabled = not used.is_empty()
	remove.tooltip_text = (
		"Take it out of the scenes that use it first" if remove.disabled else "Remove this kit"
	)
	remove.pressed.connect(ask_remove.bind(kit))
	row.add_child(remove)
	return row


# --- Export ----------------------------------------------------------------------------------


## Open the export dialog: name, version, licence, description and what goes in.
func open_export() -> void:
	if export_dialog != null and is_instance_valid(export_dialog):
		export_dialog.queue_free()
	_checks = {}
	export_dialog = _dialog("Export kit", "Save kit…")
	var page: VBoxContainer = export_dialog.get_child(0)
	page.add_child(
		UI_COMPONENTS.label(
			(
				"Recipes, inputs and the files your choices use come along, so the kit rebuilds "
				+ "in any project."
			),
			"JoineryCaption",
			true
		)
	)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	page.add_child(line)
	_name = _field(line, "Name", "Harbour props", 3.0)
	_version = _field(line, "Version", "1.0.0", 1.0)
	_version.text = "1.0.0"
	var licences: Array = listing.get("licences", [])
	var own := str(listing.get("licence", ""))
	if not own.is_empty() and own not in licences:
		licences = [own] + licences
	_licence = UI_COMPONENTS.picker(licences)
	_licence.accessibility_name = "Licence"
	_licence.select(maxi(0, licences.find(own)))
	_licence.item_selected.connect(func(_i: int) -> void: _schedule_plan())
	page.add_child(
		UI_COMPONENTS.field("Licence", _licence, "The game's licence unless you pick another")
	)
	_description = LineEdit.new()
	_description.placeholder_text = "What it is for (optional)"
	_description.accessibility_name = "Description"
	UI_COMPONENTS.bind_height(_description, "md", false)
	page.add_child(UI_COMPONENTS.field("Description", _description))
	page.add_child(UI_COMPONENTS.label("What goes in", "JoinerySection"))
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	# The list scrolls; the dialog itself stays the height of its content.
	scroll.theme_changed.connect(
		func() -> void:
			scroll.custom_minimum_size.y = UI_THEME.font_size(scroll, "body") * CHOICES_EM
	)
	page.add_child(scroll)
	var choices := VBoxContainer.new()
	choices.theme_type_variation = "JoineryTight"
	choices.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Notes on the trailing edge stay clear of the scroll bar.
	var room := MarginContainer.new()
	room.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	room.theme_changed.connect(
		func() -> void:
			var gap := UI_THEME.space(room, 4)
			if room.get_theme_constant("margin_right") != gap:
				room.add_theme_constant_override("margin_right", gap)
	)
	scroll.add_child(room)
	room.add_child(choices)
	_add_group(choices, "Prefab scenes", _items(listing.get("scenes", []), "include"))
	_add_group(choices, "Kit generators", _generator_items())
	_add_group(choices, "Assets", _asset_items())
	_custom = UI_COMPONENTS.switch("Include assets made with a custom tool (marked as such)")
	_custom.toggled.connect(func(_on: bool) -> void: _sync_custom())
	page.add_child(_custom)
	export_summary = UI_COMPONENTS.label("Choose what goes in the kit.", "JoineryCaption", true)
	export_summary.name = "Summary"
	page.add_child(export_summary)
	export_problem = UI_COMPONENTS.label("", "JoineryError", true)
	export_problem.hide()
	page.add_child(export_problem)
	_sync_custom()
	export_dialog.get_ok_button().disabled = true
	export_dialog.confirmed.connect(pick.bind("export"))
	_popup(export_dialog, "doc")


func _field(line: HBoxContainer, title: String, hint: String, ratio: float) -> LineEdit:
	var edit := LineEdit.new()
	edit.placeholder_text = hint
	edit.accessibility_name = title
	edit.text_changed.connect(func(_t: String) -> void: _schedule_plan())
	UI_COMPONENTS.bind_height(edit, "md", false)
	var field := UI_COMPONENTS.field(title, edit)
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	field.size_flags_stretch_ratio = ratio
	line.add_child(field)
	return edit


func _items(paths: Array, kind: String) -> Array:
	return paths.map(
		func(p: Variant) -> Dictionary: return {"key": "%s:%s" % [kind, p], "text": str(p)}
	)


func _generator_items() -> Array:
	var found: Array = []
	for generator: Dictionary in listing.get("generators", []):
		var from := str(generator.get("from", ""))
		(
			found
			. append(
				{
					"key": "generator:%s" % generator["id"],
					"text": str(generator["label"]),
					"note": from if not from.is_empty() else "Joinery's",
				}
			)
		)
	return found


func _asset_items() -> Array:
	var found: Array = []
	for asset: Dictionary in listing.get("assets", []):
		var refused := str(asset.get("refused", ""))
		(
			found
			. append(
				{
					"key": "include:%s" % asset["path"],
					"text": str(asset["path"]),
					"note":
					(
						"Can't go: " + refused
						if not refused.is_empty()
						else ("Custom tool" if bool(asset.get("custom", false)) else "")
					),
					"refused": not refused.is_empty(),
					"custom": bool(asset.get("custom", false)),
				}
			)
		)
	return found


func _add_group(parent: VBoxContainer, title: String, items: Array) -> void:
	if items.is_empty():
		return
	var group := VBoxContainer.new()
	group.theme_type_variation = "JoineryFlush"
	group.name = title.replace(" ", "")
	parent.add_child(group)
	group.add_child(UI_COMPONENTS.label(title, "JoineryCaption"))
	for item: Dictionary in items:
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryRow"
		group.add_child(row)
		var check := CheckBox.new()
		check.text = str(item["text"])
		check.accessibility_name = check.text
		check.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		check.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		check.set_meta("custom", bool(item.get("custom", false)))
		check.disabled = bool(item.get("refused", false))
		check.toggled.connect(func(_on: bool) -> void: _schedule_plan())
		row.add_child(check)
		var note := str(item.get("note", ""))
		if not note.is_empty():
			check.tooltip_text = note
			var words := UI_COMPONENTS.label(note, "JoineryCaption")
			words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			row.add_child(words)
		_checks[item["key"]] = check


## A custom-tool asset can be ticked only while the switch is on.
func _sync_custom() -> void:
	for check: CheckBox in _checks.values():
		if bool(check.get_meta("custom", false)):
			check.disabled = not _custom.button_pressed
			if check.disabled:
				check.set_pressed_no_signal(false)
	_schedule_plan()


## Tick [param key] ("include:<path>" or "generator:<id>"); tests and drag-in use it.
func choose(key: String, on := true) -> void:
	if _checks.has(key) and not (_checks[key] as CheckBox).disabled:
		(_checks[key] as CheckBox).button_pressed = on


func _export_arguments() -> PackedStringArray:
	var args := PackedStringArray(["kit", "export", "--name", _kit_name()])
	args.append_array(["--version", _version.text.strip_edges(), "--licence", _licence.text])
	if not _description.text.strip_edges().is_empty():
		args.append_array(["--description", _description.text.strip_edges()])
	for key: String in _checks:
		if (_checks[key] as CheckBox).button_pressed:
			var parts := key.split(":", true, 1)
			args.append_array(["--" + parts[0], parts[1]])
	if _custom.button_pressed:
		args.append("--custom")
	return args


func _kit_name() -> String:
	var text := _name.text.strip_edges()
	return text if not text.is_empty() else _name.placeholder_text


func _chosen() -> int:
	return _checks.values().filter(func(c: CheckBox) -> bool: return c.button_pressed).size()


func _schedule_plan() -> void:
	if export_dialog != null and is_instance_valid(export_dialog):
		_plan_due = PLAN_DELAY
		export_dialog.get_ok_button().disabled = true


func _start_export_plan() -> void:
	if _chosen() == 0:
		export_summary.text = "Choose what goes in the kit."
		export_problem.hide()
		return
	var args := _export_arguments()
	args.append_array(["--plan", "--json"])
	_plan_for = "export"
	_plan_job = _start(args)
	export_summary.text = "Working out what comes along…"


## Show an export plan ([param data]: `joinery kit export --plan --json`).
func show_export_plan(data: Dictionary) -> void:
	if data.has("error"):
		export_problem.text = str(data["error"]).replace("; ", "\n")
		export_problem.show()
		UI_MOTION.reveal(export_problem)
		export_summary.text = "These can't go in a kit yet:"
		export_dialog.get_ok_button().disabled = true
		return
	export_problem.hide()
	var files: Array = data.get("files", [])
	var count := "1 file" if files.size() == 1 else "%d files" % files.size()
	var words := (
		"%s · %s · %s" % [data.get("holds", ""), count, size_text(int(data.get("size", 0)))]
	)
	if int(data.get("custom", 0)) > 0:
		words += " · %d made with a custom tool" % int(data["custom"])
	export_summary.text = words
	UI_MOTION.pulse(export_summary)
	export_dialog.get_ok_button().disabled = false


## Write the kit to [param path] with the dialog's choices.
func export_to(path: String) -> void:
	var args := _export_arguments()
	args.append_array(["--out", path, "--json"])
	_run(args, "Saving the kit…")


# --- Import ----------------------------------------------------------------------------------


## Pick a .joinerykit to import or update to ([param kit] names the kit Update… is for), or
## where to save the exported one.
func pick(kind: String, kit := "") -> void:
	_picking = kind
	_expected = kit
	picker.clear_filters()
	picker.add_filter(FILTER, "Joinery kits")
	if kind == "export":
		picker.file_mode = FileDialog.FILE_MODE_SAVE_FILE
		picker.title = "Save the kit"
		picker.current_file = (
			"%s-%s.joinerykit"
			% [_kit_name().to_snake_case().replace("_", "-"), _version.text.strip_edges()]
		)
	else:
		picker.file_mode = FileDialog.FILE_MODE_OPEN_FILE
		picker.title = "Update %s" % kit if kind == "update" else "Import a kit"
	picker.popup_centered_ratio(0.6)


func _picked(path: String) -> void:
	if _picking == "export":
		export_to(path)
	else:
		open_import(path, _expected)


## Read the kit at [param path] and show what it holds before anything changes.
func open_import(path: String, expected := "") -> void:
	_import_file = path
	_expected = expected
	_plan_for = "import"
	_plan_job = _start(PackedStringArray(["kit", "import", path, "--plan", "--json"]))
	_busy("Reading %s…" % path.get_file())


## The import dialog for [param data] (`joinery kit import --plan --json`).
func show_import_plan(data: Dictionary) -> void:
	_idle()
	if data.has("error"):
		_say("Couldn't read the kit. " + str(data["error"]), true)
		return
	if import_dialog != null and is_instance_valid(import_dialog):
		import_dialog.queue_free()
	var update: Variant = data.get("update")
	import_dialog = _dialog("Import kit", "Update kit" if update != null else "Add to project")
	import_body = import_dialog.get_child(0)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	import_body.add_child(head)
	head.add_child(
		UI_COMPONENTS.label(
			"%s %s" % [data.get("name", ""), data.get("version", "")], "JoineryTitle"
		)
	)
	var by := UI_COMPONENTS.label(
		"By %s · %s" % [data.get("author", ""), data.get("licence", "")], "JoineryCaption", true
	)
	by.name = "Author"
	head.add_child(by)
	if not str(data.get("description", "")).is_empty():
		import_body.add_child(UI_COMPONENTS.label(str(data["description"]), "", true))
	var holds := UI_COMPONENTS.label(
		"%s · %s" % [data.get("holds", ""), size_text(int(data.get("size", 0)))],
		"JoineryBodyLarge",
		true
	)
	holds.name = "Holds"
	import_body.add_child(holds)
	var where := (
		"Goes into %s. Its assets keep their licence and rebuild from their recipes."
		% data.get("folder", "")
	)
	if update != null:
		where = (
			"Updates %s to %s: %s."
			% [update["from"], update["to"], _changes(data.get("changes", []))]
		)
	var place := UI_COMPONENTS.label(where, "JoineryCaption", true)
	place.name = "Place"
	import_body.add_child(place)
	var generators: Array = data.get("generator_ids", [])
	if not generators.is_empty():
		import_body.add_child(
			UI_COMPONENTS.label(
				"Generators for the Kits list: " + ", ".join(generators), "JoineryCaption", true
			)
		)
	for note: Variant in data.get("notes", []):
		import_body.add_child(UI_COMPONENTS.label(str(note), "JoineryError", true))
	if not _expected.is_empty() and str(data.get("name", "")) != _expected:
		var wrong := "This file is the %s kit, not %s." % [data.get("name", ""), _expected]
		import_body.add_child(UI_COMPONENTS.label(wrong, "JoineryError", true))
		import_dialog.get_ok_button().disabled = true
	import_dialog.confirmed.connect(apply_import)
	_popup(import_dialog, "connect")


static func _changes(rows: Array) -> String:
	var counts := {"added": 0, "changed": 0, "removed": 0}
	for row: Dictionary in rows:
		var change := str(row.get("change", ""))
		if counts.has(change):
			counts[change] += 1
	var words: Array[String] = []
	for change: String in counts:
		if int(counts[change]) > 0:
			words.append("%d %s" % [counts[change], change])
	return ", ".join(words) if not words.is_empty() else "nothing changes"


## Add the picked kit as a change waiting for review.
func apply_import() -> void:
	_run(PackedStringArray(["kit", "import", _import_file, "--json"]), "Adding the kit…")


## Ask before removing [param kit]; the removal is a change waiting for review.
func ask_remove(kit: Dictionary) -> void:
	var name := str(kit.get("name", ""))
	UI_COMPONENTS.confirm(
		self,
		"Remove the %s kit?" % name,
		"Its files, recipes and joinery.lock entries go, as one change you review first.",
		["Remove", "Cancel"],
		func() -> void:
			_run(
				PackedStringArray(["kit", "remove", "--name", name, "--json"]), "Removing the kit…"
			)
	)


# --- Jobs ------------------------------------------------------------------------------------


func _process(delta: float) -> void:
	_list_job = COMMAND_SCRIPT.finish(_list_job, _list_done)
	_plan_job = COMMAND_SCRIPT.finish(_plan_job, _plan_done)
	_run_job = COMMAND_SCRIPT.finish(_run_job, _run_done)
	if _plan_due >= 0.0:
		_plan_due -= delta
		if _plan_due < 0.0 and _plan_job == null:
			_start_export_plan()


func _list_done(result: Dictionary) -> void:
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if data is Dictionary and not (data as Dictionary).has("error"):
		set_listing(data)


func _plan_done(result: Dictionary) -> void:
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if not data is Dictionary:
		data = {"error": _reason(result)}
	if _plan_for == "export":
		if export_dialog != null and is_instance_valid(export_dialog):
			show_export_plan(data)
	else:
		show_import_plan(data)


func _run_done(result: Dictionary) -> void:
	_idle()
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if (
		int(result.get("code", 1)) != 0
		or not data is Dictionary
		or (data as Dictionary).has("error")
	):
		var why := (
			str((data as Dictionary).get("error", "")) if data is Dictionary else _reason(result)
		)
		_say("That didn't work. " + why, true)
		return
	if (data as Dictionary).has("file"):
		_say(
			"Saved %s · %s." % [str(data["file"]).get_file(), size_text(int(data.get("bytes", 0)))],
			false
		)
		return
	_say("%s. Waiting for your review in the Review tab." % data.get("summary", "Done"), false)
	changed.emit(data)
	refresh()


func _busy(text: String) -> void:
	_working_label.text = text
	_working.show()
	_indicator.running = true
	status.hide()
	import_button.disabled = true
	export_button.disabled = true
	UI_MOTION.reveal(_working)


func _idle() -> void:
	_working.hide()
	_indicator.running = false
	import_button.disabled = false
	export_button.disabled = false


func _say(text: String, failed: bool) -> void:
	status.text = text
	status.theme_type_variation = "JoineryError" if failed else "JoineryCaption"
	status.show()
	UI_MOTION.reveal(status)
	if not failed:
		UI_MOTION.pulse(status)


func _run(args: PackedStringArray, doing: String) -> void:
	if _run_job != null:
		return
	_run_job = _start(args)
	if _run_job != null:
		_busy(doing)


## "2.1 MB": a size people read at a glance.
static func size_text(bytes: int) -> String:
	if bytes >= 1024 * 1024:
		return "%.1f MB" % (bytes / 1048576.0)
	if bytes >= 1024:
		return "%d KB" % roundi(bytes / 1024.0)
	return "%d bytes" % bytes


static func _reason(result: Dictionary) -> String:
	var reason := PARTS_SCRIPT.first_line(str(result.get("error", "")))
	return reason if not reason.is_empty() else "No reason was given."


func _dialog(title: String, ok: String) -> ConfirmationDialog:
	var dialog := ConfirmationDialog.new()
	dialog.title = title
	dialog.ok_button_text = ok
	dialog.cancel_button_text = "Cancel"
	dialog.theme = UI_THEME.build(self)
	dialog.get_ok_button().theme_type_variation = "JoineryPrimaryButton"
	dialog.get_cancel_button().theme_type_variation = "JoinerySecondaryButton"
	# The window's own panel follows the Joinery theme, light or dark, not the editor's.
	var box := StyleBoxFlat.new()
	box.bg_color = dialog.get_theme_color("surface", "Joinery")
	var pad := float(UI_THEME.space(self, 4))
	box.set_content_margin_all(pad)
	dialog.add_theme_stylebox_override("panel", box)
	var page := VBoxContainer.new()
	page.theme_type_variation = "JoineryStack"
	dialog.add_child(page)
	UI_COMPONENTS.dialog_footer(dialog)
	return dialog


## Show [param dialog] as wide as the [param size_name] dialog size and as tall as its content.
func _popup(dialog: ConfirmationDialog, size_name: String) -> void:
	add_child(dialog)
	var room := roundi(get_viewport().get_visible_rect().size.y * 0.9)
	dialog.popup_centered(Vector2i(UI_THEME.dialog_size(self, size_name).x, room))
	_fit.call_deferred(dialog, room)


## Wrapped text knows its height only once the dialog has its width: fit the height then.
func _fit(dialog: ConfirmationDialog, room: int) -> void:
	await get_tree().process_frame
	if not is_instance_valid(dialog) or not dialog.visible:
		return
	var wanted := mini(roundi(dialog.get_contents_minimum_size().y), room)
	dialog.size = Vector2i(dialog.size.x, wanted)
	dialog.move_to_center()


## Run `joinery <arguments> --project <this project>` as a background job.
func _start(arguments: PackedStringArray) -> RefCounted:
	var command := "uv"
	var args := PackedStringArray()
	if cli_prefix.is_empty():
		var engine_dir := OS.get_environment("JOINERY_HOME")
		if engine_dir.is_empty():
			engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
		args.append_array(["run", "--frozen", "--quiet", "--project", engine_dir, "joinery"])
	else:
		command = cli_prefix[0]
		args.append_array(cli_prefix.slice(1))
	args.append_array(arguments)
	args.append_array(["--project", ProjectSettings.globalize_path("res://")])
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start(command, args) != OK:
		_say("Joinery's command line isn't available.", true)
		return null
	return job


func _exit_tree() -> void:
	for job: RefCounted in [_list_job, _plan_job, _run_job]:
		if job != null:
			job.cancel()
	_list_job = null
	_plan_job = null
	_run_job = null
