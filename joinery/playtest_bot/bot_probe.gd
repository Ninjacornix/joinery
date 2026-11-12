extends RefCounted
## Measures a running game for `joinery perf` and `joinery access` while the playtest bot
## plays it (--probe=perf|access, and --luma for flash measurement in a window).
##
## perf: Godot's monitors every half second (draw calls, primitives, video and texture
## memory, physics, process times, pipeline compilations), how long the first scene took
## to load, a census of the running scene (meshes and their repeats, lights and shadows,
## physics bodies and layers, particles, visibility ranges, textures in use) and, at the
## end, which scripts cost the most: each script's nodes stop processing for a few frames
## and the process time they free is that script's cost.
## access: every visible text and focusable control on the HUD during play and on every
## menu and settings page: its text, font size and colour, what it sits on, its accessible
## name and where its colour and size come from (a scene, a theme resource or code).
## luma: each rendered frame's relative luminance, whole and per quarter of the screen.
## It only reads; joinery/perf.py and joinery/access.py judge the numbers.

const SAMPLE_EVERY := 30
const TIMED_FRAMES := 31
const HOT_SCRIPTS := 16
const CENSUS_LIMIT := 40
const UI_LIMIT := 160
## The pseudolocalised pass: text 30 % longer, wrapped in right-to-left marks, as a
## translation into German or Arabic would stretch and turn it.
const EXPANSION := 0.3
const PROBLEMS_PER_SCREEN := 24

var tree: SceneTree
var kind: String = ""
var luma: bool = false
var samples: Array[Dictionary] = []
var luma_trace: Array = []
var load_ms: int = -1
var census: Dictionary = {}
var hot: Array[Dictionary] = []
var hud: Array[Dictionary] = []
var pages: Array[Dictionary] = []
## access: the pseudolocalised pass ({ran, expansion, fake_bidi, screens: [{page, checked,
## shot, problems: [{node, scene, type, text, shown, problem, rect}]}]}).
var long_text: Dictionary = {}
var _boot_usec: int = 0
var _hud_at: int = -1
## Rendered frames so far: each is 1/60 s of game time under --fixed-fps 60.
var _drawn: int = 0


func _init(on: SceneTree, probe_kind: String, with_luma: bool, total_frames: int) -> void:
	tree = on
	kind = probe_kind
	luma = with_luma
	_hud_at = maxi(int(total_frames * 0.4), 120)


func boot_started() -> void:
	_boot_usec = Time.get_ticks_usec()


## One physics frame of play: samples, the first scene's load time, the HUD.
func tick(frame: int, paused: bool, menu_open: bool) -> void:
	if load_ms < 0 and tree.current_scene != null and _boot_usec > 0:
		load_ms = int((Time.get_ticks_usec() - _boot_usec) / 1000)
	if kind == "perf" and frame % SAMPLE_EVERY == 0:
		samples.append(_sample(frame))
	if kind == "access" and hud.is_empty() and frame >= _hud_at and not paused and not menu_open:
		hud = _controls(tree.root, "hud")


## Once per rendered frame: the picture's luminance (only with --luma, in a window).
func drawn(_frame: int) -> void:
	if not luma or DisplayServer.get_name() == "headless":
		return
	_drawn += 1
	var image := tree.root.get_texture().get_image()
	if image == null or image.is_empty():
		return
	image.resize(16, 8, Image.INTERPOLATE_BILINEAR)
	var quarters: Array[float] = [0.0, 0.0, 0.0, 0.0]
	for y: int in 8:
		for x: int in 16:
			var c := image.get_pixel(x, y)
			var lum := 0.2126 * _linear(c.r) + 0.7152 * _linear(c.g) + 0.0722 * _linear(c.b)
			quarters[int(y >= 4) * 2 + int(x >= 8)] += lum / 32.0
	var whole: float = (quarters[0] + quarters[1] + quarters[2] + quarters[3]) / 4.0
	luma_trace.append([_drawn, snappedf(whole, 0.001)] + _snapped(quarters))


