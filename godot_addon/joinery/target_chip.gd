@tool
extends RefCounted
## A selected scene object or file shown above the message, as a breadcrumb
## ("hammer › Head") with its kind icon, replaced by a 16px thumbnail when the editor can
## render one. Click it to open the asset in its tool; × removes it from this message.

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## Target kind -> icon name.
const KIND_ICONS := {
	"Object": "model", "Image": "image", "Audio": "audio", "Scene": "scene", "Material": "material"
}
## Part kind -> target kind, which picks the chip icon.
const PART_KINDS := {
	"object": "Object",
	"material": "Material",
	"texture": "Image",
	"bone": "Object",
	"shape_key": "Object",
}
## Part kind -> the typed operations that change it (`joinery parts` lists the same, and
## knows more: a map with a Krita source is painted with paint_map). The agent reads them
## from the target, so a chip routes the request to the operation for its part.
const PART_OPS := {
	"object": "transform_parts",
	"material": "set_material_value,split_material",
	"texture": "adjust_map,make_map_editable,replace_map,split_material",
	"bone": "edit_bone",
	"shape_key": "edit_shape_key",
}
## How each map role reads (the same words as `joinery parts`).
const ROLE_NAMES := {
	"base_color": "Base colour",
	"roughness": "Roughness",
	"metallic": "Metallic",
	"normal": "Normal",
	"height": "Height",
	"emission": "Emission",
	"alpha": "Alpha",
	"ao": "Ambient occlusion",
}
## Breadcrumbs longer than this keep the first and the last two parts.
const MAX_CRUMBS := 3


## Receives an editor thumbnail for a chip; kept alive as chip meta until it arrives.
class _Thumbnail:
	extends RefCounted
	var picture: TextureRect

	func _init(target: TextureRect) -> void:
		picture = target

	func done(_path: String, _preview: Texture2D, thumbnail: Texture2D, _data: Variant) -> void:
		if thumbnail != null and is_instance_valid(picture):
			picture.set_meta("_joinery_glyph", "")
			picture.set_meta("_joinery_role", "")
			picture.texture = thumbnail
			picture.self_modulate = Color.WHITE


## The chip for [param target] ({id, label, kind, resource, source, crumbs?, tool?}).
## [param opened] gets the asset path on click; [param removed] runs as soon as × is pressed
## (the chip then animates out and emits its own removed signal).
static func build(
	_host: Control, target: Dictionary, opened: Callable, removed: Callable
) -> PanelContainer:
	var is_part := not str(target.get("part_kind", "")).is_empty()
	# A scene node leads with its own name: the scene's name first made every node of one
	# scene read the same once trimmed. The whole path is in the tooltip.
	var trail := crumbs(target)
	var full := " › ".join(trail)
	var text := str(target.get("label", "")) if is_part else full
	if not is_part and trail.size() > 1:
		text = trail[-1]
	var chip := UI.chip(text, "", true)
	chip.label.tooltip_text = ""
	var resource := str(target.get("resource", "")).split("#")[0]
	var path := resource if not resource.is_empty() else str(target.get("source", ""))
	var tool := str(target.get("tool", ""))
	chip.tooltip_text = (
		str(target.get("tooltip", part_tooltip(target)))
		if is_part
		else (
			("Open in " + tool if not tool.is_empty() else "Open source file")
			if not path.is_empty()
			else full
		)
	)
	if not is_part and full != text and path.is_empty():
		chip.tooltip_text = full
	chip.accessibility_name = text
	chip.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	chip.remove.tooltip_text = "Remove from this message"
	chip.remove.accessibility_name = "Remove %s from this message" % text
	chip.remove.pressed.connect(removed)
	var picture := STYLE.glyph("text_muted")
	var icon_name := str(KIND_ICONS.get(str(target.get("kind", "")), ""))
	if not icon_name.is_empty():
		STYLE.set_glyph(picture, icon_name)
		var row := chip.get_child(0)
		row.add_child(picture)
		row.move_child(picture, 0)
		_request_thumbnail(chip, picture, path)
	chip.gui_input.connect(
		func(event: InputEvent) -> void:
			var click := event as InputEventMouseButton
			if (
				click != null
				and click.pressed
				and click.button_index == MOUSE_BUTTON_LEFT
				and not path.is_empty()
			):
				opened.call(path)
	)
	chip.mouse_entered.connect(_hover.bind(chip, true))
	chip.mouse_exited.connect(_hover.bind(chip, false))
	return chip


