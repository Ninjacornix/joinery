extends SceneTree
## The asset workspace (tool_workspace.gd) with an injected index, headless: an app that is
## Ready through Joinery but not on this computer leaves "Ask Journeyman to change this …" (in
## the asset's own word: material, texture, sound) and one quiet line on installing it; assets
## that a change waiting for review adds are listed and marked, their images shown from the
## change's own copy. A model page with many materials lists its files and closed groups, its
## materials in words with their maps inside and no numbered copies, beside the preview when
## wide and under it when narrow.
## With JOINERY_ASSET_CAPTURE (a path prefix, from run_parts_tests.py --capture) and
## JOINERY_PARTS_THEME it also saves <prefix>-material.png, <prefix>-waiting.png,
## <prefix>-model.png and <prefix>-model-narrow.png.

const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")
const WORDING := preload("res://addons/joinery/asset_wording.gd")
## The model page's made-up materials: [Blender name, words in the list].
const MATERIALS := [
	["AgedGreyTrim", "Aged grey trim"],
	["screen_glass", "Screen glass"],
	["panelmetal", "Panelmetal"],
	["CRT_Bezel", "CRT bezel"],
	["knob-rubber", "Knob rubber"],
	["Speaker Grille", "Speaker grille"],
	["Label.001", "Label.001"],
	["wood_ORM_trim", "Wood ORM trim"],
]

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _krita(status: String) -> Dictionary:
	return {
		"id": "krita",
		"name": "Krita",
		"domain": "2d",
		"enabled": true,
		"available": false,
		"reason": "Application not found",
		"status": status,
	}


func _asset(path: String, kind: String, extra: Dictionary = {}) -> Dictionary:
	var asset := {
		"path": path,
		"tool": "krita",
		"origin": "tool-created",
		"integrity": "in sync",
		"recipe": "",
		"sources": [],
		"previews": [],
		"open_options": [],
		"kind": kind,
		"parts": [],
		"part_of": "",
		"pending": "",
		"staged": "",
	}
	asset.merge(extra, true)
	return asset