## After play: the census and hot scripts (perf), or the menus (access).
func finish() -> void:
	if kind == "perf":
		census = _census()
		await _hot_scripts()
	elif kind == "access":
		if hud.is_empty():
			hud = _controls(tree.root, "hud")
		await _menus()
		await _long_text()


func result() -> Dictionary:
	return {
		"kind": kind,
		"samples": samples,
		"load_ms": load_ms,
		"census": census,
		"hot": hot,
		"hud": hud,
		"pages": pages,
		"long_text": long_text,
		"luma": luma_trace,
		"headless": DisplayServer.get_name() == "headless",
		"viewport":
		[
			int(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
			int(ProjectSettings.get_setting("display/window/size/viewport_height", 648)),
		],
		"autoloads": _autoloads(),
	}


func _sample(frame: int) -> Dictionary:
	var p := Performance
	return {
		"frame": frame,
		"draw_calls": int(p.get_monitor(p.RENDER_TOTAL_DRAW_CALLS_IN_FRAME)),
		"primitives": int(p.get_monitor(p.RENDER_TOTAL_PRIMITIVES_IN_FRAME)),
		"objects": int(p.get_monitor(p.RENDER_TOTAL_OBJECTS_IN_FRAME)),
		"video_mb": snappedf(p.get_monitor(p.RENDER_VIDEO_MEM_USED) / 1048576.0, 0.1),
		"texture_mb": snappedf(p.get_monitor(p.RENDER_TEXTURE_MEM_USED) / 1048576.0, 0.1),
		"static_mb": snappedf(OS.get_static_memory_usage() / 1048576.0, 0.1),
		"nodes": int(p.get_monitor(p.OBJECT_NODE_COUNT)),
		"physics_3d": int(p.get_monitor(p.PHYSICS_3D_ACTIVE_OBJECTS)),
		"physics_2d": int(p.get_monitor(p.PHYSICS_2D_ACTIVE_OBJECTS)),
		"process_max_ms": snappedf(p.get_monitor(p.TIME_PROCESS) * 1000.0, 0.01),
		"physics_max_ms": snappedf(p.get_monitor(p.TIME_PHYSICS_PROCESS) * 1000.0, 0.01),
		"compilations":
		(
			int(p.get_monitor(p.PIPELINE_COMPILATIONS_CANVAS))
			+ int(p.get_monitor(p.PIPELINE_COMPILATIONS_MESH))
			+ int(p.get_monitor(p.PIPELINE_COMPILATIONS_SURFACE))
			+ int(p.get_monitor(p.PIPELINE_COMPILATIONS_DRAW))
			+ int(p.get_monitor(p.PIPELINE_COMPILATIONS_SPECIALIZATION))
		),
	}


# --- perf: the scene -------------------------------------------------------------------


func _census() -> Dictionary:
	var found := {
		"nodes": 0,
		"meshes": 0,
		"triangles": 0,
		"repeats": {},
		"lights": [],
		"bodies": 0,
		"rigid": 0,
		"all_layers": [],
		"particles": 0,
		"ranged": 0,
		"multimesh": 0,
		"textures": {},
	}
	_walk(tree.root, found)
	var repeats: Array = []
	for key: String in found["repeats"]:
		var entry: Dictionary = found["repeats"][key]
		if int(entry["count"]) >= 2:
			repeats.append(entry)
	repeats.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["count"] > b["count"])
	found["repeats"] = repeats.slice(0, CENSUS_LIMIT)
	var textures: Array = (found["textures"] as Dictionary).values()
	textures.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["mb"] > b["mb"])
	var total := 0.0
	for texture: Dictionary in textures:
		total += float(texture["mb"])
	found["texture_mb_est"] = snappedf(total, 0.1)
	found["textures"] = textures.slice(0, CENSUS_LIMIT)
	found["lights"] = (found["lights"] as Array).slice(0, CENSUS_LIMIT)
	found["all_layers"] = (found["all_layers"] as Array).slice(0, CENSUS_LIMIT)
	return found