## Targets for the nodes selected in the editor, with breadcrumbs from the scene file name
## down the node path. Editor only.
static func from_selection() -> Array[Dictionary]:
	var targets: Array[Dictionary] = []
	var scene := EditorInterface.get_edited_scene_root()
	for node: Node in EditorInterface.get_selection().get_selected_nodes():
		var scene_path := scene.scene_file_path if scene != null else "current-scene"
		var node_path := str(scene.get_path_to(node)) if scene != null else str(node.get_path())
		var kind := "Object"
		var resource := ""
		if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
			kind = "Audio"
			var stream := node.get("stream") as AudioStream
			if stream != null:
				resource = stream.resource_path
		var parts: Array[String] = []
		if scene != null:
			var file := scene.scene_file_path.get_file().get_basename()
			parts.append(file if not file.is_empty() else str(scene.name))
			if node != scene:
				for part: String in node_path.split("/"):
					parts.append(part)
		else:
			parts.append(str(node.name))
		var target := {
			"id": scene_path + "::" + node_path,
			"label": str(node.name),
			"kind": kind,
			"resource": resource,
			"source": node.scene_file_path,
			"crumbs": parts,
		}
		targets.append(target)
	return targets


## The target for a file (or a "file::sub-resource" path) of editor type [param type].
static func for_resource(path: String, type := "Resource", source := "") -> Dictionary:
	var file := path.split("::")[0]
	var extension := file.get_extension().to_lower()
	var kind := "Resource"
	if type.begins_with("Audio") or extension in ["wav", "ogg", "mp3", "scd"]:
		kind = "Audio"
	elif extension in ["tscn", "scn"]:
		kind = "Scene"
	elif type in ["Object", "PackedScene"] or extension in ["blend", "glb", "gltf"]:
		kind = "Object"
	elif type.begins_with("Texture") or extension in ["png", "jpg", "kra", "aseprite"]:
		kind = "Image"
	elif type.contains("Material") or extension in ["ptex", "mmg"]:
		kind = "Material"
	return {
		"id": path,
		"label": file.get_file().get_basename().replace("_", " ").capitalize(),
		"kind": kind,
		"resource": path,
		"source": source if not source.is_empty() else path,
	}


## The target for a part of a model ({part_kind, resource, object, material, image, role,
## roles, used_by, owner, owner_source, ...} from `joinery parts` or a viewport pick), with a
## leaf-first label: "Roughness · Steel" (a map, picked in the parts list), "Roughness · Head"
## (picked on Head), "Steel · Head", "Head · Hammer". The tooltip has the breadcrumb, which
## objects share the material, and where the part is edited.
static func for_part(part: Dictionary) -> Dictionary:
	var target := {}
	for key: Variant in part:
		target[str(key)] = str(part[key])
	var kind := str(target.get("part_kind", "object"))
	target["kind"] = PART_KINDS.get(kind, "Object")
	if str(target.get("ops", "")).is_empty():
		target["ops"] = str(PART_OPS.get(kind, ""))
	target["label"] = part_label(target)
	target["tooltip"] = part_tooltip(target)
	if str(target.get("id", "")).is_empty():
		target["id"] = str(target.get("resource", "")) + "#" + target["label"]
	if not str(target.get("owner", "")).is_empty():
		target["tool"] = str(target["owner"]).capitalize()
	return target


## "Hammer" for res://art/models/hammer.glb.
static func _model_name(target: Dictionary) -> String:
	var file := str(target.get("resource", "")).split("#")[0].get_file().get_basename()
	return file.replace("_", " ").replace("-", " ").capitalize()


