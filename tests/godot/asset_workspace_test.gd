extends SceneTree
## The asset workspace (tool_workspace.gd) with an injected index, headless: an app that is
## Ready through Joinery but not on this computer says Journeyman can still change the asset,
## in the asset's own word (material, texture, sound), and assets that a change waiting for
## review adds are listed and marked, their images shown from the change's own copy.
## With JOINERY_ASSET_CAPTURE (a path prefix, from run_parts_tests.py --capture) and
## JOINERY_PARTS_THEME it also saves <prefix>-material.png and <prefix>-waiting.png.

const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")
const WORDING := preload("res://addons/joinery/asset_wording.gd")

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
	var title: String = workspace._unavailable_title.text
	var line: String = workspace._unavailable_label.text
	_check(not title.contains("isn't installed"), "Krita is Ready: not 'isn't installed'")
	_check(title == "Ask Journeyman to change this material", title)
	_check(line.begins_with("Krita is ready for Journeyman."), line)
	_check(not workspace._setup_link.visible, "Nothing to set up for an app that is ready")
	await _capture("material")
	data["tools"] = [_krita("needs_docker")]
	workspace._apply_data(data)
	workspace.select_resource("res://art/sources/table_surface.kra")
	title = workspace._unavailable_title.text
	line = workspace._unavailable_label.text
	_check(title == "Krita isn't installed", title)
	_check(line.contains("changing this material needs Krita"), "Material, not model: " + line)
	await _check_pending(workspace)
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


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png when asked (a windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.3).timeout
	await RenderingServer.frame_post_draw
	root.get_texture().get_image().save_png("%s-%s.png" % [prefix, state])