func _walk(node: Node, found: Dictionary) -> void:
	found["nodes"] = int(found["nodes"]) + 1
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		var mesh_node := node as MeshInstance3D
		found["meshes"] = int(found["meshes"]) + 1
		var tris := _triangles(mesh_node.mesh)
		found["triangles"] = int(found["triangles"]) + tris
		if mesh_node.visibility_range_end > 0.0:
			found["ranged"] = int(found["ranged"]) + 1
		var key := _mesh_key(mesh_node.mesh)
		var repeats: Dictionary = found["repeats"]
		if not repeats.has(key):
			repeats[key] = {"mesh": key, "count": 0, "triangles": tris, "nodes": []}
		var entry: Dictionary = repeats[key]
		entry["count"] = int(entry["count"]) + 1
		if (entry["nodes"] as Array).size() < 24:
			(entry["nodes"] as Array).append(_where(node))
		_materials(mesh_node, found)
	elif node is MultiMeshInstance3D or node is MultiMeshInstance2D:
		found["multimesh"] = int(found["multimesh"]) + 1
	elif node is Light3D:
		var light := node as Light3D
		var entry := _where(node)
		entry["type"] = light.get_class()
		entry["shadow"] = light.shadow_enabled
		(found["lights"] as Array).append(entry)
	elif node is GPUParticles3D or node is GPUParticles2D or node is CPUParticles3D:
		found["particles"] = int(found["particles"]) + int(node.get(&"amount"))
	if node is CollisionObject3D or node is CollisionObject2D:
		found["bodies"] = int(found["bodies"]) + 1
		if node is RigidBody3D or node is RigidBody2D:
			found["rigid"] = int(found["rigid"]) + 1
		var mask := int(node.get(&"collision_mask"))
		if mask == 0xFFFFFFFF or (mask & 0xFFFF) == 0xFFFF:
			var entry := _where(node)
			entry["mask"] = mask
			(found["all_layers"] as Array).append(entry)
	if node is Sprite2D or node is TextureRect:
		_texture(node.get(&"texture") as Texture2D, found)
	for child: Node in node.get_children():
		_walk(child, found)


func _materials(node: MeshInstance3D, found: Dictionary) -> void:
	var materials: Array[Material] = []
	if node.material_override != null:
		materials.append(node.material_override)
	for surface: int in node.mesh.get_surface_count():
		var material := node.get_active_material(surface)
		if material != null and not material in materials:
			materials.append(material)
	for material: Material in materials:
		for property: Dictionary in material.get_property_list():
			if int(property["type"]) == TYPE_OBJECT:
				var value: Variant = material.get(str(property["name"]))
				if value is Texture2D:
					_texture(value as Texture2D, found)


func _texture(texture: Texture2D, found: Dictionary) -> void:
	if texture == null:
		return
	var key := texture.resource_path if not texture.resource_path.is_empty() else str(texture)
	var textures: Dictionary = found["textures"]
	if textures.has(key):
		return
	var w := texture.get_width()
	var h := texture.get_height()
	# Four bytes a texel and a third more for mipmaps: an upper bound before compression.
	textures[key] = {
		"path": texture.resource_path,
		"size": [w, h],
		"mb": snappedf(w * h * 4 * 1.33 / 1048576.0, 0.01),
	}


