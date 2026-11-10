@tool
extends HFlowContainer
## The images going with the next message, above the composer's targets: one square thumbnail
## each, the image's name as its tooltip and a × in its corner. Adding one scales it in;
## removing one leaves the message at once (optimistic) and animates the tile out. Holds up to
## chat_attachments.gd's MAX_IMAGES; the row hides when empty.

## The set of images changed (added, removed or cleared).
signal changed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const ATTACHMENTS := preload("chat_attachments.gd")

var _entries: Array[Dictionary] = []


func _init() -> void:
	hide()
	accessibility_name = "Attached images"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var gap := UI_THEME.space(self, 1)
			add_theme_constant_override("h_separation", gap)
			add_theme_constant_override("v_separation", gap)
	)


## The attached images, as target entries ({attachment, id, path, label, width, height}).
func entries() -> Array[Dictionary]:
	return _entries.duplicate(true)


func count() -> int:
	return _entries.size()


func is_full() -> bool:
	return _entries.size() >= ATTACHMENTS.MAX_IMAGES


## Add [param entry]; false when the message already has MAX_IMAGES images.
func add(entry: Dictionary) -> bool:
	if is_full() or entry.is_empty():
		return false
	_entries.append(entry.duplicate(true))
	var tile := _tile(entry)
	add_child(tile)
	show()
	UI_MOTION.enter(tile)
	changed.emit()
	return true


## Replace every image with [param entries] (a queued message put back for editing).
func set_entries(entries_in: Array[Dictionary]) -> void:
	_entries.clear()
	for child: Node in get_children():
		remove_child(child)
		child.queue_free()
	for entry: Dictionary in entries_in.slice(0, ATTACHMENTS.MAX_IMAGES):
		_entries.append(entry.duplicate(true))
		add_child(_tile(entry))
	visible = not _entries.is_empty()
	changed.emit()


func clear() -> void:
	var none: Array[Dictionary] = []
	set_entries(none)


func remove(id: String) -> void:
	var kept: Array[Dictionary] = []
	for entry: Dictionary in _entries:
		if str(entry["id"]) != id:
			kept.append(entry)
	_entries = kept
	for child: Node in get_children():
		if str(child.get_meta("attachment_id", "")) == id:
			UI_MOTION.exit(child, null, _settle.bind(child), true)
	changed.emit()


func _settle(_child: Node) -> void:
	if _entries.is_empty():
		hide()


## The images in [param targets] as a row of read-only thumbnails (the sent request's bubble),
## or null when there are none.
static func strip(targets: Array) -> HBoxContainer:
	var images: Array[Dictionary] = []
	for target: Variant in targets:
		if ATTACHMENTS.is_attachment(target):
			images.append(target)
	if images.is_empty():
		return null
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.accessibility_name = "Attached images"
	for entry: Dictionary in images:
		var frame := PanelContainer.new()
		frame.theme_type_variation = "JoineryMediaFrame"
		frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
		frame.tooltip_text = str(entry.get("label", "Image"))
		var picture := TextureRect.new()
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		frame.add_child(picture)
		STYLE.on_theme(
			frame,
			func() -> void:
				var side := float(UI_THEME.control_h(frame, "lg"))
				frame.custom_minimum_size = Vector2(side, side)
				if picture.texture == null:
					picture.texture = ATTACHMENTS.thumbnail(entry, roundi(side))
		)
		row.add_child(frame)
	return row


## A square thumbnail in a media frame, with its × in the top-right corner.
func _tile(entry: Dictionary) -> Control:
	var tile := Control.new()
	tile.set_meta("attachment_id", str(entry["id"]))
	tile.tooltip_text = str(entry.get("label", "Image"))
	tile.mouse_filter = Control.MOUSE_FILTER_PASS
	var frame := PanelContainer.new()
	frame.theme_type_variation = "JoineryMediaFrame"
	frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	frame.mouse_filter = Control.MOUSE_FILTER_IGNORE
	frame.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	tile.add_child(frame)
	var picture := TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	frame.add_child(picture)
	var close := Button.new()
	close.theme_type_variation = "JoineryChipClose"
	close.tooltip_text = "Remove this image"
	close.accessibility_name = "Remove %s" % tile.tooltip_text
	close.focus_mode = Control.FOCUS_ALL
	close.expand_icon = true
	UI.bind_icon(close, "close")
	close.pressed.connect(remove.bind(str(entry["id"])))
	tile.add_child(close)
	STYLE.on_theme(
		tile,
		func() -> void:
			var side := float(UI_THEME.control_h(tile, "lg")) * 1.5
			tile.custom_minimum_size = Vector2(side, side)
			if picture.texture == null:
				picture.texture = ATTACHMENTS.thumbnail(entry, roundi(side))
			# The smallest hit target (CONTROL_H.xs), on the picture's top-right corner.
			var button := float(UI_THEME.control_h(tile, "xs"))
			var inset := float(UI_THEME.space(tile, 1)) / 2.0
			close.custom_minimum_size = Vector2(button, button)
			close.size = Vector2(button, button)
			close.position = Vector2(side - button - inset, inset)
			# A raised disc keeps the × readable over any picture.
			var pad := roundi(button * 0.25)
			for state: String in ["normal", "hover", "pressed", "focus"]:
				var disc := StyleBoxFlat.new()
				var fill := UI_THEME.color(tile, "surface_raised")
				disc.bg_color = (
					fill.lerp(UI_THEME.color(tile, "text"), 0.1) if state != "normal" else fill
				)
				disc.set_corner_radius_all(roundi(button / 2.0))
				disc.set_content_margin_all(pad)
				if state == "focus":
					disc.draw_center = false
					disc.border_color = tile.get_theme_color("accent", "Joinery")
					disc.set_border_width_all(maxi(1, roundi(UI_THEME.unit(tile))))
				close.add_theme_stylebox_override(state, disc)
	)
	return tile
