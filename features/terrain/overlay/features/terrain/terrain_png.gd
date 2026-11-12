extends RefCounted
## PNG reading and writing for terrain maps, in plain GDScript.
##
## Godot's own image loader keeps 8 bits per channel, which turns a 16-bit
## heightmap into terraces. This reads 8- and 16-bit grey, grey+alpha, RGB and
## RGBA PNGs (not palette or interlaced ones) into floats 0-1, and writes a
## 16-bit grey PNG (heights) or an 8-bit RGBA PNG (masks). The writer uses no
## row filter and Godot's zlib, so the same values always give the same bytes:
## a terrain recipe replays byte for byte.

const SIGNATURE := [137, 80, 78, 71, 13, 10, 26, 10]
## Colour type -> channels (0 grey, 2 RGB, 4 grey+alpha, 6 RGBA).
const CHANNELS := {0: 1, 2: 3, 4: 2, 6: 4}

static var _crc_table: PackedInt64Array = PackedInt64Array()


## {"width", "height", "channels", "values": PackedFloat32Array (0-1, interleaved)},
## or {"error": why}.
static func decode(bytes: PackedByteArray) -> Dictionary:
	if bytes.size() < 33:
		return {"error": "not a PNG"}
	for i: int in SIGNATURE.size():
		if bytes[i] != SIGNATURE[i]:
			return {"error": "not a PNG"}
	var at: int = 8
	var width: int = 0
	var height: int = 0
	var depth: int = 0
	var colour: int = -1
	var idat := PackedByteArray()
	while at + 8 <= bytes.size():
		var length: int = _u32(bytes, at)
		var kind: String = bytes.slice(at + 4, at + 8).get_string_from_ascii()
		var body: PackedByteArray = bytes.slice(at + 8, at + 8 + length)
		at += 12 + length
		if kind == "IHDR":
			width = _u32(body, 0)
			height = _u32(body, 4)
			depth = body[8]
			colour = body[9]
			if body[12] != 0:
				return {"error": "interlaced PNGs are not supported; save it without interlacing"}
		elif kind == "IDAT":
			idat.append_array(body)
		elif kind == "IEND":
			break
	if not CHANNELS.has(colour):
		return {"error": "palette PNGs are not supported; save it as grey or RGB"}
	if depth != 8 and depth != 16:
		return {"error": "only 8- and 16-bit PNGs are supported"}
	if width <= 0 or height <= 0 or idat.is_empty():
		return {"error": "the PNG has no pixels"}
	var channels: int = CHANNELS[colour]
	var pixel_bytes: int = channels * (depth >> 3)
	var stride: int = width * pixel_bytes
	var raw: PackedByteArray = idat.decompress(
		(stride + 1) * height, FileAccess.COMPRESSION_DEFLATE
	)
	if raw.size() != (stride + 1) * height:
		return {"error": "the PNG's pixel data is cut short"}
	var values := PackedFloat32Array()
	values.resize(width * height * channels)
	var previous := PackedByteArray()
	previous.resize(stride)
	var out: int = 0
	for y: int in height:
		var start: int = y * (stride + 1)
		var line: PackedByteArray = raw.slice(start + 1, start + 1 + stride)
		_unfilter(raw[start], line, previous, pixel_bytes)
		if depth == 16:
			for i: int in range(0, stride, 2):
				values[out] = float((line[i] << 8) | line[i + 1]) / 65535.0
				out += 1
		else:
			for i: int in stride:
				values[out] = float(line[i]) / 255.0
				out += 1
		previous = line
	return {"width": width, "height": height, "channels": channels, "values": values}


## A PNG of `values` (0-1, interleaved): 1 channel is written 16-bit grey, 4 channels
## 8-bit RGBA.
static func encode(
	width: int, height: int, channels: int, values: PackedFloat32Array
) -> PackedByteArray:
	assert(channels == 1 or channels == 4)
	var raw := PackedByteArray()
	var stride: int = width * (2 if channels == 1 else 4)
	raw.resize((stride + 1) * height)
	var at: int = 0
	var index: int = 0
	for y: int in height:
		raw[at] = 0
		at += 1
		if channels == 1:
			for x: int in width:
				var level: int = clampi(roundi(clampf(values[index], 0.0, 1.0) * 65535.0), 0, 65535)
				raw[at] = level >> 8
				raw[at + 1] = level & 255
				at += 2
				index += 1
		else:
			for i: int in width * 4:
				raw[at] = clampi(roundi(clampf(values[index], 0.0, 1.0) * 255.0), 0, 255)
				at += 1
				index += 1
	var header := PackedByteArray()
	header.append_array(_be32(width))
	header.append_array(_be32(height))
	header.append_array(
		PackedByteArray([16 if channels == 1 else 8, 0 if channels == 1 else 6, 0, 0, 0])
	)
	var png := PackedByteArray(SIGNATURE)
	png.append_array(_chunk("IHDR", header))
	png.append_array(_chunk("IDAT", raw.compress(FileAccess.COMPRESSION_DEFLATE)))
	png.append_array(_chunk("IEND", PackedByteArray()))
	return png


static func _unfilter(
	kind: int, line: PackedByteArray, previous: PackedByteArray, bpp: int
) -> void:
	var size: int = line.size()
	match kind:
		0:
			return
		1:
			for i: int in range(bpp, size):
				line[i] = (line[i] + line[i - bpp]) & 255
		2:
			for i: int in size:
				line[i] = (line[i] + previous[i]) & 255
		3:
			for i: int in size:
				var left: int = line[i - bpp] if i >= bpp else 0
				line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
		4:
			for i: int in size:
				var a: int = line[i - bpp] if i >= bpp else 0
				var b: int = previous[i]
				var c: int = previous[i - bpp] if i >= bpp else 0
				var p: int = a + b - c
				var pa: int = absi(p - a)
				var pb: int = absi(p - b)
				var pc: int = absi(p - c)
				var pick: int = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
				line[i] = (line[i] + pick) & 255


static func _chunk(kind: String, body: PackedByteArray) -> PackedByteArray:
	var tagged := kind.to_ascii_buffer()
	tagged.append_array(body)
	var out := _be32(body.size())
	out.append_array(tagged)
	out.append_array(_be32(_crc32(tagged)))
	return out


static func _crc32(data: PackedByteArray) -> int:
	if _crc_table.is_empty():
		_crc_table.resize(256)
		for n: int in 256:
			var c: int = n
			for _k: int in 8:
				c = (0xEDB88320 ^ (c >> 1)) if c & 1 else (c >> 1)
			_crc_table[n] = c
	var crc: int = 0xFFFFFFFF
	for byte: int in data:
		crc = _crc_table[(crc ^ byte) & 255] ^ (crc >> 8)
	return crc ^ 0xFFFFFFFF


static func _u32(bytes: PackedByteArray, at: int) -> int:
	return (bytes[at] << 24) | (bytes[at + 1] << 16) | (bytes[at + 2] << 8) | bytes[at + 3]


static func _be32(value: int) -> PackedByteArray:
	return PackedByteArray(
		[(value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255]
	)