static func _triangles(mesh: Mesh) -> int:
	var count := 0
	for surface: int in mesh.get_surface_count():
		var arrays := mesh.surface_get_arrays(surface)
		if arrays.is_empty():
			continue
		var indices: Variant = arrays[Mesh.ARRAY_INDEX]
		if indices is PackedInt32Array and not (indices as PackedInt32Array).is_empty():
			count += (indices as PackedInt32Array).size() / 3
		else:
			count += (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3
	return count


static func _mesh_key(mesh: Mesh) -> String:
	if not mesh.resource_path.is_empty():
		return mesh.resource_path
	return "%s#%d" % [mesh.get_class(), mesh.get_instance_id()]


## The scene file that holds [param node] and its path there, for findings and fixes.
static func _where(node: Node) -> Dictionary:
	var owner_node := node.owner
	var scene := ""
	var path := str(node.get_path())
	if owner_node != null and not owner_node.scene_file_path.is_empty():
		scene = owner_node.scene_file_path
		path = str(owner_node.get_path_to(node))
	elif not node.scene_file_path.is_empty():
		scene = node.scene_file_path
		path = "."
	return {"scene": scene, "node": path, "name": str(node.name)}


## Each processing script's cost: its nodes stop for a few frames; the freed time is it.
func _hot_scripts() -> void:
	var groups: Dictionary = {}
	_group_scripts(tree.root, groups)
	var order: Array = groups.keys()
	order.sort_custom(
		func(a: String, b: String) -> bool:
			return (groups[a] as Array).size() > (groups[b] as Array).size()
	)
	var baseline := await _timed()
	for path: String in order.slice(0, HOT_SCRIPTS):
		var nodes: Array = groups[path]
		var states: Array = []
		for node: Node in nodes:
			states.append([node.is_processing(), node.is_physics_processing()])
			node.set_process(false)
			node.set_physics_process(false)
		var without := await _timed()
		for index: int in nodes.size():
			var node: Node = nodes[index]
			if is_instance_valid(node):
				node.set_process(bool(states[index][0]))
				node.set_physics_process(bool(states[index][1]))
		var first: Node = nodes[0]
		var cost := {
			"script": path,
			"nodes": nodes.size(),
			"ms": snappedf(maxf(0.0, baseline - without), 0.01),
			"example": _where(first) if is_instance_valid(first) else {},
		}
		hot.append(cost)
		baseline = await _timed()
	hot.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["ms"] > b["ms"])


func _group_scripts(node: Node, groups: Dictionary) -> void:
	var script := node.get_script() as Script
	if (
		script != null
		and script.resource_path.begins_with("res://")
		and not script.resource_path.begins_with("res://addons/")
		and (node.is_processing() or node.is_physics_processing())
	):
		if not groups.has(script.resource_path):
			groups[script.resource_path] = []
		(groups[script.resource_path] as Array).append(node)
	for child: Node in node.get_children():
		_group_scripts(child, groups)


## The median real time of a frame (ms) over a few frames. Headless nothing is drawn and
## nothing waits, so a frame takes as long as the game's own work: scripts and physics.
## (Godot's process-time monitors only change once a real second, too seldom here.)
func _timed() -> float:
	await tree.physics_frame
	var times: Array[float] = []
	var last := Time.get_ticks_usec()
	for _i: int in TIMED_FRAMES:
		await tree.physics_frame
		var now := Time.get_ticks_usec()
		times.append((now - last) / 1000.0)
		last = now
	times.sort()
	return times[times.size() / 2]


# --- access: the UI ---------------------------------------------------------------------


## Every menu the game core has: main, pause, settings and each settings page.
func _menus() -> void:
	var menus := tree.root.get_node_or_null(^"Menus")
	if menus == null or not menus.has_method(&"settings_pages"):
		return
	var game := tree.root.get_node_or_null(^"Game")
	if game != null and game.call(&"current_level") != null:
		menus.call(&"close_all")
		menus.call(&"open_pause")
		await _settle()
		pages.append(_page("pause", "Paused", menus))
	menus.call(&"close_all")
	menus.call(&"open_settings")
	await _settle()
	pages.append(_page("settings", "Settings", menus))
	for page: Dictionary in menus.call(&"settings_pages"):
		menus.call(&"close_all")
		menus.call(&"open_settings")
		menus.call(&"_open_settings_page", page["id"])
		await _settle()
		pages.append(_page(str(page["id"]), str(page["label"]), menus))
	menus.call(&"close_all")
	menus.call(&"open_main")
	await _settle()
	pages.append(_page("main", "Main menu", menus))
	menus.call(&"close_all")


func _settle() -> void:
	for _i: int in 3:
		await tree.process_frame


func _page(id: String, label: String, menus: Node) -> Dictionary:
	var top: Control = menus.call(&"current_page")
	return {
		"id": id,
		"label": label,
		"controls": _controls(top, id) if top != null else [],
	}


