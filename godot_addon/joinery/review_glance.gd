@tool
extends RefCounted
## The words of a review you can take in at a glance: one line of facts ("Door 20% wider · wood
## darker · 2 files"), the checks as one quiet status, the reviewer's notes as one line, and
## whether a change is small enough to decide from the chat without opening the panel.
##
## Facts come first from the agent (file_preview "facts"), then from what the review can
## measure itself (an image darker or larger, a sound shorter, lines of text), then the counts.

const DETAILS := preload("review_details.gd")
## At most this many facts before the counts, so the line stays one line.
const MAX_FACTS := 3
## Lines of text changes a change may have and still be decided from the chat card.
const SIMPLE_LINES := 40
## Difference in mean brightness (0..1) that reads as "darker" or "lighter".
const SHIFT := 0.04
## Hue (0..1) everyone calls warm: orange.
const WARM_HUE := 0.08
## Move of the mean hue towards or away from orange that reads as "warmer" or "cooler".
const HUE_SHIFT := 0.03
## Below this mean saturation a picture is grey and has no warmth to speak of.
const MIN_SATURATION := 0.12
## A picture this small (pixels on its longest side) is drawn crisp when blown up.
const CRISP_SIDE := 256
## A larger picture with at most this many colours (pixel art, a flat drawing) is too.
const CRISP_COLOURS := 64
## Samples per side when counting a picture's colours.
const CRISP_SAMPLE := 64
## Longest side, in pixels, of the copy a picture's subject is found on.
const SUBJECT_SAMPLE := 96
## How far from the ground (0..1 per channel, or alpha) a pixel must be to be the subject.
const SUBJECT_TOLERANCE := 0.08
## Room left around a subject, as a share of its longest side on each edge.
const SUBJECT_MARGIN := 0.08
## How far apart (0..1 per channel) a picture's edge colours may be and still be one ground.
const GROUND_TOLERANCE := 0.03
## Severity order of reviewer notes, worst last.
const SEVERITIES := ["info", "warn", "block"]


## The one line of facts for [param review] ({facts?, changes, diff, scene_count?}) with
## [param measured] facts from the media. Empty parts are left out; the first word is capital.
static func facts_line(review: Dictionary, measured: Array = []) -> String:
	var parts := PackedStringArray()
	for fact: Variant in review.get("facts", []):
		var text := str(fact).strip_edges()
		if not text.is_empty() and parts.size() < MAX_FACTS:
			parts.append(text)
	# The agent's words carry the meaning; measured facts only stand in when it gave none.
	for fact: Variant in measured if parts.is_empty() else []:
		if parts.size() >= MAX_FACTS:
			break
		parts.append(str(fact))
	if parts.size() < MAX_FACTS:
		var lines := text_fact(str(review.get("diff", "")))
		if not lines.is_empty():
			parts.append(lines)
	var count := int(review.get("scene_count", -1))
	if count >= 0:
		parts.append(DETAILS.scene_text(count))
	else:
		var changes: Array = review.get("changes", [])
		if not changes.is_empty():
			parts.append(DETAILS.counts_text(changes))
	var line := " · ".join(parts)
	return line.substr(0, 1).to_upper() + line.substr(1)


## "+12 −3 lines" for the text changes in a unified [param diff] ("" without any).
static func text_fact(diff: String) -> String:
	var numbers := changed_lines(diff)
	if numbers[0] + numbers[1] == 0:
		return ""
	if numbers[1] == 0:
		return DETAILS.plural(numbers[0], "line") + " added"
	if numbers[0] == 0:
		return DETAILS.plural(numbers[1], "line") + " removed"
	return "+%d −%d lines" % numbers


## [added, removed] lines in a unified [param diff], without the ---/+++ headers.
static func changed_lines(diff: String) -> Array[int]:
	var added := 0
	var removed := 0
	for lines: Variant in DETAILS.split_diff(diff).values():
		var counts := DETAILS.line_counts(lines as Array)
		added += counts[0]
		removed += counts[1]
	return [added, removed]


