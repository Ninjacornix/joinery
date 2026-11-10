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
## so the asset is still changed through chat; [param previews] says the workspace can still
## show it.
static func missing_text(
	app: String, thing: String, extension: String, previews: bool, ready: bool
) -> Array[String]:
	if ready:
		return [
			"Ask Journeyman to change this %s" % thing,
			(
				"%s is ready for Journeyman. To open the file yourself, install %s on this computer."
				% [app, app]
			),
		]
	var look := "listen" if thing == "sound" else "look at it"
	var line := "You can %s here, but changing this %s needs %s." % [look, thing, app]
	if not previews:
		line = "Install %s on this computer to see and change .%s files." % [app, extension]
	return ["%s isn't installed" % app, line]
