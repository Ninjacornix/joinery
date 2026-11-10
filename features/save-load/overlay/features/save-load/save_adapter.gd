extends RefCounted
## What one blueprint adds to save slots. A blueprint's adapter lives at
## res://features/save-load/blueprint_adapter.gd, extends this script and
## overrides what it needs; `SaveGame` loads it when it exists. Every method
## is optional and the defaults change nothing.


## One line for the slot list, already translated (for example
## "Coins: 3 of 5"). `nodes` is the decoded `{save_id: state}` of the slot.
func summary(_nodes: Dictionary) -> String:
	return ""


## Connects the blueprint's own autosave moments. Call `request` (no
## arguments) whenever the game is worth autosaving; requests are throttled
## to one per `save/autosave_min_interval` seconds. Checkpoints already
## autosave without an adapter.
func watch(_request: Callable) -> void:
	pass


## Upgrade steps for saves made by older versions of this blueprint:
## `{from_version: Callable}`, see `SaveGame.add_migration`.
func migrations() -> Dictionary:
	return {}


## Counts states in `nodes` whose save id starts with `prefix` and whose
## `key` is true. Returns [true count, all].
static func count_flag(nodes: Dictionary, prefix: String, key: String) -> Array[int]:
	var found: int = 0
	var all: int = 0
	for id: String in nodes:
		if not id.begins_with(prefix) or not (nodes[id] is Dictionary):
			continue
		all += 1
		if bool((nodes[id] as Dictionary).get(key, false)):
			found += 1
	return [found, all]