## Every screen again with the pseudo-locale on: text 30 % longer and in right-to-left marks.
## What runs off the screen, is cut off or hides past its box is listed with its node; in a
## window each screen is saved with those boxes outlined.
func _long_text() -> void:
	var domain := TranslationServer.get_or_add_domain(&"")
	var before := [
		domain.pseudolocalization_enabled,
		domain.pseudolocalization_expansion_ratio,
		domain.pseudolocalization_fake_bidi_enabled,
	]
	domain.pseudolocalization_expansion_ratio = EXPANSION
	domain.pseudolocalization_fake_bidi_enabled = true
	domain.pseudolocalization_enabled = true
	var screens: Array[Dictionary] = []
	var menus := tree.root.get_node_or_null(^"Menus")
	if menus != null:
		menus.call(&"close_all")
	if tree.current_scene != null:
		screens.append(await _scan(tree.current_scene, "hud"))
	if menus != null and menus.has_method(&"settings_pages"):
		var game := tree.root.get_node_or_null(^"Game")
		if game != null and game.call(&"current_level") != null:
			menus.call(&"open_pause")
			screens.append(await _scan_menu(menus, "pause"))
		menus.call(&"close_all")
		menus.call(&"open_settings")
		screens.append(await _scan_menu(menus, "settings"))
		for page: Dictionary in menus.call(&"settings_pages"):
			menus.call(&"close_all")
			menus.call(&"open_settings")
			menus.call(&"_open_settings_page", page["id"])
			screens.append(await _scan_menu(menus, str(page["id"])))
		menus.call(&"close_all")
		menus.call(&"open_main")
		screens.append(await _scan_menu(menus, "main"))
		menus.call(&"close_all")
	domain.pseudolocalization_enabled = before[0]
	domain.pseudolocalization_expansion_ratio = before[1]
	domain.pseudolocalization_fake_bidi_enabled = before[2]
	tree.root.propagate_notification(Node.NOTIFICATION_TRANSLATION_CHANGED)
	long_text = {"ran": true, "expansion": EXPANSION, "fake_bidi": true, "screens": screens}


func _scan_menu(menus: Node, page: String) -> Dictionary:
	var top: Control = menus.call(&"current_page")
	return await _scan(top, page)


func _scan(root_node: Node, page: String) -> Dictionary:
	tree.root.propagate_notification(Node.NOTIFICATION_TRANSLATION_CHANGED)
	# Containers lay the longer text out over a couple of frames.
	for _i: int in 4:
		await tree.process_frame
	var checked: Array[int] = [0]
	var problems: Array[Dictionary] = []
	if root_node != null:
		_overflows(root_node, page, checked, problems)
	var shot := ""
	if DisplayServer.get_name() != "headless" and root_node != null:
		shot = _outlined(page, problems)
	return {"page": page, "checked": checked[0], "problems": problems, "shot": shot}


func _overflows(node: Node, page: String, checked: Array[int], problems: Array[Dictionary]) -> void:
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return
	if node is Label or node is Button:
		var control := node as Control
		var text := str(control.get(&"text"))
		if not text.strip_edges().is_empty():
			checked[0] += 1
			var problem := _overflow(control, text)
			if not problem.is_empty() and problems.size() < PROBLEMS_PER_SCREEN:
				var entry := _where(control)
				var rect := control.get_global_rect()
				(
					entry
					. merge(
						{
							"page": page,
							"type": control.get_class(),
							"text": text.left(60),
							"shown": _shown(control, text).left(80),
							"problem": problem,
							"rect":
							[
								roundi(rect.position.x),
								roundi(rect.position.y),
								roundi(rect.size.x),
								roundi(rect.size.y)
							],
							"autowrap": control is Label and (control as Label).autowrap_mode != 0,
						}
					)
				)
				problems.append(entry)
	for child: Node in node.get_children():
		_overflows(child, page, checked, problems)