## "Roughness", "Roughness and metallic" (a packed map), or the image name.
static func _map_name(target: Dictionary) -> String:
	var names := PackedStringArray()
	var roles := str(target.get("roles", "")).split(",", false)
	if roles.is_empty() and not str(target.get("role", "")).is_empty():
		roles = PackedStringArray([str(target["role"])])
	for role: String in roles:
		var name := str(ROLE_NAMES.get(role, role.replace("_", " ").capitalize()))
		if not names.has(name):
			names.append(name if names.is_empty() else name.to_lower())
	return " and ".join(names) if not names.is_empty() else str(target.get("image", ""))


static func part_label(target: Dictionary) -> String:
	var kind := str(target.get("part_kind", ""))
	var object_name := str(target.get("object", ""))
	var material := str(target.get("material", ""))
	var context := object_name if not object_name.is_empty() else _model_name(target)
	match kind:
		"texture":
			return (
				"%s · %s"
				% [_map_name(target), object_name if not object_name.is_empty() else material]
			)
		"material":
			return "%s · %s" % [material, context]
		"bone":
			return "%s · %s" % [target.get("bone", ""), context]
		"shape_key":
			return "%s · %s" % [target.get("shape_key", ""), context]
	return "%s · %s" % [object_name, _model_name(target)]


static func part_tooltip(target: Dictionary) -> String:
	var path := PackedStringArray([_model_name(target)])
	for key: String in ["object", "material"]:
		if not str(target.get(key, "")).is_empty():
			path.append(str(target[key]))
	if str(target.get("part_kind", "")) == "texture":
		path.append(_map_name(target))
	for key: String in ["bone", "shape_key"]:
		if not str(target.get(key, "")).is_empty():
			path.append(str(target[key]))
	var lines := PackedStringArray([" › ".join(path)])
	var users := str(target.get("used_by", "")).split(",", false)
	if users.size() > 1:
		lines.append("Used by " + ", ".join(users))
	var owner := str(target.get("owner", ""))
	if not owner.is_empty():
		lines.append("In %s: %s" % [owner.capitalize(), target.get("owner_source", "")])
	return "\n".join(lines)


## Breadcrumb parts: the given "crumbs", or the file name and any sub-resource after "::".
static func crumbs(target: Dictionary) -> PackedStringArray:
	var parts := PackedStringArray()
	for part: Variant in target.get("crumbs", []):
		if not str(part).is_empty():
			parts.append(str(part))
	if parts.is_empty():
		var resource := str(target.get("resource", ""))
		if resource.is_empty():
			parts.append(str(target.get("label", "")))
		else:
			var pieces := resource.split("::")
			parts.append(pieces[0].get_file())
			if pieces.size() > 1 and not pieces[1].is_empty():
				parts.append(pieces[1])
	if parts.size() > MAX_CRUMBS:
		var short := PackedStringArray([parts[0], "…"])
		short.append_array(parts.slice(parts.size() - 2))
		return short
	return parts


static func _hover(chip: PanelContainer, inside: bool) -> void:
	chip.remove_theme_stylebox_override("panel")
	if not inside:
		return
	var source := chip.get_theme_stylebox("panel", "JoineryChip")
	if source is StyleBoxFlat:
		var box := source.duplicate() as StyleBoxFlat
		box.bg_color = chip.get_theme_color("surface_raised", "Joinery")
		chip.add_theme_stylebox_override("panel", box)


static func _request_thumbnail(chip: Control, picture: TextureRect, path: String) -> void:
	if path.is_empty() or not Engine.is_editor_hint() or not path.begins_with("res://"):
		return
	if not ResourceLoader.exists(path.split("::")[0]):
		return
	var receiver := _Thumbnail.new(picture)
	chip.set_meta("_joinery_thumbnail", receiver)
	var previewer := EditorInterface.get_resource_previewer()
	previewer.queue_resource_preview(path.split("::")[0], receiver, "done", null)
