@tool
extends RefCounted
## The asset workspace's words (tool_workspace.gd): an app's readiness, what to call an asset
## in a sentence, what to say when the app that opens it isn't on this computer, and a change
## that waits for review.

## Readiness ("status", older names too) → [dot, wording], the same marks as the app rail.
const READINESS := {
	"ready": ["ready", "Ready"],
	"preparing": ["working", "Downloading…"],
	"needs_docker": ["off", "Needs Docker"],
	"needs_licence": ["off", "Needs your licence"],
	"unavailable": ["off", "Not available yet"],
	"installed": ["ready", "Ready"],
	"licence": ["off", "Needs your licence"],
	"missing": ["off", "Not installed"],
}
## A row and caption for an asset that a change waiting for review adds or replaces.
const WAITING := "Waiting for review"
const SOUNDS := ["wav", "ogg", "mp3", "flac", "aif", "aiff"]
## Short words that read as letters ("CRT station", "Wood ORM"), never guessed beyond this list.
const ACRONYMS := [
	"ao",
	"crt",
	"fx",
	"gui",
	"hdr",
	"hud",
	"id",
	"lcd",
	"led",
	"lod",
	"npc",
	"orm",
	"pbr",
	"rgb",
	"sfx",
	"tv",
	"ui",
	"uv",
	"vfx",
]

static var _copy_suffix := RegEx.create_from_string("\\.\\d{3}$")
static var _file_copy := RegEx.create_from_string("^(.*)[._ ]\\d{3}$")
static var _lower_upper := RegEx.create_from_string("([a-z0-9])([A-Z])")
static var _upper_word := RegEx.create_from_string("([A-Z]+)([A-Z][a-z])")


## A name as words in sentence case: "aged_grey_trim" and "AgedGreyTrim" read "Aged grey
## trim", "crt-station" reads "CRT station". Words in capitals stay so; run-together
## lowercase ("agedgreytrim") is kept as written, not guessed apart. A Blender copy number
## stays as written ("Head.001").
static func readable(name: String) -> String:
	var copy := ""
	var found := _copy_suffix.search(name)
	if found != null:
		copy = found.get_string()
		name = name.substr(0, found.get_start())
	var spaced := _lower_upper.sub(name, "$1 $2", true)
	spaced = _upper_word.sub(spaced, "$1 $2", true)
	var words := spaced.replace("_", " ").replace("-", " ").replace(".", " ").split(" ", false)
	for index: int in words.size():
		var word := words[index]
		if word.to_lower() in ACRONYMS:
			word = word.to_upper()
		elif word.substr(1) == word.substr(1).to_lower():
			# "Grey" or "grey": an ordinary word. Mixed case ("iPhone") stays as written.
			word = word.to_lower()
		if index == 0:
			word = word.substr(0, 1).to_upper() + word.substr(1)
		words[index] = word
	var text := " ".join(words)
	return (text if not text.is_empty() else name) + copy


## A file name without its folder and extension, as words ("crt_station.blend" → "CRT
## station").
static func file_title(path: String) -> String:
	return readable(path.get_file().get_basename())


## Whether [param path] is a numbered copy ("albedo_001.png", "albedo.001.png") of another
## file in [param paths]: the same name without the number, in any folder.
static func is_copy(path: String, paths: Array) -> bool:
	var found := _file_copy.search(path.get_file().get_basename())
	if found == null:
		return false
	var original := "%s.%s" % [found.get_string(1), path.get_extension()]
	for other: Variant in paths:
		if str(other).get_file() == original:
			return true
	return false


## The quiet line for an app that is ready for Journeyman but not on this computer.
static func install_note(app: String) -> String:
	return "To open the file yourself, install %s on this computer." % app


## What to call an asset of [param kind] at [param path] in a sentence: "model", "material",
## "texture" (an image the game wears, under a textures folder), "image", "sound", "scene".
static func noun(kind: String, path: String) -> String:
	match kind:
		"model", "material", "sound", "scene":
			return kind
		"image":
			return "texture" if "/textures/" in "/" + path else "image"
	return "sound" if path.get_extension().to_lower() in SOUNDS else "file"


## The preview's note for [param asset] when its file isn't in the project.
static func pending_note(asset: Dictionary) -> String:
	if str(asset.get("pending", "")).is_empty():
		return "This file is missing."
	return "Waiting for your review. Accept it in the Review tab to see it here."


## The app behind [param option] (an open option, {tool, name}) is ready for Journeyman in
## the shared readiness report [param tools] (the workspace index's tool rows).
static func ready_for(tools: Array, option: Dictionary) -> bool:
	for tool: Variant in tools:
		if tool is Dictionary and str(tool.get("id", "")) == str(option.get("tool", "")):
			return str(tool.get("status", "")) in ["ready", "installed"]
	return false


## Title and line for an asset whose [param app] isn't on this computer. [param ready] is the
## app's readiness from the shared report (Journeyman works with it in the offline worker),
## so the asset is still changed through chat (the page's "Ask Journeyman" action) and only
## a quiet line says how to open it yourself; [param previews] says the workspace can still
## show it.
static func missing_text(
	app: String, thing: String, extension: String, previews: bool, ready: bool
) -> Array[String]:
	if ready:
		return ["" if previews else "No preview for .%s files" % extension, install_note(app)]
	var look := "listen" if thing == "sound" else "look at it"
	var line := "You can %s here, but changing this %s needs %s." % [look, thing, app]
	if not previews:
		line = "Install %s on this computer to see and change .%s files." % [app, extension]
	return ["%s isn't installed" % app, line]


## Why an app in the "Open with" menu can't open the file.
static func unavailable_text(option: Dictionary) -> String:
	if source_missing(option):
		return "The source file is missing."
	return "Install %s on this computer to open it here." % option["name"]


## Whether an open option failed because the source file is missing, not the app.
static func source_missing(option: Dictionary) -> bool:
	return str(option.get("reason", "")).begins_with("Source missing")
