@tool
extends SubViewportContainer
## An orbitable view of one scene in its own world, lit by Godot's empty-editor sky and sun.
## Drag to orbit; the wheel, a trackpad pinch or two-finger scroll zoom. The camera frames the
## meshes' bounds whenever the view is resized. A click (press and release without dragging)
## picks the part under the pointer.

## A click hit a part: {object, material, maps, surface, ...} from part_pick.gd.
signal part_clicked(hit: Dictionary)

const CONTEXT_SCRIPT := preload("preview_context.gd")
const PICK_SCRIPT := preload("part_pick.gd")
const UI_THEME := preload("ui_theme.gd")
## A press that moves less than this many pixels before release is a click, not an orbit.
const CLICK_SLOP := 4.0
## Minimum side as a multiple of the editor font size.
const MIN_EM := 14.0
## Orbit radians per dragged pixel, and the zoom range as multiples of the model radius.
const ORBIT_SPEED := 0.01
const ZOOM_RANGE := Vector2(0.05, 30.0)

var camera: Camera3D
## The shown model's root, for picking.
var model: Node
var _pivot: Node3D
var _viewport: SubViewport
var _bounds := AABB()
var _radius := 0.5
var _world: WorldEnvironment
var _press_at := Vector2.INF
var _highlighted := ""


func _init() -> void:
	stretch = true
	mouse_target = false
	mouse_force_pass_scroll_events = false
	size_flags_vertical = Control.SIZE_EXPAND_FILL


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED:
		var side := roundf(UI_THEME.font_size(self, "body") * MIN_EM)
		custom_minimum_size = Vector2(side, side)
	elif what == NOTIFICATION_EXIT_TREE and _world != null:
		# Let the sky go while the renderer still exists. A viewer freed later, as at
		# editor shutdown, would otherwise leak the sky's radiance textures. Viewers are
		# freed when they leave the workspace, never shown again.
		_world.environment = null
		_world = null


## Instantiate [param scene] into a fresh world with a camera aimed at its meshes.
func show_scene(scene: PackedScene) -> void:
	_viewport = SubViewport.new()
	_viewport.own_world_3d = true
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	add_child(_viewport)
	var root := Node3D.new()
	_viewport.add_child(root)
	var instance := scene.instantiate(PackedScene.GEN_EDIT_STATE_DISABLED)
	root.add_child(instance)
	model = instance
	var meshes: Array[Node] = instance.find_children("*", "MeshInstance3D", true, false)
	if instance is MeshInstance3D:
		meshes.append(instance)
	var found := false
	for node: Node in meshes:
		var mesh := node as MeshInstance3D
		var box: AABB = mesh.global_transform * mesh.get_aabb()
		_bounds = _bounds.merge(box) if found else box
		found = true
	_radius = maxf(_bounds.size.length() * 0.5, 0.5)
	_pivot = Node3D.new()
	root.add_child(_pivot)
	_pivot.position = _bounds.get_center()
	_pivot.rotation = Vector3(-0.4, PI / 4.0, 0)
	camera = Camera3D.new()
	_pivot.add_child(camera)
	camera.fov = 42
	camera.position.z = _radius * 2.0
	camera.near = maxf(_radius * 0.001, 0.001)
	camera.far = maxf(_radius * 20.0, 100.0)
	camera.current = true
	_viewport.size_changed.connect(_frame)
	_frame()
	var context: RefCounted = CONTEXT_SCRIPT.new()
	context.apply(root, camera)
	for node: Node in root.get_children():
		if node is WorldEnvironment:
			_world = node


func _frame() -> void:
	if not is_instance_valid(camera) or not is_instance_valid(_pivot):
		return
	if not _pivot.is_inside_tree():
		return
	var local_bounds: AABB = _pivot.global_transform.affine_inverse() * _bounds
	var half := local_bounds.size * 0.5
	var aspect := float(maxi(_viewport.size.x, 1)) / maxi(_viewport.size.y, 1)
	var tangent := tan(deg_to_rad(camera.fov * 0.5))
	var fit := maxf(half.y / tangent, half.x / (tangent * aspect))
	camera.position.z = maxf((fit + half.z) * 1.15, 0.1)


func _gui_input(event: InputEvent) -> void:
	if camera == null:
		return
	var button := event as InputEventMouseButton
	if button != null and button.button_index == MOUSE_BUTTON_LEFT:
		if button.pressed:
			_press_at = button.position
		elif _press_at.distance_to(button.position) <= CLICK_SLOP:
			_press_at = Vector2.INF
			var hit := pick_at(button.position)
			if not hit.is_empty():
				part_clicked.emit(hit)
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_LEFT:
		_pivot.rotation.y -= event.relative.x * ORBIT_SPEED
		_pivot.rotation.x = clampf(_pivot.rotation.x - event.relative.y * ORBIT_SPEED, -1.5, 1.5)
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_zoom(pow(0.9, event.factor))
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_zoom(pow(1.1, event.factor))
	if event is InputEventMagnifyGesture and event.factor > 0:
		_zoom(1.0 / event.factor)
	if event is InputEventPanGesture:
		_zoom(exp(event.delta.y * 0.05))


func _zoom(factor: float) -> void:
	camera.position.z = clampf(
		camera.position.z * factor, _radius * ZOOM_RANGE.x, _radius * ZOOM_RANGE.y
	)


## The part under [param point] (in this control's coordinates), or {}.
func pick_at(point: Vector2) -> Dictionary:
	if camera == null or model == null:
		return {}
	var scale := Vector2(_viewport.size) / size if size.x > 0 and size.y > 0 else Vector2.ONE
	var at := point * scale
	return PICK_SCRIPT.pick(model, camera.project_ray_origin(at), camera.project_ray_normal(at))


## Tint the surfaces that use [param material] (a Blender name), or clear the tint with "".
func highlight(material: String) -> void:
	if model == null or material == _highlighted:
		return
	_highlighted = material
	var meshes: Array[Node] = model.find_children("*", "MeshInstance3D", true, false)
	for node: Node in meshes:
		var instance := node as MeshInstance3D
		if instance.mesh == null:
			continue
		for surface: int in instance.mesh.get_surface_count():
			instance.set_surface_override_material(surface, null)
			var original := instance.get_active_material(surface)
			var name := PICK_SCRIPT.source_name(
				original, original.resource_name if original != null else ""
			)
			if material.is_empty() or original == null or name != material:
				continue
			var tinted := original.duplicate() as Material
			tinted.next_pass = _tint()
			instance.set_surface_override_material(surface, tinted)


func _tint() -> StandardMaterial3D:
	var tint := StandardMaterial3D.new()
	tint.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	tint.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	tint.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	var accent := UI_THEME.color(self, "accent")
	tint.albedo_color = Color(accent, 0.35)
	return tint