func _run() -> void:
	root.size = Vector2i(1100, 700)
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("asset-test-ui.cfg")
		var file := ConfigFile.new()
		file.set_value("appearance", "theme", theme)
		file.save(config)
		Engine.set_meta("joinery_ui_config", config)
	var backdrop := Panel.new()
	root.add_child(backdrop)
	backdrop.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var workspace: WORKSPACE = WORKSPACE.new()
	root.add_child(workspace)
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	# Only injected data here: no CLI refresh.
	workspace.set_process(false)
	var box := StyleBoxFlat.new()
	box.bg_color = load("res://addons/joinery/ui_theme.gd").color(workspace, "surface")
	backdrop.add_theme_stylebox_override("panel", box)
	_check_wording()
	var missing := {
		"tool": "krita",
		"name": "Krita",
		"path": "art/sources/table_surface.kra",
		"available": false,
		"reason": "Application not found",
	}
	var material := _asset(
		"art/sources/table_surface.kra",
		"material",
		{"open_options": [missing], "parts": ["art/textures/table_surface_albedo.png"]}
	)
	var data := {"tools": [_krita("ready")], "assets": [material], "issues": []}
	workspace._apply_data(data)
	workspace.select_resource("res://art/sources/table_surface.kra")
	_check(not workspace._unavailable.visible, "Krita is Ready: no 'isn't installed' notice")
	var note: Label = workspace._quiet_note
	_check(note.visible, "One quiet line says how to open it yourself")
	_check(note.text == "To open the file yourself, install Krita on this computer.", note.text)
	_check(workspace._ask.visible, "Ask Journeyman is offered")
	_check(workspace._ask.tooltip_text == "Ask Journeyman to change this material", "Ask wording")
	var asked: Array[int] = [0]
	workspace.ask_requested.connect(func() -> void: asked[0] += 1)
	workspace._ask.pressed.emit()
	_check(asked[0] == 1, "Ask Journeyman asks the sidebar for the message box")
	await _capture("material")
	data["tools"] = [_krita("needs_docker")]
	workspace._apply_data(data)
	workspace.select_resource("res://art/sources/table_surface.kra")
	var title: String = workspace._unavailable_title.text
	var line: String = workspace._unavailable_label.text
	_check(not note.visible, "A missing app says so once, in its notice")
	_check(title == "Krita isn't installed", title)
	_check(line.contains("changing this material needs Krita"), "Material, not model: " + line)
	await _check_pending(workspace)
	await _check_model(workspace)
	workspace.queue_free()
	await process_frame
	print("asset workspace: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_wording() -> void:
	_check(WORDING.noun("material", "art/sources/a.kra") == "material", "Material")
	_check(WORDING.noun("image", "art/textures/a_albedo.png") == "texture", "Texture")
	_check(WORDING.noun("image", "art/ui/logo.png") == "image", "Image")
	_check(WORDING.noun("sound", "audio/door.wav") == "sound", "Sound")
	_check(WORDING.noun("other", "audio/door.ogg") == "sound", "Sound by extension")
	var lines := WORDING.missing_text("Surge XT", "sound", "wav", true, false)
	_check(lines[1].begins_with("You can listen here"), lines[1])
	for pair: Array in [
		["aged_grey_trim", "Aged grey trim"],
		["AgedGreyTrim", "Aged grey trim"],
		["agedgreytrim", "Agedgreytrim"],
		["crt_station", "CRT station"],
		["Head.001", "Head.001"],
		["wood_ORM", "Wood ORM"],
		["HTTPServer", "HTTP server"],
	]:
		_check(
			WORDING.readable(pair[0]) == pair[1], "%s → %s" % [pair[0], WORDING.readable(pair[0])]
		)
	var maps := ["t/a_albedo.png", "t/a_albedo_001.png", "t/a_albedo.001.png", "t/b_002.png"]
	_check(WORDING.is_copy(maps[1], maps) and WORDING.is_copy(maps[2], maps), "Numbered copies")
	_check(not WORDING.is_copy(maps[0], maps), "The original is no copy")
	_check(not WORDING.is_copy(maps[3], maps), "A number with no original is a name")


func _check_pending(workspace: WORKSPACE) -> void:
	var id := "0123456789abcdef0123456789abcdef"
	var relative := "art/textures/ashtray_albedo.png"
	var staged := ".joinery/transactions/%s/stage/%s" % [id, relative]
	var folder := ProjectSettings.globalize_path("res://" + staged).get_base_dir()
	DirAccess.make_dir_recursive_absolute(folder)
	var pixels := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	pixels.fill(Color(0.5, 0.4, 0.3))
	pixels.save_png(ProjectSettings.globalize_path("res://" + staged))
	var waiting := _asset(
		relative,
		"image",
		{"tool": "blender", "integrity": "pending", "pending": id, "staged": staged}
	)
	var model := _asset(
		"art/sources/ashtray.blend",
		"model",
		{
			"tool": "blender",
			"integrity": "pending",
			"pending": id,
			"staged": staged.replace(relative, "art/sources/ashtray.blend")
		}
	)
	var table := _asset("art/sources/table_surface.kra", "material")
	workspace._apply_data(
		{"tools": [_krita("ready")], "assets": [waiting, model, table], "issues": []}
	)
	workspace.select_tool("")
	_check(workspace._rows.size() == 3, "Waiting assets are listed: %d" % workspace._rows.size())
	var marked := 0
	for index: int in workspace._rows.size():
		var row: Button = workspace._assets._rows[index]
		if not str(workspace._rows[index].get("pending", "")).is_empty():
			marked += 1
			_check(row.dot.visible, "A waiting asset is marked")
			_check(row.tooltip_text.ends_with("Waiting for review"), row.tooltip_text)
		else:
			_check(not row.dot.visible, "An accepted asset has no mark")
	_check(marked == 2, "Two assets wait for review")
	workspace.select_resource("res://" + relative)
	_check(workspace._caption.text.ends_with("waiting for review"), workspace._caption.text)
	_check(workspace._preview.get_child(0) is TextureRect, "Its image shows from the change")
	await _capture("waiting")
	workspace.select_resource("res://art/sources/ashtray.blend")
	var note := workspace._preview.get_child(0)
	var text := ""
	for label: Node in note.find_children("*", "Label", true, false):
		text += (label as Label).text
	_check(text.begins_with("Waiting for your review."), "A waiting model says so: " + text)
	_check(not workspace._open.visible, "Nothing to open before it is accepted")
	DirAccess.remove_absolute(ProjectSettings.globalize_path("res://" + staged))


## A Blender model with eight materials, two maps each and a numbered copy of every map: the
## files, closed groups, names in words, picking a material, and the wide and narrow page.
func _check_model(workspace: WORKSPACE) -> void:
	var folder := "art/textures/crt_station"
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://" + folder))
	var files: Array = ["art/models/crt_station.glb"]
	var parts: Array[Dictionary] = []
	for index: int in MATERIALS.size():
		var material: String = MATERIALS[index][0]
		var stem := material.to_lower().replace(" ", "_").replace(".", "_")
		parts.append(_part("material", {"material": material, "used_by": "Body,Screen"}))
		for map: Array in [["albedo", "base_color"], ["rough", "roughness"]]:
			for copy: String in ["", "_001"]:
				var image: String = "%s_%s%s" % [stem, map[0], copy.replace("_", ".")]
				var path: String = "%s/%s_%s%s.png" % [folder, stem, map[0], copy]
				var pixels := Image.create(16, 16, false, Image.FORMAT_RGBA8)
				var tone := Color.from_hsv(index / 8.0, 0.55 if map[0] == "albedo" else 0.0, 0.7)
				pixels.fill(tone)
				pixels.save_png(ProjectSettings.globalize_path("res://" + path))
				files.append(path)
				var fields := {"material": material, "image": image, "texture": path}
				fields["role"] = map[1]
				fields["roles"] = map[1]
				parts.append(_part("texture", fields))
	for object: String in ["Body", "Screen", "Knob"]:
		parts.append(_part("object", {"object": object}))
	var blender := {"tool": "blender", "name": "Blender", "path": "art/sources/crt_station.blend"}
	blender.merge({"available": false, "reason": "Application not found"})
	var model := _asset(
		"art/sources/crt_station.blend",
		"model",
		{
			"tool": "blender",
			"open_options": [blender],
			"parts": files,
			"previews": ["art/models/crt_station.glb"]
		}
	)
	var tool := _krita("ready")
	tool.merge({"id": "blender", "name": "Blender", "domain": "3d"}, true)
	workspace._apply_data({"tools": [tool], "assets": [model], "issues": []})
	workspace.select_resource("res://art/sources/crt_station.blend")
	var panel: VBoxContainer = workspace._parts_panel
	if panel._job != null:
		panel._job.cancel()
		panel._job = null
	panel.set_report({"resource": "res://art/models/crt_station.glb", "parts": parts})
	_check(workspace._title.text == "CRT station", workspace._title.text)
	_check(workspace._caption.text == "Model · made in Blender", workspace._caption.text)
	_check(workspace._ask.tooltip_text == "Ask Journeyman to change this model", "Ask a model")
	_check(workspace._quiet_note.text.contains("install Blender"), workspace._quiet_note.text)
	_check(panel.file_names() == ["Source", "Model"], "Files: %s" % [panel.file_names()])
	var counts: Dictionary = panel.group_counts()
	_check(int(counts.get("material", 0)) == 8, "8 materials: %s" % [counts])
	_check(int(counts.get("texture", 0)) == 16, "No numbered copies of maps: %s" % [counts])
	var tree_root: TreeItem = panel.tree.get_root()
	var group: TreeItem = null
	for item: TreeItem in tree_root.get_children():
		if item.get_text(0) == "8 materials":
			group = item
	_check(group != null and group.collapsed, "Materials start closed with their count")
	if group == null:
		return
	var names: Array[String] = []
	for item: TreeItem in group.get_children():
		names.append(item.get_text(0))
		_check(item.get_child_count() == 2, "Two maps inside %s" % item.get_text(0))
		_check(item.get_text(1) == "2 maps", item.get_text(1))
	var expected: Array[String] = []
	for pair: Array in MATERIALS:
		expected.append(pair[1])
	_check(names == expected, "Materials in words: %s" % [names])
	var picked: Array[Dictionary] = []
	workspace.part_selected.connect(func(target: Dictionary) -> void: picked.append(target))
	group.collapsed = false
	var first := group.get_child(0)
	first.collapsed = false
	panel.tree.set_selected(first, 0)
	_check(picked.size() == 1 and picked[0]["material"] == "AgedGreyTrim", "Pick a material")
	_check(first.get_icon(0) != null, "A material shows a swatch")
	await process_frame
	_check(not workspace._stage.vertical, "Wide: the parts list is beside the preview")
	await _capture("model")
	root.size = Vector2i(720, 800)
	await process_frame
	await process_frame
	_check(workspace._stage.vertical, "Narrow: the parts list is under the preview")
	_check(workspace._ask.text == "Ask Journeyman", "Narrow: " + workspace._ask.text)
	await _capture("model-narrow")
	for path: Variant in files.slice(1):
		DirAccess.remove_absolute(ProjectSettings.globalize_path("res://" + str(path)))


func _part(kind: String, fields: Dictionary) -> Dictionary:
	var part := {
		"part_kind": kind,
		"resource": "res://art/models/crt_station.glb",
		"owner": "blender",
		"owner_source": "art/sources/crt_station.blend",
	}
	part.merge(fields)
	part["id"] = "%s#%s" % [kind, "&".join(PackedStringArray(fields.values()))]
	return part


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png when asked (a windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.3).timeout
	await RenderingServer.frame_post_draw
	root.get_texture().get_image().save_png("%s-%s.png" % [prefix, state])
