extends SceneTree
## Joinery's playtest bot: plays a game headless for a while and writes what it saw.
##
## Run (joinery/playtest.py does): godot --headless --path <game> --fixed-fps 60
##   --script <this file> -- --playtest-out=<dir> --seed=<n> --seconds=<n>
##   [--scene=res://…] [--softlock=<seconds>] [--focus=word,word] [--shots=f,f,…]
##   [--shot-width=<px>]
##   [--route=<file>] [--probe=perf|access] [--luma] [--reach=word,word]
##   --settings-path=<file>
##
## It loads the game's main scene (or --scene), starts the level the way the game core's
## own tests do, and plays it through the input map (bot_drive.gd) while bot_watch.gd and
## bot_logger.gd watch. Where the game has them it also checks pause and resume, save and
## load (on the result screen too, once a level ends), and text that does not fit in the
## menus in every language. Every second it prints a `PLAYTEST {…}` line, and it writes
## <out>/bot.json every few seconds and at the end, so a crash still leaves what it saw.
## With --shots it saves a picture at those frames instead (a windowed replay of the same
## seed), SHOT_WIDTH wide unless --shot-width says otherwise (0 keeps the full
## frame, for store screenshots). With --route it only walks the player along a
## blockout's routes (bot_route.gd) and reports which targets it reached. With
## --probe it also measures the game for
## `joinery perf` or `joinery access` (bot_probe.gd), its numbers under "probe" in bot.json.
## It maps the level as it plays (bot_cover.gd) and explores toward the parts it hasn't seen,
## reporting the share it reached under "coverage" and a map of it as <out>/coverage.png.
## With --reach it heads for the nodes those words name first, results under "reach".
##
## The bot never writes inside the project: everything goes to --playtest-out, and the
## game's user:// is wherever the caller's HOME points.

const SCRIPTS := [
	"bot_logger.gd", "bot_watch.gd", "bot_drive.gd", "bot_route.gd", "bot_probe.gd", "bot_cover.gd"
]
const SAVE_SLOT := 2
const SHOT_WIDTH := 480
const LIMIT_EVENTS := 300
## Nodes that report their own state to the playtester (see _states()).
const STATE_GROUP := &"playtest_state"
const STATES_MAX := 20

var out_dir: String = ""
var seed_value: int = 1
var seconds: float = 60.0
var scene_path: String = ""
var softlock_seconds: float = 15.0
var shots: Dictionary = {}
## Width of a saved picture in pixels; 0 keeps the frame as rendered.
var shot_width: int = SHOT_WIDTH
var focus: PackedStringArray = PackedStringArray()
## Places to reach (--reach): words naming nodes.
var reach: PackedStringArray = PackedStringArray()
## A blockout's routes to walk (--route), and the walker once it runs.
var route: Dictionary = {}
var walker: RefCounted
## --probe=perf|access and --luma: what bot_probe.gd measures, and the probe once it runs.
var probe_kind: String = ""
var luma: bool = false
var probe: RefCounted
var fps: int = 60
var frame: int = 0
var rng: RandomNumberGenerator = RandomNumberGenerator.new()
var logger: Object
var watch: RefCounted
var drive: RefCounted
var cover: RefCounted
var game: Node
var events_node: Node
var menus: Node
var save_game: Node
var events: Array[Dictionary] = []
var findings: Array[Dictionary] = []
var moments: Array[Dictionary] = []
## Where the player was when each level was lost: {t, frame, at, reason} (the heatmaps).
var deaths: Array[Dictionary] = []
var goals: Dictionary = {
	"levels_started": 0,
	"completed": [],
	"failed": [],
	"checkpoints": 0,
	"score_changes": 0,
	"last_score": null,
	"world_changes": 0,
}
var checks: Dictionary = {}
var player_kind: String = "none"
var _player: Node = null
var _scene: Node = null
var _busy: bool = false
var _level_over: int = -1
var _finished: bool = false
var _menu_since: int = -1


