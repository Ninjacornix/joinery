@tool
extends EditorPlugin
## Editor screens for the visual suite: layout, sidebar states, review with model,
## image and audio previews, tools workspace, focus mode and the setup dialogs.
## Installed by run_visual_suite.py into a disposable testbed copy beside a copy
## of ui_checks.gd. JOINERY_VISUAL_THEME names the seeded theme (light or dark).

var _checks: GDScript
var _animation: GDScript
var _sound: GDScript
var _review: GDScript
var _theme := "dark"
var _failed := false
var _plugin: EditorPlugin


func _enter_tree() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	_animation = load(get_script().resource_path.get_base_dir().path_join("visual_animation.gd"))
	_sound = load(get_script().resource_path.get_base_dir().path_join("visual_sound.gd"))
	_review = load(get_script().resource_path.get_base_dir().path_join("visual_review.gd"))
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
	await _timeline()
	await load(get_script().resource_path.get_base_dir().path_join("visual_team.gd")).screens(self)
	await _share()
	await _sound.mixer(self)
	await _tools()
	await _panels(base, dock)
	await load(get_script().resource_path.get_base_dir().path_join("visual_tour.gd")).tour(self)
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
	# The far side: the same render seen from behind.
	var far := cache.path_join("hammer-back.png")
	var flipped := rendered.duplicate() as Image
	flipped.flip_x()
	flipped.save_png(far)
	var panel: Control = _plugin.get("_review_panel")
	(
		dock
		. show_file_review(
			"Create a low-poly hammer",
			"",
			[
				{
					"path": "art/sources/hammer.blend",
					"media": "image",
					"after": still,
					"after_back": far,
				}
			],
			[
				{"path": "art/sources/hammer.blend", "change": "created"},
				{"path": "art/models/hammer.glb", "change": "created"},
				{"path": "recipes/hammer.toml", "change": "created"},
			],
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
	await _animation.live_model(self, dock, panel, cache, [still, far])
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
	await _review.narrow(self, panel, dock)
	dock.hide_edit_review()
	await _frames()
	# The review opens as soon as the checks pass; the render fills in when it is done.
	var rendering := cache.path_join("wood-rendering.png")
	DirAccess.remove_absolute(rendering)
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
					"after": rendering,
				}
			],
		)
	)
	await _shot("review-rendering", [panel, dock])
	_planks(Color("6b4423")).save_png(rendering)
	(
		(panel as Object)
		. call(
			"update_media",
			[
				{
					"path": "art/textures/door_wood.png",
					"media": "image",
					"before": before,
					"after": rendering,
				}
			]
		)
	)
	await get_tree().create_timer(0.6).timeout
	await _shot("review-rendered", [panel, dock])
	await _before_after(panel, "review-image-before")
	# Zoomed in on the texture: both sides magnified together, the planks' pixels crisp.
	var swipe: Control = panel.get("_media").call("swipe")
	swipe.call("zoom_at", swipe.size / 2.0, 3.0)
	await _shot("review-image-zoom", [panel, dock])
	swipe.call("reset_view")
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
	await _sound.review_music(self, dock, panel)
	dock.hide_edit_review()
	await _frames()
	await _glance_reviews(dock, panel, cache, before, after, still)


