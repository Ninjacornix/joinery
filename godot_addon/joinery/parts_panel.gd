@tool
extends VBoxContainer
## The parts of one asset as a compact list: its files (the source, the game model, loose
## files), then, for a model made in Blender, its materials with their maps inside, objects,
## shape keys and bones, as the owning tool names them (`joinery parts <asset> --json`, read
## from the native source, so maps the game copy cannot carry, such as height, are listed
## too). Groups start closed with a count ("8 materials"); a material shows a swatch of its
## base colour and its maps show as thumbnails once opened. Numbered copies of a file
## ("albedo_001.png") are left out.
##
## Click a file to view it; click a part, or the model in the preview, to make the part the
## message target and tint it on the model; drag a part onto the message to add it. A picked
## map shows in [member map_view] (the workspace places it). The model's actions are listed
## and played by [member animations] (animation_browser.gd), placed under the preview.

## The user picked a part: a message target from target_chip.for_part (label, tooltip, ids).
signal part_selected(target: Dictionary)
## The parts report arrived (or was set).
signal parts_loaded(report: Dictionary)
## The user picked one of the asset's files (project-relative) to view.
signal file_selected(path: String)

const COMMAND_SCRIPT := preload("command_job.gd")
const INDICATOR_SCRIPT := preload("activity_indicator.gd")
const CHIP_SCRIPT := preload("target_chip.gd")
const ANIMATIONS_SCRIPT := preload("animation_browser.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const WORDING := preload("asset_wording.gd")
const IMAGES := ["png", "jpg", "jpeg", "webp", "bmp", "tga", "svg"]
## Files the preview shows as the game's model.
const MODELS := ["glb", "gltf"]
## Part groups in list order: [part kind, one, many].
const GROUPS := [
	["material", "material", "materials"],
	["object", "object", "objects"],
	["shape_key", "shape key", "shape keys"],
	["bone", "bone", "bones"],
]
## Loose image files beyond this many fold into a closed "N textures" group.
const LOOSE_IMAGES := 3
## Under the preview the list is as tall as its rows, up to this many ems; map preview width
## and thumbnail side, in ems too.
const LIST_EM := 15.0
const MAP_EM := 12.0
const THUMB_EM := 1.15

var tree: Tree
## The picked map; hidden unless a map is picked.
var map_view: VBoxContainer
## The model's animations under the preview; hidden when it has none.
var animations: ANIMATIONS_SCRIPT
## Beside the preview the list fills its column; under it, it fits its rows.
var fill := false:
	set = set_fill
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
## Part id → its row.
var _items: Dictionary = {}
var _error := ""
## The asset (index entry) whose files are listed, the file being viewed, and whether it is a
## model made in Blender (which has parts).
var _owner: Dictionary = {}
var _viewing := ""
var _model := false
## Rows the user opened (group or material keys), kept across rebuilds.
var _open: Dictionary = {}
## Image path → thumbnail (null when it can't be read).
var _thumbs: Dictionary = {}
## Selecting rows from code doesn't count as the user's pick.
var _quiet := false


func _init() -> void:
	theme_type_variation = "JoineryTight"
	tree = Tree.new()
	tree.hide_root = true
	tree.columns = 2
	tree.select_mode = Tree.SELECT_ROW
	tree.accessibility_name = "Parts"
	tree.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tree.set_column_expand(0, true)
	tree.set_column_expand(1, false)
	tree.set_column_clip_content(0, true)
	tree.item_selected.connect(_on_item_selected)
	tree.item_collapsed.connect(_on_item_collapsed)
	tree.draw.connect(func() -> void: _fit.call_deferred(), CONNECT_ONE_SHOT)
	tree.set_drag_forwarding(_drag_part, Callable(), Callable())
	tree.hide()
	add_child(tree)
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
	resized.connect(func() -> void: _fit.call_deferred())


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and tree != null:
		var em := UI_THEME.font_size(self, "body")
		map_view.custom_minimum_size.x = roundf(em * MAP_EM)
		_map_picture.custom_minimum_size = Vector2.ONE * roundf(em * MAP_EM)
		_thumbs.clear()
		_style_tree()
		_rebuild()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.cancel()
		_job = null
	elif what == NOTIFICATION_PREDELETE and is_instance_valid(map_view):
		for view: Control in [map_view, animations]:
			if is_instance_valid(view) and view.get_parent() == null:
				view.free()


func set_fill(value: bool) -> void:
	fill = value
	size_flags_vertical = Control.SIZE_EXPAND_FILL if fill else Control.SIZE_FILL
	tree.size_flags_vertical = Control.SIZE_FILL
	_fit()


## List the files of [param asset] (an index entry: its source and derived parts) with
## [param viewing] the one shown, and, for a [param model] made in Blender, its parts.
## Hidden when there is nothing to list beyond the source itself.
func show_for(asset: Dictionary, viewing := "", model := false) -> void:
	var path := str(asset.get("path", ""))
	var changed := asset != _owner
	if path != str(_owner.get("path", "")):
		_open.clear()
	_owner = asset
	_viewing = viewing if not viewing.is_empty() else path
	_model = model and str(asset.get("tool", "")) == "blender"
	if not _model or str(_target.get("owner_source", path)) != path:
		show_part({})
	if _model:
		load_asset(path)
	elif not _asset.is_empty():
		if _job != null:
			_job.cancel()
			_job = null
		_asset = ""
		_report = {}
		_error = ""
		changed = true
	visible = _model or not _files().is_empty()
	if changed:
		_rebuild()
	else:
		_mark_viewing()
	_update_state()


## Picks on [param viewer] (a model_preview) select parts, and it shows the picked part.
func attach(viewer: Control) -> void:
	_viewer = viewer
	viewer.connect("part_clicked", _on_model_clicked)
	_highlight()
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
	_rebuild()
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


## Show [param report] (the `joinery parts --json` report) with the asset's files.
func set_report(report: Dictionary) -> void:
	_report = report
	_error = ""
	_rebuild()
	_update_state()
	animations.set_report(report)
	parts_loaded.emit(report)


func report() -> Dictionary:
	return _report


## Listed parts per kind: {object, material, texture, shape_key, bone} → count.
func group_counts() -> Dictionary:
	var counts := {}
	for id: String in _items:
		var kind := str((_items[id] as TreeItem).get_metadata(0).get("part_kind", ""))
		counts[kind] = int(counts.get(kind, 0)) + 1
	return counts


## The file rows' names in list order ("Source", "Model", …).
func file_names() -> Array[String]:
	var names: Array[String] = []
	var root := tree.get_root()
	if root != null:
		_collect_files(root, names)
	return names


func _collect_files(item: TreeItem, names: Array[String]) -> void:
	for child: TreeItem in item.get_children():
		var meta: Variant = child.get_metadata(0)
		if meta is Dictionary and meta.has("file"):
			names.append(child.get_text(0))
		_collect_files(child, names)


## Select the row of part [param id], opening its group, without emitting part_selected.
## False when not listed.
func select_part(id: String) -> bool:
	var item: TreeItem = _items.get(id)
	_quiet = true
	if item == null:
		tree.deselect_all()
		_quiet = false
		return false
	_reveal(item)
	tree.set_selected(item, 0)
	tree.scroll_to_item(item)
	_quiet = false
	return true


## The listed part matching a viewport pick ({object, material}), with [param image] its map.
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


# --- Rows ----------------------------------------------------------------------------------


## The asset's own files besides its source, without numbered copies.
func _files() -> Array[String]:
	var parts: Array = _owner.get("parts", [])
	var files: Array[String] = []
	for item: Variant in parts:
		var path := str(item)
		if not WORDING.is_copy(path, parts):
			files.append(path)
	return files


func _rebuild() -> void:
	if tree == null:
		return
	var picked := _row_key(tree.get_selected())
	tree.clear()
	_items.clear()
	var root := tree.create_item()
	if _owner.is_empty():
		return
	var covered := {}
	for part: Dictionary in _report.get("parts", []):
		if not str(part.get("texture", "")).is_empty():
			covered[str(part["texture"])] = true
	var owner_path := str(_owner.get("path", ""))
	_file_row(root, owner_path, "Source")
	var loose: Array[String] = []
	for path: String in _files():
		var extension := path.get_extension().to_lower()
		if covered.has(path):
			continue
		if extension in MODELS:
			_file_row(root, path, "Model")
		elif extension in IMAGES:
			loose.append(path)
		else:
			_file_row(root, path, _file_name(owner_path, path))
	var images := root
	if loose.size() > LOOSE_IMAGES:
		images = _group(root, "texture_files", loose.size(), "texture", "textures")
	for path: String in loose:
		_file_row(images, path, _file_name(owner_path, path))
	_part_rows(root)
	for item: TreeItem in root.get_children():
		if not item.collapsed:
			_load_thumbs(item)
	_quiet = true
	var again := _find_row(root, picked)
	if again != null:
		tree.set_selected(again, 0)
	_quiet = false
	_mark_viewing()
	_fit_detail(root)
	_fit()


func _part_rows(root: TreeItem) -> void:
	var parts: Array = _report.get("parts", [])
	for group: Array in GROUPS:
		var rows: Array[Dictionary] = []
		for part: Dictionary in parts:
			if str(part.get("part_kind", "")) == group[0]:
				rows.append(part)
		if rows.is_empty():
			continue
		var heading := _group(root, group[0], rows.size(), group[1], group[2])
		for part: Dictionary in rows:
			var item := _part_row(heading, part)
			if group[0] == "material":
				_map_rows(item, str(part.get("material", "")), parts)


## A material's maps inside its row; a numbered copy of a map of the same material is left
## out. The row says how many maps it holds.
func _map_rows(item: TreeItem, material: String, parts: Array) -> void:
	var maps: Array[Dictionary] = []
	var names: Array = []
	for part: Dictionary in parts:
		if str(part.get("part_kind", "")) == "texture" and str(part.get("material")) == material:
			maps.append(part)
			names.append(str(part.get("image", "")) + ".png")
	var shown := 0
	for part: Dictionary in maps:
		if WORDING.is_copy(str(part.get("image", "")) + ".png", names):
			continue
		_part_row(item, part)
		shown += 1
	if shown > 0:
		item.set_text(1, "%d map%s" % [shown, "" if shown == 1 else "s"])
	item.collapsed = not _open.has("material:" + material)


func _group(parent: TreeItem, key: String, count: int, one: String, many: String) -> TreeItem:
	var item := tree.create_item(parent)
	item.set_text(0, "%d %s" % [count, one if count == 1 else many])
	item.set_metadata(0, {"group": key})
	item.set_custom_color(0, UI_THEME.color(self, "text_muted"))
	item.collapsed = not _open.has(key)
	return item


func _file_row(parent: TreeItem, path: String, label: String) -> TreeItem:
	var item := tree.create_item(parent)
	var extension := path.get_extension().to_lower()
	item.set_text(0, label)
	item.set_tooltip_text(0, path)
	item.set_metadata(0, {"file": path})
	item.set_text(1, extension.to_upper())
	item.set_text_alignment(1, HORIZONTAL_ALIGNMENT_RIGHT)
	item.set_custom_color(1, UI_THEME.color(self, "text_muted"))
	var icon := "image" if extension in IMAGES or extension in ["kra", "ora"] else "model"
	if extension in ["ptex"]:
		icon = "material"
	item.set_icon(0, UI_THEME.icon(self, icon, UI_THEME.color(self, "text_muted")))
	item.set_icon_max_width(0, _thumb_px())
	return item


func _part_row(parent: TreeItem, part: Dictionary) -> TreeItem:
	var item := tree.create_item(parent)
	item.set_text(0, _row_text(part))
	item.set_tooltip_text(0, CHIP_SCRIPT.part_tooltip(part))
	item.set_metadata(0, part)
	item.set_text_alignment(1, HORIZONTAL_ALIGNMENT_RIGHT)
	item.set_custom_color(1, UI_THEME.color(self, "text_muted"))
	item.set_text(1, _row_detail(part))
	var kind := str(part.get("part_kind", ""))
	if kind in ["material", "texture"]:
		var icon := "material" if kind == "material" else "image"
		item.set_icon(0, UI_THEME.icon(self, icon, UI_THEME.color(self, "text_muted")))
		item.set_icon_max_width(0, _thumb_px())
	_items[str(part.get("id", ""))] = item
	return item


## "Aged grey trim" (a material, in words); "Roughness" (a map, inside its material);
## Blender's own names for objects, shape keys and bones ("Head.001").
func _row_text(part: Dictionary) -> String:
	match str(part.get("part_kind", "")):
		"object":
			return str(part.get("object", ""))
		"material":
			return WORDING.readable(str(part.get("material", "")))
		"texture":
			return CHIP_SCRIPT._map_name(part)
		"shape_key":
			return str(part.get("shape_key", ""))
		"bone":
			return str(part.get("bone", ""))
	return str(part.get("label", ""))


## The muted right-hand detail: "on 2 objects", "Source only", the object of a shape key.
func _row_detail(part: Dictionary) -> String:
	match str(part.get("part_kind", "")):
		"material":
			var users := str(part.get("used_by", "")).split(",", false)
			return "on %d objects" % users.size() if users.size() > 1 else ""
		"texture":
			return "Source only" if str(part.get("source_only", "")) == "true" else ""
		"shape_key", "bone":
			return str(part.get("object", ""))
	return ""


## A derived file's name in words, without the asset's own name in front ("Albedo").
func _file_name(owner: String, part: String) -> String:
	var base := owner.get_file().get_basename()
	var name := part.get_file().get_basename()
	var trimmed := name.trim_prefix(base).lstrip("_-. ")
	return WORDING.readable(trimmed if not trimmed.is_empty() else name)


## The viewed file's row carries the eye.
func _mark_viewing() -> void:
	var root := tree.get_root() if tree != null else null
	if root == null:
		return
	var eye := UI_THEME.icon(self, "view", UI_THEME.color(self, "accent"))
	for item: TreeItem in _all_rows(root):
		var meta: Variant = item.get_metadata(0)
		if meta is Dictionary and meta.has("file"):
			var viewing := str(meta["file"]) == _viewing
			item.set_icon(1, eye if viewing else null)
			item.set_icon_max_width(1, _thumb_px())
			item.set_custom_color(0, UI_THEME.color(self, "text" if viewing else "text_muted"))


func _all_rows(item: TreeItem) -> Array[TreeItem]:
	var rows: Array[TreeItem] = []
	for child: TreeItem in item.get_children():
		rows.append(child)
		rows.append_array(_all_rows(child))
	return rows


## "file:<path>", "part:<id>" or "" for a row, to find it again after a rebuild.
func _row_key(item: TreeItem) -> String:
	if item == null or not item.get_metadata(0) is Dictionary:
		return ""
	var meta: Dictionary = item.get_metadata(0)
	if meta.has("file"):
		return "file:" + str(meta["file"])
	if meta.has("part_kind"):
		return "part:" + str(meta.get("id", ""))
	return ""


func _find_row(root: TreeItem, key: String) -> TreeItem:
	if key.is_empty():
		return null
	for item: TreeItem in _all_rows(root):
		if _row_key(item) == key:
			return item
	return null


## Open the groups above [param item] so it shows.
func _reveal(item: TreeItem) -> void:
	var parent := item.get_parent()
	while parent != null and parent != tree.get_root():
		if parent.collapsed:
			parent.collapsed = false
		parent = parent.get_parent()


# --- Thumbnails ----------------------------------------------------------------------------


func _thumb_px() -> int:
	return roundi(UI_THEME.font_size(self, "body") * THUMB_EM)


## Swatches for the materials and thumbnails for the maps and images directly in [param item].
func _load_thumbs(item: TreeItem) -> void:
	for child: TreeItem in item.get_children():
		var meta: Variant = child.get_metadata(0)
		if not meta is Dictionary:
			continue
		var path := ""
		if meta.has("file") and str(meta["file"]).get_extension().to_lower() in IMAGES:
			path = str(meta["file"])
		elif str(meta.get("part_kind", "")) == "texture":
			path = str(meta.get("texture", ""))
		elif str(meta.get("part_kind", "")) == "material":
			path = _swatch_path(child)
		var picture := _thumb(path)
		if picture != null:
			child.set_icon(0, picture)


## A material's swatch: its base colour map, or its first map.
func _swatch_path(material: TreeItem) -> String:
	var first := ""
	for map: TreeItem in material.get_children():
		var part: Dictionary = map.get_metadata(0)
		var texture := str(part.get("texture", ""))
		if texture.is_empty() or str(part.get("source_only", "")) == "true":
			continue
		if "base_color" in str(part.get("roles", part.get("role", ""))).split(","):
			return texture
		if first.is_empty():
			first = texture
	return first


## A small square of the image at [param path] with rounded corners, or null.
func _thumb(path: String) -> Texture2D:
	if path.is_empty():
		return null
	if _thumbs.has(path):
		return _thumbs[path]
	var picture: Texture2D = null
	var image := load_image("res://" + path)
	if image != null:
		var side := _thumb_px()
		image.convert(Image.FORMAT_RGBA8)
		image.resize(side, side, Image.INTERPOLATE_BILINEAR)
		_round_corners(image, maxf(2.0, side / 5.0))
		picture = ImageTexture.create_from_image(image)
	_thumbs[path] = picture
	return picture


static func _round_corners(image: Image, radius: float) -> void:
	var side := float(image.get_width())
	for y: int in image.get_height():
		for x: int in image.get_width():
			var at := Vector2(x + 0.5, y + 0.5)
			var centre := at.clamp(Vector2.ONE * radius, Vector2.ONE * (side - radius))
			var distance := at.distance_to(centre)
			if distance > radius - 1.0:
				var pixel := image.get_pixel(x, y)
				pixel.a *= clampf(radius - distance + 0.5, 0.0, 1.0)
				image.set_pixel(x, y, pixel)


# --- Picking -------------------------------------------------------------------------------


func _on_item_selected() -> void:
	if _quiet:
		return
	var item := tree.get_selected()
	if item == null or not item.get_metadata(0) is Dictionary:
		return
	var meta: Dictionary = item.get_metadata(0)
	if meta.has("group"):
		# A group title opens or closes; the picked part stays picked.
		item.collapsed = not item.collapsed
		_quiet = true
		var picked := _find_row(tree.get_root(), _picked_key())
		if picked != null:
			tree.set_selected(picked, 0)
		else:
			item.deselect(0)
		_quiet = false
		return
	if meta.has("file"):
		file_selected.emit(str(meta["file"]))
		return
	tree.scroll_to_item(item)
	var target := CHIP_SCRIPT.for_part(meta)
	show_part(target)
	part_selected.emit(target)


func _picked_key() -> String:
	return "part:" + str(_target.get("id", "")) if not _target.is_empty() else ""


func _on_item_collapsed(item: TreeItem) -> void:
	var meta: Variant = item.get_metadata(0)
	var key := ""
	if meta is Dictionary and meta.has("group"):
		key = str(meta["group"])
	elif meta is Dictionary and str(meta.get("part_kind", "")) == "material":
		key = "material:" + str(meta.get("material", ""))
	if not key.is_empty():
		if item.collapsed:
			_open.erase(key)
		else:
			_open[key] = true
	if not item.collapsed:
		_load_thumbs(item)
	_fit.call_deferred()


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


## Show [param target]'s map and tint its material (or its object) on the model; {} clears.
func show_part(target: Dictionary) -> void:
	_target = target
	_highlight()
	var map := map_image(target)
	map_view.visible = map != null
	_map_picture.texture = ImageTexture.create_from_image(map) if map != null else null
	if map != null:
		_map_caption.text = "%s · %d × %d" % [target["image"], map.get_width(), map.get_height()]


func _highlight() -> void:
	if not is_instance_valid(_viewer):
		return
	var kind := str(_target.get("part_kind", ""))
	var object := str(_target.get("object", "")) if kind == "object" else ""
	_viewer.call("highlight", str(_target.get("material", "")), object)


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


## JSON from a command's output, or null. Unlike JSON.parse_string it logs
## nothing when the output is empty or not JSON.
static func parse_json(text: String) -> Variant:
	var json := JSON.new()
	return json.data if json.parse(text) == OK else null


## The first non-empty line of [param text], trimmed.
static func first_line(text: String) -> String:
	for line: String in text.split("\n"):
		if not line.strip_edges().is_empty():
			return line.strip_edges()
	return ""


## Dragging a part row carries the part; the sidebar adds it as a target when dropped there.
func _drag_part(at: Vector2) -> Variant:
	var item := tree.get_item_at_position(at)
	if item == null or not item.get_metadata(0) is Dictionary:
		return null
	var part: Dictionary = item.get_metadata(0)
	if not part.has("part_kind"):
		return null
	var target := CHIP_SCRIPT.for_part(part)
	var preview := UI_COMPONENTS.chip(str(target["label"]))
	tree.set_drag_preview(preview)
	return {"type": "joinery_part", "part": target}


# --- Look ----------------------------------------------------------------------------------


## The list in Joinery's colours: a sunken panel, no guide lines, the accent tint for the
## picked row, muted group titles and details.
func _style_tree() -> void:
	var colors := UI_THEME.palette(self)
	var pad := UI_THEME.space(self, 1)
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
	tree.add_theme_constant_override("h_separation", UI_THEME.space(self, 2))
	tree.add_theme_constant_override("inner_item_margin_left", UI_THEME.space(self, 1))
	tree.add_theme_constant_override("inner_item_margin_right", UI_THEME.space(self, 1))


## Under the preview the list is as tall as its open rows, up to LIST_EM; beside it, it fills
## the column.
func _fit() -> void:
	if tree == null or not is_inside_tree():
		return
	var em := UI_THEME.font_size(self, "body")
	var rows := 0
	var root := tree.get_root()
	if root != null:
		rows = _open_rows(root)
	var font := tree.get_theme_font("font")
	var font_px := tree.get_theme_font_size("font_size")
	var row := maxf(font.get_height(font_px), _thumb_px()) + tree.get_theme_constant("v_separation")
	# Once drawn, the tree's own row pitch (it adds item margins of its own).
	if root != null and root.get_child_count() > 1:
		var pitch := (
			tree.get_item_area_rect(root.get_child(1)).position.y
			- tree.get_item_area_rect(root.get_child(0)).position.y
		)
		row = pitch if pitch > 0.0 else row
	var panel := tree.get_theme_stylebox("panel")
	var height := roundf(rows * row + panel.get_minimum_size().y + UI_THEME.space(self, 1))
	if not fill:
		tree.custom_minimum_size.y = minf(height, roundf(em * LIST_EM))
		return
	# Beside the preview: as tall as the rows while they fit the column, then the column.
	var fits := height <= size.y
	var flags := Control.SIZE_FILL if fits else Control.SIZE_EXPAND_FILL
	var tall := height if fits else roundf(em * 6.0)
	if tree.size_flags_vertical != flags or tree.custom_minimum_size.y != tall:
		tree.size_flags_vertical = flags
		tree.custom_minimum_size.y = tall


## The detail column as wide as its widest text (and the eye beside a file's type).
func _fit_detail(root: TreeItem) -> void:
	var font := tree.get_theme_font("font")
	var size := tree.get_theme_font_size("font_size")
	var widest := 0.0
	for item: TreeItem in _all_rows(root):
		var text := item.get_text(1)
		if not text.is_empty():
			widest = maxf(widest, font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	var extra := _thumb_px() + UI_THEME.space(self, 6)
	tree.set_column_custom_minimum_width(1, ceili(widest + extra) if widest > 0.0 else 0)


func _open_rows(item: TreeItem) -> int:
	var count := 0
	for child: TreeItem in item.get_children():
		count += 1
		if not child.collapsed:
			count += _open_rows(child)
	return count


func _update_state() -> void:
	var loading := _job != null
	_loading.visible = loading
	_indicator.running = loading
	_error_box.visible = not _error.is_empty()
	_error_label.text = "Couldn't list the parts. " + _error
	tree.visible = tree.get_root() != null and tree.get_root().get_child_count() > 0
