@tool
extends RefCounted
## A grid of numbers over a terrain: heights (1 channel) or a mask (4 channels:
## r, g, b paint layers 1-3, a is free for scatter). Values are floats; a
## heightmap file stores 0-1, which `Terrain` maps to its height range.
##
## Files: 16-bit or 8-bit PNG (read in GDScript, so 16 bits are kept), or any
## image Godot imports (EXR keeps its float values). Saving writes a 16-bit grey
## PNG for one channel and an 8-bit RGBA PNG for four.

const Png := preload("res://features/terrain/terrain_png.gd")
## This script, for its own types: the terrain recipe runs it before Godot knows
## any class names, so it never names itself.
const MapScript := preload("res://features/terrain/terrain_map.gd")

var width: int = 0
var height: int = 0
var channels: int = 1
var values: PackedFloat32Array = PackedFloat32Array()


static func create(
	map_width: int, map_height: int, map_channels: int = 1, fill: float = 0.0
) -> MapScript:
	var map := MapScript.new()
	map.width = map_width
	map.height = map_height
	map.channels = map_channels
	map.values.resize(map_width * map_height * map_channels)
	map.values.fill(fill)
	return map


## The map in `path` with `wanted` channels (1 or 4), or null (the reason is pushed
## as an error). Grey becomes every channel; RGB gets a=0; heights take the first.
static func load_file(path: String, wanted: int = 1) -> MapScript:
	var decoded: Dictionary = {}
	if path.get_extension().to_lower() == "png" and FileAccess.file_exists(path):
		decoded = Png.decode(FileAccess.get_file_as_bytes(path))
		if decoded.has("error"):
			push_error("Terrain map %s: %s." % [path, decoded["error"]])
			return null
	else:
		decoded = _decode_image(path)
		if decoded.is_empty():
			return null
	return _from_decoded(decoded, wanted)


static func _decode_image(path: String) -> Dictionary:
	var image: Image = null
	if ResourceLoader.exists(path):
		var texture: Texture2D = load(path) as Texture2D
		if texture != null:
			image = texture.get_image()
	elif FileAccess.file_exists(path):
		image = Image.load_from_file(path)
	if image == null or image.is_empty():
		push_error("Terrain map %s: cannot be read." % path)
		return {}
	if image.is_compressed():
		image.decompress()
	image.convert(Image.FORMAT_RGBAF)
	return {
		"width": image.get_width(),
		"height": image.get_height(),
		"channels": 4,
		"values": image.get_data().to_float32_array(),
	}


static func _from_decoded(decoded: Dictionary, wanted: int) -> MapScript:
	var w: int = decoded["width"]
	var h: int = decoded["height"]
	var have: int = decoded["channels"]
	var source: PackedFloat32Array = decoded["values"]
	var map := create(w, h, wanted)
	for i: int in w * h:
		if wanted == 1:
			map.values[i] = source[i * have]
			continue
		for c: int in 4:
			var value: float = 0.0
			if have <= 2:
				value = source[i * have] if c < 3 else (source[i * have + 1] if have == 2 else 0.0)
			elif c < have:
				value = source[i * have + c]
			map.values[i * 4 + c] = value
	return map


func save_png(path: String) -> Error:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	file.store_buffer(Png.encode(width, height, channels, values))
	file.close()
	return OK


func duplicate_map() -> MapScript:
	var copy := MapScript.new()
	copy.width = width
	copy.height = height
	copy.channels = channels
	copy.values = values.duplicate()
	return copy


func get_value(x: int, y: int, channel: int = 0) -> float:
	return values[(clampi(y, 0, height - 1) * width + clampi(x, 0, width - 1)) * channels + channel]


func set_value(x: int, y: int, value: float, channel: int = 0) -> void:
	values[(y * width + x) * channels + channel] = value


## Bilinear value at u, v (0-1 across the map).
func sample(u: float, v: float, channel: int = 0) -> float:
	var fx: float = clampf(u, 0.0, 1.0) * (width - 1)
	var fy: float = clampf(v, 0.0, 1.0) * (height - 1)
	var x0: int = floori(fx)
	var y0: int = floori(fy)
	var tx: float = fx - x0
	var ty: float = fy - y0
	var top: float = lerpf(get_value(x0, y0, channel), get_value(x0 + 1, y0, channel), tx)
	var bottom: float = lerpf(
		get_value(x0, y0 + 1, channel), get_value(x0 + 1, y0 + 1, channel), tx
	)
	return lerpf(top, bottom, ty)


## This map resampled to another size (bilinear).
func resized(new_width: int, new_height: int) -> MapScript:
	if new_width == width and new_height == height:
		return duplicate_map()
	var out := create(new_width, new_height, channels)
	for y: int in new_height:
		for x: int in new_width:
			for c: int in channels:
				out.values[(y * new_width + x) * channels + c] = sample(
					x / float(maxi(new_width - 1, 1)), y / float(maxi(new_height - 1, 1)), c
				)
	return out


## The lowest and highest value of a channel.
func value_range(channel: int = 0) -> Vector2:
	var low: float = INF
	var high: float = -INF
	for i: int in width * height:
		var value: float = values[i * channels + channel]
		low = minf(low, value)
		high = maxf(high, value)
	return Vector2(low, high)


## An RGBA8 image of a 4-channel map (a mask for the splat shader).
func to_image() -> Image:
	var bytes := PackedByteArray()
	bytes.resize(width * height * 4)
	for i: int in width * height * 4:
		var c: int = i % 4
		var value: float = values[(i >> 2) * channels + mini(c, channels - 1)]
		bytes[i] = clampi(roundi(clampf(value, 0.0, 1.0) * 255.0), 0, 255)
	return Image.create_from_data(width, height, false, Image.FORMAT_RGBA8, bytes)
