@tool
extends RefCounted
## The words the Review tab and the task cards share: app names and how long ago something was.

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
