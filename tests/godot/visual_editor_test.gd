@tool
extends EditorPlugin
## Editor screens for the visual suite: layout, sidebar states, review with model,
## image and audio previews, tools workspace, focus mode and the setup dialogs.
## Installed by run_visual_suite.py into a disposable testbed copy beside a copy
## of ui_checks.gd. JOINERY_VISUAL_THEME names the seeded theme (light or dark).

var _checks: GDScript
var _theme := "dark"
var _failed := false
var _plugin: EditorPlugin


func _enter_tree() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	# Windowed runs animate; captures must show end states.
	load("res://addons/joinery/ui_motion.gd").set_enabled(false)
	var theme := OS.get_environment("JOINERY_VISUAL_THEME")
	if not theme.is_empty():
		_theme = theme
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count: int = 4) -> void:
	for index: int in count:
		await get_tree().process_frame


func _shot(name: String, roots: Array, window: Window = null) -> void:
	await _frames()
	var target: Window = window if window != null else get_tree().root
	if window != null and window.is_embedded():
		target = get_tree().root
	var screen := "editor-%s-%s" % [name, _theme]
	_check(_checks.snapshot(screen, target, roots), "Capture " + screen)


func _wait_workspace() -> void:
	var workspace: Control = _plugin.get("_workspace")
	var deadline := Time.get_ticks_msec() + 30000
	while workspace.get("_command_job") != null and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	await _frames(6)


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			_plugin = node as EditorPlugin
	_check(_plugin != null, "Joinery plugin must load")
	if _plugin == null:
		get_tree().quit(1)
		return
	await _wait_workspace()
	var base := EditorInterface.get_base_control()
	var dock: Control = _plugin.get("_dock")
	EditorInterface.set_main_screen_editor("3D")
	# No agent is configured in the isolated profile: the real disconnected state.
	await _shot("disconnected", [dock])
	_converse(dock)
	await _shot("layout", [base])
	await _sidebar(dock)
	await _reviews(dock)
	await _approvals()
	await _tools()
	await _panels(base, dock)
	await _dialogs()
	var workspace: Control = _plugin.get("_workspace")
	workspace.call("_clear_preview")
	dock.hide_edit_review()
	var chat: Node = _plugin.get("_chat")
	if chat != null:
		chat.call("close")
	await _frames(2)
	RenderingServer.force_draw()
	print("visual editor: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


func _converse(dock: Control) -> void:
	dock.set_chat_agent("codex")
	dock.set_chat_state("connected", "Codex connected")
	(
		dock
		. set_model_options(
			[
				{"id": "fixture-sol", "name": "GPT-6.1 Sol", "efforts": ["low", "medium", "high"]},
				{"id": "fixture-luna", "name": "GPT-6 Luna", "efforts": ["low", "high"]},
			]
		)
	)
	var transcript: Node = dock.get("_transcript")
	transcript.call("add_message", "You", "Make the door a little wider and darken the wood.")
	# The fixture request reached the assistant; without this its reply reads as a failure.
	var card: Control = transcript.call("current_card")
	if card != null:
		card.call("set_sent")
	dock.show_agent_message(
		"I widened **Door** by 20% in Blender and darkened the `Wood` material."
	)
	dock.show_timing({"outcome": "done", "total_ms": 12000, "first_response_ms": 250})
	dock.set_resource_target("res://art/props/door.blend", "PackedScene")


func _sidebar(dock: Control) -> void:
	var prompt: TextEdit = dock.get("_prompt")
	if prompt != null and dock.has_method("_update_commands"):
		prompt.text = "/"
		dock.call("_update_commands", "/")
		await _shot("commands", [dock])
		prompt.text = ""
		dock.call("_update_commands", "")
	dock.set_chat_busy(true)
	dock.show_execution({"name": "GPT-6.1 Sol", "mode": "fast", "effort": "low"})
	dock.set_chat_state("working", "Exporting the model…")
	await _shot("busy", [dock])
	dock.set_chat_busy(false)
	var settings: Node = dock.get("_settings")
	if settings != null and settings.has_method("set_open"):
		settings.call("set_open", true)
		await _shot("settings", [dock])
		settings.call("set_open", false)


func _reviews(dock: Control) -> void:
	var cache := OS.get_cache_dir().path_join("joinery-visual")
	DirAccess.make_dir_recursive_absolute(cache)
	var still := cache.path_join("hammer.png")
	var rendered := await _render_model()
	rendered.save_png(still)
	var panel: Control = _plugin.get("_review_panel")
	(
		dock
		. show_file_review(
			"Create a low-poly hammer",
			"+ art/sources/hammer.blend\n+ art/models/hammer.glb\n+ recipes/hammer.toml",
			[{"path": "art/models/hammer.glb", "media": "image", "after": still}],
		)
	)
	await _shot("review-model", [panel, dock])
	panel.call("set_details_open", true)
	await _shot("review-details", [panel, dock])
	panel.call("set_details_open", false)
	# Accept pressed: the decision is applying, then it fails and the review comes back.
	dock.set_review_applying(true)
	await _shot("review-applying", [panel, dock])
	dock.set_review_applying(false)
	dock.show_review_error(
		"Blender couldn't save hammer.blend. Check that the file isn't open elsewhere."
	)
	await _shot("review-error", [panel, dock])
	# Focus mode with the review open: only the Review tab stays in the bottom panel.
	if _plugin.has_method("_on_toggle_focus"):
		_plugin.call("_on_toggle_focus")
		await get_tree().create_timer(0.2).timeout
		var accept: Button = panel.get("_accept")
		_check(accept != null and accept.is_visible_in_tree(), "Focus mode keeps Accept on screen")
		await _shot("focus-review", [EditorInterface.get_base_control()])
		_plugin.call("_on_toggle_focus")
		await _frames()
	dock.hide_edit_review()
	await _frames()
	var before := cache.path_join("wood-before.png")
	var after := cache.path_join("wood-after.png")
	_planks(Color("b07a45")).save_png(before)
	_planks(Color("6b4423")).save_png(after)
	(
		dock
		. show_file_review(
			"Darken the door wood",
			"~ art/textures/door_wood.kra\n~ art/textures/door_wood.png",
			[
				{
					"path": "art/textures/door_wood.png",
					"media": "image",
					"before": before,
					"after": after,
				}
			],
		)
	)
	await _shot("review-image", [panel, dock])
	await _before_after(panel, "review-image-before")
	dock.hide_edit_review()
	await _frames()
	var creak := ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav")
	var close := ProjectSettings.globalize_path("res://audio/sfx/door_close.wav")
	(
		dock
		. show_file_review(
			"Make the door creak shorter",
			"~ audio/sfx/door_creak.scd\n~ audio/sfx/door_creak.wav",
			[
				{
					"path": "audio/sfx/door_creak.wav",
					"media": "audio",
					"before": creak,
					"after": close,
				}
			],
		)
	)
	await _shot("review-audio", [panel, dock])
	await _before_after(panel, "review-audio-before")
	dock.hide_edit_review()
	await _frames()


## The Review tab's list of approvals, with fixture changes, then with none.
func _approvals() -> void:
	var tab: Control = _plugin.get("_review_tab")
	_check(tab != null, "The Review tab exists")
	if tab == null:
		return
	var deadline := Time.get_ticks_msec() + 30000
	while not (tab.get("_jobs") as Dictionary).is_empty() and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	tab.set("cli_enabled", false)
	tab.call("show_list")
	_plugin.make_bottom_panel_item_visible(tab)
	var now := Time.get_unix_time_from_system()
	var row := func(
		id: String, state: String, summary: String, ago: float, tools: Array
	) -> Dictionary:
		return {
			"id": id,
			"state": state,
			"summary": summary,
			"created": now - ago - 60,
			"decided": 0.0 if state == "review" else now - ago,
			"revertible": id != "old",
			"tools": tools,
			"file_count": 2,
			"files":
			[
				{"path": "art/models/lamp.glb", "change": "created"},
				{"path": "art/sources/lamp.blend", "change": "created"},
			],
		}
	(
		tab
		. call(
			"apply_listing",
			{
				"transactions":
				[
					row.call("lamp", "review", "Model a brass harbour lamp", 120, ["blender"]),
					row.call("net", "review", "Add online play", 900, ["joinery"]),
					row.call("door", "accepted", "Darken the door wood", 3000, ["krita"]),
					row.call("floor", "rejected", "Make the floor blue", 7300, ["godot"]),
					row.call("crate", "reverted", "Widen the crate", 90000, ["blender"]),
					row.call("old", "accepted", "Add a mooring post", 400000, ["blender"]),
				],
				"art_bible": {"state": "needs_approval"},
			}
		)
	)
	# The change Journeyman just finished, still being checked.
	var preparing := {"transaction_id": "ashtray", "summary": "Texture the ashtray"}
	tab.call("set_preparing", preparing)
	var dock := tab.get_parent() as Control
	var bottom: Control = dock.get_parent() as Control if dock != null else tab
	await get_tree().create_timer(0.3).timeout
	await _shot("review-list", [bottom])
	tab.call("clear_live")
	var settled := {"state": "approved", "by": "creation"}
	tab.call("apply_listing", {"transactions": [], "art_bible": settled})
	await _shot("review-list-empty", [bottom])
	tab.call("apply_listing", {"transactions": [], "art_bible": {"state": "missing"}})
	_plugin.hide_bottom_panel()
	await _frames()


## Switch the review to Before, capture it, and switch back to After.
func _before_after(panel: Control, screen: String) -> void:
	var before: Button = panel.get("_before")
	var after: Button = panel.get("_after")
	_check(before != null and before.is_visible_in_tree(), "Before/After shows for a pair")
	if before == null or after == null:
		return
	before.button_pressed = true
	before.pressed.emit()
	await _frames()
	await _shot(screen, [panel])
	after.button_pressed = true
	after.pressed.emit()
	await _frames()


func _render_model() -> Image:
	var viewport := SubViewport.new()
	viewport.size = Vector2i(640, 480)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("3a3d42")
	environment.ambient_light_color = Color(0.6, 0.6, 0.6)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	var world := WorldEnvironment.new()
	world.environment = environment
	viewport.add_child(world)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-45, 35, 0)
	viewport.add_child(light)
	var steel := StandardMaterial3D.new()
	steel.albedo_color = Color("9aa3ad")
	steel.metallic = 0.6
	steel.roughness = 0.4
	var wood := StandardMaterial3D.new()
	wood.albedo_color = Color("8a5a32")
	var head := MeshInstance3D.new()
	var head_mesh := BoxMesh.new()
	head_mesh.size = Vector3(1.2, 0.4, 0.4)
	head_mesh.material = steel
	head.mesh = head_mesh
	head.position = Vector3(0, 0.9, 0)
	viewport.add_child(head)
	var handle := MeshInstance3D.new()
	var handle_mesh := CylinderMesh.new()
	handle_mesh.top_radius = 0.09
	handle_mesh.bottom_radius = 0.11
	handle_mesh.height = 1.8
	handle_mesh.material = wood
	handle.mesh = handle_mesh
	viewport.add_child(handle)
	var camera := Camera3D.new()
	camera.position = Vector3(1.6, 1.0, 2.4)
	viewport.add_child(camera)
	add_child(viewport)
	camera.look_at(Vector3(0, 0.3, 0))
	await _frames(6)
	var image := viewport.get_texture().get_image()
	viewport.queue_free()
	return image


