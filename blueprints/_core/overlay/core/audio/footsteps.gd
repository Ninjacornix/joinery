class_name Footsteps
extends Node
## Footstep, jump and landing sounds for the CharacterBody3D or CharacterBody2D
## it is a child of. A step plays every `stride` of ground covered (metres; in
## 2D, pixels / 64). The surface is the floor collider's metadata `surface`
## (a String such as "grass" or "stone"), else "default"; `surfaces` maps a
## surface to its sound. A body in floating motion mode (top-down) is always on
## the ground and every direction counts. Silent until it is given streams;
## the signals fire either way.

signal stepped(surface: String)
signal jumped
signal landed

const PIXELS_PER_METRE := 64.0
## A jump further than this in one physics frame is a teleport, not a step.
const TELEPORT_METRES := 2.0
## Shorter airborne spells (stairs, bumps) are not a landing.
const MIN_AIR_SECONDS := 0.12

## Surface name -> AudioStream; "default" is used for any other surface.
@export var surfaces: Dictionary = {}
@export var stride: float = 1.7
@export var jump: AudioStream
@export var land: AudioStream
@export var bus: StringName = &"SFX"
@export_range(-60.0, 12.0, 0.1, "suffix:dB") var volume_db: float = 0.0
@export_range(0.0, 0.5, 0.01) var pitch_jitter: float = 0.08

var _travelled: float = 0.0
var _was_on_floor: bool = true
var _air_time: float = 0.0
var _last_position: Variant = null
var _player: Node


func _ready() -> void:
	var body: Node = get_parent()
	if body is CharacterBody3D:
		_player = AudioStreamPlayer3D.new()
	elif body is CharacterBody2D:
		_player = AudioStreamPlayer2D.new()
	else:
		push_warning("Footsteps: %s is not a CharacterBody3D or CharacterBody2D." % body)
		set_physics_process(false)
		return
	_player.name = "FootstepPlayer"
	_player.set(&"bus", bus)
	_player.set(&"max_polyphony", 4)
	body.add_child.call_deferred(_player)
	_was_on_floor = body.call(&"is_on_floor")


## The surface under the body: the floor collider's `surface` metadata.
func floor_surface() -> String:
	var body: Node = get_parent()
	var count: int = int(body.call(&"get_slide_collision_count"))
	for index: int in count:
		var collision: Object = body.call(&"get_slide_collision", index)
		var normal: Variant = collision.call(&"get_normal")
		var up: Variant = body.get(&"up_direction")
		var floor_angle: float = float(body.get(&"floor_max_angle"))
		if acos(clampf(float(normal.dot(up)), -1.0, 1.0)) > floor_angle + 0.01:
			continue
		var collider: Object = collision.call(&"get_collider")
		if collider != null and collider.has_meta(&"surface"):
			return str(collider.get_meta(&"surface"))
		return "default"
	return "default"


func _physics_process(delta: float) -> void:
	var body: Node = get_parent()
	var floating: bool = int(body.get(&"motion_mode")) == 1
	var on_floor: bool = floating or bool(body.call(&"is_on_floor"))
	var at: Variant = body.get(&"global_position")
	var moved: float = 0.0
	if _last_position != null:
		moved = _ground_distance(at - _last_position, floating)
		if moved > TELEPORT_METRES:
			moved = 0.0
	_last_position = at
	if on_floor and not _was_on_floor:
		if _air_time >= MIN_AIR_SECONDS:
			_travelled = 0.0
			_play(land)
			landed.emit()
	elif not on_floor and _was_on_floor:
		var velocity: Variant = body.get(&"velocity")
		if float(velocity.dot(body.get(&"up_direction"))) > 0.0:
			_play(jump)
			jumped.emit()
	_air_time = 0.0 if on_floor else _air_time + delta
	_was_on_floor = on_floor
	if not on_floor:
		return
	_travelled += moved
	if _travelled >= maxf(stride, 0.01):
		_travelled = 0.0
		var surface: String = floor_surface()
		var stream: AudioStream = surfaces.get(surface, surfaces.get("default")) as AudioStream
		_play(stream)
		stepped.emit(surface)


## Horizontal distance (along the floor) in metres.
func _ground_distance(step: Variant, floating: bool) -> float:
	if step is Vector3:
		var flat: Vector3 = step
		return flat.length() if floating else Vector2(flat.x, flat.z).length()
	if step is Vector2:
		var flat_2d: Vector2 = step
		return (flat_2d.length() if floating else absf(flat_2d.x)) / PIXELS_PER_METRE
	return 0.0


func _play(stream: AudioStream) -> void:
	if stream == null or _player == null or not _player.is_inside_tree():
		return
	_player.set(&"stream", stream)
	_player.set(&"volume_db", volume_db)
	_player.set(&"pitch_scale", 1.0 + randf_range(-pitch_jitter, pitch_jitter))
	_player.call(&"play")