## The asset's name in plain words: "art/textures/door_wood.png" -> "door wood".
static func plain_name(path: String) -> String:
	var stem := path.get_file().get_basename()
	for mark: String in ["_", "-", "."]:
		stem = stem.replace(mark, " ")
	return stem.strip_edges()


## What changed in an image, measured, as one fact: "door wood darker and warmer, 256 → 512
## px". Empty when either side is missing or nothing visible changed.
static func image_facts(name: String, before: Image, after: Image) -> Array[String]:
	var found: Array[String] = []
	if before == null or after == null or before.is_empty() or after.is_empty():
		return found
	var old := _mean(before)
	var new := _mean(after)
	var looks := PackedStringArray()
	var light := new.get_luminance() - old.get_luminance()
	if absf(light) >= SHIFT:
		looks.append("lighter" if light > 0.0 else "darker")
	# Warmth is the hue moving towards or away from orange; brightness alone never counts.
	if old.s >= MIN_SATURATION and new.s >= MIN_SATURATION:
		var towards := _hue_gap(old.h, WARM_HUE) - _hue_gap(new.h, WARM_HUE)
		if absf(towards) >= HUE_SHIFT:
			looks.append("warmer" if towards > 0.0 else "cooler")
	var parts := PackedStringArray()
	if not looks.is_empty():
		parts.append(" and ".join(looks))
	if before.get_size() != after.get_size():
		parts.append("%s → %s px" % [_size_words(before), _size_words(after)])
	if not parts.is_empty():
		found.append("%s %s" % [name, ", ".join(parts)])
	return found


## "door creak 1.4 → 0.9 s" when the length of a sound changed by a tenth of a second or more.
static func audio_fact(name: String, before: float, after: float) -> String:
	if before <= 0.0 or after <= 0.0 or absf(after - before) < 0.1:
		return ""
	return "%s %.1f → %.1f s" % [name, before, after]


## The checks as one quiet status: a review only opens once its checks passed.
static func checks_text(_review: Dictionary) -> String:
	return "Checks passed"


## What the checks covered, for the status tooltip and Details: "Checked references,
## dependencies and the Godot import. No tests to run." ("" when unknown).
static func checks_detail(review: Dictionary) -> String:
	var checks: Variant = review.get("checks", {})
	if not checks is Dictionary:
		return ""
	var ran := PackedStringArray()
	for name: Variant in (checks as Dictionary).get("ran", []):
		ran.append(_check_words(str(name)))
	var text := ""
	if not ran.is_empty():
		var last := ran[-1]
		var lead := ", ".join(ran.slice(0, -1))
		text = "Checked " + (last if lead.is_empty() else lead + " and " + last) + "."
	if "tests" in (checks as Dictionary).get("skipped", []):
		text = (text + " No tests to run.").strip_edges()
	return text


## Notes as [{severity, text, file?}], worst first; unknown severities count as info.
static func sorted_notes(notes: Variant) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	if not notes is Array:
		return found
	for note: Variant in notes:
		if (
			note is Dictionary
			and not str((note as Dictionary).get("text", "")).strip_edges().is_empty()
		):
			found.append(note as Dictionary)
	found.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return severity(a) > severity(b))
	return found


## 0 info, 1 warn, 2 block.
static func severity(note: Dictionary) -> int:
	return maxi(0, SEVERITIES.find(str(note.get("severity", "info"))))


## The reviewer's notes as one line: the worst note, then "+2 more" ("" without notes).
static func notes_line(notes: Array[Dictionary]) -> String:
	if notes.is_empty():
		return ""
	var line := str(notes[0].get("text", "")).strip_edges().split("\n")[0]
	if notes.size() > 1:
		line += " · %d more" % (notes.size() - 1)
	return line


