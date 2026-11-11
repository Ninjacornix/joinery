extends Node3D
## The game's menus on a panel in front of the player, for the headset, where
## the desktop's 2D layers cannot be seen. The core `Menus` layer draws into
## this panel's own viewport while VR is on, at the window's size, so the
## menus keep their layout; the hand's pointer ray clicks it.

const WIDTH := 1.4
const DISTANCE := 1.5
const GROUP := &"vr_panel"
## The physics layer of the panel (20): only the pointer rays look for it.
const LAYER := 1 << 19

var viewport: SubViewport
## The panel's size in pixels: the window's.
var size: Vector2i = Vector2i(1280, 720)
var screen: MeshInstance3D
var body: StaticBody3D
var _quad: QuadMesh = QuadMesh.new()
var _box: BoxShape3D = BoxShape3D.new()
var _pressed: bool = false
var _last: Vector2 = Vector2(-1.0, -1.0)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	viewport = SubViewport.new()
	viewport.name = "Viewport"
	viewport.transparent_bg = true
	viewport.disable_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_WHEN_VISIBLE
	add_child(viewport)
	var material: StandardMaterial3D = StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_texture = viewport.get_texture()
	material.render_priority = 10
	_quad.material = material
	screen = MeshInstance3D.new()
	screen.name = "Screen"
	screen.mesh = _quad
	add_child(screen)
	body = StaticBody3D.new()
	body.name = "Body"
	body.add_to_group(GROUP)
	body.collision_layer = LAYER
	body.collision_mask = 0
	body.set_meta(&"panel", self)
	var shape: CollisionShape3D = CollisionShape3D.new()
	shape.shape = _box
	body.add_child(shape)
	add_child(body)
	_fit()
	Menus.set(&"custom_viewport", viewport)
	visible = false
	body.process_mode = Node.PROCESS_MODE_DISABLED


func _exit_tree() -> void:
	# Godot cannot clear a layer's custom viewport; hand the menus back to the
	# window so the layer leaves the tree cleanly.
	if Menus.get(&"custom_viewport") == viewport:
		Menus.set(&"custom_viewport", get_tree().root)


func _process(_delta: float) -> void:
	var open: bool = Menus.is_open()
	if open != visible:
		visible = open
		body.process_mode = Node.PROCESS_MODE_INHERIT if open else Node.PROCESS_MODE_DISABLED


## The panel's height in metres.
func height() -> float:
	return WIDTH * float(size.y) / float(size.x)


## Puts the panel `DISTANCE` metres in front of the rig's camera, facing it.
func show_in_front(rig: Node3D) -> void:
	_fit()
	if not is_instance_valid(rig) or not rig.is_inside_tree():
		return
	var camera: Node3D = rig.get_node_or_null(^"XRCamera3D") as Node3D
	if camera == null:
		return
	var forward: Vector3 = -camera.global_basis.z
	forward.y = 0.0
	if forward.length_squared() < 0.0001:
		forward = Vector3.FORWARD
	forward = forward.normalized()
	var at: Vector3 = camera.global_position + forward * DISTANCE
	global_transform = Transform3D(Basis.looking_at(forward, Vector3.UP), at)


## Where `point` (world space) is on the panel, in pixels, or (-1, -1) when
## it is off the panel.
func to_pixels(point_at: Vector3) -> Vector2:
	var local: Vector3 = to_local(point_at)
	var u: float = local.x / WIDTH + 0.5
	var v: float = 0.5 - local.y / height()
	if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
		return Vector2(-1.0, -1.0)
	return Vector2(u * float(size.x), v * float(size.y))


## The world-space point of a pixel on the panel.
func to_world(pixel: Vector2) -> Vector3:
	var local: Vector3 = Vector3(
		(pixel.x / float(size.x) - 0.5) * WIDTH, (0.5 - pixel.y / float(size.y)) * height(), 0.0
	)
	return to_global(local)


## The pointer ray touches the panel at `point_at`; `pressed` is the trigger.
func point(point_at: Vector3, pressed: bool) -> void:
	var at: Vector2 = to_pixels(point_at)
	if at.x < 0.0:
		return
	if at != _last:
		var motion: InputEventMouseMotion = InputEventMouseMotion.new()
		motion.position = at
		motion.global_position = at
		motion.relative = at - _last if _last.x >= 0.0 else Vector2.ZERO
		_push(motion)
		_last = at
	if pressed != _pressed:
		var click: InputEventMouseButton = InputEventMouseButton.new()
		click.button_index = MOUSE_BUTTON_LEFT
		click.pressed = pressed
		click.position = at
		click.global_position = at
		if pressed:
			click.button_mask = MOUSE_BUTTON_MASK_LEFT
		_push(click)
		_pressed = pressed


## Matches the panel to the window: the menus lay out at the window's size.
func _fit() -> void:
	var window: Vector2 = get_tree().root.get_visible_rect().size
	if window.x >= 1.0 and window.y >= 1.0:
		size = Vector2i(window)
	viewport.size = size
	_quad.size = Vector2(WIDTH, height())
	_box.size = Vector3(WIDTH, height(), 0.01)


## The menus' controls take input through the game window's viewport (their
## place in the tree); the panel only shows them. Same size, same pixels.
func _push(event: InputEvent) -> void:
	get_tree().root.push_input(event, true)