## The other kinds at a glance: text as changed lines, a scene edit as viewport frames, and a
## change with several items as a file list with the agent's facts and the reviewer's notes.
func _glance_reviews(
	dock: Control, panel: Control, cache: String, before: String, after: String, still: String
) -> void:
	var diff := (
		"\n"
		. join(
			[
				"--- scripts/door.gd",
				"+++ scripts/door.gd",
				"@@ -3,7 +3,8 @@",
				" extends AnimatableBody3D",
				"-const OPEN_SECONDS := 0.8",
				"+const OPEN_SECONDS := 0.45",
				"+const EASE := Tween.EASE_OUT",
				" ",
				'-	tween.tween_property(self, "rotation_degrees:y", 90.0, OPEN_SECONDS)',
				'+	tween.tween_property(self, "rotation_degrees:y", 90.0, OPEN_SECONDS).set_ease(EASE)',
			]
		)
	)
	dock.show_file_review(
		"Make the door open faster", diff, [], [{"path": "scripts/door.gd", "change": "modified"}]
	)
	panel.call("show_glance", {"checks": {"ran": ["references", "Godot import", "tests"]}})
	await _shot("review-text", [panel, dock])
	await _review.done_when(self, panel, dock)
	dock.hide_edit_review()
	await _frames()
	var room_before := cache.path_join("room-before.png")
	var room_after := cache.path_join("room-after.png")
	(await _render_model(Color("3a3d42"))).save_png(room_before)
	(await _render_model(Color("5a4a36"))).save_png(room_after)
	var frames := [
		{
			"path": "scenes/workshop.tscn",
			"media": "image",
			"label": "Viewport",
			"before": room_before,
			"after": room_after,
		}
	]
	# The changed node is in the open scene, so the review offers to show it there.
	var crate: Node = _animation.mark_changed("Crate")
	panel.call("show_scene_review", "Warm up the workshop light", 2, frames)
	await _shot("review-scene", [panel, dock])
	_check(crate == null or panel.get("_open_link").is_visible_in_tree(), "Open in editor shows")
	_animation.mark_changed("")
	dock.hide_edit_review()
	await _frames()
	var creak := ProjectSettings.globalize_path("res://audio/sfx/door_creak.wav")
	(
		dock
		. show_file_review(
			"Widen the door and darken the wood",
			"--- scripts/door.gd\n+++ scripts/door.gd\n@@ -1 +1 @@\n-var width := 1.0\n+var width := 1.2",
			[
				{
					"path": "art/textures/door_wood.png",
					"media": "image",
					"before": before,
					"after": after
				},
				{"path": "art/sources/door.blend", "media": "image", "after": still},
				{"path": "audio/sfx/door_creak.wav", "media": "audio", "after": creak},
			],
			[
				{"path": "art/sources/door.blend", "change": "modified"},
				{"path": "art/textures/door_wood.png", "change": "modified"},
				{"path": "audio/sfx/door_creak.wav", "change": "modified"},
				{"path": "scripts/door.gd", "change": "modified"},
			],
		)
	)
	(
		panel
		. call(
			"show_glance",
			{
				"facts": ["door 20% wider", "wood darker"],
				"checks": {"ran": ["references", "provenance", "art bible"], "skipped": ["tests"]},
				"review_notes":
				[
					{
						"severity": "warn",
						"text": "The hinge side lost its bevel",
						"file": "door.blend"
					},
					{"severity": "info", "text": "Wood tone matches the palette"},
				],
			}
		)
	)
	await _shot("review-files", [panel, dock])
	dock.hide_edit_review()
	await _frames()
	await _review.blockout(self, dock, panel)
	await _review.variant(self, dock, panel)
	await _clip_review(dock, panel, cache)


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
			# What it cost: tokens reported for most, not by the agent that added the post.
			"cost":
			(
				null
				if state != "accepted"
				else {"total_ms": 125000, "tokens": null if id == "old" else 18200, "rounds": 1}
			),
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
	# The change Journeyman just submitted, open for review while its renders still run.
	var submitted := {"transaction_id": "ashtray", "summary": "Texture the ashtray"}
	tab.call("show_live", submitted)
	var dock := tab.get_parent() as Control
	var bottom: Control = dock.get_parent() as Control if dock != null else tab
	await get_tree().create_timer(0.3).timeout
	await _shot("review-list", [bottom])
	# Decided, scrolled into view: each change with what making it cost under its summary.
	var decided: Control = tab.get("_decided_body")
	var scroller := decided.get_parent() as Control
	while scroller != null and not scroller is ScrollContainer:
		scroller = scroller.get_parent() as Control
	if scroller != null:
		(scroller as ScrollContainer).scroll_vertical = int(decided.position.y + 100000)
	await get_tree().create_timer(0.2).timeout
	await _shot("review-decided", [bottom])
	if scroller != null:
		(scroller as ScrollContainer).scroll_vertical = 0
	# A parallel task's change, opened from its row: the same full review.
	var cache := OS.get_cache_dir().path_join("joinery-visual")
	var task := {
		"kind": "file_preview",
		"task_id": "t1",
		"transaction_id": "planks",
		"summary": "Weather the dock planks",
		"diff": "~ art/textures/dock_planks.kra\n~ art/textures/dock_planks.png",
		"changes":
		[
			{"path": "art/textures/dock_planks.kra", "change": "modified"},
			{"path": "art/textures/dock_planks.png", "change": "modified"},
		],
		"media":
		[
			{
				"path": "art/textures/dock_planks.png",
				"media": "image",
				"before": cache.path_join("wood-before.png"),
				"after": cache.path_join("wood-after.png"),
			}
		],
	}
	tab.call("_on_task_event", task)
	tab.call("show_task", "planks")
	await get_tree().create_timer(0.6).timeout
	await _shot("review-task", [bottom])
	await _variants(tab, bottom, cache)
	tab.call("show_list")
	tab.call("clear_live")
	var settled := {"state": "approved", "by": "creation"}
	tab.call("apply_listing", {"transactions": [], "art_bible": settled})
	await _shot("review-list-empty", [bottom])
	tab.call("apply_listing", {"transactions": [], "art_bible": {"state": "missing"}})
	_plugin.hide_bottom_panel()
	await _frames()