## The reviewer's state as a short phrase ("Reviewer checking…"), "" when done or absent.
static func reviewer_text(status: String) -> String:
	if status in ["running", "working", "reviewing", "pending"]:
		return "Reviewer checking…"
	# The reviewer agent was skipped (ADR 0037): the change opened without it.
	if status == "timeout":
		return "Reviewer ran out of time"
	if status == "error":
		return "Reviewer couldn't run"
	return ""


## True when [param review] can be decided from the chat card: at most one asset, few text
## lines, and no reviewer warning or block.
static func is_simple(review: Dictionary, assets: int, notes: Array[Dictionary]) -> bool:
	if assets > 1:
		return false
	if not notes.is_empty() and severity(notes[0]) > 0:
		return false
	var numbers := changed_lines(str(review.get("diff", "")))
	if numbers[0] + numbers[1] > SIMPLE_LINES:
		return false
	return int(review.get("scene_count", 0)) <= 3


static func _check_words(name: String) -> String:
	if name == "Godot import":
		return "the Godot import"
	if name == "art bible":
		return "the art bible"
	return name


static func _size_words(image: Image) -> String:
	var size := image.get_size()
	return str(size.x) if size.x == size.y else "%d×%d" % [size.x, size.y]


## Distance between two hues on the colour wheel (0..0.5).
static func _hue_gap(a: float, b: float) -> float:
	var gap := absf(a - b)
	return minf(gap, 1.0 - gap)


## The mean colour, from a small copy of the image.
static func _mean(image: Image) -> Color:
	var small := image.duplicate() as Image
	if small.is_compressed():
		small.decompress()
	small.convert(Image.FORMAT_RGBA8)
	small.resize(8, 8, Image.INTERPOLATE_BILINEAR)
	var total := Color(0, 0, 0, 0)
	for y: int in 8:
		for x: int in 8:
			total += small.get_pixel(x, y)
	return total / 64.0


## How a picture shown at [param shown] pixels is sampled: nearest-neighbour when it is blown
## up and is pixel art or a small or flat drawing (a sketch, a plan, a 32 px texture), so its
## pixels stay crisp; smooth for photos and renders, and for anything shown smaller.
static func picture_filter(texture: Texture2D, shown: Vector2) -> CanvasItem.TextureFilter:
	if texture == null or texture.get_width() <= 0 or texture.get_height() <= 0:
		return CanvasItem.TEXTURE_FILTER_LINEAR
	var scale := minf(shown.x / texture.get_width(), shown.y / texture.get_height())
	if scale <= 1.0 or not crisp(texture):
		return CanvasItem.TEXTURE_FILTER_LINEAR
	return CanvasItem.TEXTURE_FILTER_NEAREST


## True for pixel art and small or flat pictures: at most [constant CRISP_SIDE] pixels on a
## side, or at most [constant CRISP_COLOURS] colours in a sample of its pixels. Remembered on
## the texture.
static func crisp(texture: Texture2D) -> bool:
	if texture.has_meta(&"joinery_crisp"):
		return bool(texture.get_meta(&"joinery_crisp"))
	var found := maxi(texture.get_width(), texture.get_height()) <= CRISP_SIDE
	if not found:
		var image := texture.get_image()
		if image != null and not image.is_empty():
			if image.is_compressed():
				image = image.duplicate() as Image
				image.decompress()
			var colours := {}
			var step := Vector2i(
				maxi(1, image.get_width() / CRISP_SAMPLE),
				maxi(1, image.get_height() / CRISP_SAMPLE)
			)
			for y: int in range(0, image.get_height(), step.y):
				for x: int in range(0, image.get_width(), step.x):
					colours[image.get_pixel(x, y).to_rgba32()] = true
					if colours.size() > CRISP_COLOURS:
						break
				if colours.size() > CRISP_COLOURS:
					break
			found = colours.size() <= CRISP_COLOURS
	texture.set_meta(&"joinery_crisp", found)
	return found


