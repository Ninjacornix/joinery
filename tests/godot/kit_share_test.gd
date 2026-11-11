extends SceneTree
## Shared kits in the Kits section (kit_share.gd in kits_panel.gd), headless, through the real
## `joinery kit` commands: the Kits tab lists the imported kits; a .joinerykit written here is
## read by `kit import --plan` and shown with its author, licence and what it holds; Add to
## project leaves a change for review (rejected again at the end); a kit in use can't be
## removed; the export dialog offers scenes, generators and assets, keeps custom-tool assets
## behind their switch, and shows what `kit export --plan` says the kit would hold.
## With JOINERY_ASSET_CAPTURE (a path prefix) and JOINERY_PARTS_THEME it also saves
## <prefix>-kits-import.png and <prefix>-kits-export.png.

const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")
const SHARE := preload("res://addons/joinery/kit_share.gd")

var _failed := false
var _changed: Dictionary = {}


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 60.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000)
	while Time.get_ticks_msec() < deadline:
		if condition.call():
			return true
		await process_frame
	return false


func _run() -> void:
	root.size = Vector2i(1100, 760)
	var theme := OS.get_environment("JOINERY_PARTS_THEME")
	if not theme.is_empty():
		var config := OS.get_user_data_dir().path_join("kit-share-test-ui.cfg")
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
	workspace.set_process(false)
	var box := StyleBoxFlat.new()
	box.bg_color = load("res://addons/joinery/ui_theme.gd").color(workspace, "surface")
	backdrop.add_theme_stylebox_override("panel", box)
	var kits: Variant = workspace._kits
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	kits.cli_prefix = PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	var share: SHARE = kits.share
	_check(share.cli_prefix == kits.cli_prefix, "The shared kits use the same CLI")
	share.changed.connect(func(result: Dictionary) -> void: _changed = result)
	kits._kits_tab.pressed.emit()
	# No Blender here: stop the Kits panel once its list is in, before a preview starts.
	_check(await _until(func() -> bool: return not kits.catalogue.is_empty()), "kits list")
	kits.set_process(false)
	_check(share.visible and share.import_button.visible, "Import kit… in the Kits tab")
	_check(share.export_button.text == "Export kit…", share.export_button.text)
	_check(await _until(func() -> bool: return not share.listing.is_empty()), "kit list answers")
	_check(not share.rows.visible, "No imported kits yet: no rows")
	_check(share.listing.get("generators", []).size() >= 7, "Joinery's generators can go")
	await _check_import(share)
	await _check_export(share)
	await _check_rows(share)
	workspace.queue_free()
	await process_frame
	print("kit share: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## A real kit file: one picture, imported by the user with its own licence.
func _kit_file() -> String:
	var image := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	image.fill(Color(0.55, 0.45, 0.35))
	var png := image.save_png_to_buffer()
	var hashing := HashingContext.new()
	hashing.start(HashingContext.HASH_SHA256)
	hashing.update(png)
	var digest := hashing.finish().hex_encode()
	var manifest := (
		"\n"
		. join(
			[
				"format = 1",
				'name = "Harbour"',
				'version = "1.0.0"',
				'author = "Ana Harbour"',
				'licence = "CC0-1.0"',
				'description = "Stones for a harbour wall."',
				'joinery = "0.4.0"',
				'created = "2026-11-11"',
				"",
				"[[contents]]",
				'path = "art/textures/stone.png"',
				'kind = "asset"',
				'sha256 = "%s"' % digest,
				"size = %d" % png.size(),
				'origin = "human-import"',
				'licence = "CC0-1.0"',
				"",
			]
		)
	)
	var path := OS.get_user_data_dir().path_join("harbour-1.0.0.joinerykit")
	var zip := ZIPPacker.new()
	zip.open(path)
	zip.start_file("kit.toml")
	zip.write_file(manifest.to_utf8_buffer())
	zip.close_file()
	zip.start_file("files/art/textures/stone.png")
	zip.write_file(png)
	zip.close_file()
	zip.close()
	return path


func _check_import(share: SHARE) -> void:
	share.open_import(OS.get_user_data_dir().path_join("missing.joinerykit"))
	var said := func() -> bool: return share.status.visible
	_check(await _until(said), "A missing file is said")
	_check(share.status.text.begins_with("Couldn't read the kit."), share.status.text)
	share.open_import(_kit_file())
	_check(share._working.visible, "Reading the kit shows it is working")
	var shown := func() -> bool: return share.import_dialog != null and share.import_dialog.visible
	_check(await _until(shown), "The kit's manifest is shown")
	var body := share.import_body
	var author: Label = body.find_child("Author", true, false)
	_check(author.text == "By Ana Harbour · CC0-1.0", author.text)
	var holds: Label = body.find_child("Holds", true, false)
	_check(holds.text.begins_with("1 picture · "), holds.text)
	var place: Label = body.find_child("Place", true, false)
	_check(place.text.begins_with("Goes into kits/harbour."), place.text)
	_check(share.import_dialog.ok_button_text == "Add to project", "Add to project")
	await _capture("kits-import")
	share.import_dialog.get_ok_button().pressed.emit()
	var done := func() -> bool: return not _changed.is_empty()
	_check(await _until(done), "Add to project leaves a change: " + share.status.text)
	_check(share.status.text.contains("Waiting for your review"), share.status.text)
	_check(str(_changed.get("state", "")) == "review", "Not accepted without review")
	_check(not DirAccess.dir_exists_absolute(ProjectSettings.globalize_path("res://kits")), "")
	_reject(str(_changed.get("transaction_id", "")))


## Leave the shared test project as it was.
func _reject(id: String) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	var output: Array = []
	var code := (
		OS
		. execute(
			python,
			[
				"-c",
				"import sys; from joinery.cli import main; sys.exit(main())",
				"transactions",
				"reject",
				"--project",
				ProjectSettings.globalize_path("res://"),
				"--id",
				id,
			],
			output,
			true
		)
	)
	_check(code == 0, "Rejected the test change: %s" % output)


func _check_export(share: SHARE) -> void:
	share.open_export()
	_check(share.export_dialog.visible, "Export kit… opens its dialog")
	_check(share.export_dialog.get_ok_button().disabled, "Nothing chosen: nothing to save")
	_check(share._licence.text == str(share.listing["licence"]), "The game's licence first")
	share.choose("generator:rock")
	share._name.text = "Harbour"
	var planned := func() -> bool: return share.export_summary.text.contains(" · ")
	_check(await _until(planned), "The plan says what goes in: " + share.export_summary.text)
	_check(share.export_summary.text.begins_with("1 generator · 1 file"), share.export_summary.text)
	_check(not share.export_dialog.get_ok_button().disabled, "Save kit… once it can go")
	share.export_dialog.hide()
	# The rest with a project that has more to share (no CLI: the dialog's own rules).
	share.set_process(false)
	share.set_listing(_listing())
	share.open_export()
	share._name.text = "Harbour props"
	var custom: CheckBox = share._checks["include:art/props/odd.glb"]
	var refused: CheckBox = share._checks["include:art/reference/look.png"]
	_check(custom.disabled and refused.disabled, "Custom and reference assets can't be ticked")
	share._custom.button_pressed = true
	_check(not custom.disabled and refused.disabled, "The switch allows custom-tool assets only")
	for key: String in [
		"include:scenes/harbour.tscn", "generator:rock", "include:art/props/odd.glb"
	]:
		share.choose(key)
	var args := share._export_arguments()
	_check(
		"--custom" in args and "--generator" in args and "scenes/harbour.tscn" in args, str(args)
	)
	share.show_export_plan({"error": "a.glb has no joinery.lock entry; b.png is a reference image"})
	_check(share.export_problem.visible, "Why it can't go is shown")
	_check(share.export_problem.text.count("\n") == 1, "One reason a line")
	_check(share.export_dialog.get_ok_button().disabled, "And nothing can be saved")
	(
		share
		. show_export_plan(
			{
				"holds": "3 models, 1 sound, 1 scene and 1 generator",
				"files": range(14),
				"size": 2_310_000,
				"custom": 1,
			}
		)
	)
	var summary := share.export_summary.text
	_check(
		(
			summary
			== (
				"3 models, 1 sound, 1 scene and 1 generator · 14 files · 2.2 MB · "
				+ "1 made with a custom tool"
			)
		),
		summary
	)
	await _capture("kits-export")
	share.export_dialog.hide()


func _check_rows(share: SHARE) -> void:
	var rows := share.rows.get_children()
	_check(rows.size() == 2, "A row per imported kit: %d" % rows.size())
	var facts: Label = rows[0].find_child("Facts", true, false)
	_check(facts.text == "Ana Harbour · CC-BY-4.0 · 3 models and 1 scene", facts.text)
	var remove: Button = rows[0].find_child("Remove", true, false)
	_check(remove.disabled, "A kit a scene uses can't be removed")
	var used: Label = rows[0].find_child("UsedBy", true, false)
	_check(used.text == "Used by level.tscn", used.text)
	var free: Button = rows[1].find_child("Remove", true, false)
	_check(not free.disabled, "An unused kit can be removed")
	await _capture("kits-shared")
	_check(SHARE.size_text(2_310_000) == "2.2 MB" and SHARE.size_text(5000) == "5 KB", "Sizes")
	_check(
		SHARE._changes([{"change": "changed"}, {"change": "removed"}]) == "1 changed, 1 removed", ""
	)


func _listing() -> Dictionary:
	return {
		"installed":
		[
			{
				"name": "Harbour",
				"version": "1.0.0",
				"author": "Ana Harbour",
				"licence": "CC-BY-4.0",
				"holds": "3 models and 1 scene",
				"used_by": ["scenes/level.tscn"],
			},
			{
				"name": "Market stalls",
				"version": "2.1.0",
				"author": "Sam Okafor",
				"licence": "CC0-1.0",
				"holds": "6 models",
				"used_by": [],
			},
		],
		"assets":
		[
			{"path": "art/environment/rocks_01.glb", "origin": "tool-created", "refused": ""},
			{"path": "art/props/odd.glb", "origin": "custom-tool", "custom": true, "refused": ""},
			{
				"path": "art/reference/look.png",
				"origin": "human-import",
				"refused": "a reference image"
			},
			{"path": "audio/sfx/splash.wav", "origin": "tool-created", "refused": ""},
		],
		"scenes": ["scenes/harbour.tscn"],
		"generators":
		[
			{"id": "rock", "label": "Rock", "from": ""},
			{"id": "crate", "label": "Crate", "from": ""},
			{"id": "market_stall", "label": "Stall", "from": "Market stalls 2.1.0 by Sam Okafor"},
		],
		"author": "Ana Harbour",
		"licence": "CC-BY-4.0",
		"licences": ["CC-BY-4.0", "CC0-1.0", "CC-BY-SA-4.0", "MIT", "All rights reserved"],
	}


## Save the window as <JOINERY_ASSET_CAPTURE>-<state>.png when asked (a windowed run).
func _capture(state: String) -> void:
	var prefix := OS.get_environment("JOINERY_ASSET_CAPTURE")
	if prefix.is_empty() or DisplayServer.get_name() == "headless":
		return
	await create_timer(0.4).timeout
	await RenderingServer.frame_post_draw
	root.get_texture().get_image().save_png("%s-%s.png" % [prefix, state])