## A request tried three ways: the variant strip above the way on show, each way's planks.
func _variants(tab: Control, bottom: Control, cache: String) -> void:
	var notes := ["chunky and worn", "slim and clean", "ornate and detailed"]
	var colours := [Color("5a3a22"), Color("b07a45"), Color("8a5a2e")]
	var ways: Array = []
	for index: int in 3:
		var after := cache.path_join("way-%d.png" % index)
		_planks(colours[index]).save_png(after)
		var way := {"task_id": "v%d" % index, "variant": index + 1, "direction": notes[index]}
		way.merge({"state": "in_review", "transaction_id": "way%d" % index})
		ways.append(way)
		var event := way.duplicate()
		event.merge({"kind": "task", "variant_group": "g", "title": "Dock planks"})
		tab.call("_on_task_event", event)
		var preview := {"kind": "file_preview", "task_id": way["task_id"], "summary": notes[index]}
		preview["transaction_id"] = way["transaction_id"]
		preview["facts"] = [["darker planks", "lighter planks", "carved edges"][index]]
		preview["changes"] = [{"path": "art/textures/dock_planks.png", "change": "modified"}]
		preview["media"] = [
			{
				"path": "art/textures/dock_planks.png",
				"media": "image",
				"before": cache.path_join("wood-before.png"),
				"after": after,
			}
		]
		tab.call("_on_task_event", preview)
	var group := {"kind": "variants", "group_id": "g", "title": "Weather the dock planks"}
	group.merge({"state": "working", "ways": ways})
	tab.get("_tasks").call("_on_group", group)
	tab.call("show_task", "way1")
	await get_tree().create_timer(0.6).timeout
	var strip: Control = tab.get("_tasks").get("strip")
	_check(strip != null and strip.is_visible_in_tree(), "A way's review shows the variant strip")
	await _shot("review-variants", [bottom])


## The Timeline tab: entries with a filter, one entry's files open, and the offer to start one.
func _timeline() -> void:
	var tab: Control
	for node: Node in EditorInterface.get_base_control().find_children("Timeline", "", true, false):
		if node.get_script() == load("res://addons/joinery/timeline_tab.gd"):
			tab = node as Control
	_check(tab != null, "The Timeline tab exists")
	if tab == null:
		return
	tab.set("cli_enabled", false)
	tab.call("show_list")
	_plugin.make_bottom_panel_item_visible(tab)
	var now := Time.get_unix_time_from_system()
	var people := ["Ada Lovelace", "Ada Lovelace", "Grace Hopper", "Ada Lovelace", "Joinery user"]
	var subjects := [
		"Darken the door wood",
		"Revert: Add fog to the harbour",
		"Add fog to the harbour",
		"Model a brass harbour lamp",
		"Start Harbour Watch",
	]
	var entries: Array = []
	for index: int in subjects.size():
		(
			entries
			. append(
				{
					"commit": "%040x" % (index + 1),
					"subject": subjects[index],
					"author": people[index],
					"time": now - [180, 4000, 5000, 90000, 400000][index],
					"files": [2, 1, 1, 3, 48][index],
				}
			)
		)
	var listing := {"state": "ready", "auto_commit": true, "entries": entries, "more": true}
	tab.call("apply_listing", listing)
	var dock := tab.get_parent() as Control
	var bottom: Control = dock.get_parent() as Control if dock != null else tab
	# Taller, as someone reading their timeline would drag it; put back afterwards.
	var split: SplitContainer
	var walker: Node = bottom
	while walker != null and split == null:
		walker = walker.get_parent()
		split = walker as SplitContainer
	var offset := split.split_offset if split != null else 0
	if split != null:
		split.split_offset = offset - 360
	await get_tree().create_timer(0.3).timeout
	await _shot("timeline-list", [bottom])
	var cache := OS.get_cache_dir().path_join("joinery-visual")
	var before := cache.path_join("timeline-wood-before.png")
	var after := cache.path_join("timeline-wood-after.png")
	_planks(Color("b07a45")).save_png(before)
	_planks(Color("6b4423")).save_png(after)
	tab.call("open_entry", entries[0])
	var view: Control = tab.call("entry_view")
	var files := [
		{
			"path": "scenes/door.tscn",
			"change": "modified",
			"kind": "text",
			"added": 2,
			"removed": 1
		},
		{"path": "art/textures/door_wood.png", "change": "modified", "kind": "image"},
	]
	view.call(
		"show_entry",
		{"subject": subjects[0], "author": people[0], "time": now - 180, "files": files}
	)
	view.call("set_file_open", "art/textures/door_wood.png", true)
	(
		view
		. call(
			"apply_file",
			{
				"path": "scenes/door.tscn",
				"kind": "text",
				"diff":
				[
					"@@ -12,3 +12,4 @@",
					' [node name="Door" type="MeshInstance3D" parent="."]',
					'-material_override = ExtResource("oak")',
					'+material_override = ExtResource("oak_dark")',
					"+transform = Transform3D(1.2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)",
				],
			}
		)
	)
	(
		view
		. call(
			"apply_file",
			{
				"path": "art/textures/door_wood.png",
				"kind": "image",
				"before": before,
				"after": after,
				"before_size": 48_200,
				"after_size": 47_900,
			}
		)
	)
	await get_tree().create_timer(0.3).timeout
	await _shot("timeline-entry", [bottom])
	view.call("set_restore", "art/textures/door_wood.png", "done")
	await _shot("timeline-restored", [bottom])
	tab.call("show_list")
	tab.call("apply_listing", {"state": "none", "message": "", "auto_commit": true, "entries": []})
	await _shot("timeline-start", [bottom])
	if split != null:
		split.split_offset = offset
	_plugin.hide_bottom_panel()
	await _frames()


