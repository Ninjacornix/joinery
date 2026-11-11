@tool
extends RefCounted
## The before/after of a live scene edit, from the editor's own 3D viewport: one frame taken
## just before the edit is applied ([method take_before]) and one when its review opens
## ([method media]). Both are the same camera, so the review shows them as one split picture.
## Outside the editor, or when the viewport has nothing drawn yet, there is no snapshot and the
## review points at the viewport instead. The review's "Open in editor" selects the edit's
## nodes in the live scene ([method open_in_editor]).

## Longest side of a saved snapshot, in pixels.
const MAX_SIDE := 960

## The frame before the edit (null when none was taken).
static var before: Image
## The nodes the live edit changed (scene_edits.gd sets them with the preview).
static var nodes: Array[Node] = []


## Take the viewport's current frame as the scene before the edit.
static func take_before() -> void:
	before = capture()


## Forget the frame taken before the edit.
static func forget() -> void:
	before = null


## True when the changed nodes are still in the edited scene, so they can be shown there.
static func can_open() -> bool:
	return not _live_nodes().is_empty()


## Show the change in the editor: the 3D view, with the changed nodes selected and the first
## one in the Inspector. The same selection the edit makes when it is previewed.
static func open_in_editor() -> void:
	var changed := _live_nodes()
	var editor := Engine.get_singleton("EditorInterface")
	if changed.is_empty() or editor == null:
		return
	editor.call("set_main_screen_editor", "3D")
	var selection: Object = editor.call("get_selection")
	selection.call("clear")
	for node: Node in changed:
		selection.call("add_node", node)
	editor.call("edit_node", changed[0])


static func _live_nodes() -> Array[Node]:
	var found: Array[Node] = []
	var editor := Engine.get_singleton("EditorInterface") if Engine.is_editor_hint() else null
	var scene := editor.call("get_edited_scene_root") as Node if editor != null else null
	if scene == null:
		return found
	# Only nodes of the scene being edited: a preview in an empty scene has nothing to select.
	for node: Node in nodes:
		if is_instance_valid(node) and (node == scene or scene.is_ancestor_of(node)):
			found.append(node)
	return found


## The live edit as one review media item ({path, media: "image", before, after}) with both
## frames saved under the user cache, named after the edited scene; empty when either frame
## is missing.
static func media() -> Array:
	var after := capture()
	if before == null or after == null:
		return []
	var editor := Engine.get_singleton("EditorInterface")
	var scene := editor.call("get_edited_scene_root") as Node if editor != null else null
	var scene_path := scene.scene_file_path if scene != null else ""
	var folder := OS.get_cache_dir().path_join("joinery-review")
	DirAccess.make_dir_recursive_absolute(folder)
	# Only the latest edit's frames are kept.
	for old: String in DirAccess.get_files_at(folder):
		if old.begins_with("scene-"):
			DirAccess.remove_absolute(folder.path_join(old))
	var stamp := str(Time.get_ticks_usec())
	var paths := [
		folder.path_join("scene-before-%s.png" % stamp),
		folder.path_join("scene-after-%s.png" % stamp)
	]
	if before.save_png(paths[0]) != OK or after.save_png(paths[1]) != OK:
		return []
	var path := scene_path.trim_prefix("res://")
	return [
		{
			"path": path if not path.is_empty() else "Scene",
			"media": "image",
			"label": "Viewport",
			"before": paths[0],
			"after": paths[1],
		}
	]


## The editor's first 3D viewport as an image, scaled to MAX_SIDE; null outside the editor or
## when it has not drawn.
static func capture() -> Image:
	if not Engine.is_editor_hint() or DisplayServer.get_name() == "headless":
		return null
	var editor := Engine.get_singleton("EditorInterface")
	if editor == null:
		return null
	var viewport := editor.call("get_editor_viewport_3d", 0) as SubViewport
	if viewport == null or viewport.size.x <= 0 or viewport.size.y <= 0:
		return null
	var image := viewport.get_texture().get_image()
	if image == null or image.is_empty():
		return null
	var longest := maxi(image.get_width(), image.get_height())
	if longest > MAX_SIDE:
		var scale := float(MAX_SIDE) / longest
		image.resize(
			maxi(1, int(image.get_width() * scale)),
			maxi(1, int(image.get_height() * scale)),
			Image.INTERPOLATE_BILINEAR
		)
	return image