## The plain colour around a picture (a render's or a snapshot's background), or a clear
## colour when its edges differ. The review fills the frame with it, so a picture narrower
## than the frame doesn't sit as a box between empty bands. Remembered on the texture.
static func ground(texture: Texture2D) -> Color:
	if texture == null:
		return Color.TRANSPARENT
	if texture.has_meta(&"joinery_ground"):
		return texture.get_meta(&"joinery_ground")
	var found := Color.TRANSPARENT
	var image := texture.get_image()
	if image != null and not image.is_empty():
		if image.is_compressed():
			image = image.duplicate() as Image
			image.decompress()
		var right := image.get_width() - 1
		var bottom := image.get_height() - 1
		var points := [
			Vector2i(0, 0),
			Vector2i(right / 2, 0),
			Vector2i(right, 0),
			Vector2i(0, bottom / 2),
			Vector2i(right, bottom / 2),
			Vector2i(0, bottom),
			Vector2i(right / 2, bottom),
			Vector2i(right, bottom),
		]
		var first := image.get_pixelv(points[0])
		found = first if first.a >= 0.99 else Color.TRANSPARENT
		for point: Vector2i in points:
			var colour := image.get_pixelv(point)
			var apart := maxf(
				absf(colour.r - first.r), maxf(absf(colour.g - first.g), absf(colour.b - first.b))
			)
			if apart > GROUND_TOLERANCE or colour.a < 0.99:
				found = Color.TRANSPARENT
				break
	texture.set_meta(&"joinery_ground", found)
	return found


## The part of [param texture] that shows its subject: the box around every pixel that differs
## from the ground (or is not see-through), with a margin, as a square in the picture's pixels.
## A model render frames its model small on a wide ground; a thumbnail cropped to this shows
## the model, not the ground. The whole picture when it has no plain ground to tell apart.
static func subject(texture: Texture2D) -> Rect2:
	if texture == null:
		return Rect2()
	if texture.has_meta(&"joinery_subject"):
		return texture.get_meta(&"joinery_subject")
	var found := Rect2(Vector2.ZERO, texture.get_size())
	var image := texture.get_image()
	if image != null and not image.is_empty():
		image = image.duplicate() as Image
		if image.is_compressed():
			image.decompress()
		var box := _subject_box(image, ground(texture))
		if box.has_area():
			var side := maxf(box.size.x, box.size.y) * (1.0 + SUBJECT_MARGIN * 2.0)
			found = Rect2(box.get_center() - Vector2(side, side) / 2.0, Vector2(side, side))
	texture.set_meta(&"joinery_subject", found)
	return found


## The box (in [param image]'s pixels) around what is not [param plain], or see-through
## ground when [param plain] is clear; empty when there is no plain ground to tell apart.
static func _subject_box(image: Image, plain: Color) -> Rect2:
	if plain.a <= 0.0 and image.detect_alpha() == Image.ALPHA_NONE:
		return Rect2()
	# Measured on a small copy: the box only has to be right to a few pixels.
	var scale := minf(1.0, float(SUBJECT_SAMPLE) / maxf(image.get_width(), image.get_height()))
	var small := Vector2i(
		maxi(1, roundi(image.get_width() * scale)), maxi(1, roundi(image.get_height() * scale))
	)
	image.resize(small.x, small.y, Image.INTERPOLATE_BILINEAR)
	var low := small
	var high := Vector2i(-1, -1)
	for y: int in small.y:
		for x: int in small.x:
			var colour := image.get_pixel(x, y)
			var apart := colour.a
			if plain.a > 0.0:
				apart = maxf(
					absf(colour.r - plain.r),
					maxf(absf(colour.g - plain.g), absf(colour.b - plain.b))
				)
			if apart > SUBJECT_TOLERANCE:
				low = Vector2i(mini(low.x, x), mini(low.y, y))
				high = Vector2i(maxi(high.x, x), maxi(high.y, y))
	if high.x < 0:
		return Rect2()
	var box := Rect2(Vector2(low), Vector2(high - low + Vector2i.ONE))
	return Rect2(box.position / scale, box.size / scale)
