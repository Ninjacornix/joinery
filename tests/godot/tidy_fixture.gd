extends RefCounted
## A `joinery tidy --json` report for the tidy card's tests and screenshots: a project with
## an old model set, textures, a sound, an input action, a hidden node and two levels a
## script may still load by a path it builds.

const MB := 1 << 20
const MAYBE := (
	"Nothing names res://game/levels/level_%s.tscn, but game/main.gd builds paths like "
	+ "res://game/levels/level_….tscn, which could load it."
)


static func _finding(
	id: String, kind: String, title: String, size: int, why: String, extra := {}
) -> Dictionary:
	var item := {
		"id": id,
		"key": extra.get("key", "art/" + title),
		"kind": kind,
		"path": extra.get("path", "art/" + title),
		"subject": extra.get("subject", ""),
		"title": title,
		"confidence": extra.get("confidence", "unused"),
		"size": size,
		"why": why,
		"evidence": extra.get("evidence", ["Searched every scene, script, resource and setting."]),
		"goes_with": extra.get("goes_with", []),
		"edits": extra.get("edits", []),
	}
	return item


static func report() -> Dictionary:
	var nothing := "Nothing in the game loads it: no scene, script, resource or setting names it."
	var findings := [
		_finding(
			"a1",
			"model",
			"old_tower.glb",
			int(21.4 * MB),
			nothing,
			{
				"goes_with":
				[
					{
						"path": "art/models/old_tower.glb.import",
						"size": 900,
						"what": "import settings"
					},
					{"path": "art/models/old_tower_lod1.glb", "size": 6 * MB, "what": "LOD"},
					{
						"path": "art/textures/tower_albedo.png",
						"size": 4 * MB,
						"what": "only it uses it"
					},
					{
						"path": "art/sources/old_tower.blend",
						"size": 9 * MB,
						"what": "native source"
					},
					{"path": "recipes/old_tower.toml", "size": 700, "what": "recipe"},
				]
			}
		),
		_finding("a2", "model", "crate_v1.glb", int(3.2 * MB), nothing),
		_finding("a3", "texture", "grass_old.png", int(5.5 * MB), nothing),
		_finding(
			"a4",
			"texture",
			"sky_backup.exr",
			int(6.8 * MB),
			"Only game/old_sky.tres uses it, and nothing in the game uses that."
		),
		_finding("a5", "sound", "beep_test.wav", 310 * 1024, nothing),
		_finding(
			"a6",
			"input",
			"dash input action",
			0,
			"No script, scene or design file reads the dash action, so pressing its keys does nothing.",
			{
				"key": "input:dash",
				"path": "project.godot",
				"subject": "dash",
				"edits": ["project.godot: the dash action and its bindings"]
			}
		),
		_finding(
			"a7",
			"node",
			"Hidden MeshInstance3D OldSign in level.tscn",
			0,
			"OldSign is hidden and only draws; no script, animation or NodePath names it.",
			{
				"key": "game/level.tscn::OldSign",
				"path": "game/level.tscn",
				"subject": "OldSign",
				"edits": ["game/level.tscn: the OldSign node"]
			}
		),
		_finding(
			"m1",
			"scene",
			"level_7.tscn",
			12 * 1024,
			MAYBE % "7",
			{
				"confidence": "maybe",
				"path": "game/levels/level_7.tscn",
				"key": "game/levels/level_7.tscn",
				"evidence": ["game/main.gd:14 builds res://game/levels/level_….tscn"]
			}
		),
		_finding(
			"m2",
			"scene",
			"level_8.tscn",
			9 * 1024,
			MAYBE % "8",
			{
				"confidence": "maybe",
				"path": "game/levels/level_8.tscn",
				"key": "game/levels/level_8.tscn",
			}
		),
	]
	return {
		"project": "/game",
		"summary": "7 unused things · 38 MB",
		"count": 7,
		"size": int(38.2 * MB),
		"maybe": 2,
		"findings": findings,
		"kept": [],
		"used_by_tests": [],
		"read": {"files": 412, "scenes": 18, "scripts": 41, "reached": 300},
		"caches": [{"path": ".joinery/render_cache", "what": "review renders", "size": 120 * MB}],
		"cache_size": 124 * MB,
	}


static func clean() -> Dictionary:
	return {
		"project": "/game",
		"summary": "Nothing unused",
		"count": 0,
		"size": 0,
		"maybe": 0,
		"findings": [],
		"kept": [],
		"used_by_tests": [],
		"read": {"files": 212, "scenes": 9, "scripts": 25, "reached": 200},
		"caches": [],
		"cache_size": 0,
	}
