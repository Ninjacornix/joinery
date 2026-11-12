@tool
extends RefCounted
## What a review item is, from its dictionary alone ({path, media, before?, after?,
## before_back?, after_back?, before_model?, after_model?, reference?, label?, error?}): its
## kind, whether both sides show at once, its live models, its name and its place in the file
## list. Shared by the stage (review_media.gd) and its file list (review_files.gd).

const DETAILS := preload("review_details.gd")
const GLANCE := preload("review_glance.gd")


## "image", "model", "audio", "clip" (an animation), "dialogue" (a .dlg file to play
## through) or "text" for a media [param item].
static func kind_of(item: Dictionary) -> String:
	var media := str(item.get("media", "image"))
	if media in ["audio", "text", "clip", "dialogue"]:
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


## The item's name for its row in the file list and the stage's tooltip ("run · hero.blend").
static func name_of(item: Dictionary) -> String:
	if kind_of(item) == "text":
		return str(item.get("path", "Text changes"))
	var label := str(item.get("label", ""))
	var file := str(item.get("path", "")).get_file()
	return file if label.is_empty() else "%s · %s" % [label, file]


## "new" (an After only), "removed" (a Before only) or "changed", for the file list's mark.
static func mark(item: Dictionary) -> String:
	if kind_of(item) == "text":
		return ""
	if item.has("after") and not item.has("before"):
		return "new" if not item.has("reference") else "changed"
	if item.has("before") and not item.has("after"):
		return "removed"
	return "changed"


## [param items] in the file list's order: each model or source first, then its part views and
## clips (same file) and the textures that belong to it, then every other file as it came, text
## last. A texture belongs to the only model, or to the one whose name starts its file name or
## that shares its folder. [param depths] receives 1 for a row grouped under a model, else 0.
static func grouped(items: Array, depths: Array[int]) -> Array:
	var sources: Array[Dictionary] = []
	for item: Dictionary in items:
		if kind_of(item) == "model" and not item.has("part"):
			sources.append(item)
	var owner_of := {}
	for item: Dictionary in items:
		if item in sources or kind_of(item) == "text":
			continue
		var found := _owner(item, sources)
		if found != null:
			owner_of[item] = found
	var ordered: Array = []
	depths.clear()
	for source: Dictionary in sources:
		ordered.append(source)
		depths.append(0)
		for item: Dictionary in items:
			if owner_of.get(item) == source:
				ordered.append(item)
				depths.append(1)
	for last: bool in [false, true]:
		for item: Dictionary in items:
			if not item in ordered and (kind_of(item) == "text") == last:
				ordered.append(item)
				depths.append(0)
	return ordered


## The model [param item] is grouped under, or null.
static func _owner(item: Dictionary, sources: Array[Dictionary]) -> Variant:
	var path := str(item.get("path", ""))
	for source: Dictionary in sources:
		if str(source.get("path", "")) == path:
			return source
	var picture := path.get_extension().to_lower() in DETAILS.KINDS["image"]
	if kind_of(item) != "image" or not picture or item.has("reference") or sources.is_empty():
		return null
	if sources.size() == 1:
		return sources[0]
	for source: Dictionary in sources:
		var model := str(source.get("path", ""))
		var stem := model.get_file().get_basename().to_lower()
		if (
			path.get_file().to_lower().begins_with(stem)
			or path.get_base_dir() == model.get_base_dir()
		):
			return source
	return null


## The background the shown pictures share (a model's two renders), else clear.
static func common_ground(picture: Texture2D, back: Texture2D) -> Color:
	var colour := GLANCE.ground(picture)
	if back != null and not GLANCE.ground(back).is_equal_approx(colour):
		return Color.TRANSPARENT
	return colour