## Switch the review to Before, capture it, and switch back to After.
func _before_after(panel: Control, screen: String) -> void:
	var sides: Control = panel.get("_media").call("sides")
	var before: Button = sides.call("tag", "before")
	var after: Button = sides.call("tag", "after")
	_check(before.is_visible_in_tree(), "The Before tag shows on the media for a pair")
	before.pressed.emit()
	await _frames()
	await _shot(screen, [panel])
	after.pressed.emit()
	await _frames()


func _render_model(ground := Color("3a3d42")) -> Image:
	var viewport := SubViewport.new()
	viewport.size = Vector2i(640, 480)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = ground
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


## An animation change: the run before and after as two looping clips, with its facts.
func _clip_review(dock: Control, panel: Control, cache: String) -> void:
	var media: Array = await _animation.clip_media(self, cache)
	var files := [{"path": "art/sources/hero.blend", "change": "modified"}]
	dock.show_file_review("Give the hero a bouncier run", "", media, files)
	await get_tree().create_timer(0.3).timeout
	await _shot("review-clip", [panel, dock])
	dock.hide_edit_review()
	await _frames()


## The Blender tab with an animated model: its actions listed under the preview, run playing.
func _animations(workspace: Control, rail: Control) -> void:
	workspace.call("_clear_preview")
	workspace.call("_show_scene", _animation.hero())
	var parts: Control = workspace.get("_parts_panel")
	var browser: Control = parts.get("animations")
	browser.call("set_report", {"resource": "", "parts": _animation.report_rows()})
	browser.call("show_animation", 2)
	browser.call("seek", 0.2)
	await _frames(6)
	await _shot("tools-animation", [workspace, rail])
	workspace.call("_clear_preview")


