@tool
extends RefCounted
## What a review item is, from its dictionary alone ({path, media, before?, after?,
## before_back?, after_back?, before_model?, after_model?, reference?, label?, error?}): its
## kind, whether both sides show at once, its live models and its name. Shared by the stage
## (review_media.gd) and its filmstrip.

const DETAILS := preload("review_details.gd")
const GLANCE := preload("review_glance.gd")


## "image", "model", "audio", "clip" (an animation) or "text" for a media [param item].
static func kind_of(item: Dictionary) -> String:
	var media := str(item.get("media", "image"))
	if media in ["audio", "text", "clip"]:
		return media
	if item.has("before_back") or item.has("after_back"):
		return "model"
	var extension := str(item.get("path", "")).get_extension().to_lower()
	if extension in DETAILS.KINDS["model"] and not item.has("part"):
		return "model"
	return "image"


## True when [param item] shows both sides at once: a split picture (texture, part view or scene
## snapshot) or two clips. Model renders have their own framing per side, so they crossfade;
## live models are side by side (review_media.gd decides, once they load).
static func swipes(item: Dictionary) -> bool:
	return (
		kind_of(item) in ["image", "clip"]
		and paired(item)
		and not item.has("error")
		and not item.has("reference")
	)


## True when [param item] has both a Before and an After.
static func paired(item: Dictionary) -> bool:
	return item.has("before") and item.has("after")


## What identifies the live models of [param item] ("" when it names no GLB files).
static func live_key(item: Dictionary) -> String:
	if not kind_of(item) in ["model", "clip"]:
		return ""
	var files := "%s|%s" % [item.get("before_model", ""), item.get("after_model", "")]
	if files == "|":
		return ""
	return "%s#%s|%s" % [item.get("path", ""), item.get("part", ""), files]


## The item's name for its filmstrip tile and the stage's tooltip ("run · hero.blend").
static func name_of(item: Dictionary) -> String:
	if kind_of(item) == "text":
		return str(item.get("path", "Text changes"))
	var label := str(item.get("label", ""))
	var file := str(item.get("path", "")).get_file()
	return file if label.is_empty() else "%s · %s" % [label, file]


## The background the shown pictures share (a model's two renders), else clear.
static func common_ground(picture: Texture2D, back: Texture2D) -> Color:
	var colour := GLANCE.ground(picture)
	if back != null and not GLANCE.ground(back).is_equal_approx(colour):
		return Color.TRANSPARENT
	return colour
