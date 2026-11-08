@tool
extends EditorPlugin

var _failed := false


func _enter_tree() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _event(plugin: EditorPlugin, payload: Dictionary, targets: Array = []) -> Dictionary:
	var selection: Array = []
	for id: String in targets:
		selection.append({"id": id})
	return {"payload": payload, "context": plugin._editor_context(selection), "targets": targets}


func _run() -> void:
	await get_tree().create_timer(2.0).timeout
	var plugin: EditorPlugin
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			plugin = node as EditorPlugin
	_check(plugin != null, "Joinery plugin must load")
	if plugin == null:
		get_tree().quit(1)
		return
	await _chat_layout(plugin)
	await _accepted_model_preview(plugin)
	var box := {
		"summary": "Create a one metre box",
		"operations": [{"operation": "create_box", "name": "ReviewBox", "size": [1, 1, 1]}]
	}
	if OS.get_environment("JOINERY_EMPTY_SCENE") == "1":
		await _empty_scene(plugin, box)
	else:
		await _existing_scene(plugin, box)
	print("editor scene edits: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


func _chat_layout(plugin: EditorPlugin) -> void:
	var dock: Control = plugin._dock
	_check(not dock.is_ancestor_of(dock._review), "Review controls live outside the chat")
	dock._prompt.text = "/art"
	dock._update_commands("/art")
	for index: int in 4:
		await get_tree().process_frame
	var popup: Rect2 = dock._command_popup.get_global_rect()
	var prompt: Rect2 = dock._prompt.get_global_rect()
	_check(dock._command_popup.visible, "Slash command completion is visible")
	_check(popup.end.y < prompt.position.y, "Entire command popup sits above the input")
	_check(popup.size.y > dock.get_theme_default_font_size() * 1.5, "A command row is not clipped")
	await _capture("joinery-command-popup.png")
	dock._prompt.text = "/help"
	dock._update_commands("/help")
	var enter := InputEventKey.new()
	enter.keycode = KEY_ENTER
	enter.pressed = true
	dock._on_prompt_input(enter)
	_check(
		not dock._command_popup.visible and dock._prompt.text.is_empty(),
		"Enter submits slash commands"
	)
	var lines := PackedStringArray()
	for index: int in 250:
		lines.append("+ Long reviewed scene change %d with properties and source context" % index)
	dock.show_file_review("Large technical fixture import", "\n".join(lines))
	for index: int in 6:
		await get_tree().process_frame
	var bar: VScrollBar = dock._review_scroll.get_v_scroll_bar()
	_check(bar.max_value > bar.page, "Large import review has scrollable content")
	dock._review_scroll.scroll_vertical = int(bar.max_value)
	await get_tree().process_frame
	_check(dock._review_scroll.scroll_vertical > 0, "Import review can scroll to its end")
	var choices: Control = dock._review.get_child(dock._review.get_child_count() - 1)
	_check(
		choices.get_global_rect().end.y <= dock.get_viewport_rect().end.y,
		"Accept and Reject remain on screen"
	)
	_check(
		dock._prompt.get_global_rect().end.y <= dock.get_viewport_rect().end.y,
		"Input remains on screen during review"
	)
	await _capture("joinery-scroll-review.png")
	dock.hide_edit_review()
	await get_tree().process_frame


func _accepted_model_preview(plugin: EditorPlugin) -> void:
	var model := Node3D.new()
	var mesh := MeshInstance3D.new()
	mesh.mesh = BoxMesh.new()
	model.add_child(mesh)
	mesh.owner = model
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	_check(document.append_from_scene(model, state) == OK, "Export technical model fixture")
	DirAccess.make_dir_recursive_absolute("res://art/models")
	_check(
		document.write_to_filesystem(state, "res://art/models/accepted.glb") == OK,
		"Write portable fixture"
	)
	model.free()
	plugin._file_review = {"transaction_id": "accepted-model"}
	await plugin._on_file_decision(
		{
			"transaction_id": "accepted-model",
			"accepted": true,
			"changes": [{"path": "art/models/accepted.glb", "change": "created"}]
		}
	)
	var deadline := Time.get_ticks_msec() + 10000
	while (
		(
			plugin._workspace._current.get("path") != "art/models/accepted.glb"
			or (
				plugin
				. _workspace
				. _preview
				. find_children("*", "SubViewportContainer", true, false)
				. is_empty()
			)
		)
		and Time.get_ticks_msec() < deadline
	):
		await get_tree().process_frame
	_check(plugin._workspace.visible, "Accepted model opens the main asset viewer")
	_check(
		plugin._workspace._selected_path == "art/models/accepted.glb",
		"Accepted model is selected for preview"
	)
	_check(
		not (
			plugin
			. _workspace
			. _preview
			. find_children("*", "SubViewportContainer", true, false)
			. is_empty()
		),
		"Accepted model has visible 3D geometry preview"
	)
	await _capture("joinery-accepted-model.png")
	EditorInterface.set_main_screen_editor("3D")


func _existing_scene(plugin: EditorPlugin, box: Dictionary) -> void:
	var scene := EditorInterface.get_edited_scene_root() as Node3D
	_check(scene != null, "Test scene must be open")
	if scene == null:
		return
	var bytes := FileAccess.get_file_as_bytes(scene.scene_file_path)
	var result: Dictionary = plugin._preview_scene_edit(_event(plugin, box))
	_check(result["success"], "Create box in the live editor")
	var node := scene.get_node_or_null("ReviewBox") as MeshInstance3D
	_check(node != null and node.mesh.size == Vector3.ONE, "One metre native BoxMesh")
	_check(plugin._dock._review.visible, "Show Accept/Reject with the live preview")
	_check(
		FileAccess.get_file_as_bytes(scene.scene_file_path) == bytes,
		"A preview must not write the scene file"
	)
	plugin._dock.reject_edit_requested.emit()
	_check(not scene.has_node("ReviewBox"), "Reject removes preview geometry")
	_check(not plugin._edits.pending(), "Reject clears the transaction")
	result = plugin._preview_scene_edit(_event(plugin, box))
	_check(result["success"], "Create a second preview")
	plugin._dock.accept_edit_requested.emit()
	_check(not plugin._edits.pending(), "Accept commits the preview")
	_check(scene.has_node("ReviewBox"), "Accepted box remains in the scene")
	var packed := PackedScene.new()
	_check(packed.pack(scene) == OK, "Accepted changes serialize as a normal Godot scene")
	var reloaded := packed.instantiate()
	_check(
		reloaded.get_node("ReviewBox").mesh.size == Vector3.ONE,
		"Native scene round-trip preserves the one metre box"
	)
	reloaded.free()
	_check(_history(plugin).undo(), "Accepted creation supports editor undo")
	_check(not scene.has_node("ReviewBox"), "Undo removes the accepted box")
	_check(_history(plugin).redo(), "Accepted creation supports redo")
	_check(scene.has_node("ReviewBox"), "Redo restores the box")
	_history(plugin).undo()
	var target := scene.get_node("Existing") as Node3D
	var before := target.transform
	var id := scene.scene_file_path + "::Existing"
	var move := {
		"summary": "Move left and double width",
		"operations":
		[
			{
				"operation": "set_transform",
				"target": id,
				"relative": true,
				"position": [-1, 0, 0],
				"scale": [2, 1, 1]
			}
		]
	}
	result = plugin._preview_scene_edit(_event(plugin, move, [id]))
	_check(result["success"], "Selected-object transform previews")
	_check(target.position == Vector3(1, 3, 4), "Target moved left by one metre")
	_check(target.scale == Vector3(2, 1, 1), "Target doubled in width")
	plugin._dock.accept_edit_requested.emit()
	_check(_history(plugin).undo(), "Transform supports undo")
	_check(target.transform == before, "Undo restores exact transform")
	_history(plugin).redo()
	_check(target.position == Vector3(1, 3, 4), "Redo reapplies transform")
	_history(plugin).undo()
	var removal := {
		"summary": "Delete selected object",
		"operations": [{"operation": "delete_node", "target": id}]
	}
	result = plugin._preview_scene_edit(_event(plugin, removal, [id]))
	_check(result["success"], "Deletion previews through the shared editor operation family")
	_check(not scene.has_node("Existing"), "Deletion preview removes the original object")
	plugin._dock.reject_edit_requested.emit()
	_check(scene.get_node("Existing") == target, "Reject restores original object identity")
	_check(target.owner == scene, "Reject restores serialization ownership")
	result = plugin._preview_scene_edit(_event(plugin, removal, [id]))
	_check(result["success"], "Deletion can be requested again")
	plugin._dock.accept_edit_requested.emit()
	_check(not scene.has_node("Existing"), "Accepted deletion keeps the object removed")
	_check(_history(plugin).undo(), "Delete supports editor undo")
	_check(scene.get_node("Existing") == target, "Undo deletion restores original object")
	_check(target.owner == scene, "Undo deletion restores ownership")
	_check(_history(plugin).redo(), "Delete supports editor redo")
	_check(not scene.has_node("Existing"), "Redo removes the object again")
	_history(plugin).undo()
	var renamed := {
		"summary": "Rename selected object",
		"operations": [{"operation": "rename_node", "target": id, "name": "Renamed"}]
	}
	result = plugin._preview_scene_edit(_event(plugin, renamed, [id]))
	_check(result["success"] and scene.has_node("Renamed"), "Rename previews")
	plugin._dock.accept_edit_requested.emit()
	_history(plugin).undo()
	_check(scene.get_node("Existing") == target, "Rename undo restores its original name")
	_native_families(plugin, scene, target, id)
	_instanced_scene(plugin, scene)
	_combined_accept_conflict(plugin, target, move, id)
	var stale := _event(plugin, box)
	plugin._scene_revision += 1
	_check(not plugin._preview_scene_edit(stale)["success"], "Reject cancelled request")
	var identity := _event(plugin, move, [id])
	scene.remove_child(target)
	var replacement := Node3D.new()
	replacement.name = "Existing"
	scene.add_child(replacement)
	_check(
		not plugin._preview_scene_edit(identity)["success"],
		"Never modify a replacement object with the same path"
	)
	scene.remove_child(replacement)
	replacement.free()
	scene.add_child(target)
	target.owner = scene
	result = plugin._preview_scene_edit(_event(plugin, box))
	_check(result["success"], "Preview before save")
	plugin._apply_changes()
	_check(not scene.has_node("ReviewBox"), "Pending previews are removed before saving")
	_check(
		FileAccess.get_file_as_bytes(scene.scene_file_path) == bytes,
		"Original disk file is unchanged after previews and undo"
	)
	_check(
		DirAccess.dir_exists_absolute("res://.joinery/transactions"),
		"Accepted edits have native-operation records"
	)
	await get_tree().process_frame
	if OS.get_environment("JOINERY_SCENE_EDIT_LIVE_CRUD") == "1":
		await _live_crud(plugin, scene, target)


func _instanced_scene(plugin: EditorPlugin, scene: Node3D) -> void:
	var native := Node3D.new()
	native.name = "NativeSource"
	var child := MeshInstance3D.new()
	child.name = "SourcePart"
	child.mesh = BoxMesh.new()
	native.add_child(child)
	child.owner = native
	var packed := PackedScene.new()
	_check(packed.pack(native) == OK, "Pack technical source scene")
	_check(ResourceSaver.save(packed, "res://source.tscn") == OK, "Save source scene")
	native.free()
	var edit := {
		"summary": "Place source instance",
		"operations":
		[
			{
				"operation": "instantiate_scene",
				"resource_path": "res://source.tscn",
				"name": "PlacedSource"
			}
		]
	}
	var result: Dictionary = plugin._preview_scene_edit(_event(plugin, edit))
	_check(result["success"], "Instantiate an existing source scene in the live editor")
	var instance := scene.get_node_or_null("PlacedSource")
	_check(instance != null and instance.has_node("SourcePart"), "Instance retains source parts")
	plugin._accept_scene_edit()
	_check(
		_history(plugin).undo() and not scene.has_node("PlacedSource"), "Instance placement undo"
	)
	_history(plugin).redo()
	_check(
		scene.get_node("PlacedSource").owner == scene, "Instance placement redo retains ownership"
	)
	packed = PackedScene.new()
	_check(packed.pack(scene) == OK, "Scene with source instance serializes")
	var reopened := packed.instantiate()
	_check(
		reopened.has_node("PlacedSource/SourcePart"), "Source instance survives scene round trip"
	)
	reopened.free()
	_history(plugin).undo()


func _combined_accept_conflict(
	plugin: EditorPlugin, target: Node3D, move: Dictionary, id: String
) -> void:
	var result: Dictionary = plugin._preview_scene_edit(_event(plugin, move, [id]))
	_check(result["success"], "Combined review has a live preview")
	plugin._file_review = {"transaction_id": "fixture", "changes": []}
	plugin._file_accept_live_id = plugin._edits.transaction_id
	target.position.x += 5.0
	var connected: bool = plugin._chat._connected
	plugin._chat._connected = false
	plugin._on_file_decision({"transaction_id": "fixture", "accepted": true})
	plugin._chat._connected = connected
	_check(plugin._edits.pending(), "Stale live preview cannot be accepted with file changes")
	_check(not plugin._file_review.is_empty(), "File review waits for compensating restoration")
	plugin._on_file_decision({"transaction_id": "fixture", "accepted": false})
	_check(plugin._dock._review.visible, "User can still reject the stale live preview")
	plugin._reject_scene_edit()


func _native_families(plugin: EditorPlugin, scene: Node3D, target: Node3D, id: String) -> void:
	var duplicate := {
		"summary": "Duplicate",
		"operations": [{"operation": "duplicate_node", "target": id, "name": "Copy"}]
	}
	var result: Dictionary = plugin._preview_scene_edit(_event(plugin, duplicate, [id]))
	_check(result["success"] and scene.has_node("Copy"), "Generic duplication previews")
	plugin._accept_scene_edit()
	_check(_history(plugin).undo() and not scene.has_node("Copy"), "Duplicate undo")
	_history(plugin).redo()
	_check(scene.get_node("Copy").owner == scene, "Duplicate redo preserves owner")
	_history(plugin).undo()
	var parent := Node3D.new()
	parent.name = "Destination"
	scene.add_child(parent)
	parent.owner = scene
	parent.position = Vector3(7.125, 2.75, -1.5)
	var parent_id := scene.scene_file_path + "::Destination"
	var transform := target.transform
	var global := target.global_transform
	var move := {
		"summary": "Reparent",
		"operations": [{"operation": "reparent_node", "target": id, "parent": parent_id}]
	}
	result = plugin._preview_scene_edit(_event(plugin, move, [id, parent_id]))
	_check(result["success"] and target.get_parent() == parent, "Reparent previews")
	_check(target.global_transform.is_equal_approx(global), "Reparent preserves global transform")
	plugin._reject_scene_edit()
	_check(
		target.get_parent() == scene and target.transform == transform,
		"Reparent rejection restores exact local transform"
	)
	var script_file := FileAccess.open("res://receiver.gd", FileAccess.WRITE)
	script_file.store_string("@tool\nextends Node3D\nfunc receive() -> void:\n\tpass\n")
	script_file.close()
	var attach := {
		"summary": "Attach script",
		"operations":
		[{"operation": "attach_script", "target": id, "script_path": "res://receiver.gd"}]
	}
	result = plugin._preview_scene_edit(_event(plugin, attach, [id]))
	_check(result["success"] and target.get_script() != null, "Attach compiled project script")
	plugin._accept_scene_edit()
	_check(
		_history(plugin).undo() and target.get_script() == null, "Script attachment supports undo"
	)
	_history(plugin).redo()
	var root_id := scene.scene_file_path + "::."
	var connect_op := {
		"summary": "Connect signal",
		"operations":
		[
			{
				"operation": "connect_signal",
				"target": root_id,
				"receiver": id,
				"signal_name": "renamed",
				"method_name": "receive"
			}
		]
	}
	result = plugin._preview_scene_edit(_event(plugin, connect_op, [root_id, id]))
	var callback := Callable(target, "receive")
	_check(
		result["success"] and scene.is_connected("renamed", callback),
		"Persistent signal connection previews"
	)
	plugin._accept_scene_edit()
	var packed := PackedScene.new()
	_check(
		packed.pack(scene) == OK and packed.get_state().get_connection_count() > 0,
		"Signal connections serialize in normal scenes"
	)
	_check(
		_history(plugin).undo() and not scene.is_connected("renamed", callback),
		"Signal connection undo"
	)
	parent.free()


func _live_crud(plugin: EditorPlugin, scene: Node3D, target: Node3D) -> void:
	var deadline := Time.get_ticks_msec() + 20000
	while not plugin._chat._connected and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	_check(plugin._chat._connected, "Live CRUD agent connects")
	plugin._dock._on_submit(
		"Delete Existing from the live scene. Use the editor tool and leave a review preview."
	)
	deadline = Time.get_ticks_msec() + 90000
	while (not plugin._edits.pending() or plugin._dock._busy) and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	_check(
		plugin._edits.pending() and not scene.has_node("Existing"),
		"Natural-language deletion invokes live operation"
	)
	if plugin._edits.pending():
		plugin._reject_scene_edit()
	_check(
		scene.get_node_or_null("Existing") == target, "Reject real agent deletion restores identity"
	)
	print(plugin._dock._transcript.get_parsed_text())


func _empty_scene(plugin: EditorPlugin, box: Dictionary) -> void:
	_check(EditorInterface.get_edited_scene_root() == null, "Start with a truly empty editor")
	var result: Dictionary
	if OS.get_environment("JOINERY_SCENE_EDIT_LIVE") == "1":
		var deadline := Time.get_ticks_msec() + 15000
		while not plugin._chat._connected and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._chat._connected, "Codex must connect")
		plugin._dock._on_submit("create box 1 by 1 matre")
		deadline = Time.get_ticks_msec() + 90000
		while not plugin._edits.pending() and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._edits.pending(), "The actual model must invoke the scene-edit tool")
		if not plugin._edits.pending():
			print(plugin._dock._transcript.get_parsed_text())
			return
		var mesh := plugin._edits.root.get_child(0) as MeshInstance3D
		_check(
			mesh != null and mesh.mesh.size == Vector3.ONE, "Actual request makes a one metre box"
		)
		deadline = Time.get_ticks_msec() + 30000
		while plugin._dock._busy and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(not plugin._dock._busy, "Codex must finish the turn after the tool responds")
		await get_tree().create_timer(1.0).timeout
		await _capture()
		plugin._reject_scene_edit()
	else:
		result = plugin._preview_scene_edit(_event(plugin, box))
		_check(result["success"], "Preview in an editor with no scene root")
		_check(
			EditorInterface.get_edited_scene_root() == null,
			"Preview must not install a scene root before acceptance"
		)
		_check(plugin._edits.root.has_node("ReviewBox"), "Empty-editor preview has geometry")
		plugin._dock.reject_edit_requested.emit()
	_check(EditorInterface.get_edited_scene_root() == null, "Reject restores the empty editor")
	result = plugin._preview_scene_edit(_event(plugin, box))
	_check(result["success"], "Preview again after rejecting")
	plugin._dock.accept_edit_requested.emit()
	var scene := EditorInterface.get_edited_scene_root() as Node3D
	_check(scene != null and scene.has_node("ReviewBox"), "Accept adopts a normal Godot scene")
	_check(not plugin._edits.pending(), "Empty-scene acceptance completes")
	_check(_history(plugin).undo(), "Undo creation in new scene")
	_check(not scene.has_node("ReviewBox"), "Undo removes geometry from new scene")
	_check(_history(plugin).redo(), "Redo creation in new scene")
	_check(scene.has_node("ReviewBox"), "Redo restores geometry in new scene")
	await get_tree().process_frame


func _capture(filename: String = "joinery-live-box-preview.png") -> void:
	var directory := OS.get_environment("JOINERY_CAPTURE_DIR")
	if directory.is_empty():
		directory = OS.get_cache_dir().path_join("joinery-native-captures")
	var error := DirAccess.make_dir_recursive_absolute(directory)
	_check(error == OK, "Create screenshot directory: " + directory)
	if error != OK:
		return
	RenderingServer.force_draw()
	var image := EditorInterface.get_base_control().get_viewport().get_texture().get_image()
	_check(image != null and not image.is_empty(), "Capture a rendered editor framebuffer")
	if image == null or image.is_empty():
		return
	var path := directory.path_join(filename)
	_check(image.save_png(path) == OK, "Save native editor screenshot: " + path)


func _history(plugin: EditorPlugin) -> UndoRedo:
	var manager := plugin.get_undo_redo()
	return manager.get_history_undo_redo(
		manager.get_object_history_id(EditorInterface.get_edited_scene_root())
	)
