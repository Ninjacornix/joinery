extends RefCounted
## Writes a terrain's heightmap and mask from its recipe document, in Godot.
##
## Joinery's terrain tools write the document (`recipes/<id>.json`) and a three-line
## recipe script that calls `bake()`; `joinery rebuild` runs that script again with
## `godot --headless --script`, which writes the same files byte for byte.
## Prints one `JOINERY_TERRAIN {…}` line: the height range and any problems.

const OpsScript := preload("res://features/terrain/terrain_ops.gd")


## 0 when both files were written.
func bake(document_path: String) -> int:
	var text: String = FileAccess.get_file_as_string(document_path)
	var doc: Variant = JSON.parse_string(text)
	if not doc is Dictionary:
		printerr("Terrain document %s is not a JSON object." % document_path)
		return 1
	var outputs: Dictionary = (doc as Dictionary).get("outputs", {})
	var height_path: String = _res(str(outputs.get("height", "")))
	var mask_path: String = _res(str(outputs.get("mask", "")))
	if height_path == "res://" or mask_path == "res://":
		printerr("Terrain document %s names no outputs." % document_path)
		return 1
	var ops: OpsScript = OpsScript.new()
	var built: Dictionary = ops.run(doc)
	for folder: String in [height_path.get_base_dir(), mask_path.get_base_dir()]:
		DirAccess.make_dir_recursive_absolute(folder)
	var saved: Error = ops.normalised().save_png(height_path)
	if saved == OK:
		saved = ops.mask.save_png(mask_path)
	var span: Vector2 = ops.height.value_range()
	var report := {
		"ok": saved == OK,
		"low": snappedf(span.x, 0.001),
		"high": snappedf(span.y, 0.001),
		"resolution": ops.resolution,
		"problems": Array(built["problems"]),
	}
	print("JOINERY_TERRAIN " + JSON.stringify(report))
	return 0 if saved == OK else 1


static func _res(path: String) -> String:
	return path if path.begins_with("res://") else "res://" + path