func _planks(tone: Color) -> Image:
	var image := Image.create(320, 320, false, Image.FORMAT_RGBA8)
	for plank: int in 5:
		var shade := tone.darkened(0.08 * (plank % 2)).lightened(0.03 * plank)
		image.fill_rect(Rect2i(plank * 64, 0, 62, 320), shade)
	for row: int in 16:
		image.fill_rect(Rect2i(0, row * 20 + 9, 320, 1), tone.darkened(0.25))
	return image


func _tools() -> void:
	var workspace: Control = _plugin.get("_workspace")
	var rail: Control = _plugin.get("_rail")
	EditorInterface.set_main_screen_editor("Tools")
	await _wait_workspace()
	await _shot("tools", [workspace, rail])
	if rail == null:
		return
	var buttons: Dictionary = rail.get("_buttons")
	for id: String in ["blender", "supercollider"]:
		if not buttons.has(id):
			_check(false, "Rail must offer " + id)
			continue
		(buttons[id] as Button).pressed.emit()
		await _wait_workspace()
		await _shot("tools-" + id, [workspace, rail])
	await _readiness(workspace, rail)
	EditorInterface.set_main_screen_editor("3D")
	await _frames()


## Every readiness state at once on the rail, as `joinery tools --json` would report them.
func _readiness(workspace: Control, rail: Control) -> void:
	var job: Variant = rail.get("_job")
	if job != null:
		(job as RefCounted).call("cancel")
		rail.set("_job", null)
	rail.set("_since_check", -1.0e9)
	var states := {
		"godot": {"status": "ready"},
		"blender": {"status": "preparing", "reason": "Downloading the Blender worker image."},
		"krita": {"status": "needs_docker", "reason": "Start Docker Desktop, then try again."},
		"material-maker": {"status": "needs_licence", "reason": "Add your licence in Settings."},
		"supercollider": {"status": "unavailable", "reason": "Not available on this platform yet."},
	}
	rail.call("_apply_readiness", states)
	await _shot("tools-readiness", [workspace, rail])


