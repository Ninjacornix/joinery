@tool
extends RefCounted
## Reads the project's saved Journeyman chats: one JSON-lines file per chat in
## <project>/.joinery/chats/, written by the chat host (joinery/chats.py). The first line is
## the chat ({id, title, provider, profile, handle…}); every later line is one entry: user,
## agent, step, notice, change, turn or session. The sidebar only reads them; renaming goes
## through `joinery chats rename` so the host and the CLI never write at once.

## The folder under the project, the same as joinery/chats.py.
const FOLDER := ".joinery/chats"
const MONTHS := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
## Provider ids as the sidebar names them.
const PROVIDERS := {
	"codex": "Codex", "claude": "Claude", "gemini": "Gemini", "opencode": "opencode"
}


## The absolute chats folder of the open project (or of [param project], a path).
static func folder(project := "") -> String:
	var root := project if not project.is_empty() else ProjectSettings.globalize_path("res://")
	return root.path_join(FOLDER)


## Every saved chat, most recent first: {id, title, provider, updated (unix seconds)}. Only
## each file's first line is read; the host stamps it after every turn.
static func list(project := "") -> Array[Dictionary]:
	var chats: Array[Dictionary] = []
	var directory := folder(project)
	for file_name: String in DirAccess.get_files_at(directory):
		if not file_name.ends_with(".jsonl"):
			continue
		var path := directory.path_join(file_name)
		var file := FileAccess.open(path, FileAccess.READ)
		if file == null:
			continue
		var meta := _record(file.get_line())
		if str(meta.get("type", "")) != "chat":
			continue
		(
			chats
			. append(
				{
					"id": str(meta.get("id", file_name.get_basename())),
					"title": str(meta.get("title", "")),
					"provider": str(meta.get("provider", "")),
					"updated": _unix(str(meta.get("updated", "")), path),
				}
			)
		)
	chats.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool: return a["updated"] > b["updated"]
	)
	return chats


## A chat's first line and its entries ({chat, entries}), or {} when it is gone.
static func read(chat_id: String, project := "") -> Dictionary:
	if chat_id.is_empty() or not chat_id.is_valid_filename() or chat_id.contains(".."):
		return {}
	var path := folder(project).path_join(chat_id + ".jsonl")
	if not FileAccess.file_exists(path):
		return {}
	var lines := FileAccess.get_file_as_string(path).split("\n", false)
	if lines.is_empty():
		return {}
	var meta := _record(lines[0])
	if str(meta.get("type", "")) != "chat":
		return {}
	var entries: Array[Dictionary] = []
	for index: int in range(1, lines.size()):
		var entry := _record(lines[index])
		if not entry.is_empty():
			entries.append(entry)
	return {"chat": meta, "entries": entries}


## A provider id as the sidebar names it ("codex" -> "Codex").
static func provider_name(provider: String) -> String:
	return str(PROVIDERS.get(provider, provider.capitalize()))


## When a chat was last active, in words: "Just now", "5 min ago", "3 h ago", "Yesterday",
## "4 days ago", then the date.
static func when(unix: int, now := -1) -> String:
	var current := now if now >= 0 else int(Time.get_unix_time_from_system())
	var seconds := maxi(0, current - unix)
	if seconds < 60:
		return "Just now"
	if seconds < 3600:
		return "%d min ago" % floori(seconds / 60.0)
	if seconds < 86400:
		return "%d h ago" % floori(seconds / 3600.0)
	if seconds < 2 * 86400:
		return "Yesterday"
	if seconds < 7 * 86400:
		return "%d days ago" % floori(seconds / 86400.0)
	var date := Time.get_datetime_dict_from_unix_time(unix)
	return "%s %d, %d" % [MONTHS[int(date["month"]) - 1], int(date["day"]), int(date["year"])]


## When a chat was last active: its "updated" time (UTC, ISO 8601), else the file's.
static func _unix(iso: String, path: String) -> int:
	if iso.length() >= 19:
		var seconds := Time.get_unix_time_from_datetime_string(iso.substr(0, 19))
		if seconds > 0:
			return seconds
	return FileAccess.get_modified_time(path)


static func _record(line: String) -> Dictionary:
	var parser := JSON.new()
	if parser.parse(line) != OK or not parser.data is Dictionary:
		return {}
	return parser.data