## The Share tab: the templates offer before a first build, a finished build with its media
## and page text, and the publish confirmation naming the itch.io page and channels.
func _share() -> void:
	var tab: Control
	for node: Node in EditorInterface.get_base_control().find_children("Share", "", true, false):
		if node.get_script() == load("res://addons/joinery/share_tab.gd"):
			tab = node as Control
	_check(tab != null, "The Share tab exists")
	if tab == null:
		return
	tab.set("cli_enabled", false)
	_plugin.make_bottom_panel_item_visible(tab)
	var cache := OS.get_cache_dir().path_join("joinery-visual")
	DirAccess.make_dir_recursive_absolute(cache)
	var shots: Array = []
	for index: int in 5:
		var path := cache.path_join("share-shot-%d.png" % index)
		_scene_picture(index).save_png(path)
		shots.append(path)
	var cover := cache.path_join("share-cover.png")
	var cover_image := _scene_picture(2)
	cover_image.resize(630, 500)
	cover_image.save_png(cover)
	var page := (
		"<!-- Drafted by Joinery. -->\n\n# Harbour Watch\n\nKeep the lamps lit until the "
		+ "boats are home.\n\n## About\n\n- 30 s: climb to a lamp, light it, watch the "
		+ "fog.\n\n## Controls\n\n| Action | Keyboard and mouse |\n|---|---|\n| Jump | "
		+ "Space |\n| Light | E |\n\n## Features\n\n_Write this part._\n"
	)
	var status := {
		"name": "Harbour Watch",
		"targets": [{"id": "web"}, {"id": "windows"}, {"id": "macos"}, {"id": "linux"}],
		"templates": {"ready": false, "download_gb": 1.3},
		"build": null,
		"media": {"screenshots": [], "trailer": {}, "cover": null},
		"page": null,
		"itch": {"butler": null, "version": "15.24.0", "pinned": true, "signed_in": "butler login"},
	}
	tab.call("apply_status", status)
	var dock := tab.get_parent() as Control
	var bottom: Control = dock.get_parent() as Control if dock != null else tab
	var split: SplitContainer
	var walker: Node = bottom
	while walker != null and split == null:
		walker = walker.get_parent()
		split = walker as SplitContainer
	var offset := split.split_offset if split != null else 0
	if split != null:
		split.split_offset = offset - 420
	await get_tree().create_timer(0.3).timeout
	await _shot("share-empty", [bottom])
	status["templates"] = {"ready": true}
	status["build"] = {
		"changes": ["Added the Web export preset", "Added the macOS export preset"],
		"targets":
		{
			"web":
			{
				"label": "Web",
				"ok": true,
				"size": 10_326_066,
				"warnings": [],
				"boot": {"ran": false, "detail": "Plays in a browser; try it on itch.io."},
			},
			"windows":
			{
				"label": "Windows",
				"ok": true,
				"size": 48_204_112,
				"warnings": [],
				"boot": {"ran": false, "detail": "Built here; start it on a Windows computer."},
			},
			"macos":
			{
				"label": "macOS",
				"ok": true,
				"size": 60_560_573,
				"warnings": [],
				"boot": {"ran": true, "ok": true, "detail": "Started and quit cleanly."},
				"playtest":
				{"status": "played", "summary": "Played 0:30 (seed 4242): nothing to report."},
			},
		},
	}
	status["media"] = {
		"seed": 777,
		"screenshots": shots,
		"cover": cover,
		"trailer": {"preview": null, "seconds": 31.5, "sound": "Silent."},
	}
	var share: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("visual_share.gd")
	)
	status["steam"] = share.call("steam_status", cache, _scene_picture)
	status["page"] = page
	status["itch"] = {
		"butler": null,
		"version": "15.24.0",
		"pinned": true,
		"signed_in": "butler login",
		"target": "harbour-studio/harbour-watch",
	}
	tab.call("apply_status", status)
	await get_tree().create_timer(0.3).timeout
	await _shot("share-built", [bottom])
	# The Steam art set, scrolled into view.
	var scroll := tab.get_child(0) as ScrollContainer
	var group := tab.find_child("Steam", true, false) as Control
	if scroll != null and group != null:
		scroll.scroll_vertical = int(
			group.get_global_rect().position.y - scroll.get_global_rect().position.y
		)
	await get_tree().create_timer(0.3).timeout
	await _shot("share-steam", [bottom])
	if scroll != null:
		scroll.scroll_vertical = 0
	var publish: Button = tab.get("_publish_button")
	publish.pressed.emit()
	var dialog: Window = tab.call("publish_dialog")
	(
		dialog
		. call(
			"apply_dry_run",
			{
				"steps":
				[
					{"channel": "web", "file": "harbour-watch-web.zip", "size": 10_326_066},
					{"channel": "windows", "file": "harbour-watch-windows.zip", "size": 48_204_112},
					{"channel": "mac", "file": "harbour-watch-macos.zip", "size": 60_560_573},
				]
			}
		)
	)
	await get_tree().create_timer(0.3).timeout
	await _shot("share-publish", [dialog], dialog)
	dialog.hide()
	if split != null:
		split.split_offset = offset
	_plugin.hide_bottom_panel()
	await _frames()


## A small flat-shaded "game frame": sky, sea, a pier and a lamp, varied by [param index].
func _scene_picture(index: int) -> Image:
	var image := Image.create(320, 180, false, Image.FORMAT_RGB8)
	image.fill(Color("9fb8c8").darkened(0.06 * index))
	image.fill_rect(Rect2i(0, 110 - index * 4, 320, 70 + index * 4), Color("2f5d73"))
	image.fill_rect(Rect2i(40 + index * 30, 96, 140, 12), Color("8a6a48"))
	image.fill_rect(Rect2i(150 + index * 12, 52, 8, 46), Color("3b3b3b"))
	image.fill_rect(Rect2i(144 + index * 12, 44, 20, 12), Color("f2c14e"))
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
		if id == "blender":
			await _animations(workspace, rail)
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
