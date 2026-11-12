@tool
extends RefCounted
## A bare "/variant" in the sidebar (ADR 0066): a variant, Godot's inherited scene, of the
## scene open in the editor. The chat host makes it without an agent (`variant_make`,
## bridge/scene_variant.py) and it is reviewed like any change: the picture of the base
## beside the variant, and what it overrides (nothing yet). "/variant <what differs>" is a
## request to the agent instead, which calls make_variant.

const NO_SCENE := "Open the scene to make a variant of, then type /variant."
const OFFLINE := "Journeyman isn't connected."


## Connect the sidebar's "/variant" to [param tasks]' bridge (sidebar_tasks.gd).
static func attach(sidebar: Control, tasks: Node) -> void:
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("scene_variant_requested"):
		slash.connect("scene_variant_requested", make.bind(sidebar, tasks))


## Ask for a variant of the edited scene; [param scene] overrides it (tests).
static func make(sidebar: Control, tasks: Node, scene := "") -> bool:
	if scene.is_empty() and Engine.is_editor_hint():
		var root := EditorInterface.get_edited_scene_root()
		scene = root.scene_file_path if root != null else ""
	if scene.is_empty():
		_say(sidebar, NO_SCENE, "info")
		return false
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	var line := {"kind": "variant_make", "base": scene}
	if bridge == null or not bridge.has_method("start_task"):
		_say(sidebar, OFFLINE, "error")
		return false
	if not bridge.call("start_task", "", [], line):
		_say(sidebar, OFFLINE, "error")
		return false
	if sidebar.has_method("show_status"):
		sidebar.call("show_status", "Making a variant of %s…" % scene.get_file(), 3.0)
	return true


static func _say(sidebar: Control, text: String, kind: String) -> void:
	var transcript: Object = sidebar.get("_transcript") if sidebar != null else null
	if transcript != null:
		transcript.call("add_notice", text, kind)
