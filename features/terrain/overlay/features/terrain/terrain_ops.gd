@tool
extends RefCounted
## The terrain operations: build a heightmap and a mask from a document of ops.
##
## A document is what a terrain recipe stores (see GUIDE.md):
##   {"size": [x, z] metres, "resolution": n (points a side), "min_height", "max_height",
##    "seed", "base": "res://…png" (optional start), "base_mask": "", "ops": [ … ]}
## Positions are metres in the terrain's own space, centred on its origin: x right,
## z towards the camera. Heights are metres above the Terrain node. Everything is
## seeded integer hashing and plain float maths, so a document always gives the
## same maps.

const MapScript := preload("res://features/terrain/terrain_map.gd")
const CHANNEL := {"r": 0, "g": 1, "b": 2, "a": 3}

var size: Vector2 = Vector2(64.0, 64.0)
var resolution: int = 129
var min_height: float = 0.0
var max_height: float = 20.0
var seed_value: int = 1
## Heights in metres.
var height: MapScript
var mask: MapScript
var problems: PackedStringArray = PackedStringArray()


## Runs `doc`; returns {"height": map in metres, "mask": map, "problems": […]}.
static func build(doc: Dictionary) -> Dictionary:
	var run: RefCounted = load("res://features/terrain/terrain_ops.gd").new()
	return run.call(&"run", doc)


func run(doc: Dictionary) -> Dictionary:
	var dims: Array = doc.get("size", [64.0, 64.0])
	size = Vector2(float(dims[0]), float(dims[1]))
	resolution = int(doc.get("resolution", 129))
	min_height = float(doc.get("min_height", 0.0))
	max_height = float(doc.get("max_height", 20.0))
	seed_value = int(doc.get("seed", 1))
	height = MapScript.create(resolution, resolution, 1, 0.0)
	mask = MapScript.create(resolution, resolution, 4, 0.0)
	_start(str(doc.get("base", "")), str(doc.get("base_mask", "")))
	var number: int = 0
	for op: Variant in doc.get("ops", []):
		number += 1
		if op is Dictionary:
			apply(op)
		else:
			problems.append("op %d is not an object" % number)
	for i: int in height.values.size():
		height.values[i] = clampf(height.values[i], min_height, max_height)
	return {"height": height, "mask": mask, "problems": problems}


## The heightmap file's values (0-1 across the height range).
func normalised() -> MapScript:
	var out: MapScript = height.duplicate_map()
	var span: float = maxf(max_height - min_height, 0.0001)
	for i: int in out.values.size():
		out.values[i] = (out.values[i] - min_height) / span
	return out


func apply(op: Dictionary) -> void:
	match str(op.get("op", "")):
		"noise":
			_noise(op)
		"raise":
			_raise(op, 1.0)
		"lower":
			_raise(op, -1.0)
		"flatten":
			_flatten(op)
		"smooth":
			_smooth(op)
		"erode":
			_erode(op)
		"path":
			_path(op)
		"paint":
			_paint(op)
		var other:
			problems.append("unknown op %s" % other)


# --- Grid -------------------------------------------------------------------------------


func cell() -> Vector2:
	return size / float(resolution - 1)


func position_of(x: int, y: int) -> Vector2:
	return Vector2(x, y) * cell() - size * 0.5


func height_at(at: Vector2) -> float:
	var uv: Vector2 = (at + size * 0.5) / size
	return height.sample(uv.x, uv.y)


static func _vec(value: Variant, fallback: Vector2 = Vector2.ZERO) -> Vector2:
	if value is Array and (value as Array).size() >= 2:
		return Vector2(float(value[0]), float(value[1]))
	return fallback


## 1 inside radius * (1 - falloff), easing to 0 at radius.
static func falloff_weight(distance: float, radius: float, falloff: float) -> float:
	if distance >= radius:
		return 0.0
	var inner: float = radius * (1.0 - clampf(falloff, 0.0, 1.0))
	if distance <= inner:
		return 1.0
	return 1.0 - smoothstep(inner, radius, distance)


## [[index, weight], …] for the grid points a round brush touches.
func brush(op: Dictionary) -> Array:
	var at: Vector2 = _vec(op.get("at"))
	var radius: float = maxf(float(op.get("radius", 8.0)), 0.001)
	var falloff: float = float(op.get("falloff", 0.5))
	var step: Vector2 = cell()
	var lo := Vector2i(((at - Vector2(radius, radius) + size * 0.5) / step).floor())
	var hi := Vector2i(((at + Vector2(radius, radius) + size * 0.5) / step).ceil())
	var found: Array = []
	for y: int in range(maxi(lo.y, 0), mini(hi.y, resolution - 1) + 1):
		for x: int in range(maxi(lo.x, 0), mini(hi.x, resolution - 1) + 1):
			var weight: float = falloff_weight(position_of(x, y).distance_to(at), radius, falloff)
			if weight > 0.0:
				found.append([y * resolution + x, weight])
	return found


