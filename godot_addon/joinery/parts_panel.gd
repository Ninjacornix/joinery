@tool
extends VBoxContainer
## The parts of one model: its objects, materials and texture maps, as the owning tool names
## them (`joinery parts <asset> --json`, read from the native source, so maps the game copy
## cannot carry, such as height, are listed too). Click a row, or the model in the preview, to
## make a part the message target; drag a row onto the message to add it. A picked map shows
## beside the model in [member map_view] (the workspace places it), and the surfaces of the
## picked material are tinted in the preview. The model's actions are listed and played by
## [member animations] (animation_browser.gd), which the workspace places under the preview.

## The user picked a part: a message target from target_chip.for_part (label, tooltip, ids).
signal part_selected(target: Dictionary)
## The parts report arrived (or was set).
signal parts_loaded(report: Dictionary)

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR_SCRIPT := preload("activity_indicator.gd")
const CHIP_SCRIPT := preload("target_chip.gd")
const ANIMATIONS_SCRIPT := preload("animation_browser.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const IMAGES := ["png", "jpg", "jpeg", "webp", "bmp", "tga", "svg"]
## Group order and titles.
const GROUPS := [
	["object", "Objects"],
	["material", "Materials"],
	["texture", "Textures"],
	["shape_key", "Shape keys"],
	["bone", "Bones"],
]
## Tree height and map preview width, as multiples of the editor font size.
const TREE_EM := 13.0
const MAP_EM := 12.0

var tree: Tree
## The picked map beside the model; hidden unless a map is picked.
var map_view: VBoxContainer
## The model's animations under the preview; hidden when it has none.
var animations: ANIMATIONS_SCRIPT
var _map_picture: TextureRect
var _map_caption: Label
var _viewer: Control
## The picked part's target, or {}.
var _target: Dictionary = {}
var _loading: HBoxContainer
var _indicator: INDICATOR_SCRIPT
var _error_box: HBoxContainer
var _error_label: Label
var _job: RefCounted
var _asset := ""
var _report: Dictionary = {}
var _items: Dictionary = {}
var _error := ""


func _init() -> void:
	theme_type_variation = "JoineryTight"
	_loading = HBoxContainer.new()
	_loading.theme_type_variation = "JoineryRow"
	_indicator = INDICATOR_SCRIPT.new()
	_loading.add_child(_indicator)
	_loading.add_child(UI_COMPONENTS.label("Reading parts…", "JoineryCaption"))
	_loading.hide()
	add_child(_loading)
	_error_box = HBoxContainer.new()
	_error_box.theme_type_variation = "JoineryRow"
	_error_label = UI_COMPONENTS.label("", "JoineryError", true)
	_error_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_error_box.add_child(_error_label)
	var retry := UI_COMPONENTS.secondary("Retry")
	retry.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	retry.pressed.connect(func() -> void: load_asset(_asset, true))
	_error_box.add_child(retry)
	_error_box.hide()
	add_child(_error_box)
	tree = Tree.new()
	tree.hide_root = true
	tree.select_mode = Tree.SELECT_ROW
	tree.accessibility_name = "Parts"
	tree.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tree.item_selected.connect(_on_item_selected)
	tree.set_drag_forwarding(_drag_part, Callable(), Callable())
	tree.hide()
	add_child(tree)
	map_view = VBoxContainer.new()
	map_view.theme_type_variation = "JoineryTight"
	map_view.hide()
	_map_picture = TextureRect.new()
	_map_picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_map_picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_map_picture.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_map_picture.accessibility_name = "Texture map"
	map_view.add_child(_map_picture)
	_map_caption = UI_COMPONENTS.label("", "JoineryCaption", true)
	_map_caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	map_view.add_child(_map_caption)
	animations = ANIMATIONS_SCRIPT.new()
	animations.part_selected.connect(part_selected.emit)


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and tree != null:
		var em := UI_THEME.font_size(self, "body")
		tree.custom_minimum_size.y = roundf(em * TREE_EM)
		map_view.custom_minimum_size.x = roundf(em * MAP_EM)
		_map_picture.custom_minimum_size = Vector2.ONE * roundf(em * MAP_EM)
		_style_tree()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.cancel()
		_job = null
	elif what == NOTIFICATION_PREDELETE and is_instance_valid(map_view):
		for view: Control in [map_view, animations]:
			if is_instance_valid(view) and view.get_parent() == null:
				view.free()


## Show the parts of [param asset] (an index entry) when it is a model made in Blender;
## otherwise hide the list and any picked map.
func show_for(asset: Dictionary, model: bool) -> void:
	var path := str(asset.get("path", ""))
	visible = model and str(asset.get("tool", "")) == "blender"
	if not visible or str(_target.get("owner_source", path)) != path:
		show_part({})
	if visible:
		load_asset(path)


## Picks on [param viewer] (a model_preview) select parts, and it shows the picked material.
func attach(viewer: Control) -> void:
	_viewer = viewer
	viewer.connect("part_clicked", _on_model_clicked)
	viewer.call("highlight", str(_target.get("material", "")))
	animations.attach(viewer)


## List the parts of [param asset] (a model or its source, project-relative or res://).
func load_asset(asset: String, refresh := false) -> void:
	asset = asset.trim_prefix("res://")
	if asset == _asset and not refresh and (not _report.is_empty() or _job != null):
		return
	_asset = asset
	_report = {}
	_error = ""
	if _job != null:
		_job.cancel()
	_job = COMMAND_SCRIPT.new()
	var engine_dir := OS.get_environment("JOINERY_HOME")
	if engine_dir.is_empty():
		engine_dir = ProjectSettings.globalize_path("res://").path_join("..").simplify_path()
	var arguments := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", engine_dir, "python", "-m", "joinery.parts"]
	)
	arguments.append_array(
		["parts", asset, "--project", ProjectSettings.globalize_path("res://"), "--json"]
	)
	var error: Error = _job.start("uv", arguments)
	if error != OK:
		_job = null
		_error = "The parts command could not start (%s)." % error_string(error)
	_update_state()


