@tool
extends RefCounted
## The words the Review tab and the task cards share: app names, how long ago something was,
## the files a change touches, and what making it cost.

const UI := preload("ui_components.gd")

## App ids as the gateway records them -> the names people know.
const APP_NAMES := {
	"godot": "Godot",
	"blender": "Blender",
	"blender-ops": "Blender",
	"krita": "Krita",
	"gimp": "GIMP",
	"paint": "Krita",
	"materialmaker": "Material Maker",
	"material-maker": "Material Maker",
	"surge": "Surge XT",
	"surge-xt": "Surge XT",
	"sound": "Surge XT",
	"supercollider": "SuperCollider",
	"claude-collider": "SuperCollider",
	"sclang": "SuperCollider",
	"inkscape": "Inkscape",
	"registry": "Joinery",
	"joinery": "Joinery",
}


## Display names for recorded app ids, without repeats.
static func app_names(tools: Variant) -> Array[String]:
	var names: Array[String] = []
	if not tools is Array:
		return names
	for tool: Variant in tools:
		var id := str(tool)
		var app := str(APP_NAMES.get(id, id.replace("-", " ").capitalize()))
		if not app.is_empty() and not names.has(app):
			names.append(app)
	return names


## "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then the date ("12 Oct").
static func relative_time(stamp: float, now: float) -> String:
	if stamp <= 0.0:
		return ""
	var seconds := maxf(0.0, now - stamp)
	if seconds < 60.0:
		return "just now"
	if seconds < 3600.0:
		return "%d min ago" % int(seconds / 60.0)
	if seconds < 86400.0:
		return "%d h ago" % int(seconds / 3600.0)
	var days := int(seconds / 86400.0)
	if days == 1:
		return "yesterday"
	if days < 7:
		return "%d days ago" % days
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var date := Time.get_date_dict_from_unix_time(int(stamp) + bias)
	var months := [
		"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
	]
	return "%d %s" % [int(date["day"]), months[int(date["month"]) - 1]]


## The files [param entry] (a `joinery transactions list` row) touches, one per line
## ("+ art/models/hammer.glb"), "and 3 more" past the listed ones.
static func files_text(entry: Dictionary) -> String:
	var lines: Array[String] = []
	var marks := {"created": "+", "deleted": "−", "modified": "~"}
	for file: Variant in entry.get("files", []):
		if file is Dictionary:
			lines.append(
				"%s %s" % [marks.get(str(file.get("change", "")), "~"), str(file.get("path", ""))]
			)
	var more := int(entry.get("file_count", lines.size())) - lines.size()
	if more > 0:
		lines.append("and %d more" % more)
	if lines.is_empty():
		lines.append("No file list for this change.")
	return "\n".join(lines)


## What making a change cost, from the `cost` of a `joinery transactions list` row ({total_ms,
## tokens, rounds}, or null when it was not measured): "2 min · 18k tokens · 1 round". Tokens
## are left out when the agent did not report them, never shown as 0. "" when there is nothing.
static func cost_text(cost: Variant) -> String:
	if not cost is Dictionary:
		return ""
	var parts: Array[String] = []
	var ms: Variant = (cost as Dictionary).get("total_ms")
	if (ms is int or ms is float) and float(ms) > 0.0:
		parts.append(duration_text(float(ms) / 1000.0))
	var tokens: Variant = (cost as Dictionary).get("tokens")
	if (tokens is int or tokens is float) and float(tokens) > 0.0:
		parts.append(tokens_text(int(tokens)) + " tokens")
	var rounds: Variant = (cost as Dictionary).get("rounds")
	if (rounds is int or rounds is float) and int(rounds) > 0:
		parts.append("%d round%s" % [int(rounds), "" if int(rounds) == 1 else "s"])
	return " · ".join(parts)


## [param title] (a decided change's summary) with what making it cost quietly under it
## ("2 min · 18k tokens · 1 round"), or [param title] alone when it was not measured.
static func with_cost(title: Label, entry: Dictionary) -> Control:
	var text := cost_text(entry.get("cost"))
	if text.is_empty():
		return title
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryFlush"
	column.size_flags_horizontal = title.size_flags_horizontal
	column.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	column.add_child(title)
	var cost := UI.label(text, "JoineryNumeric")
	cost.name = "Cost"
	cost.tooltip_text = "What making this change took: time, tokens and agent rounds"
	cost.mouse_filter = Control.MOUSE_FILTER_PASS
	column.add_child(cost)
	return column


## "40 s", "2 min", "1 h 5 min" for [param seconds].
static func duration_text(seconds: float) -> String:
	if seconds < 60.0:
		return "%d s" % maxi(1, roundi(seconds))
	var minutes := roundi(seconds / 60.0)
	if minutes < 60:
		return "%d min" % minutes
	var rest := minutes % 60
	return "%d h" % (minutes / 60) + (" %d min" % rest if rest > 0 else "")


## "800", "1.5k", "18k", "1.2M" for a token [param count].
static func tokens_text(count: int) -> String:
	if count < 1000:
		return str(count)
	if count < 10000:
		return ("%.1fk" % (count / 1000.0)).replace(".0k", "k")
	if count < 1000000:
		return "%dk" % roundi(count / 1000.0)
	return ("%.1fM" % (count / 1000000.0)).replace(".0M", "M")
