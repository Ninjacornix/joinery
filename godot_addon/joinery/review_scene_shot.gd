@tool
extends Node
## Pictures of a scene variant for its review (ADR 0066): the base (or the variant as it
## was) and the staged variant, each drawn in an offscreen SubViewport of its own world and
## saved where the review item expects its "before" and "after" files. Both sides share one
## framing (the union of their bounds), so the split view compares like with like. A 3D
## scene gets a three-quarter camera, a light and a plain sky when it has none; a 2D scene
## a camera fitted to its sprites and polygons; a UI scene is drawn as laid out.
##
## The item names the scenes as `scenes: {before, after}` (res:// or absolute paths, the
## staged variant lives outside the project). A side that can't load or draw fails the
## item with the reason ([signal failed]); review_media.gd shows it like a failed render.

## Both pictures of [param item] are written.
signal finished(item: Dictionary)
## [param item] couldn't be drawn: [param reason] says why.
signal failed(item: Dictionary, reason: String)

## Pixels of each picture.
const SIZE := Vector2i(640, 400)
## Frames a scene is given to settle before it is captured.
const SETTLE := 3
const BACKGROUND := Color("2e3034")

var _queue: Array[Dictionary] = []
var _busy := false
## Bumped by [method cancel]: a picture still being drawn then reports nothing.
var _generation := 0


## Draw [param item]'s scenes into its before/after files (once per item at a time).
func request(item: Dictionary) -> void:
	var path := str(item.get("path", ""))
	for queued: Dictionary in _queue:
		if str(queued.get("path", "")) == path:
			return
	_queue.append(item)
	if not _busy:
		_next.call_deferred()


## Drop the queue; what is being drawn now is finished but not reported.
func cancel() -> void:
	_queue.clear()
	_generation += 1


## True while pictures are being drawn.
func is_busy() -> bool:
	return _busy


func _next() -> void:
	if _queue.is_empty() or not is_inside_tree():
		_busy = false
		return
	_busy = true
	var item: Dictionary = _queue.pop_front()
	var generation := _generation
	var reason: String = await _shoot(item)
	if generation != _generation:
		pass
	elif reason.is_empty():
		finished.emit(item)
	else:
		failed.emit(item, reason)
	_next.call_deferred()


## Both sides of [param item]; "" when written, else why not.
func _shoot(item: Dictionary) -> String:
	if DisplayServer.get_name() == "headless":
		return "No picture without a display. The overrides are listed below."
	var scenes: Dictionary = item.get("scenes", {})
	var sides: Array[String] = []
	var roots: Array[Node] = []
	for side: String in ["before", "after"]:
		var source := str(scenes.get(side, ""))
		if source.is_empty() or not item.has(side):
			continue
		var packed := _load(source)
		if packed == null:
			_free(roots)
			return "%s can't be opened, so it has no picture." % source.get_file()
		var root := packed.instantiate()
		if root == null:
			_free(roots)
			return "%s can't be opened, so it has no picture." % source.get_file()
		sides.append(side)
		roots.append(root)
	if roots.is_empty():
		return "Nothing to draw."
	var viewport := SubViewport.new()
	viewport.size = SIZE
	viewport.own_world_3d = true
	viewport.transparent_bg = false
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	viewport.render_target_clear_mode = SubViewport.CLEAR_MODE_ALWAYS
	add_child(viewport)
	var three := roots.any(func(root: Node) -> bool: return _is_3d(root))
	var stage := _stage_3d(viewport, roots) if three else _stage_2d(viewport, roots)
	var reason := ""
	for index: int in roots.size():
		var root := roots[index]
		stage.add_child(root)
		# An editor in the background draws only when something asks it to: draw it here.
		for frame: int in SETTLE:
			await get_tree().process_frame
			RenderingServer.force_draw(false)
		var image := viewport.get_texture().get_image()
		stage.remove_child(root)
		root.free()
		if image == null or image.is_empty():
			reason = "The editor couldn't draw the scene."
			break
		var target := str(item[sides[index]])
		DirAccess.make_dir_recursive_absolute(target.get_base_dir())
		if image.save_png(target) != OK:
			reason = "The picture couldn't be saved."
			break
	for index: int in range(roots.size()):
		if is_instance_valid(roots[index]):
			roots[index].free()
	viewport.queue_free()
	return reason


static func _load(source: String) -> PackedScene:
	if not source.begins_with("res://") and not FileAccess.file_exists(source):
		return null
	if source.begins_with("res://") and not ResourceLoader.exists(source):
		return null
	return (
		ResourceLoader.load(source, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE) as PackedScene
	)


static func _free(roots: Array[Node]) -> void:
	for root: Node in roots:
		if is_instance_valid(root):
			root.free()