func _process(_delta: float) -> void:
	if _job == null:
		return
	var result: Dictionary = _job.poll()
	if result.is_empty():
		return
	_job = null
	var payload: Variant = parse_json(str(result.get("output", "")))
	if int(result.get("code", 1)) != 0 or not payload is Dictionary:
		var reason := first_line(str(result.get("error", "")))
		_error = reason if not reason.is_empty() else "The parts command failed."
		_update_state()
		return
	set_report(payload)


## Show [param report] (the `joinery parts --json` report) as grouped rows.
func set_report(report: Dictionary) -> void:
	_report = report
	_error = ""
	_items.clear()
	tree.clear()
	var root := tree.create_item()
	for group: Array in GROUPS:
		var rows: Array[Dictionary] = []
		for part: Dictionary in report.get("parts", []):
			if str(part.get("part_kind", "")) == group[0]:
				rows.append(part)
		if rows.is_empty():
			continue
		var heading := tree.create_item(root)
		heading.set_text(0, "%s  %d" % [group[1], rows.size()])
		heading.set_selectable(0, false)
		heading.set_custom_color(0, UI_THEME.color(self, "text_muted"))
		heading.set_metadata(0, group[0])
		for part: Dictionary in rows:
			var item := tree.create_item(heading)
			item.set_text(0, _row_text(part))
			item.set_tooltip_text(0, CHIP_SCRIPT.part_tooltip(part))
			item.set_metadata(0, part)
			_items[str(part.get("id", ""))] = item
	_update_state()
	animations.set_report(report)
	parts_loaded.emit(report)


func report() -> Dictionary:
	return _report


## Rows per group: [object, material, texture, shape key, bone] counts.
func group_counts() -> Dictionary:
	var counts := {}
	for heading: TreeItem in tree.get_root().get_children() if tree.get_root() != null else []:
		counts[str(heading.get_metadata(0))] = heading.get_child_count()
	return counts


