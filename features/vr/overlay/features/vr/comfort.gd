extends RefCounted
## Locomotion and comfort maths for the VR rig. No nodes, so tests check it
## directly: snap turn, smooth move, the teleport arc and where it may land,
## seated height and the vignette.

## Snap turn angles the comfort page offers, in degrees.
const SNAP_ANGLES: Array[int] = [30, 45]
const SNAP_DEFAULT := 45
## The turn stick must pass PRESS to turn once, then drop below RELEASE
## before it turns again.
const TURN_PRESS := 0.6
const TURN_RELEASE := 0.3
## Pushing the turn stick forward past this aims a teleport.
const TELEPORT_PRESS := 0.6
const STICK_DEADZONE := 0.2
const SPEED_MIN := 1.0
const SPEED_MAX := 3.0
const SPEED_DEFAULT := 2.0
## Steepest floor a teleport may land on, in degrees.
const TELEPORT_SLOPE := 30.0
## Farthest a teleport may land, in metres from the hand (along the floor).
const TELEPORT_RANGE := 8.0
const ARC_SPEED := 7.0
const ARC_GRAVITY := 9.8
const ARC_STEP := 0.05
const ARC_POINTS := 40
## How much lower a seated player's eyes are than a standing player's (m):
## seated play lifts the rig by this much, so the world keeps its scale.
const SEATED_LIFT := 0.45
## Group of bodies a teleport never lands on (tables, shelves, props).
const NO_TELEPORT := &"no_teleport"
## Physics layers the teleport arc hits: all but layer 20, the menu panel's.
const ARC_MASK := 0xFFFFFFFF & ~(1 << 19)


## The snap turn angle in radians for a setting in degrees (30 or 45; any
## other value means the default).
static func snap_angle(degrees: int) -> float:
	return deg_to_rad(float(degrees if degrees in SNAP_ANGLES else SNAP_DEFAULT))


## -1, 0 or 1: which way one frame of the turn stick snaps the view (1 is a
## turn to the left, counter-clockwise from above). Only an armed stick turns.
static func snap_step(stick_x: float, armed: bool) -> int:
	if not armed or absf(stick_x) < TURN_PRESS:
		return 0
	return -1 if stick_x > 0.0 else 1


## Whether the turn stick is armed for the next snap after this frame.
static func rearm(stick_x: float, armed: bool) -> bool:
	if absf(stick_x) < TURN_RELEASE:
		return true
	if absf(stick_x) >= TURN_PRESS:
		return false
	return armed


## `rig` turned by `radians` about the vertical line through `pivot` (the
## head), so the player turns on the spot instead of swinging around the
## play area's centre.
static func snap_turn(rig: Transform3D, pivot: Vector3, radians: float) -> Transform3D:
	var around: Vector3 = Vector3(pivot.x, rig.origin.y, pivot.z)
	var turn: Transform3D = Transform3D(Basis(Vector3.UP, radians), Vector3.ZERO)
	var moved: Transform3D = Transform3D(Basis.IDENTITY, around) * turn
	return moved * Transform3D(Basis.IDENTITY, -around) * rig


## A stick past the deadzone, rescaled so it still reaches 1.
static func deadzone(stick: Vector2, zone: float = STICK_DEADZONE) -> Vector2:
	var length: float = stick.length()
	if length <= zone:
		return Vector2.ZERO
	var scaled: float = minf((length - zone) / (1.0 - zone), 1.0)
	return stick / length * scaled


## Clamped walking speed in metres a second.
static func move_speed(setting: float) -> float:
	return clampf(setting, SPEED_MIN, SPEED_MAX)


## Horizontal velocity for the move stick (y up is forward) relative to where
## the head looks. Looking up or down never slows walking.
static func smooth_velocity(
	stick: Vector2, head: Basis, speed: float, zone: float = STICK_DEADZONE
) -> Vector3:
	var input: Vector2 = deadzone(stick, zone)
	if input == Vector2.ZERO:
		return Vector3.ZERO
	var forward: Vector3 = Vector3(-head.z.x, 0.0, -head.z.z)
	if forward.length_squared() < 0.0001:
		# Looking straight down: walk where the top of the head points.
		forward = Vector3(head.y.x, 0.0, head.y.z)
	forward = forward.normalized()
	var right: Vector3 = Vector3(-forward.z, 0.0, forward.x)
	return (right * input.x + forward * input.y) * move_speed(speed)


## Points of a thrown arc from `start` along `direction`, for the teleport
## pointer: `count` points `step` seconds apart.
static func arc_points(
	start: Vector3,
	direction: Vector3,
	speed: float = ARC_SPEED,
	gravity: float = ARC_GRAVITY,
	step: float = ARC_STEP,
	count: int = ARC_POINTS
) -> PackedVector3Array:
	var points: PackedVector3Array = PackedVector3Array()
	var velocity: Vector3 = direction.normalized() * speed
	for i: int in count:
		var t: float = float(i) * step
		points.append(start + velocity * t + Vector3.DOWN * (0.5 * gravity * t * t))
	return points


## Whether a surface with `normal`, `distance` metres away along the floor,
## is somewhere a teleport may land.
static func is_valid_landing(normal: Vector3, distance: float) -> bool:
	if normal.length_squared() < 0.0001:
		return false
	var slope: float = rad_to_deg(normal.normalized().angle_to(Vector3.UP))
	return slope <= TELEPORT_SLOPE + 0.001 and distance <= TELEPORT_RANGE


## Casts the arc through `space` and returns where it lands:
## {"hit": bool, "valid": bool, "position": Vector3, "normal": Vector3,
## "collider": Object, "points": PackedVector3Array (up to the hit)}.
static func find_landing(
	space: PhysicsDirectSpaceState3D, points: PackedVector3Array, exclude: Array[RID] = []
) -> Dictionary:
	var found: Dictionary = {
		"hit": false,
		"valid": false,
		"position": Vector3.ZERO,
		"normal": Vector3.ZERO,
		"collider": null,
		"points": points,
	}
	if points.size() < 2:
		return found
	for i: int in range(1, points.size()):
		var query: PhysicsRayQueryParameters3D = PhysicsRayQueryParameters3D.create(
			points[i - 1], points[i]
		)
		query.exclude = exclude
		query.collision_mask = ARC_MASK
		var hit: Dictionary = space.intersect_ray(query)
		if hit.is_empty():
			continue
		var position: Vector3 = hit["position"]
		var normal: Vector3 = hit["normal"]
		var collider: Object = hit.get("collider")
		var flat: Vector2 = Vector2(position.x - points[0].x, position.z - points[0].z)
		var allowed: bool = not (collider is Node and (collider as Node).is_in_group(NO_TELEPORT))
		found["hit"] = true
		found["position"] = position
		found["normal"] = normal
		found["collider"] = collider
		found["valid"] = allowed and is_valid_landing(normal, flat.length())
		found["points"] = points.slice(0, i) + PackedVector3Array([position])
		return found
	return found


## How far the rig is lifted above the player's feet.
static func height_offset(seated: bool) -> float:
	return SEATED_LIFT if seated else 0.0


## Vignette opacity: `strength` (the setting, 0 = off) while moving or
## turning, scaled by how fast (0-1).
static func vignette_amount(strength: float, motion: float) -> float:
	return clampf(strength, 0.0, 1.0) * clampf(motion, 0.0, 1.0)


## The hand that walks ("left" or "right"); the other turns, teleports and
## points. Left-handed play swaps them.
static func move_hand(left_handed: bool) -> StringName:
	return &"right" if left_handed else &"left"