func _panels(base: Control, dock: Control) -> void:
	var bottom: TabContainer
	for candidate: Node in base.find_children("*", "EditorBottomPanel", true, false):
		bottom = candidate as TabContainer
	if bottom != null:
		for index: int in bottom.get_tab_count():
			if bottom.get_tab_title(index) in ["Files", "FileSystem"]:
				bottom.current_tab = index
				break
		await get_tree().create_timer(0.3).timeout
		await _shot("bottom-panel", [bottom])
		bottom.current_tab = -1
	if _plugin.has_method("_on_toggle_focus"):
		_plugin.call("_on_toggle_focus")
		await get_tree().create_timer(0.2).timeout
		await _shot("focus", [base])
		_plugin.call("_on_toggle_focus")
		await _frames()
	_check(dock.is_visible_in_tree(), "Journeyman stays visible")


func _dialogs() -> void:
	var setup: Window = _plugin.get("_setup")
	if setup != null and setup.has_method("open_for_current_project"):
		setup.call("open_for_current_project")
		await get_tree().create_timer(0.5).timeout
		await _shot("setup", [setup], setup)
		setup.hide()
		await _frames()
	if _plugin.has_method("_on_art_review"):
		var bible := FileAccess.get_file_as_string("res://art/bible.md")
		if bible.is_empty():
			bible = (
				"# Art bible\n\nPalette: warm harbour wood, weathered brass, fog blues.\n"
				+ "Scale: metres, +Y up. Pivots at the base centre.\n"
				+ "Shapes: chunky silhouettes, bevelled edges, no fine noise."
			)
		_plugin.call("_on_art_review", {"text": bible, "sha256": ""})
		await get_tree().create_timer(0.3).timeout
		var dock: Control = _plugin.get("_dock")
		var dialog: ConfirmationDialog
		for node: Node in dock.find_children("*", "ConfirmationDialog", true, false):
			if (node as Window).visible:
				dialog = node as ConfirmationDialog
		_check(dialog != null, "Art review dialog must open")
		if dialog != null:
			await _shot("art-review", [dialog], dialog)
			dialog.hide()
			dialog.queue_free()
			await _frames()