static func _is_3d(root: Node) -> bool:
	return root is Node3D or not root.find_children("*", "VisualInstance3D", true, false).is_empty()


## A camera, light and sky around the shared bounds; returns the node the scenes go under.
static func _stage_3d(viewport: SubViewport, roots: Array[Node]) -> Node:
	var bounds := AABB()
	var found := false
	for root: Node in roots:
		for node: Node in [root] + root.find_children("*", "GeometryInstance3D", true, false):
			if not node is GeometryInstance3D:
				continue
			var box := _to_root(node, root) * (node as VisualInstance3D).get_aabb()
			bounds = bounds.merge(box) if found else box
			found = true
	if not found:
		bounds = AABB(Vector3(-0.5, 0.0, -0.5), Vector3.ONE)
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = BACKGROUND
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.75, 0.75, 0.8)
	environment.ambient_light_energy = 0.6
	var world := WorldEnvironment.new()
	world.environment = environment
	viewport.add_child(world)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50.0, 35.0, 0.0)
	viewport.add_child(sun)
	var camera := Camera3D.new()
	camera.fov = 40.0
	var center := bounds.get_center()
	var radius := maxf(bounds.size.length() * 0.5, 0.05)
	var aspect := float(SIZE.x) / float(SIZE.y)
	var half := deg_to_rad(camera.fov * 0.5)
	var reach := minf(half, atan(tan(half) * aspect))
	var distance := radius / sin(reach) * 1.1
	var direction := Vector3(1.0, 0.8, 1.3).normalized()
	camera.near = maxf(distance * 0.01, 0.001)
	camera.far = maxf(distance * 10.0, 10.0)
	camera.position = center + direction * distance
	camera.basis = Basis.looking_at(-direction, Vector3.UP)
	viewport.add_child(camera)
	camera.current = true
	return viewport


## A camera fitted to the shared bounds of the scenes' sprites and polygons.
static func _stage_2d(viewport: SubViewport, roots: Array[Node]) -> Node:
	var bounds := Rect2()
	var found := false
	for root: Node in roots:
		for node: Node in [root] + root.find_children("*", "CanvasItem", true, false):
			var rect := _rect_of(node, root)
			if rect.size == Vector2.ZERO and rect.position == Vector2.ZERO:
				continue
			bounds = bounds.merge(rect) if found else rect
			found = true
	var ground := ColorRect.new()
	ground.color = BACKGROUND
	ground.size = Vector2(SIZE)
	var back := CanvasLayer.new()
	back.layer = -100
	back.add_child(ground)
	viewport.add_child(back)
	if roots.all(func(root: Node) -> bool: return root is Control):
		return viewport
	var camera := Camera2D.new()
	if found:
		bounds = bounds.grow(maxf(bounds.size.x, bounds.size.y) * 0.08 + 2.0)
		camera.position = bounds.get_center()
		var fit := minf(SIZE.x / maxf(bounds.size.x, 1.0), SIZE.y / maxf(bounds.size.y, 1.0))
		camera.zoom = Vector2.ONE * clampf(fit, 0.05, 64.0)
	viewport.add_child(camera)
	camera.make_current()
	return viewport


## The rectangle [param node] draws, in [param root]'s space (empty when it draws nothing
## measurable).
static func _rect_of(node: Node, root: Node) -> Rect2:
	var local := Rect2()
	if node is Sprite2D and (node as Sprite2D).texture != null:
		local = (node as Sprite2D).get_rect()
	elif node is Polygon2D and not (node as Polygon2D).polygon.is_empty():
		var points := (node as Polygon2D).polygon
		local = Rect2(points[0], Vector2.ZERO)
		for point: Vector2 in points:
			local = local.expand(point)
	elif node is AnimatedSprite2D:
		local = Rect2(-8.0, -8.0, 16.0, 16.0)
	elif node is Line2D and (node as Line2D).get_point_count() > 0:
		var line := node as Line2D
		local = Rect2(line.get_point_position(0), Vector2.ZERO)
		for index: int in line.get_point_count():
			local = local.expand(line.get_point_position(index))
	else:
		return Rect2()
	return _to_root_2d(node, root) * local


static func _to_root_2d(node: Node, root: Node) -> Transform2D:
	var result := Transform2D.IDENTITY
	var walk := node
	while walk != null:
		if walk is Node2D:
			result = (walk as Node2D).transform * result
		if walk == root:
			break
		walk = walk.get_parent()
	return result


static func _to_root(node: Node, root: Node) -> Transform3D:
	var result := Transform3D.IDENTITY
	var walk := node
	while walk != null:
		if walk is Node3D:
			result = (walk as Node3D).transform * result
		if walk == root:
			break
		walk = walk.get_parent()
	return result