## "off_screen", "cut_off", "cut_below", "past_box" or "" when [param text] fits.
func _overflow(control: Control, text: String) -> String:
	var rect := control.get_global_rect()
	if not control.get_viewport_rect().encloses(rect.grow(-1.0)):
		return "off_screen"
	if control is Label:
		var label := control as Label
		if label.autowrap_mode != TextServer.AUTOWRAP_OFF:
			if label.get_line_count() > label.get_visible_line_count():
				return "cut_below"
		elif label.clip_text or label.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING:
			if _width(control, text) > control.size.x + 1.0:
				return "cut_off"
	elif control is Button:
		var button := control as Button
		if button.clip_text or button.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING:
			var box := button.get_theme_stylebox(&"normal")
			var room := button.size.x - (box.get_margin(SIDE_LEFT) + box.get_margin(SIDE_RIGHT))
			if button.icon != null:
				room -= button.icon.get_width() + button.get_theme_constant(&"h_separation")
			if _width(control, text) > room + 1.0:
				return "cut_off"
	# A clipping ancestor (a scroll area, a panel that clips) hides what runs past it.
	var parent := control.get_parent()
	while parent != null:
		if parent is Control and (parent as Control).clip_contents:
			if not (parent as Control).get_global_rect().grow(1.0).encloses(rect):
				return "past_box"
			break
		parent = parent.get_parent()
	return ""


func _shown(control: Control, text: String) -> String:
	if control.auto_translate_mode == Node.AUTO_TRANSLATE_MODE_DISABLED:
		return text
	return control.tr(text)


func _width(control: Control, text: String) -> float:
	var font := control.get_theme_font(&"font")
	var size := control.get_theme_font_size(&"font_size")
	return font.get_string_size(_shown(control, text), HORIZONTAL_ALIGNMENT_LEFT, -1, size).x


## The screen as drawn, each problem outlined, saved beside the bot's report.
func _outlined(page: String, problems: Array[Dictionary]) -> String:
	var out := str(tree.get(&"out_dir"))
	if out.is_empty():
		return ""
	var image := tree.root.get_texture().get_image()
	if image == null or image.is_empty():
		return ""
	var scale := float(image.get_width()) / maxf(tree.root.get_visible_rect().size.x, 1.0)
	var ink := Color(0.93, 0.18, 0.36)
	for problem: Dictionary in problems:
		var box: Array = problem["rect"]
		var rect := Rect2i(
			roundi(box[0] * scale),
			roundi(box[1] * scale),
			roundi(box[2] * scale),
			roundi(box[3] * scale)
		)
		rect = rect.intersection(Rect2i(Vector2i.ZERO, image.get_size()))
		if rect.size.x <= 0 or rect.size.y <= 0:
			continue
		for edge: Rect2i in [
			Rect2i(rect.position.x, rect.position.y, rect.size.x, 2),
			Rect2i(rect.position.x, rect.end.y - 2, rect.size.x, 2),
			Rect2i(rect.position.x, rect.position.y, 2, rect.size.y),
			Rect2i(rect.end.x - 2, rect.position.y, 2, rect.size.y),
		]:
			image.fill_rect(edge, ink)
	var path := out.path_join("long_text_%s.png" % page.validate_filename())
	return path if image.save_png(path) == OK else ""


func _controls(root_node: Node, where: String) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	_collect(root_node, where, found)
	return found


func _collect(node: Node, where: String, found: Array[Dictionary]) -> void:
	if found.size() >= UI_LIMIT:
		return
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return
	if node is Control:
		var entry := _control(node as Control, where)
		if not entry.is_empty():
			found.append(entry)
	for child: Node in node.get_children():
		_collect(child, where, found)


func _control(control: Control, where: String) -> Dictionary:
	var text := ""
	if control is Label or control is Button or control is LineEdit:
		text = str(control.get(&"text"))
	elif control is RichTextLabel:
		text = (control as RichTextLabel).get_parsed_text()
	var focusable := control.focus_mode == Control.FOCUS_ALL
	if text.strip_edges().is_empty() and not focusable:
		return {}
	var disabled := control is BaseButton and (control as BaseButton).disabled
	var colour_key := &"font_color" if not control is RichTextLabel else &"default_color"
	var entry := _where(control)
	(
		entry
		. merge(
			{
				"page": where,
				"type": control.get_class(),
				"text": text.left(60),
				"focusable": focusable,
				"disabled": disabled,
				"accessible": str(control.accessibility_name),
				"tooltip": control.tooltip_text.left(60),
				"label": _row_label(control),
				"size": control.get_theme_font_size(&"font_size"),
				"colour": "#" + control.get_theme_color(colour_key).to_html(false),
				"outline": control.get_theme_constant(&"outline_size"),
				"background": _background(control),
				"colour_from": _source(control, "color", colour_key),
				"size_from": _source(control, "font_size", &"font_size"),
			}
		)
	)
	return entry


