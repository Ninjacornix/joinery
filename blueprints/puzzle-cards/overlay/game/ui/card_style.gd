extends RefCounted
## The table and card look, built from StyleBoxFlat only: no images, no
## fonts beyond Godot's own. Sizes are given at 1080p and scaled with the
## core's `UiStyle.px`, so the art bible's text and radius rules hold.

const TABLE := Color("#123a33")
const TABLE_EDGE := Color("#0b2622")
const FELT := Color("#18493f")
const BACK := Color("#27395e")
const BACK_HOVER := Color("#33497a")
const BACK_TRIM := Color("#f2b632")
const FACE := Color("#f6f1e4")
const FACE_EDGE := Color("#d8cfba")
const MATCH := Color("#4caf6a")
const MISS := Color("#e0574f")
const FOCUS := Color("#ffd75e")
const SLOT := Color(1.0, 1.0, 1.0, 0.14)
const SHADOW := Color(0.0, 0.0, 0.0, 0.35)

const RADIUS := 16
const BORDER := 2
const GLYPH := 64


static func px(size_at_1080: float) -> int:
	return UiStyle.px(size_at_1080)


static func _card(bg: Color, edge: Color, width: int) -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = bg
	style.set_corner_radius_all(px(RADIUS))
	style.border_color = edge
	style.set_border_width_all(px(width))
	style.set_content_margin_all(px(8))
	style.shadow_color = SHADOW
	style.shadow_size = px(6)
	style.shadow_offset = Vector2(0, px(3))
	style.anti_aliasing = true
	return style


static func back() -> StyleBoxFlat:
	return _card(BACK, BACK.darkened(0.35), BORDER)


static func back_hover() -> StyleBoxFlat:
	return _card(BACK_HOVER, BACK_TRIM.darkened(0.2), BORDER)


static func face() -> StyleBoxFlat:
	return _card(FACE, FACE_EDGE, BORDER)


static func matched() -> StyleBoxFlat:
	return _card(FACE, MATCH, BORDER * 3)


static func missed() -> StyleBoxFlat:
	return _card(FACE, MISS, BORDER * 3)


## An empty place where a matched pair used to be.
static func slot() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.draw_center = false
	style.set_corner_radius_all(px(RADIUS))
	style.border_color = SLOT
	style.set_border_width_all(px(BORDER))
	return style


## A thick ring drawn around the focused card.
static func focus_ring() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.draw_center = false
	style.set_corner_radius_all(px(RADIUS + 4))
	style.border_color = FOCUS
	style.set_border_width_all(px(5))
	var grow: int = px(5)
	style.expand_margin_left = grow
	style.expand_margin_right = grow
	style.expand_margin_top = grow
	style.expand_margin_bottom = grow
	return style


## The inner frame on a card back.
static func back_frame() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = BACK.lightened(0.06)
	style.set_corner_radius_all(px(10))
	style.border_color = BACK_TRIM
	style.set_border_width_all(px(2))
	return style


## The diamond pip in the middle of a card back.
static func back_pip() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = BACK_TRIM
	style.set_corner_radius_all(px(3))
	return style


## The felt the cards lie on.
static func table() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = FELT
	style.set_corner_radius_all(px(28))
	style.border_color = TABLE_EDGE
	style.set_border_width_all(px(6))
	style.set_content_margin_all(px(28))
	style.shadow_color = SHADOW
	style.shadow_size = px(18)
	return style


## The strip along the top that holds the counters.
static func bar() -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = Color(0.0, 0.0, 0.0, 0.28)
	style.set_corner_radius_all(px(14))
	style.content_margin_left = px(28)
	style.content_margin_right = px(28)
	style.content_margin_top = px(8)
	style.content_margin_bottom = px(8)
	return style


## A pill behind the "whose turn" text; `active` lights it up.
static func seat(active: bool) -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = FOCUS if active else Color(1.0, 1.0, 1.0, 0.08)
	style.set_corner_radius_all(px(20))
	style.content_margin_left = px(18)
	style.content_margin_right = px(18)
	style.content_margin_top = px(4)
	style.content_margin_bottom = px(4)
	return style
