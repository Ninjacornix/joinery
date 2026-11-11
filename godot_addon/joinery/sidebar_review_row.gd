@tool
extends HBoxContainer
## The change waiting for a decision, inside its chat card: a tiny picture of it, one line of
## what changed, and Discard / Accept, so a small change is decided without opening the Review
## panel. A bigger one (several assets, many lines, a reviewer's warning) offers Review
## instead, which brings the panel into view. Both go through the panel (review_panel.gd), so
## the decision looks and behaves the same wherever it is made.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")

var _review: Object
var _frame: PanelContainer
var _thumb: TextureRect
var _line: Label
var _actions: HBoxContainer
var _discard: Button
var _accept: Button
var _open: Button


func _init() -> void:
	theme_type_variation = "JoineryRow"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Change waiting for your decision"
	_frame = PanelContainer.new()
	_frame.theme_type_variation = "JoineryMediaFrame"
	_frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	_frame.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	add_child(_frame)
	_thumb = TextureRect.new()
	_thumb.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_thumb.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	_thumb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_frame.add_child(_thumb)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(column)
	_line = UI.label("", "JoineryCaption")
	_line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_line.mouse_filter = Control.MOUSE_FILTER_PASS
	column.add_child(_line)
	_actions = HBoxContainer.new()
	_actions.theme_type_variation = "JoineryRowTight"
	column.add_child(_actions)
	_discard = UI.secondary("Discard")
	_discard.accessibility_name = "Discard this change"
	_discard.tooltip_text = "Throw this change away. Your files stay as they are."
	_discard.pressed.connect(_decide.bind(false))
	_accept = UI.primary("Accept")
	_accept.accessibility_name = "Accept this change"
	_accept.tooltip_text = "Keep this change"
	_accept.pressed.connect(_decide.bind(true))
	_open = UI.link("Review")
	_open.accessibility_name = "Open the review"
	_open.tooltip_text = "A bigger change: look at it in the Review panel"
	_open.pressed.connect(_show_panel)
	var ordered := (
		[_accept, _discard] if DisplayServer.get_swap_cancel_ok() else [_discard, _accept]
	)
	for button: Button in ordered + [_open]:
		UI.bind_height(button, "sm")
		_actions.add_child(button)
	STYLE.on_theme(
		_thumb,
		func() -> void:
			var side := float(UI_THEME.control_h(_thumb, "lg"))
			_frame.custom_minimum_size = Vector2(side, side)
	)
	hide()


## Follow [param review] (review_panel.gd), or keep the one followed when null, and show
## [param media]'s first picture until the panel has its own.
func show_for(media: Array, review: Object = null) -> void:
	if review != null and review != _review:
		if _review != null and _review.has_signal("glance_changed"):
			_review.disconnect("glance_changed", refresh)
		_review = review
		if _review.has_signal("glance_changed"):
			_review.connect("glance_changed", refresh)
	refresh(media)


## Read the panel again: its line, picture, and whether the change is small.
func refresh(media: Array = []) -> void:
	var glance: Dictionary = {}
	if _review != null and _review.has_method("glance"):
		glance = _review.call("glance")
	var picture: Texture2D = glance.get("thumbnail") as Texture2D
	if picture == null:
		picture = first_picture(media)
	_thumb.texture = picture
	_frame.visible = picture != null
	_line.text = str(glance.get("line", ""))
	_line.tooltip_text = str(glance.get("title", _line.text))
	_line.visible = not _line.text.is_empty()
	var decidable := not glance.is_empty()
	var simple := bool(glance.get("simple", false))
	_actions.visible = decidable
	_discard.visible = simple
	_accept.visible = simple
	_open.visible = decidable and not simple
	var was := visible
	visible = _frame.visible or decidable
	if visible and not was:
		UI_MOTION.reveal(self)


## The thumbnail on show (null without one).
func thumbnail() -> Texture2D:
	return _thumb.texture if _frame.visible else null


## True when Accept and Discard are offered here.
func decidable() -> bool:
	return visible and _accept.visible


## The first "after" image of [param media] whose file exists, as a texture.
static func first_picture(media: Array) -> Texture2D:
	for item: Variant in media:
		if not item is Dictionary:
			continue
		var entry := item as Dictionary
		# A render still running has no file yet; review_media brings it.
		if (
			entry.get("media") == "image"
			and entry.has("after")
			and FileAccess.file_exists(str(entry["after"]))
		):
			var image := Image.load_from_file(str(entry["after"]))
			if image != null and not image.is_empty():
				return ImageTexture.create_from_image(image)
	return null


func _decide(accepted: bool) -> void:
	if _review != null and _review.has_method("decide"):
		_review.call("decide", accepted)


func _show_panel() -> void:
	if _review != null and _review.has_method("request_show"):
		_review.call("request_show")
