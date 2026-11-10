@tool
extends RefCounted
## The editor's viewport as the user sees it, saved as an attachment: the 3D viewport, or the 2D
## one when that is the main screen showing (or the open scene is 2D while another screen
## shows). "Share my view" in the composer and the agent's view_editor tool both use it.
##
## The result is {success, path, view, width, height, camera, scene, selected}, or
## {success: false, message}. The camera is {position, rotation_degrees, forward, projection,
## fov or size} in 3D and {zoom, visible_rect} in 2D.

const ATTACHMENTS := preload("chat_attachments.gd")


## "3D" or "2D": the viewport the user is looking at, or the open scene's kind otherwise.
static func active_view() -> String:
	var main := EditorInterface.get_editor_main_screen()
	if main != null:
		for child: Node in main.get_children():
			if child is Control and (child as Control).visible:
				match child.get_class():
					"Node3DEditor":
						return "3D"
					"CanvasItemEditor":
						return "2D"
	var root := EditorInterface.get_edited_scene_root()
	return "2D" if root is Node2D or root is Control else "3D"


## Capture the viewport the user is looking at and save it under .joinery/attachments.
static func capture() -> Dictionary:
	if not Engine.is_editor_hint():
		return _failed("Open the project in the Joinery editor to share the view.")
	var view := active_view()
	var viewport: SubViewport = (
		EditorInterface.get_editor_viewport_3d(0)
		if view == "3D"
		else EditorInterface.get_editor_viewport_2d()
	)
	if viewport == null or viewport.get_texture() == null:
		return _failed("The %s viewport isn't available." % view)
	var image := viewport.get_texture().get_image()
	if image == null or image.is_empty():
		return _failed("The %s viewport couldn't be captured." % view)
	var entry := ATTACHMENTS.save(image, "%s view" % view, "view")
	if entry.is_empty():
		return _failed("Couldn't save the screenshot.")
	var root := EditorInterface.get_edited_scene_root()
	var selected: Array[String] = []
	if root != null:
		for node: Node in EditorInterface.get_selection().get_selected_nodes():
			if node == root or root.is_ancestor_of(node):
				selected.append(str(root.get_path_to(node)))
	return {
		"success": true,
		"entry": entry,
		"path": entry["path"],
		"view": view,
		"width": entry["width"],
		"height": entry["height"],
		"camera": camera_3d(viewport) if view == "3D" else camera_2d(viewport),
		"scene": root.scene_file_path if root != null else "",
		"selected": selected,
	}


## Where the 3D editor camera is and what it sees.
static func camera_3d(viewport: Viewport) -> Dictionary:
	var camera := viewport.get_camera_3d()
	if camera == null:
		return {}
	var forward := -camera.global_transform.basis.z.normalized()
	var orthogonal := camera.projection == Camera3D.PROJECTION_ORTHOGONAL
	var info := {
		"position": _vector(camera.global_position),
		"rotation_degrees": _vector(camera.global_rotation_degrees),
		"forward": _vector(forward),
		"projection": "orthogonal" if orthogonal else "perspective",
	}
	if orthogonal:
		info["size"] = snappedf(camera.size, 0.001)
	else:
		info["fov"] = snappedf(camera.fov, 0.01)
	return info


## The 2D editor's zoom and the part of the canvas on screen, in canvas units.
static func camera_2d(viewport: Viewport) -> Dictionary:
	var transform := viewport.global_canvas_transform
	var shown := transform.affine_inverse() * Rect2(Vector2.ZERO, Vector2(viewport.size))
	return {
		"zoom": snappedf(transform.get_scale().x, 0.001),
		"visible_rect":
		[
			snappedf(shown.position.x, 0.01),
			snappedf(shown.position.y, 0.01),
			snappedf(shown.size.x, 0.01),
			snappedf(shown.size.y, 0.01),
		],
	}


static func _vector(value: Vector3) -> Array[float]:
	return [snappedf(value.x, 0.001), snappedf(value.y, 0.001), snappedf(value.z, 0.001)]


static func _failed(message: String) -> Dictionary:
	return {"success": false, "message": message}