## Select the row of part [param id] without emitting part_selected. False when not listed.
func select_part(id: String) -> bool:
	var item: TreeItem = _items.get(id)
	if item == null:
		tree.deselect_all()
		return false
	tree.set_selected(item, 0)
	tree.scroll_to_item(item)
	return true


## The listed part matching a viewport pick ({object, material}), with [param role] its map.
func find_part(material: String, image := "") -> Dictionary:
	for part: Dictionary in _report.get("parts", []):
		var kind := "texture" if not image.is_empty() else "material"
		if (
			str(part.get("part_kind")) == kind
			and str(part.get("material")) == material
			and (image.is_empty() or str(part.get("image")) == image)
		):
			return part
	return {}


## "Head.001"; "Steel · used by 2"; "Roughness · Steel" (map, and "not in the game yet" for a
## height map until it is baked).
func _row_text(part: Dictionary) -> String:
	match str(part.get("part_kind", "")):
		"object":
			return str(part.get("object", ""))
		"material":
			var users := str(part.get("used_by", "")).split(",", false)
			var text := str(part.get("material", ""))
			return text + (" · on %d objects" % users.size() if users.size() > 1 else "")
		"texture":
			var label := CHIP_SCRIPT.part_label(part)
			return label + (" · source only" if str(part.get("source_only", "")) == "true" else "")
		"shape_key":
			return "%s · %s" % [part.get("shape_key", ""), part.get("object", "")]
		"bone":
			return "%s · %s" % [part.get("bone", ""), part.get("object", "")]
	return str(part.get("label", ""))


func _on_item_selected() -> void:
	var item := tree.get_selected()
	if item == null or not item.get_metadata(0) is Dictionary:
		return
	tree.scroll_to_item(item)
	var target := CHIP_SCRIPT.for_part(item.get_metadata(0))
	show_part(target)
	part_selected.emit(target)


## A click on the model picks the material there; a picked map of that material stays picked.
func _on_model_clicked(hit: Dictionary) -> void:
	var material := str(hit.get("material", ""))
	var part := {}
	if str(_target.get("material", "")) == material and _target.get("part_kind") == "texture":
		part = find_part(material, str(_target.get("image", "")))
	if part.is_empty():
		part = find_part(material)
	if part.is_empty():
		return
	select_part(str(part.get("id", "")))
	part = part.duplicate()
	part["object"] = str(hit.get("object", ""))
	var target := CHIP_SCRIPT.for_part(part)
	show_part(target)
	part_selected.emit(target)


## Show [param target]'s map beside the model and tint its material there; {} clears both.
func show_part(target: Dictionary) -> void:
	_target = target
	if is_instance_valid(_viewer):
		_viewer.call("highlight", str(target.get("material", "")))
	var map := map_image(target)
	map_view.visible = map != null
	_map_picture.texture = ImageTexture.create_from_image(map) if map != null else null
	if map != null:
		_map_caption.text = "%s · %d × %d" % [target["image"], map.get_width(), map.get_height()]


## The exact source pixels Joinery exported for a map (not Godot's copy); one channel of a
## packed map (ORM Green) shows as grey.
static func map_image(target: Dictionary) -> Image:
	var texture := str(target.get("texture", ""))
	if str(target.get("part_kind", "")) != "texture" or texture.is_empty():
		return null
	var image := load_image("res://" + texture)
	var channel := ["Red", "Green", "Blue", "Alpha"].find(str(target.get("channel", "")))
	if image == null or channel < 0:
		return image
	image.convert(Image.FORMAT_RGBA8)
	var grey := Image.create(image.get_width(), image.get_height(), false, Image.FORMAT_L8)
	for y: int in image.get_height():
		for x: int in image.get_width():
			var value: float = image.get_pixel(x, y)[channel]
			grey.set_pixel(x, y, Color(value, value, value))
	return grey