# --- Height ops -------------------------------------------------------------------------


func _start(base: String, base_mask: String) -> void:
	if not base.is_empty():
		var loaded: MapScript = MapScript.load_file(base, 1)
		if loaded == null:
			problems.append("base %s cannot be read" % base)
		else:
			var fitted: MapScript = loaded.resized(resolution, resolution)
			for i: int in fitted.values.size():
				height.values[i] = lerpf(min_height, max_height, fitted.values[i])
	if not base_mask.is_empty():
		var painted: MapScript = MapScript.load_file(base_mask, 4)
		if painted == null:
			problems.append("base_mask %s cannot be read" % base_mask)
		else:
			mask = painted.resized(resolution, resolution)


func _noise(op: Dictionary) -> void:
	var amplitude: float = float(op.get("amplitude", 4.0))
	var scale: float = maxf(float(op.get("scale", 32.0)), 0.01)
	var octaves: int = clampi(int(op.get("octaves", 4)), 1, 8)
	var noise_seed: int = int(op.get("seed", seed_value))
	var ridged: bool = bool(op.get("ridged", false))
	var offset: float = float(op.get("offset", 0.0))
	for y: int in resolution:
		for x: int in resolution:
			var p: Vector2 = position_of(x, y) / scale
			var value: float = fbm(p, octaves, noise_seed)
			if ridged:
				value = 1.0 - 2.0 * absf(value)
			height.values[y * resolution + x] += amplitude * value + offset


func _raise(op: Dictionary, direction: float) -> void:
	var amount: float = float(op.get("amount", 1.0)) * direction
	for item: Array in brush(op):
		height.values[item[0]] += amount * float(item[1])


func _flatten(op: Dictionary) -> void:
	var target: float = float(op.get("height", height_at(_vec(op.get("at")))))
	for item: Array in brush(op):
		var index: int = item[0]
		height.values[index] = lerpf(height.values[index], target, float(item[1]))


func _smooth(op: Dictionary) -> void:
	var strength: float = clampf(float(op.get("strength", 0.5)), 0.0, 1.0)
	var iterations: int = clampi(int(op.get("iterations", 2)), 1, 20)
	var weights := PackedFloat32Array()
	weights.resize(resolution * resolution)
	if op.has("at"):
		for item: Array in brush(op):
			weights[item[0]] = float(item[1]) * strength
	else:
		weights.fill(strength)
	for _pass: int in iterations:
		var before: PackedFloat32Array = height.values.duplicate()
		for y: int in resolution:
			for x: int in resolution:
				var index: int = y * resolution + x
				if weights[index] <= 0.0:
					continue
				var total: float = 0.0
				var count: int = 0
				for dy: int in range(-1, 2):
					for dx: int in range(-1, 2):
						var nx: int = x + dx
						var ny: int = y + dy
						if nx >= 0 and ny >= 0 and nx < resolution and ny < resolution:
							total += before[ny * resolution + nx]
							count += 1
				height.values[index] = lerpf(before[index], total / count, weights[index])


## Thermal erosion: wherever a slope is steeper than `talus` (rise per metre),
## part of the excess slides to the lower neighbour. Softens cliffs into screes.
func _erode(op: Dictionary) -> void:
	var iterations: int = clampi(int(op.get("iterations", 10)), 1, 50)
	var talus: float = maxf(float(op.get("talus", 0.6)), 0.0) * cell().x
	var amount: float = clampf(float(op.get("amount", 0.5)), 0.0, 1.0) * 0.25
	var offsets: Array[Vector2i] = [Vector2i(1, 0), Vector2i(0, 1)]
	for _pass: int in iterations:
		var moved := PackedFloat32Array()
		moved.resize(resolution * resolution)
		for y: int in resolution:
			for x: int in resolution:
				var index: int = y * resolution + x
				for offset: Vector2i in offsets:
					var nx: int = x + offset.x
					var ny: int = y + offset.y
					if nx >= resolution or ny >= resolution:
						continue
					var other: int = ny * resolution + nx
					var drop: float = height.values[index] - height.values[other]
					if absf(drop) <= talus:
						continue
					var shift: float = (absf(drop) - talus) * amount * signf(drop)
					moved[index] -= shift
					moved[other] += shift
		for i: int in moved.size():
			height.values[i] += moved[i]