func _initialize() -> void:
	_parse_args()
	var base := (get_script() as Script).resource_path.get_base_dir()
	logger = (load(base.path_join(SCRIPTS[0])) as GDScript).new()
	OS.add_logger(logger as Logger)
	fps = Engine.physics_ticks_per_second
	rng.seed = seed_value
	seed(seed_value)
	watch = (load(base.path_join(SCRIPTS[1])) as GDScript).new(fps, softlock_seconds)
	drive = (load(base.path_join(SCRIPTS[2])) as GDScript).new(self, rng)
	drive.set(&"focus", focus)
	drive.set(&"reach", reach)
	cover = (load(base.path_join(SCRIPTS[5])) as GDScript).new(self)
	drive.set(&"cover", cover)
	if not route.is_empty():
		walker = (load(base.path_join(SCRIPTS[3])) as GDScript).new(self, route, fps)
	process_frame.connect(func() -> void: watch.call(&"tick_process"))
	if not probe_kind.is_empty() or luma:
		var probe_script := load(base.path_join(SCRIPTS[4])) as GDScript
		probe = probe_script.new(self, probe_kind, luma, int(seconds * fps))
		process_frame.connect(func() -> void: probe.call(&"drawn", frame))
	_run.call_deferred()


func _run() -> void:
	if DisplayServer.get_name() == "headless":
		# Headless has no window, so the root viewport is 64 px square and every menu
		# would "run off the screen". Lay out at the game's own window size.
		root.size = Vector2i(
			int(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
			int(ProjectSettings.get_setting("display/window/size/viewport_height", 648))
		)
	game = root.get_node_or_null(^"Game")
	events_node = root.get_node_or_null(^"GameEvents")
	menus = root.get_node_or_null(^"Menus")
	save_game = root.get_node_or_null(^"SaveGame")
	_connect_events()
	if not _boot():
		_finish()
		return
	var total := int(seconds * fps)
	if walker != null:
		await _walk(total)
		_finish()
		return
	var check_at := {
		maxi(int(total * 0.3), fps * 6): _check_pause,
		maxi(int(total * 0.55), fps * 9): _check_save_load,
	}
	while frame < total and not _finished:
		await _tick(true)
		if _busy:
			continue
		if _level_over >= 0 and frame - _level_over == fps and not checks.has("save_after_end"):
			_busy = true
			await _check_save_after_end()
			_busy = false
			continue
		if not check_at.has(frame):
			continue
		var check: Callable = check_at[frame]
		if _level_over >= 0 or paused:
			# These compare a running level: check once the next one is under way.
			check_at[frame + fps * 5] = check
			continue
		_busy = true
		await check.call()
		_busy = false
	if probe != null:
		_busy = true
		drive.call(&"release_all")
		await probe.call(&"finish")
	_finish()


## One physics frame: play (when [param play]), watch, report.
func _tick(play: bool) -> void:
	await physics_frame
	frame += 1
	logger.set(&"frame", frame)
	if probe != null:
		probe.call(&"tick", frame, paused, menus != null and bool(menus.call(&"is_open")))
	if current_scene != _scene:
		_scene = current_scene
		watch.call(&"scene_changed", frame)
	var player := _find_player()
	if player != _player:
		_player = player
		player_kind = _kind(player)
		drive.call(&"follow", player)
		watch.call(&"follow", player, frame)
	if play:
		if not _busy:
			_handle_menus()
		drive.call(&"step", frame)
	var found: Array = watch.call(
		&"observe", frame, drive.call(&"moving"), paused, _state(), drive.get(&"interactive")
	)
	for item: Dictionary in found:
		_finding(item)
		if item["kind"] == "softlock" and walker == null:
			await _recover()
	if shots.has(frame):
		_shot(str(shots[frame]))
	if frame % fps == 0:
		print("PLAYTEST " + JSON.stringify({"t": frame / fps, "frame": frame}))
	if frame % (fps * 5) == 0:
		_write(false)


## Walk the blockout's routes until every target is reached or missed (or time is up).
func _walk(total: int) -> void:
	while frame < total and not bool(walker.get(&"done")):
		walker.call(&"step", frame, _player)
		await _tick(false)
	walker.call(&"step", frame, null)


func _boot() -> bool:
	var main := str(ProjectSettings.get_setting("application/run/main_scene", ""))
	var path := scene_path if not scene_path.is_empty() else main
	if path.is_empty() or not ResourceLoader.exists(path):
		_finding({"kind": "boot", "detail": "The game has no main scene to play.", "frame": 0})
		return false
	if game != null and scene_path.is_empty():
		game.set(&"autostart", true)
	if probe != null:
		probe.call(&"boot_started")
	if change_scene_to_file(path) != OK:
		_finding({"kind": "boot", "detail": "The scene %s did not load." % path, "frame": 0})
		return false
	_moment("start", fps * 2)
	return true


func _connect_events() -> void:
	if events_node == null:
		return
	var hooks := {
		&"level_started": _on_level_started,
		&"level_completed": _on_level_completed,
		&"level_failed": _on_level_failed,
		&"checkpoint_reached": _on_event.bind("checkpoint"),
		&"score_changed": _on_score,
		&"world_changed": _on_event.bind("world_changed"),
		&"menu_opened": _on_event.bind("menu_opened"),
	}
	for name: StringName in hooks:
		if events_node.has_signal(name):
			events_node.connect(name, hooks[name])


func _on_level_started(_level: Node) -> void:
	goals["levels_started"] = int(goals["levels_started"]) + 1
	_level_over = -1
	_event("level_started", {})
	watch.call(&"scene_changed", frame)


func _on_level_completed(result: Dictionary) -> void:
	var time := float(result.get("time", game.call(&"level_time") if game != null else 0.0))
	(goals["completed"] as Array).append(
		{"t": snappedf(frame / float(fps), 0.01), "level_time": snappedf(time, 0.01)}
	)
	_event("level_completed", {"level_time": snappedf(time, 0.01)})
	_moment("level completed", frame + fps)
	_level_over = frame


func _on_level_failed(reason: String) -> void:
	(goals["failed"] as Array).append({"t": snappedf(frame / float(fps), 0.01), "reason": reason})
	if _player != null and is_instance_valid(_player) and deaths.size() < LIMIT_EVENTS:
		(
			deaths
			. append(
				{
					"t": snappedf(frame / float(fps), 0.01),
					"frame": frame,
					"at": _round(_where(_player)),
					"reason": reason,
				}
			)
		)
	_event("level_failed", {"reason": reason})
	_level_over = frame


func _on_score(value: int) -> void:
	goals["score_changes"] = int(goals["score_changes"]) + 1
	goals["last_score"] = value
	_event("score", {"value": value})


func _on_event(_arg: Variant, kind: String) -> void:
	if kind == "checkpoint":
		goals["checkpoints"] = int(goals["checkpoints"]) + 1
	elif kind == "world_changed":
		goals["world_changes"] = int(goals["world_changes"]) + 1
	_event(kind, {})


func _event(kind: String, data: Dictionary) -> void:
	watch.call(&"progress", frame)
	if events.size() < LIMIT_EVENTS:
		var entry := {"type": kind, "frame": frame}
		entry.merge(data)
		events.append(entry)


func _finding(item: Dictionary) -> void:
	var entry := item.duplicate()
	entry["t"] = snappedf(int(item.get("frame", frame)) / float(fps), 0.01)
	if _player != null and is_instance_valid(_player):
		entry["at"] = _round(_where(_player))
	findings.append(entry)
	_moment(str(item["kind"]), int(item.get("frame", frame)))


func _moment(label: String, at_frame: int) -> void:
	if moments.size() < 12:
		moments.append({"label": label, "frame": at_frame})


## Menus the bot didn't open: start from the main menu, close a stray pause menu, and
## carry on after the level ends (a result panel's button, else a restart).
func _handle_menus() -> void:
	var open := menus != null and bool(menus.call(&"is_open"))
	if not open:
		_menu_since = -1
	elif _menu_since < 0:
		_menu_since = frame
	if open and frame - _menu_since == fps:
		var which := str(menus.call(&"current_menu"))
		if which == "main" and game != null:
			_act(game, &"start_game", [game.get(&"options")])
		else:
			_act(menus, &"close_all")
	if _level_over >= 0 and frame - _level_over == fps * 4 and game != null:
		checks["continue_after_level"] = "restart"
		_act(game, &"restart_level")
		_level_over = -1


## A softlock was found: get the game going again so the run can go on.
func _recover() -> void:
	drive.call(&"release_all")
	if menus != null and bool(menus.call(&"is_open")):
		_act(menus, &"close_all")
	paused = false
	if game != null:
		_act(game, &"restart_level")
	elif current_scene != null:
		reload_current_scene()
	watch.call(&"recovered", frame)
	await _wait(fps)


## Pause, then resume, with the game's own pause action.
func _check_pause() -> void:
	if not InputMap.has_action(&"pause") or current_scene == null:
		checks["pause"] = "skipped: no pause action"
		return
	drive.call(&"release_all")
	drive.call(&"tap", &"pause", frame, 2)
	await _wait(20)
	var stopped := paused or (menus != null and bool(menus.call(&"is_open")))
	if not stopped:
		checks["pause"] = "failed: pause did nothing"
		_finding({"kind": "pause", "detail": "Pressing pause did nothing.", "frame": frame})
		return
	_moment("paused", frame)
	await _check_text()
	drive.call(&"tap", &"pause", frame, 2)
	await _wait(20)
	if paused:
		drive.call(&"tap", &"ui_cancel", frame, 2)
		await _wait(20)
	if paused:
		checks["pause"] = "failed: could not resume"
		_finding(
			{
				"kind": "softlock",
				"detail": "The pause menu would not close with pause or Esc.",
				"frame": frame,
			}
		)
		await _recover()
		return
	checks["pause"] = "ok"


## Save, play on, load: the game must come back exactly as it was saved (every node in
## group `saveable`, the player's place among them).
func _check_save_load() -> void:
	if save_game == null or not save_game.has_method(&"save_slot"):
		checks["save_load"] = "skipped: no save feature"
		return
	if game == null or game.call(&"current_level") == null:
		checks["save_load"] = "skipped: no level running"
		return
	drive.call(&"release_all")
	await _wait(2)
	var before: Dictionary = game.call(&"capture_state")
	var saved := int(_act(save_game, &"save_slot", [SAVE_SLOT]))
	if saved != OK:
		_save_problem("Saving failed: %s." % error_string(saved))
		return
	await _wait(fps * 2, true)
	drive.call(&"release_all")
	var started := int(goals["levels_started"])
	var loaded := int(_act(save_game, &"load_slot", [SAVE_SLOT]))
	if loaded != OK:
		_save_problem("Loading a fresh save failed: %s." % error_string(loaded))
		return
	for _i: int in fps * 5:
		if int(goals["levels_started"]) > started:
			break
		await _tick(false)
	await _wait(1)
	var after: Dictionary = game.call(&"capture_state")
	var differences: Array[String] = []
	for id: Variant in before:
		if not after.has(id):
			differences.append("%s is missing" % id)
		elif not _same(before[id], after[id]):
			differences.append("%s came back different (%s)" % [id, _diff(before[id], after[id])])
	if not differences.is_empty():
		_save_problem(
			"Loading a save didn't restore the game: %s." % ", ".join(differences.slice(0, 4))
		)
		return
	checks["save_load"] = "ok"


## On the result screen a level shows when it ends: a save made there (if the game takes
## one at all) must load into that result again or into a game the player can go on with.
func _check_save_after_end() -> void:
	if save_game == null or not save_game.has_method(&"save_slot"):
		return
	drive.call(&"release_all")
	var saved := int(_act(save_game, &"save_slot", [SAVE_SLOT]))
	if saved != OK:
		checks["save_after_end"] = "ok: refused"
		return
	var started := int(goals["levels_started"])
	var ends := _ends()
	var loaded := int(_act(save_game, &"load_slot", [SAVE_SLOT]))
	if loaded != OK:
		_save_after_end_problem(
			"A save made after the level ended doesn't load: %s." % error_string(loaded)
		)
		return
	for _i: int in fps * 5:
		if int(goals["levels_started"]) > started:
			break
		await _tick(false)
	await _wait(fps)
	if _ends() > ends or _can_play(_find_player()):
		checks["save_after_end"] = "ok: loads"
		return
	var stuck := "A save made after the level ended loads a level nobody can play: "
	_save_after_end_problem(stuck + "no result screen, and the player can't move.")
	await _recover()


func _save_after_end_problem(detail: String) -> void:
	checks["save_after_end"] = "failed"
	_finding({"kind": "save", "detail": detail, "frame": frame})


## Levels ended so far, won or lost.
func _ends() -> int:
	return (goals["completed"] as Array).size() + (goals["failed"] as Array).size()


## The player is up and taking input (true when the game doesn't say).
static func _can_play(player: Node) -> bool:
	if player == null:
		return true
	if "controls_enabled" in player and not bool(player.get(&"controls_enabled")):
		return false
	if player.has_method(&"is_alive") and not bool(player.call(&"is_alive")):
		return false
	return true


func _save_problem(detail: String) -> void:
	checks["save_load"] = "failed"
	_finding({"kind": "save", "detail": detail, "frame": frame})


## The first value that differs, as "key: saved -> loaded".
static func _diff(a: Variant, b: Variant, path: String = "") -> String:
	if a is Dictionary and b is Dictionary:
		for key: Variant in a:
			if not (b as Dictionary).has(key):
				return "%s%s missing" % [path, key]
			if not _same(a[key], b[key]):
				return _diff(a[key], b[key], "%s%s." % [path, key])
	return "%s %s -> %s" % [path.trim_suffix("."), str(a).left(40), str(b).left(40)]


## Saved and loaded values match: numbers and vectors within a little physics drift.
static func _same(a: Variant, b: Variant) -> bool:
	if a is Dictionary and b is Dictionary:
		if (a as Dictionary).size() != (b as Dictionary).size():
			return false
		for key: Variant in a:
			if not (b as Dictionary).has(key) or not _same(a[key], b[key]):
				return false
		return true
	if a is Array and b is Array:
		if (a as Array).size() != (b as Array).size():
			return false
		for index: int in (a as Array).size():
			if not _same(a[index], b[index]):
				return false
		return true
	if (a is float or a is int) and (b is float or b is int):
		return absf(float(a) - float(b)) <= maxf(0.6, absf(float(a)) * 0.02)
	if a is Vector3 and b is Vector3:
		return (a as Vector3).distance_to(b) <= 0.6
	if a is Vector2 and b is Vector2:
		return (a as Vector2).distance_to(b) <= 24.0
	if a is Transform3D and b is Transform3D:
		var x := a as Transform3D
		var y := b as Transform3D
		return _same(x.origin, y.origin) and _near_basis(x.basis, y.basis)
	if a is Transform2D and b is Transform2D:
		var x := a as Transform2D
		var y := b as Transform2D
		return _same(x.origin, y.origin) and absf(x.get_rotation() - y.get_rotation()) < 0.1
	return a == b


static func _near_basis(a: Basis, b: Basis) -> bool:
	return (a.x - b.x).length() < 0.1 and (a.y - b.y).length() < 0.1 and (a.z - b.z).length() < 0.1


## With the pause menu open: text that runs past its box, in every language the game has
## and in the pseudo-locale (30 % longer text).
func _check_text() -> void:
	var has_language := root.get_node_or_null(^"Language") != null
	var translations: Array = ProjectSettings.get_setting(
		"internationalization/locale/translations", []
	)
	if not has_language and translations.is_empty():
		checks["text"] = "skipped: no translations"
		return
	var start_locale := TranslationServer.get_locale()
	var locales: Array[String] = []
	for locale: String in TranslationServer.get_loaded_locales():
		if not locale in locales:
			locales.append(locale)
	var problems: Array[String] = []
	for locale: String in locales + ["pseudo"]:
		if locale == "pseudo":
			TranslationServer.set_locale(start_locale)
			TranslationServer.pseudolocalization_enabled = true
		else:
			TranslationServer.set_locale(locale)
		root.propagate_notification(NOTIFICATION_TRANSLATION_CHANGED)
		await _wait(3)
		for problem: String in _overflows(menus if menus != null else current_scene):
			var line := "%s: %s" % [locale, problem]
			if problems.size() < 10 and not line in problems:
				problems.append(line)
	TranslationServer.pseudolocalization_enabled = false
	TranslationServer.set_locale(start_locale)
	root.propagate_notification(NOTIFICATION_TRANSLATION_CHANGED)
	checks["text"] = "ok" if problems.is_empty() else "failed"
	for problem: String in problems:
		_finding({"kind": "text", "detail": problem, "frame": frame})


## Visible text that is cut off or runs outside the screen or its panel.
func _overflows(node: Node) -> Array[String]:
	var found: Array[String] = []
	if node == null:
		return found
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return found
	if node is Label or node is Button:
		var control := node as Control
		var text := str(control.get(&"text"))
		var rect := control.get_global_rect()
		var screen := control.get_viewport_rect()
		if not text.is_empty() and not screen.encloses(rect.grow(-1.0)):
			found.append('"%s" runs off the screen' % text.left(40))
		elif not text.is_empty() and _clipped(control, text):
			found.append('"%s" is cut off' % text.left(40))
	for child: Node in node.get_children():
		found.append_array(_overflows(child))
	return found


func _clipped(control: Control, text: String) -> bool:
	var clips := false
	if control is Label:
		var label := control as Label
		clips = label.clip_text or label.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING
		clips = clips and label.autowrap_mode == TextServer.AUTOWRAP_OFF
	elif control is Button:
		var button := control as Button
		clips = button.clip_text or button.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING
	if not clips:
		return false
	var font := control.get_theme_font(&"font")
	var size := control.get_theme_font_size(&"font_size")
	var shown := (
		control.tr(text)
		if control.auto_translate_mode != Node.AUTO_TRANSLATE_MODE_DISABLED
		else text
	)
	var width := font.get_string_size(shown, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
	return width > control.size.x + 1.0


## Calls [param method] on a core autoload. Through the game core's Replays where the
## game has it, so a recording of this run (`joinery playtest --record`) makes the same
## call when it is replayed.
func _act(target: Node, method: StringName, args: Array = []) -> Variant:
	var replays := root.get_node_or_null(^"Replays")
	if replays != null and replays.has_method(&"perform"):
		return replays.call(&"perform", target, method, args)
	return target.callv(method, args)


## Lets [param count] frames pass, watching (and playing, with [param play]).
func _wait(count: int, play: bool = false) -> void:
	for _i: int in count:
		await _tick(play)


func _find_player() -> Node:
	if game != null and game.has_method(&"local_player"):
		var local: Node = game.call(&"local_player")
		if local != null:
			return local
	for node: Node in get_nodes_in_group(&"player"):
		if node.is_inside_tree():
			return node
	return _first_body(current_scene)


func _first_body(node: Node) -> Node:
	if node == null:
		return null
	if node is CharacterBody3D or node is CharacterBody2D:
		return node
	for child: Node in node.get_children():
		var found := _first_body(child)
		if found != null:
			return found
	return null


## What the player could act on, as text: it changes whenever something does.
func _state() -> String:
	var focus_owner := root.gui_get_focus_owner()
	var parts := [
		str(current_scene.get_instance_id()) if current_scene != null else "",
		str(menus.call(&"current_menu")) if menus != null else "",
		str(focus_owner.get_instance_id()) if focus_owner != null else "",
		str(paused),
	]
	if _player == null and current_scene != null:
		parts.append(str(current_scene.get_child_count()))
	return "|".join(parts)


func _shot(name: String) -> void:
	var image := root.get_texture().get_image()
	if image == null or image.is_empty():
		return
	if shot_width > 0 and image.get_width() != shot_width:
		var height := int(image.get_height() * shot_width / float(maxi(image.get_width(), 1)))
		image.resize(shot_width, maxi(height, 1), Image.INTERPOLATE_BILINEAR)
	image.save_png(out_dir.path_join(name))


func _finish() -> void:
	if _finished:
		return
	_finished = true
	drive.call(&"release_all")
	_moment("end", frame)
	if not out_dir.is_empty() and shots.is_empty():
		cover.call(&"save_map", out_dir.path_join("coverage.png"))
	_write(true)
	print("PLAYTEST " + JSON.stringify({"t": frame / fps, "frame": frame, "done": true}))
	quit(0)


func _write(done: bool) -> void:
	if out_dir.is_empty():
		return
	var report := {
		"version": 1,
		"finished": done,
		"seed": seed_value,
		"seconds_planned": seconds,
		"seconds_played": snappedf(frame / float(fps), 0.01),
		"frames": frame,
		"fps": fps,
		"scene": scene_path,
		"core": game != null,
		"player": player_kind,
		"style": str(drive.get(&"style")),
		"goals": goals,
		"goals_reached": int(drive.get(&"goals_reached")),
		"interactive": bool(drive.get(&"interactive")),
		"inputs": drive.get(&"used"),
		"events": events,
		"findings": findings,
		"checks": checks,
		"moments": moments,
		"deaths": deaths,
		"route": walker.get(&"results") if walker != null else [],
		"coverage": cover.call(&"summary"),
		"reach": drive.get(&"reach_results"),
		"perf": watch.call(&"summary"),
		"probe": probe.call(&"result") if probe != null and done else {},
		"log": logger.call(&"entries"),
		"log_dropped": int(logger.get(&"dropped")),
		"states": _states(),
	}
	var file := FileAccess.open(out_dir.path_join("bot.json"), FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(report))
		file.close()


## What game nodes report about themselves: every node in the `playtest_state`
## group with a `playtest_state() -> Dictionary` (an enemy's behaviour tree, the
## open conversation), keyed by node path. At most STATES_MAX nodes.
func _states() -> Dictionary:
	var found := {}
	for node: Node in get_nodes_in_group(STATE_GROUP):
		if found.size() >= STATES_MAX:
			break
		if is_instance_valid(node) and node.has_method(&"playtest_state"):
			var state: Variant = node.call(&"playtest_state")
			if state is Dictionary:
				found[str(node.get_path())] = state
	return found


func _parse_args() -> void:
	for arg: String in OS.get_cmdline_user_args():
		var value := arg.get_slice("=", 1) if arg.contains("=") else ""
		if arg.begins_with("--playtest-out="):
			out_dir = value
		elif arg.begins_with("--seed="):
			seed_value = value.to_int()
		elif arg.begins_with("--seconds="):
			seconds = maxf(value.to_float(), 1.0)
		elif arg.begins_with("--scene="):
			scene_path = value
		elif arg.begins_with("--softlock="):
			softlock_seconds = maxf(value.to_float(), 2.0)
		elif arg.begins_with("--focus="):
			focus = value.to_lower().split(",", false)
		elif arg.begins_with("--reach="):
			reach = value.to_lower().split(",", false)
		elif arg.begins_with("--route="):
			var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(value))
			route = parsed if parsed is Dictionary else {}
		elif arg.begins_with("--probe="):
			probe_kind = value
		elif arg == "--luma":
			luma = true
		elif arg.begins_with("--shot-width="):
			shot_width = maxi(value.to_int(), 0)
		elif arg.begins_with("--shots="):
			for part: String in value.split(",", false):
				shots[part.to_int()] = "shot_%06d.png" % part.to_int()


func _kind(player: Node) -> String:
	if player is Node3D:
		return "3d"
	if player is Node2D:
		return "2d"
	return "none"


static func _where(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at := (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO


static func _round(at: Vector3) -> Array:
	return [snappedf(at.x, 0.01), snappedf(at.y, 0.01), snappedf(at.z, 0.01)]
