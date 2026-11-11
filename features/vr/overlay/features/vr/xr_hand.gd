extends XRController3D
## One hand of the VR rig: reads its controller (stick, grip, trigger), grabs
## the nearest `pickable` node within reach on grip and lets go on release,
## throwing it with the hand's speed.
##
## A pickable node is any Node3D in group `pickable` with
## `grab(holder: Node3D) -> bool` and `release(velocity: Vector3)`, and
## optionally `can_grab() -> bool`. It follows the holder itself while held.
##
## Tests (and the rig without a headset) drive a hand with `simulate`.

signal grabbed(node: Node3D)
signal released(node: Node3D, velocity: Vector3)

## Metres from the hand's centre to a pickable's origin it can still grab.
const GRAB_RADIUS := 0.2
const GRIP_PRESS := 0.6
const TRIGGER_PRESS := 0.6
const VELOCITY_FRAMES := 5

## The node this hand holds, or null.
var held: Node3D = null

var _simulated: bool = false
var _sim_stick: Vector2 = Vector2.ZERO
var _sim_grip: bool = false
var _sim_trigger: bool = false
var _sim_menu: bool = false
var _grip_was: bool = false
var _samples: Array[Vector3] = []
var _sample_times: Array[float] = []
var _clock: float = 0.0


## The nearest node in `candidates` within `radius` of `at` that can be
## grabbed, or null.
static func nearest_pickable(candidates: Array[Node], at: Vector3, radius: float) -> Node3D:
	var best: Node3D = null
	var best_distance: float = radius
	for node: Node in candidates:
		var item: Node3D = node as Node3D
		if item == null or not item.is_inside_tree() or not item.has_method(&"grab"):
			continue
		if item.has_method(&"can_grab") and not bool(item.call(&"can_grab")):
			continue
		var distance: float = item.global_position.distance_to(at)
		if distance <= best_distance:
			best = item
			best_distance = distance
	return best


func _ready() -> void:
	process_physics_priority = -1


func _physics_process(delta: float) -> void:
	_clock += delta
	_samples.append(global_position)
	_sample_times.append(_clock)
	if _samples.size() > VELOCITY_FRAMES:
		_samples.pop_front()
		_sample_times.pop_front()
	var grip: bool = grip_down()
	if grip and not _grip_was:
		try_grab()
	elif not grip and _grip_was:
		release()
	_grip_was = grip
	if held != null and not is_instance_valid(held):
		held = null


## Feeds this hand by hand: stick, grip, trigger and menu button.
func simulate(
	stick: Vector2, grip: bool = false, trigger: bool = false, menu: bool = false
) -> void:
	_simulated = true
	_sim_stick = stick
	_sim_grip = grip
	_sim_trigger = trigger
	_sim_menu = menu


func stop_simulating() -> void:
	_simulated = false
	_sim_stick = Vector2.ZERO
	_sim_grip = false
	_sim_trigger = false
	_sim_menu = false


func is_simulated() -> bool:
	return _simulated


func stick() -> Vector2:
	if _simulated:
		return _sim_stick
	return get_vector2(&"primary")


func grip_down() -> bool:
	if _simulated:
		return _sim_grip
	return get_float(&"grip") >= GRIP_PRESS or is_button_pressed(&"grip_click")


func trigger_down() -> bool:
	if _simulated:
		return _sim_trigger
	return get_float(&"trigger") >= TRIGGER_PRESS or is_button_pressed(&"trigger_click")


func menu_down() -> bool:
	if _simulated:
		return _sim_menu
	return is_button_pressed(&"menu_button") or is_button_pressed(&"by_button")


## Grabs the nearest pickable within reach. Returns true when it did.
func try_grab() -> bool:
	if held != null:
		return false
	var candidates: Array[Node] = get_tree().get_nodes_in_group(&"pickable")
	var found: Node3D = nearest_pickable(candidates, global_position, GRAB_RADIUS)
	if found == null or not bool(found.call(&"grab", self)):
		return false
	held = found
	pulse(0.4, 0.06)
	grabbed.emit(found)
	return true


## Lets go of what this hand holds, throwing it with the hand's speed.
func release() -> void:
	if held == null:
		return
	var item: Node3D = held
	held = null
	var velocity: Vector3 = throw_velocity()
	if is_instance_valid(item):
		item.call(&"release", velocity)
	released.emit(item, velocity)


## The hand's average velocity over the last few physics frames.
func throw_velocity() -> Vector3:
	if _samples.size() < 2:
		return Vector3.ZERO
	var elapsed: float = _sample_times[-1] - _sample_times[0]
	if elapsed <= 0.0:
		return Vector3.ZERO
	return (_samples[-1] - _samples[0]) / elapsed


## A short buzz, when there is a headset and the player keeps rumble on.
func pulse(amplitude: float, seconds: float) -> void:
	if _simulated or not get_is_active():
		return
	var controller: Node = get_node_or_null(^"/root/Controller")
	if controller != null and controller.has_method(&"rumble_enabled"):
		if not bool(controller.call(&"rumble_enabled")):
			return
	trigger_haptic_pulse(&"haptic", 0.0, amplitude, seconds, 0.0)