## The image at [param path] (also the merged layer of a Krita or OpenRaster file), or null.
static func load_image(path: String) -> Image:
	var extension := path.get_extension().to_lower()
	if extension in IMAGES:
		var image := Image.load_from_file(ProjectSettings.globalize_path(path))
		return image if image != null and not image.is_empty() else null
	if extension not in ["kra", "ora"]:
		return null
	var archive := ZIPReader.new()
	if archive.open(ProjectSettings.globalize_path(path)) != OK:
		return null
	var layered := Image.new()
	var error := layered.load_png_from_buffer(archive.read_file("mergedimage.png"))
	archive.close()
	return layered if error == OK else null


## The first non-empty line of [param text], trimmed.
## JSON from a command's output, or null. Unlike JSON.parse_string it logs
## nothing when the output is empty or not JSON.
static func parse_json(text: String) -> Variant:
	var json := JSON.new()
	return json.data if json.parse(text) == OK else null


static func first_line(text: String) -> String:
	for line: String in text.split("\n"):
		if not line.strip_edges().is_empty():
			return line.strip_edges()
	return ""


## Dragging a row carries the part; the sidebar adds it as a target when dropped there.
func _drag_part(at: Vector2) -> Variant:
	var item := tree.get_item_at_position(at)
	if item == null or not item.get_metadata(0) is Dictionary:
		return null
	var part: Dictionary = item.get_metadata(0)
	var target := CHIP_SCRIPT.for_part(part)
	var preview := UI_COMPONENTS.chip(str(target["label"]))
	tree.set_drag_preview(preview)
	return {"type": "joinery_part", "part": target}


## The list in Joinery's colours: a sunken panel, no guide lines, the accent tint for the
## picked row, muted group titles.
func _style_tree() -> void:
	var colors := UI_THEME.palette(self)
	var pad := UI_THEME.space(self, 2)
	var outer := UI_THEME.radius(self, "md")
	var panel := StyleBoxFlat.new()
	panel.bg_color = colors["surface_sunken"]
	panel.border_color = colors["border_subtle"]
	panel.set_border_width_all(1)
	panel.set_corner_radius_all(outer)
	panel.set_content_margin_all(pad)
	tree.add_theme_stylebox_override("panel", panel)
	tree.add_theme_stylebox_override("focus", StyleBoxEmpty.new())
	var row := StyleBoxFlat.new()
	row.set_corner_radius_all(roundi(UI_THEME.nested(outer, pad)))
	row.set_content_margin_all(0)
	for state: Array in [["selected", "accent_tint"], ["selected_focus", "accent_tint"]]:
		var box := row.duplicate() as StyleBoxFlat
		box.bg_color = colors[state[1]]
		tree.add_theme_stylebox_override(state[0], box)
	var hover := row.duplicate() as StyleBoxFlat
	hover.bg_color = colors["surface_raised"]
	tree.add_theme_stylebox_override("hovered", hover)
	tree.add_theme_stylebox_override("hovered_dimmed", hover)
	for key: String in ["font_color", "font_selected_color", "font_hovered_color"]:
		tree.add_theme_color_override(key, colors["text"])
	for key: String in ["guide_color", "relationship_line_color", "children_hl_line_color"]:
		tree.add_theme_color_override(key, Color.TRANSPARENT)
	tree.add_theme_constant_override("draw_guides", 0)
	tree.add_theme_constant_override("draw_relationship_lines", 0)
	tree.add_theme_constant_override("v_separation", UI_THEME.space(self, 1))
	var root := tree.get_root()
	for heading: TreeItem in root.get_children() if root != null else []:
		heading.set_custom_color(0, colors["text_muted"])


func _update_state() -> void:
	var loading := _job != null
	_loading.visible = loading
	_indicator.running = loading
	_error_box.visible = not _error.is_empty()
	_error_label.text = "Couldn't list the parts. " + _error
	tree.visible = not loading and _error.is_empty() and not _report.is_empty()