## The text of a Label beside [param control] in its row (UiStyle.row and the like).
static func _row_label(control: Control) -> String:
	var parent := control.get_parent()
	if parent == null or not (parent is HBoxContainer or parent is GridContainer):
		return ""
	for sibling: Node in parent.get_children():
		if sibling is Label and sibling != control:
			return str((sibling as Label).text).left(60)
	return ""


## The colour [param control]'s text sits on: its own normal box (a button), then the
## nearest panel or ColorRect behind it, blended over what is behind that; "" = the game.
func _background(control: Control) -> String:
	var layers: Array[Color] = []
	if control is Button:
		var own := control.get_theme_stylebox(&"normal") as StyleBoxFlat
		if own != null and own.draw_center:
			layers.append(own.bg_color)
	var node: Node = control
	while node.get_parent() != null and (layers.is_empty() or layers.back().a < 0.999):
		var parent := node.get_parent()
		# A ColorRect drawn before this node (a backdrop) sits behind it.
		for index: int in range(node.get_index() - 1, -1, -1):
			var sibling := parent.get_child(index) as ColorRect
			if sibling != null and sibling.is_visible_in_tree():
				layers.append(sibling.color)
				break
		if parent is PanelContainer or parent is Panel:
			var box := (parent as Control).get_theme_stylebox(&"panel") as StyleBoxFlat
			if box != null and box.draw_center:
				layers.append(box.bg_color)
		elif parent is ColorRect:
			layers.append((parent as ColorRect).color)
		node = parent
	if layers.is_empty() or layers.back().a < 0.999:
		return ""
	# Front to back in the list: paint from the back.
	var colour: Color = layers.back()
	for index: int in range(layers.size() - 2, -1, -1):
		colour = colour.blend(layers[index])
	return "#" + colour.to_html(false)


## Where a theme value comes from: {kind: scene|theme|code, file, node}.
static func _source(control: Control, what: String, key: StringName) -> Dictionary:
	var overridden := (
		control.has_theme_color_override(key)
		if what == "color"
		else control.has_theme_font_size_override(key)
	)
	if overridden:
		var where := _where(control)
		if not str(where["scene"]).is_empty():
			return {"kind": "scene", "file": where["scene"], "node": where["node"]}
		return {"kind": "code", "file": _script_of(control), "node": ""}
	var node: Node = control
	while node != null:
		if node is Control and (node as Control).theme != null:
			var theme := (node as Control).theme
			if not theme.resource_path.is_empty() and not theme.resource_path.contains("::"):
				return {"kind": "theme", "file": theme.resource_path, "node": ""}
			return {"kind": "code", "file": _script_of(node), "node": ""}
		node = node.get_parent()
	var project_theme := str(ProjectSettings.get_setting("gui/theme/custom", ""))
	if not project_theme.is_empty():
		return {"kind": "theme", "file": project_theme, "node": ""}
	return {"kind": "default", "file": "", "node": ""}


static func _script_of(node: Node) -> String:
	var at: Node = node
	while at != null:
		var script := at.get_script() as Script
		if script != null and script.resource_path.begins_with("res://"):
			return script.resource_path
		at = at.get_parent()
	return ""


func _autoloads() -> Array[String]:
	var names: Array[String] = []
	for child: Node in tree.root.get_children():
		if child != tree.current_scene:
			names.append(str(child.name))
	return names


static func _linear(c: float) -> float:
	return c / 12.92 if c <= 0.04045 else pow((c + 0.055) / 1.055, 2.4)


static func _snapped(values: Array) -> Array:
	var out: Array = []
	for value: Variant in values:
		out.append(snappedf(float(value), 0.001))
	return out
