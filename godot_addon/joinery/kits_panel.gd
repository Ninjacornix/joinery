@tool
extends VBoxContainer
## The asset workspace's Kits section (tool_workspace.gd). Pick a kit, set its parameters and
## see Blender draw the model in the worker a moment after each change; then make one model or
## ten variations. Each make is a change waiting for review, like an agent's kit_make. Opened
## from a model made with a kit (edit_button), it changes that model's set instead.
##
## The workspace places [member switch] (Assets | Kits) and [member list] (the kits) in its
## browser and swaps this panel in for the asset view while the Kits tab is on.

## The Kits tab was turned on (true) or off.
signal toggled(on: bool)
## A make or change finished; [param result] is the CLI's JSON (transaction_id, files…).
signal made(result: Dictionary)

const COMMAND_SCRIPT := preload("res://addons/joinery/command_job.gd")
const LIST_SCRIPT := preload("res://addons/joinery/asset_list.gd")
const INDICATOR_SCRIPT := preload("res://addons/joinery/activity_indicator.gd")
const PARTS_SCRIPT := preload("res://addons/joinery/parts_panel.gd")
const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_COMPONENTS := preload("res://addons/joinery/ui_components.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
## Seconds after the last change before Blender draws the preview.
const PREVIEW_DELAY := 0.45
const VARIATIONS := 10
## Preview square as a multiple of the editor font size.
const PREVIEW_EM := 18.0

## The command that runs Joinery's CLI (tests use a Python interpreter); empty runs uv.
var cli_prefix := PackedStringArray()
var switch: HBoxContainer
var list: LIST_SCRIPT
var edit_button: Button
var catalogue: Array = []
var _assets_tab: Button
var _kits_tab: Button
var _kit: Dictionary = {}
var _values: Dictionary = {}
var _seed := 1
## The model being changed (an asset of the index, with its "kit"), or empty when making.
var _editing: Dictionary = {}
var _asset: Dictionary = {}
var _title: Label
var _summary: Label
var _picture: TextureRect
var _drawing: HBoxContainer
var _drawing_indicator: INDICATOR_SCRIPT
var _facts: Label
var _form: VBoxContainer
var _name: LineEdit
var _seed_label: Label
var _make: Button
var _vary: Button
var _working: HBoxContainer
var _working_indicator: INDICATOR_SCRIPT
var _working_label: Label
var _status: Label
var _empty: Label
var _list_job: RefCounted
var _preview_job: RefCounted
var _make_job: RefCounted
var _preview_due := -1.0
var _preview_wanted := ""
var _preview_shown := ""


func _init() -> void:
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	_build_switch()
	list = LIST_SCRIPT.new()
	list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	list.item_selected.connect(_on_kit_chosen)
	list.hide()
	edit_button = UI_COMPONENTS.secondary("Kit settings")
	edit_button.tooltip_text = "Change this model's kit settings"
	edit_button.pressed.connect(func() -> void: edit(_asset))
	edit_button.hide()
	_build_body()


func _build_switch() -> void:
	switch = HBoxContainer.new()
	switch.theme_type_variation = "JoineryRowTight"
	_assets_tab = _tab("Assets", "Every asset of the project")
	_assets_tab.set_pressed_no_signal(true)
	_assets_tab.pressed.connect(set_open.bind(false))
	switch.add_child(_assets_tab)
	_kits_tab = _tab("Kits", "Make rocks, crates, fences, huts and trees from a kit")
	_kits_tab.pressed.connect(set_open.bind(true))
	switch.add_child(_kits_tab)


func _tab(text: String, tip: String) -> Button:
	var tab := Button.new()
	tab.theme_type_variation = "JoinerySegment"
	tab.toggle_mode = true
	tab.focus_mode = Control.FOCUS_ALL
	tab.text = text
	tab.tooltip_text = tip
	tab.accessibility_name = text
	tab.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI_COMPONENTS.bind_height(tab, "sm")
	UI_MOTION.press_feedback(tab)
	return tab


func _build_body() -> void:
	var heading := VBoxContainer.new()
	heading.theme_type_variation = "JoineryTight"
	add_child(heading)
	_title = UI_COMPONENTS.label("Kits", "JoineryTitle")
	heading.add_child(_title)
	_summary = UI_COMPONENTS.label("", "JoineryCaption", true)
	heading.add_child(_summary)
	_empty = UI_COMPONENTS.label("Loading kits…", "JoineryCaption", true)
	add_child(_empty)
	var stage := HBoxContainer.new()
	stage.theme_type_variation = "JoineryRow"
	stage.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(stage)
	var look := VBoxContainer.new()
	look.theme_type_variation = "JoineryTight"
	stage.add_child(look)
	_picture = TextureRect.new()
	_picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_picture.accessibility_name = "Preview"
	_picture.theme_changed.connect(
		func() -> void:
			var side := roundf(UI_THEME.font_size(_picture, "body") * PREVIEW_EM)
			_picture.custom_minimum_size = Vector2(side, side)
	)
	look.add_child(_picture)
	_drawing = HBoxContainer.new()
	_drawing.theme_type_variation = "JoineryRow"
	_drawing_indicator = INDICATOR_SCRIPT.new()
	_drawing.add_child(_drawing_indicator)
	_drawing.add_child(UI_COMPONENTS.label("Blender is drawing it…", "JoineryCaption"))
	_drawing.modulate.a = 0.0
	look.add_child(_drawing)
	_facts = UI_COMPONENTS.label("", "JoineryCaption", true)
	look.add_child(_facts)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	stage.add_child(scroll)
	var room := MarginContainer.new()
	room.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Values stay clear of the scroll bar.
	room.theme_changed.connect(
		func() -> void:
			var gap := UI_THEME.space(room, 4)
			if room.get_theme_constant("margin_right") != gap:
				room.add_theme_constant_override("margin_right", gap)
	)
	scroll.add_child(room)
	_form = VBoxContainer.new()
	_form.theme_type_variation = "JoineryStack"
	_form.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	room.add_child(_form)
	_build_footer()


func _build_footer() -> void:
	var footer := HBoxContainer.new()
	footer.theme_type_variation = "JoineryRow"
	add_child(footer)
	_name = LineEdit.new()
	_name.accessibility_name = "Name"
	_name.tooltip_text = "File name for the new models; empty picks a free one"
	_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_name.size_flags_stretch_ratio = 3.0
	UI_COMPONENTS.bind_height(_name, "md")
	footer.add_child(_name)
	var gap := Control.new()
	gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	footer.add_child(gap)
	_seed_label = UI_COMPONENTS.label("Seed 1", "JoineryCaption")
	_seed_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	footer.add_child(_seed_label)
	var reseed := UI_COMPONENTS.icon_button("reload", "Try another seed")
	reseed.pressed.connect(_reseed)
	footer.add_child(reseed)
	_vary = UI_COMPONENTS.secondary("Make %d variations" % VARIATIONS)
	_vary.pressed.connect(_start_make.bind(VARIATIONS))
	footer.add_child(_vary)
	_make = UI_COMPONENTS.primary("Make")
	_make.pressed.connect(_start_make.bind(1))
	footer.add_child(_make)
	_working = HBoxContainer.new()
	_working.theme_type_variation = "JoineryRow"
	_working_indicator = INDICATOR_SCRIPT.new()
	_working.add_child(_working_indicator)
	_working_label = UI_COMPONENTS.label("", "JoineryCaption", true)
	_working.add_child(_working_label)
	_working.hide()
	add_child(_working)
	_status = UI_COMPONENTS.label("", "JoineryCaption", true)
	_status.hide()
	add_child(_status)


## Turn the Kits tab on or off; on loads the kit list the first time.
func set_open(on: bool) -> void:
	_assets_tab.set_pressed_no_signal(not on)
	_kits_tab.set_pressed_no_signal(on)
	if not on:
		_editing = {}
	elif catalogue.is_empty() and _list_job == null:
		_list_job = _start(PackedStringArray(["kits", "list", "--json"]))
	toggled.emit(on)


## The selected asset: models made with a kit offer their kit settings.
func show_asset(asset: Dictionary) -> void:
	_asset = asset
	var kit: Dictionary = asset.get("kit", {})
	edit_button.visible = not kit.is_empty()
	edit_button.disabled = not str(asset.get("pending", "")).is_empty()
	edit_button.tooltip_text = (
		"Accept or discard the change first"
		if edit_button.disabled
		else "Change this model's %s kit settings" % str(kit.get("label", "")).to_lower()
	)


## Open the Kits tab on [param asset]'s set, its values ready to change.
func edit(asset: Dictionary) -> void:
	var kit: Dictionary = asset.get("kit", {})
	if kit.is_empty():
		return
	_editing = asset
	set_open(true)
	if not catalogue.is_empty():
		_choose_by_id(str(kit.get("kit", "")))


## Use [param data] (the `joinery kits list --json` payload) as the catalogue.
func set_catalogue(data: Dictionary) -> void:
	catalogue = data.get("kits", [])
	list.clear()
	for kit: Dictionary in catalogue:
		var index := list.add_item(str(kit["label"]), "model")
		list.set_item_tooltip(index, str(kit.get("summary", "")))
		list.set_item_metadata(index, str(kit["id"]))
	list.stagger()
	_empty.visible = catalogue.is_empty()
	_empty.text = "No kits in this Joinery." if catalogue.is_empty() else ""
	var wanted := str((_editing.get("kit", {}) as Dictionary).get("kit", ""))
	_choose_by_id(
		(
			wanted
			if not wanted.is_empty()
			else str(catalogue[0]["id"]) if not catalogue.is_empty() else ""
		)
	)


func _choose_by_id(kit_id: String) -> void:
	for index: int in list.item_count:
		if str(list.get_item_metadata(index)) == kit_id:
			list.select(index)
			_on_kit_chosen(index)
			return


func _on_kit_chosen(index: int) -> void:
	var kit_id := str(list.get_item_metadata(index))
	for kit: Dictionary in catalogue:
		if str(kit["id"]) == kit_id:
			_show_kit(kit)
			return


func _show_kit(kit: Dictionary) -> void:
	var editing := str((_editing.get("kit", {}) as Dictionary).get("kit", "")) == str(kit["id"])
	if not editing:
		_editing = {}
	_kit = kit
	_values = {}
	for param: Dictionary in kit.get("params", []):
		_values[param["name"]] = param["default"]
	var made: Dictionary = _editing.get("kit", {})
	if editing:
		_values.merge(made.get("base", {}), true)
		_seed = int(made.get("seed", 1))
	_title.text = str(kit["label"]) if not editing else "%s · %s" % [kit["label"], _model_name()]
	_summary.text = str(kit.get("summary", "")) if not editing else _edit_note(made)
	_name.visible = not editing
	_name.placeholder_text = str(kit["id"])
	_vary.visible = not editing
	_make.text = "Apply changes" if editing else "Make"
	_make.disabled = editing
	_status.hide()
	_picture.texture = null
	_facts.text = ""
	_preview_shown = ""
	_build_form()
	_seed_label.text = "Seed %d" % _seed
	_schedule_preview()


func _model_name() -> String:
	return str(_editing.get("path", "")).get_file().get_basename()


func _edit_note(made: Dictionary) -> String:
	var count := int(made.get("count", 1))
	if count > 1:
		return "Changes all %d models of this set; each keeps its own variation." % count
	return "Changes this model; scenes that place it pick up the change."


func _build_form() -> void:
	for child: Node in _form.get_children():
		_form.remove_child(child)
		child.queue_free()
	for param: Dictionary in _kit.get("params", []):
		_form.add_child(_param_row(param))
	UI_MOTION.stagger(_form.get_children())


## One parameter: its name and value on a line, the control under it, the help as tooltip.
func _param_row(param: Dictionary) -> Control:
	var name := str(param["name"])
	var kind := str(param["kind"])
	var title := str(param.get("label", name))
	var help := str(param.get("help", ""))
	if kind == "bool":
		var check := UI_COMPONENTS.switch()
		check.accessibility_name = title
		check.set_pressed_no_signal(bool(_values[name]))
		check.toggled.connect(func(on: bool) -> void: _set_value(name, on))
		return _line(title, help, check)
	if kind == "choice":
		var choices: Array = param.get("choices", [])
		var picker := UI_COMPONENTS.picker(choices.map(func(c: Variant) -> String: return _word(c)))
		picker.accessibility_name = title
		picker.select(choices.find(_values[name]))
		picker.item_selected.connect(func(index: int) -> void: _set_value(name, choices[index]))
		return _line(title, help, picker)
	var box := VBoxContainer.new()
	box.theme_type_variation = "JoineryFlush"
	box.tooltip_text = help
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	box.add_child(line)
	var words := UI_COMPONENTS.label(title)
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.add_child(words)
	var shown := UI_COMPONENTS.label(_shown(param, _values[name]), "JoineryCaption")
	shown.name = "Value"
	shown.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	line.add_child(shown)
	var slider := HSlider.new()
	slider.name = "Slider"
	slider.min_value = float(param["min"])
	slider.max_value = float(param["max"])
	slider.step = float(param.get("step", 1 if kind == "int" else 0.01))
	slider.value = float(_values[name])
	slider.focus_mode = Control.FOCUS_ALL
	slider.accessibility_name = title
	slider.tooltip_text = help
	UI_COMPONENTS.bind_height(slider, "sm", false)
	slider.value_changed.connect(
		func(value: float) -> void:
			var typed: Variant = roundi(value) if kind == "int" else snappedf(value, slider.step)
			shown.text = _shown(param, typed)
			_set_value(name, typed)
	)
	box.add_child(slider)
	return box


## A row of a [param title] on the leading edge and its [param control] on the trailing one.
func _line(title: String, help: String, control: Control) -> HBoxContainer:
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.tooltip_text = help
	var words := UI_COMPONENTS.label(title)
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(words)
	control.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	control.tooltip_text = help
	line.add_child(control)
	UI_COMPONENTS.bind_height(line, "md", false)
	return line


static func _word(choice: Variant) -> String:
	return str(choice).capitalize()


## A value as the row shows it: tabular figures, the unit after.
static func _shown(param: Dictionary, value: Variant) -> String:
	var unit := str(param.get("unit", ""))
	var text := str(roundi(float(value))) if str(param["kind"]) == "int" else "%.2f" % float(value)
	if str(param["kind"]) == "float" and float(param.get("step", 0.01)) >= 0.1:
		text = "%.1f" % float(value)
	return text + ("" if unit.is_empty() else (unit if unit == "°" else " " + unit))


func _set_value(name: String, value: Variant) -> void:
	_values[name] = value
	if not _editing.is_empty():
		_make.disabled = _values == _merged_base() and _seed == _edit_seed()
	_schedule_preview()


func _merged_base() -> Dictionary:
	var base := {}
	for param: Dictionary in _kit.get("params", []):
		base[param["name"]] = param["default"]
	base.merge((_editing.get("kit", {}) as Dictionary).get("base", {}), true)
	return base


func _edit_seed() -> int:
	return int((_editing.get("kit", {}) as Dictionary).get("seed", 1))


func _reseed() -> void:
	_seed = randi_range(1, 99999)
	_seed_label.text = "Seed %d" % _seed
	UI_MOTION.pulse(_seed_label)
	if not _editing.is_empty():
		_make.disabled = false
	_schedule_preview()


func _schedule_preview() -> void:
	_preview_due = PREVIEW_DELAY
	_preview_wanted = JSON.stringify([_kit.get("id", ""), _values, _seed])


func _process(delta: float) -> void:
	_poll_list()
	_poll_preview()
	_poll_make()
	if _preview_due >= 0.0:
		_preview_due -= delta
		if _preview_due < 0.0 and _preview_job == null and not _kit.is_empty():
			_start_preview()


func _start_preview() -> void:
	if _preview_wanted == _preview_shown:
		return
	_preview_job = _start(
		PackedStringArray(
			[
				"kits",
				"preview",
				"--kit",
				str(_kit["id"]),
				"--params",
				JSON.stringify(_values),
				"--seed",
				str(_seed),
			]
		)
	)
	_preview_shown = _preview_wanted
	_set_drawing(_preview_job != null)


func _set_drawing(on: bool) -> void:
	_drawing_indicator.running = on
	if _drawing.is_inside_tree():
		UI_MOTION.tween_to(_drawing, _drawing, "modulate:a", 1.0 if on else 0.0, "fast")


func _poll_list() -> void:
	var result := _take(_list_job)
	if result.is_empty():
		return
	_list_job = null
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if data is Dictionary and not (data as Dictionary).has("error"):
		set_catalogue(data)
		return
	_empty.text = "Couldn't load the kits. " + _reason(result, data)
	_empty.show()


func _poll_preview() -> void:
	var result := _take(_preview_job)
	if result.is_empty():
		return
	_preview_job = null
	_set_drawing(false)
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if not data is Dictionary or (data as Dictionary).has("error"):
		_facts.text = "Couldn't draw the preview. " + _reason(result, data)
		return
	show_preview(data)
	if _preview_wanted != _preview_shown:
		_preview_due = 0.0


## Show a preview payload: the picture Blender drew, triangles against the bible's budget,
## and the model's size.
func show_preview(data: Dictionary) -> void:
	var image := Image.load_from_file(str(data.get("image", "")))
	if image != null and not image.is_empty():
		var old := _picture.texture
		_picture.texture = ImageTexture.create_from_image(image)
		if old == null:
			UI_MOTION.fade_in(_picture)
	var words: Array[String] = ["%s triangles" % _thousands(int(data.get("triangles", 0)))]
	if data.get("budget") != null:
		words[0] += " of %s" % _thousands(int(data["budget"]))
	var size: Array = data.get("size_m", [])
	if size.size() == 3:
		# Blender's x, y, z: width, depth and height in metres.
		words.append("%.1f × %.1f × %.1f m" % [size[0], size[1], size[2]])
	_facts.text = " · ".join(words)


static func _thousands(value: int) -> String:
	var text := str(absi(value))
	var out := ""
	while text.length() > 3:
		out = "," + text.right(3) + out
		text = text.left(text.length() - 3)
	return ("-" if value < 0 else "") + text + out


func _start_make(count: int) -> void:
	if _make_job != null or _kit.is_empty():
		return
	var args := PackedStringArray(["kits"])
	if _editing.is_empty():
		args.append_array(["make", "--kit", str(_kit["id"]), "--count", str(count)])
		var name := _name.text.strip_edges()
		if not name.is_empty():
			args.append_array(["--name", name])
		_working_label.text = (
			"Blender is making %d %ss…" % [count, str(_kit["label"]).to_lower()]
			if count > 1
			else "Blender is making the %s…" % str(_kit["label"]).to_lower()
		)
	else:
		args.append_array(["edit", "--asset", str(_editing.get("path", ""))])
		_working_label.text = "Blender is making %s again…" % _model_name()
	args.append_array(["--params", JSON.stringify(_values), "--seed", str(_seed), "--json"])
	_make_job = _start(args)
	if _make_job == null:
		return
	_make.disabled = true
	_vary.disabled = true
	_status.hide()
	_working.show()
	_working_indicator.running = true
	UI_MOTION.reveal(_working)


func _poll_make() -> void:
	var result := _take(_make_job)
	if result.is_empty():
		return
	_make_job = null
	_working.hide()
	_working_indicator.running = false
	_vary.disabled = false
	_make.disabled = false
	var data: Variant = PARTS_SCRIPT.parse_json(str(result.get("output", "")))
	if (
		int(result.get("code", 1)) != 0
		or not data is Dictionary
		or (data as Dictionary).has("error")
	):
		_say("Couldn't make it. " + _reason(result, data), true)
		return
	var models: Array = data.get("models", [])
	var what := (
		"%d %ss" % [models.size(), str(_kit["label"]).to_lower()]
		if models.size() > 1
		else (
			str((models[0] as Dictionary).get("glb", "")).get_file()
			if not models.is_empty()
			else "the model"
		)
	)
	var verb := "Changed" if not _editing.is_empty() else "Made"
	_say("%s %s. Waiting for your review in the Review tab." % [verb, what], false)
	if not _editing.is_empty():
		_make.disabled = true
	made.emit(data)


func _say(text: String, failed: bool) -> void:
	_status.text = text
	_status.theme_type_variation = "JoineryError" if failed else "JoineryCaption"
	_status.show()
	UI_MOTION.reveal(_status)
	if not failed:
		UI_MOTION.pulse(_status)


static func _reason(result: Dictionary, data: Variant) -> String:
	if data is Dictionary and (data as Dictionary).has("error"):
		return str(data["error"])
	var reason := PARTS_SCRIPT.first_line(str(result.get("error", "")))
	return reason if not reason.is_empty() else "No reason was given."


func _take(job: RefCounted) -> Dictionary:
	return job.poll() if job != null else {}


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
	for job: RefCounted in [_list_job, _preview_job, _make_job]:
		if job != null:
			job.cancel()
	_list_job = null
	_preview_job = null
	_make_job = null