## A path: the ground along the points is eased to a smooth profile (and sunk by
## `depth`), so a road or trail climbs gently; `paint` also paints it into the mask.
func _path(op: Dictionary) -> void:
	var points: Array[Vector2] = []
	for point: Variant in op.get("points", []):
		points.append(_vec(point))
	if points.size() < 2:
		problems.append("a path needs two or more points")
		return
	var width: float = maxf(float(op.get("width", 3.0)), 0.1)
	var edge: float = maxf(float(op.get("falloff", width * 0.5)), 0.0)
	var depth: float = float(op.get("depth", 0.0))
	var channel: int = CHANNEL.get(str(op.get("paint", "")), -1)
	var value: float = clampf(float(op.get("value", 1.0)), 0.0, 1.0)
	var profile := PackedFloat32Array()
	for point: Vector2 in points:
		profile.append(height_at(point))
	for _pass: int in 2:
		var eased: PackedFloat32Array = profile.duplicate()
		for i: int in range(1, profile.size() - 1):
			eased[i] = (profile[i - 1] + profile[i] * 2.0 + profile[i + 1]) * 0.25
		profile = eased
	var reach: float = width * 0.5 + edge
	for y: int in resolution:
		for x: int in resolution:
			var at: Vector2 = position_of(x, y)
			var best: float = INF
			var target: float = 0.0
			for i: int in points.size() - 1:
				var a: Vector2 = points[i]
				var b: Vector2 = points[i + 1]
				var along: float = clampf(
					(at - a).dot(b - a) / maxf((b - a).length_squared(), 0.0001), 0.0, 1.0
				)
				var distance: float = at.distance_to(a.lerp(b, along))
				if distance < best:
					best = distance
					target = lerpf(profile[i], profile[i + 1], along)
			if best >= reach:
				continue
			var weight: float = (
				1.0 if best <= width * 0.5 else 1.0 - smoothstep(width * 0.5, reach, best)
			)
			var index: int = y * resolution + x
			height.values[index] = lerpf(height.values[index], target - depth, weight)
			if channel >= 0:
				var slot: int = index * 4 + channel
				mask.values[slot] = lerpf(mask.values[slot], value, weight)


# --- Mask ops ---------------------------------------------------------------------------


func _paint(op: Dictionary) -> void:
	var channel: int = CHANNEL.get(str(op.get("channel", "r")), -1)
	if channel < 0:
		problems.append("paint needs a channel: r, g, b or a")
		return
	var value: float = clampf(float(op.get("value", 1.0)), 0.0, 1.0)
	for item: Array in brush(op):
		var slot: int = int(item[0]) * 4 + channel
		mask.values[slot] = lerpf(mask.values[slot], value, float(item[1]))


# --- Noise ------------------------------------------------------------------------------


## Fractal value noise, about -1 to 1. Integer hashing, so the same seed gives the
## same ground on every machine.
static func fbm(p: Vector2, octaves: int, noise_seed: int) -> float:
	var total: float = 0.0
	var amplitude: float = 0.5
	var norm: float = 0.0
	var frequency: float = 1.0
	for octave: int in octaves:
		total += value_noise(p * frequency, noise_seed + octave * 7919) * amplitude
		norm += amplitude
		amplitude *= 0.5
		frequency *= 2.0
	return total / norm


static func value_noise(p: Vector2, noise_seed: int) -> float:
	var ix: int = floori(p.x)
	var iz: int = floori(p.y)
	var fx: float = p.x - ix
	var fz: float = p.y - iz
	var ux: float = fx * fx * fx * (fx * (fx * 6.0 - 15.0) + 10.0)
	var uz: float = fz * fz * fz * (fz * (fz * 6.0 - 15.0) + 10.0)
	var a: float = lerpf(hash_value(ix, iz, noise_seed), hash_value(ix + 1, iz, noise_seed), ux)
	var b: float = lerpf(
		hash_value(ix, iz + 1, noise_seed), hash_value(ix + 1, iz + 1, noise_seed), ux
	)
	return lerpf(a, b, uz)


## -1 to 1 from two integers and a seed.
static func hash_value(ix: int, iz: int, noise_seed: int) -> float:
	var h: int = (
		(ix * 374761393 + iz * 668265263 + (noise_seed & 0xFFFFFF) * 1442695041) & 0xFFFFFFFF
	)
	h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
	h = h ^ (h >> 16)
	return float(h) / 4294967295.0 * 2.0 - 1.0
