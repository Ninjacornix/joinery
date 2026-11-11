extends RefCounted
## The save-load checks, run by save_load_test.gd once the autoloads exist.
##
## A blueprint adapter ships res://tests/features/save-load/steps.gd with
## `change(play) -> PackedStringArray` (changes saved state the way playing
## does and returns the save ids it changed) and, optionally,
## `prepare(level)` (called at every level start), `finish(play)` (ends the
## level the way playing does; without it `GameEvents.level_failed` is
## raised) and `result_shown(level) -> bool` (the level's result screen is
## up). Without one (the blank starting point) only the data registered
## through `SaveGame.register` is changed.

const STEPS := "res://tests/features/save-load/steps.gd"
const ADAPTER := "res://features/save-load/blueprint_adapter.gd"
const ClientSession := preload("res://tests/features/save-load/client_session.gd")
const EXTRA_ID := "save_load_test"
const EXPECT_FILE := "expect.json"
const TOLERANCE := 0.25
const SAVED_COUNTER := 7
const SAVED_MARKER := Vector2(1.5, -2.25)
const MIGRATED_COUNTER := 42

var tree: SceneTree
var play: PlayHarness
var steps: Variant = null
## Test data kept in the save's `extra` through `SaveGame.register`.
var counter: int = 0
var marker: Vector2 = Vector2.ZERO


func _init(scene_tree: SceneTree, harness: PlayHarness) -> void:
	tree = scene_tree
	play = harness


func run(phase: String) -> void:
	if ResourceLoader.exists(STEPS):
		var script: GDScript = load(STEPS) as GDScript
		play.expect(script != null, "the blueprint's save-load steps load")
		if script != null:
			steps = script.new()
	GameEvents.level_started.connect(_on_level_started)
	SaveGame.register(EXTRA_ID, _capture_extra, _restore_extra)
	match phase:
		"save":
			await _phase_save()
		"load":
			await _phase_load()
		_:
			await _phase_all()
	print("save-load dir: %s" % ProjectSettings.globalize_path(SaveGame.directory))


# --- phases --------------------------------------------------------------------

func _phase_all() -> void:
	_check_fresh()
	await play.boot()
	play.expect(Game.current_level() != null, "the level is running")
	if Game.current_level() == null:
		return
	await play.physics_frames(10)
	var baseline: Dictionary = Game.capture_state()
	var changed: PackedStringArray = await _change()
	var after: Dictionary = Game.capture_state()
	for id: String in changed:
		play.expect(
			after.has(id) and baseline.has(id) and not _same(baseline[id], after[id]),
			"playing changes the saved state of %s" % id
		)
	counter = SAVED_COUNTER
	marker = SAVED_MARKER
	await _check_save(changed, after)
	await _check_restart_and_load(after, changed)
	await _check_refused(changed)
	await _check_migration(changed)
	await _check_autosave()
	await _check_pages(changed)
	await _check_client()
	await _check_continue(changed)
	await _check_level_over()
	_check_delete_all()


## Changes the game and saves slot 1 into `--saves-dir`, with the expected
## states beside it for the `load` phase.
func _phase_save() -> void:
	await play.boot()
	var changed: PackedStringArray = await _change()
	var state: Dictionary = Game.capture_state()
	counter = SAVED_COUNTER
	marker = SAVED_MARKER
	play.expect(SaveGame.save_slot(1) == OK, "slot 1 saves")
	var nodes: Dictionary = {}
	for id: String in changed:
		nodes[id] = JSON.from_native(state.get(id))
	var expect: Dictionary = {"changed": Array(changed), "nodes": nodes}
	var file: FileAccess = FileAccess.open(SaveGame.directory.path_join(EXPECT_FILE), FileAccess.WRITE)
	play.expect(file != null, "the expected state is written")
	if file != null:
		file.store_string(JSON.stringify(expect))
		file.close()


## A new process: Continue from the main menu restores what `save` saved.
func _phase_load() -> void:
	var info: Dictionary = SaveGame.slot_info(1)
	play.expect(bool(info["exists"]) and not bool(info["damaged"]), "slot 1 survived the restart")
	var expect: Dictionary = _read_json(SaveGame.directory.path_join(EXPECT_FILE))
	play.expect(not expect.is_empty(), "the expected state is there")
	await play.boot(false)
	play.expect(Menus.current_menu() == &"main", "the main menu opens")
	play.expect(play.focused_text() == "Continue", "Continue is the first main menu entry")
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	await play.tap(&"ui_accept")
	var args: Array = await play.wait_watched(loaded, 300)
	play.expect(args == [1], "Continue loads slot 1")
	await play.physics_frames(2)
	var changed: PackedStringArray = PackedStringArray()
	for id: Variant in expect.get("changed", []):
		changed.append(str(id))
	_expect_restored(changed, _decode(expect.get("nodes", {})), "after a restart")
	_expect_extra(SAVED_COUNTER, "after a restart")


# --- checks ----------------------------------------------------------------------

func _check_fresh() -> void:
	play.expect(
		SaveGame.directory.begins_with("user://test_saves/"),
		"test runs save into a throwaway folder (got %s)" % SaveGame.directory
	)
	for slot: int in SaveGame.SLOTS:
		play.expect(not SaveGame.has_slot(slot), "slot %d starts empty" % slot)
	play.expect(SaveGame.latest_slot() == -1, "no slot to continue from")
	var info: Dictionary = SaveGame.slot_info(1)
	play.expect(not bool(info["exists"]), "an empty slot reports it does not exist")
	play.expect(str(info["reason"]) == SaveGame.REASON_EMPTY, "an empty slot says so")
	play.expect(_entry(&"main", &"load").get("order") == 15, "main menu has Load (15)")
	play.expect(_entry(&"main", &"continue").is_empty(), "no Continue without a save")
	play.expect(_entry(&"pause", &"save").get("order") == 30, "pause menu has Save (30)")
	play.expect(_entry(&"pause", &"load").get("order") == 40, "pause menu has Load (40)")
	play.expect(SaveGame.save_slot(1) == ERR_UNCONFIGURED, "nothing to save before a level runs")
	play.expect(
		SaveGame.save_slot(SaveGame.SLOTS) == ERR_PARAMETER_RANGE_ERROR, "slot numbers are checked"
	)
	play.expect(SaveGame.load_slot(-1) == ERR_PARAMETER_RANGE_ERROR, "negative slots are refused")


func _check_save(changed: PackedStringArray, after: Dictionary) -> void:
	var saved: Dictionary = play.watch(SaveGame.saved)
	play.expect(SaveGame.save_slot(1) == OK, "slot 1 saves")
	var args: Array = await play.wait_watched(saved, 5)
	play.expect(args == [1], "saved(1) fires")
	var path: String = SaveGame.slot_path(1)
	play.expect(FileAccess.file_exists(path), "slot_1.json is written")
	play.expect(not FileAccess.file_exists(path + ".tmp"), "no .tmp is left behind")
	var data: Dictionary = _read_json(path)
	play.expect(_number(data.get("format")) == 1.0, "format is 1")
	play.expect(_number(data.get("version")) == 1.0, "data version is 1")
	play.expect(
		data.get("game") == str(ProjectSettings.get_setting("application/config/name")),
		"the game name is saved"
	)
	play.expect(
		data.get("blueprint") == str(ProjectSettings.get_setting("game/blueprint")),
		"the starting point is saved"
	)
	play.expect(
		data.get("level") == Game.current_level().scene_file_path, "the running level is saved"
	)
	var now: float = Time.get_unix_time_from_system()
	play.expect(absf(_number(data.get("saved_at")) - now) < 60.0, "the save time is now")
	play.expect(_number(data.get("playtime")) > 0.0, "play time is saved")
	var nodes: Dictionary = _decode(data.get("nodes", {}))
	play.expect(not nodes.is_empty() or changed.is_empty(), "saveable nodes are saved")
	for id: String in changed:
		play.expect(
			nodes.has(id) and _same(nodes[id], after[id]), "the file holds the new state of %s" % id
		)
	var extra: Dictionary = _decode(data.get("extra", {}))
	var mine: Variant = extra.get(EXTRA_ID)
	play.expect(mine is Dictionary, "registered extra data is saved")
	if mine is Dictionary:
		play.expect(
			typeof((mine as Dictionary).get("marker")) == TYPE_VECTOR2,
			"vectors keep their type through JSON.from_native"
		)
	var info: Dictionary = SaveGame.slot_info(1)
	play.expect(bool(info["exists"]) and not bool(info["damaged"]), "slot 1 is loadable")
	play.expect(str(info["reason"]) == "", "a loadable slot has no reason")
	play.expect(float(info["playtime"]) > 0.0, "slot info shows play time")
	if ResourceLoader.exists(ADAPTER) and not changed.is_empty():
		play.expect(str(info["summary"]) != "", "the blueprint adapter sums up the slot")
	print("save-load summary: %s" % info["summary"])
	play.expect(SaveGame.latest_slot() == 1, "slot 1 is the latest")
	play.expect(not _entry(&"main", &"continue").is_empty(), "the main menu gains Continue")
	play.expect(_entry(&"main", &"continue").get("order") == 5, "Continue comes first (5)")
	# A crash while writing leaves a .tmp beside a good save: the good one wins.
	_write_text(path + ".tmp", "{half a save")
	play.expect(not bool(SaveGame.slot_info(1)["damaged"]), "a stray .tmp does not damage a slot")
	play.expect(SaveGame.save_slot(1) == OK, "saving over a stray .tmp works")
	play.expect(not FileAccess.file_exists(path + ".tmp"), "the stray .tmp is gone after saving")


func _check_restart_and_load(after: Dictionary, changed: PackedStringArray) -> void:
	var expected: Dictionary = _slot_nodes(1)
	counter = 0
	marker = Vector2.ZERO
	var started: Dictionary = play.watch(GameEvents.level_started)
	Game.restart_level()
	await play.wait_watched(started, 300)
	await play.physics_frames(2)
	var reset: Dictionary = Game.capture_state()
	for id: String in changed:
		play.expect(
			reset.has(id) and not _same(after[id], reset[id]), "restarting undoes %s" % id
		)
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	play.expect(SaveGame.load_slot(1) == OK, "slot 1 loads")
	var args: Array = await play.wait_watched(loaded, 300)
	play.expect(args == [1], "loaded(1) fires")
	await play.physics_frames(2)
	_expect_restored(changed, expected, "after loading slot 1")
	_expect_extra(SAVED_COUNTER, "after loading slot 1")
	play.expect(
		SaveGame.playtime() >= float(SaveGame.slot_info(1)["playtime"]),
		"play time carries on from the save"
	)


func _check_refused(changed: PackedStringArray) -> void:
	var good: Dictionary = _read_json(SaveGame.slot_path(1))
	var cases: Array[Array] = [
		["{not json", SaveGame.REASON_DAMAGED, "broken JSON"],
		["", SaveGame.REASON_DAMAGED, "an empty file"],
		["[1, 2]", SaveGame.REASON_DAMAGED, "a JSON list"],
		[JSON.stringify({"format": 1, "level": "x"}), SaveGame.REASON_DAMAGED, "missing fields"],
		[_variant(good, "format", 99), SaveGame.REASON_NEWER, "a newer format"],
		[_variant(good, "version", 7), SaveGame.REASON_NEWER, "a newer data version"],
		[_variant(good, "blueprint", "elsewhere"), SaveGame.REASON_OTHER_GAME, "another game"],
		[_variant(good, "nodes", "nope"), SaveGame.REASON_DAMAGED, "nodes that are not a map"],
	]
	for case: Array in cases:
		await _expect_refused(2, str(case[0]), str(case[1]), str(case[2]), changed)
	play.expect(SaveGame.latest_slot() == 1, "Continue skips a slot it cannot load")
	play.expect(SaveGame.delete_slot(2) == OK, "a damaged slot can be deleted")


func _expect_refused(
	slot: int, text: String, reason: String, what: String, changed: PackedStringArray
) -> void:
	_write_text(SaveGame.slot_path(slot), text)
	var info: Dictionary = SaveGame.slot_info(slot)
	play.expect(bool(info["exists"]) and bool(info["damaged"]), "%s is reported" % what)
	play.expect(
		str(info["reason"]) == reason, "%s: reason %s (got %s)" % [what, reason, info["reason"]]
	)
	var level: Node = Game.current_level()
	var before: Dictionary = Game.capture_state()
	var failed: Dictionary = play.watch(SaveGame.load_failed)
	var started: Dictionary = play.watch(GameEvents.level_started)
	play.expect(SaveGame.load_slot(slot) != OK, "%s is not loaded" % what)
	var args: Array = await play.wait_watched(failed, 5)
	play.expect(args == [slot, reason], "%s fires load_failed with the reason" % what)
	await play.physics_frames(3)
	_stop(started)
	play.expect(play.fire_count(started) == 0, "%s does not restart the level" % what)
	play.expect(Game.current_level() == level, "%s leaves the running level alone" % what)
	var now: Dictionary = Game.capture_state()
	for id: String in changed:
		play.expect(_same(before[id], now[id]), "%s leaves %s alone" % [what, id])


func _check_migration(changed: PackedStringArray) -> void:
	var expected: Dictionary = _slot_nodes(1)
	ProjectSettings.set_setting(SaveGame.VERSION_SETTING, 2)
	var info: Dictionary = SaveGame.slot_info(1)
	play.expect(
		bool(info["damaged"]) and str(info["reason"]) == SaveGame.REASON_TOO_OLD,
		"an old save without an upgrade step cannot be loaded"
	)
	var failed: Dictionary = play.watch(SaveGame.load_failed)
	play.expect(SaveGame.load_slot(1) != OK, "an old save is not loaded without a step")
	var args: Array = await play.wait_watched(failed, 5)
	play.expect(args == [1, SaveGame.REASON_TOO_OLD], "load_failed says it is too old")

	var refuse: Callable = func(_save: Dictionary) -> Dictionary: return {}
	SaveGame.add_migration(1, refuse)
	play.expect(not bool(SaveGame.slot_info(1)["damaged"]), "with a step the save looks loadable")
	failed = play.watch(SaveGame.load_failed)
	play.expect(SaveGame.load_slot(1) != OK, "a step that refuses stops the load")
	args = await play.wait_watched(failed, 5)
	play.expect(args == [1, SaveGame.REASON_TOO_OLD], "a refused upgrade is reported")

	var upgrade: Callable = func(save: Dictionary) -> Dictionary:
		var extra: Dictionary = save.get("extra", {})
		var mine: Dictionary = extra.get(EXTRA_ID, {})
		mine["counter"] = MIGRATED_COUNTER
		extra[EXTRA_ID] = mine
		save["extra"] = extra
		return save
	SaveGame.add_migration(1, upgrade)
	counter = 0
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	play.expect(SaveGame.load_slot(1) == OK, "an old save loads through the upgrade step")
	args = await play.wait_watched(loaded, 300)
	play.expect(args == [1], "loaded(1) fires after the upgrade")
	await play.physics_frames(2)
	_expect_extra(MIGRATED_COUNTER, "after the upgrade")
	_expect_restored(changed, expected, "after the upgrade")

	play.expect(SaveGame.save_slot(1) == OK, "saving after the upgrade works")
	play.expect(_number(_read_json(SaveGame.slot_path(1)).get("version")) == 2.0, "it saves version 2")
	SaveGame.remove_migration(1)
	ProjectSettings.set_setting(SaveGame.VERSION_SETTING, 1)
	info = SaveGame.slot_info(1)
	play.expect(
		bool(info["damaged"]) and str(info["reason"]) == SaveGame.REASON_NEWER,
		"a save from a newer data version is refused"
	)
	counter = SAVED_COUNTER
	play.expect(SaveGame.save_slot(1) == OK, "slot 1 saves again at version 1")


func _check_autosave() -> void:
	SaveGame.delete_slot(SaveGame.AUTOSAVE_SLOT)
	SaveGame.cancel_autosave()
	await play.physics_frames(3)
	var saved: Dictionary = play.watch(SaveGame.saved)
	GameEvents.checkpoint_reached.emit("save_load_test")
	var args: Array = await play.wait_watched(saved, 30)
	play.expect(args == [SaveGame.AUTOSAVE_SLOT], "a checkpoint autosaves into slot 0")
	play.expect(SaveGame.has_slot(SaveGame.AUTOSAVE_SLOT), "the autosave slot exists")
	play.expect(SaveGame.latest_slot() == SaveGame.AUTOSAVE_SLOT, "the autosave is the latest")
	play.expect(SaveGame.slot_name(SaveGame.AUTOSAVE_SLOT) == "Autosave", "slot 0 is the Autosave")
	var dir_files: PackedStringArray = DirAccess.get_files_at(SaveGame.directory)
	for file_name: String in dir_files:
		play.expect(not file_name.ends_with(".tmp"), "no .tmp after an autosave (%s)" % file_name)

	saved = play.watch(SaveGame.saved)
	SaveGame.request_autosave(true)
	await play.physics_frames(30)
	_stop(saved)
	play.expect(play.fire_count(saved) == 0, "a throttled autosave waits for the interval")
	play.expect(SaveGame.autosave_pending(), "the throttled autosave is still waiting")
	SaveGame.cancel_autosave()
	play.expect(not SaveGame.autosave_pending(), "a waiting autosave can be dropped")

	ProjectSettings.set_setting(SaveGame.AUTOSAVE_SETTING, false)
	saved = play.watch(SaveGame.saved)
	GameEvents.checkpoint_reached.emit("save_load_test")
	await play.physics_frames(10)
	_stop(saved)
	play.expect(play.fire_count(saved) == 0, "checkpoints do not autosave when switched off")
	ProjectSettings.set_setting(SaveGame.AUTOSAVE_SETTING, true)


func _check_pages(changed: PackedStringArray) -> void:
	_write_text(SaveGame.slot_path(2), "{damaged")
	var opened: Dictionary = play.watch(GameEvents.menu_opened)
	await play.tap(&"pause")
	await play.wait_watched(opened, 30)
	play.expect(Menus.current_menu() == &"pause", "pause opens the pause menu")
	play.expect(tree.paused, "the pause menu pauses the game")

	# Save over slot 1: the first press asks, the second saves.
	play.expect(await play.focus_button("Save"), "the pause menu reaches Save by keyboard")
	await play.tap(&"ui_accept")
	var page: Control = Menus.current_page()
	play.expect(page != null and page.name == &"SavePage", "Save opens the save page")
	if page == null or page.name != &"SavePage":
		Menus.close_all()
		return
	play.expect_min_text(page, "save page")
	play.expect(page.find_child("Slot0", true, false) == null, "the autosave cannot be saved over")
	var pick: Button = page.find_child("Slot1", true, false).get_node(^"Pick") as Button
	play.expect(play.focused() == pick, "slot 1 has focus")
	play.expect(page.find_child("Slot2", true, false) != null, "slot 2 is listed")
	var saved: Dictionary = play.watch(SaveGame.saved)
	await play.tap(&"ui_accept")
	play.expect(play.fire_count(saved) == 0, "the first press over a save only asks")
	play.expect(pick.text == "Press again to overwrite", "the button asks to press again")
	await play.tap(&"ui_accept")
	var args: Array = await play.wait_watched(saved, 10)
	play.expect(args == [1], "the second press saves slot 1")
	play.expect(Menus.current_menu() == &"pause", "saving returns to the pause menu")
	play.expect(tree.paused, "the game is still paused after saving")

	# Load: the damaged slot is shown but cannot be picked; delete it.
	play.expect(await play.focus_button("Load"), "the pause menu reaches Load by keyboard")
	await play.tap(&"ui_accept")
	page = Menus.current_page()
	play.expect(page != null and page.name == &"LoadPage", "Load opens the load page")
	if page == null or page.name != &"LoadPage":
		Menus.close_all()
		return
	play.expect_min_text(page, "load page")
	play.expect(page.find_child("Slot0", true, false) != null, "the autosave is listed")
	var row: Node = page.find_child("Slot2", true, false)
	var damaged: Button = row.get_node(^"Pick") as Button
	play.expect(damaged.disabled, "a damaged slot cannot be picked")
	var details: Label = row.find_child("Details", true, false) as Label
	play.expect(details.text == SaveGame.REASON_DAMAGED, "a damaged slot says why")
	var row_one: Node = page.find_child("Slot1", true, false)
	var summary: Label = row_one.find_child("Summary", true, false) as Label
	if ResourceLoader.exists(ADAPTER) and not changed.is_empty():
		play.expect(summary != null and summary.text != "", "slot 1 shows the adapter summary")
	var remove: Button = row.get_node(^"Delete") as Button
	remove.grab_focus()
	await play.tap(&"ui_accept")
	play.expect(SaveGame.has_slot(2), "the first Delete press only asks")
	play.expect(remove.text == "Press again to delete", "Delete asks to press again")
	await play.tap(&"ui_accept")
	play.expect(not SaveGame.has_slot(2), "the second Delete press deletes")
	await play.physics_frames(1)
	row = page.find_child("Slot2", true, false)
	play.expect(row != null and row.get_node_or_null(^"Delete") == null, "the row is empty now")
	play.expect(play.focused() != null, "focus stays on the page after deleting")

	# Pick slot 1.
	var expected: Dictionary = _slot_nodes(1)
	counter = 0
	pick = page.find_child("Slot1", true, false).get_node(^"Pick") as Button
	pick.grab_focus()
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	await play.tap(&"ui_accept")
	args = await play.wait_watched(loaded, 300)
	play.expect(args == [1], "picking slot 1 loads it")
	await play.physics_frames(2)
	play.expect(not Menus.is_open() and not tree.paused, "loading closes the menus and resumes")
	_expect_restored(changed, expected, "after loading from the page")
	_expect_extra(SAVED_COUNTER, "after loading from the page")


func _check_client() -> void:
	Game.set_session(ClientSession.new())
	play.expect(not SaveGame.can_save(), "an online client cannot save")
	play.expect(SaveGame.save_slot(1) == ERR_UNAUTHORIZED, "save_slot refuses a client")
	var failed: Dictionary = play.watch(SaveGame.load_failed)
	play.expect(SaveGame.load_slot(1) == ERR_UNAUTHORIZED, "load_slot refuses a client")
	var args: Array = await play.wait_watched(failed, 5)
	play.expect(args == [1, SaveGame.REASON_CLIENT], "a client is told only the host can load")
	SaveGame.request_autosave()
	play.expect(not SaveGame.autosave_pending(), "a client never autosaves")
	Menus.open_pause()
	var page: Control = Menus.current_page()
	play.expect(page != null and page.name == &"PauseMenu", "a client opens the pause menu")
	if page != null:
		for id: String in ["save", "load"]:
			var button: Button = page.find_child(id, true, false) as Button
			play.expect(button != null and button.disabled, "%s is disabled for a client" % id)
	Menus.close_all()
	Game.set_session(GameSession.new())
	play.expect(SaveGame.can_save(), "offline again, saving is allowed")


func _check_continue(changed: PackedStringArray) -> void:
	var latest: int = SaveGame.latest_slot()
	play.expect(latest >= 0, "there is a slot to continue from")
	var expected: Dictionary = _slot_nodes(latest)
	var opened: Dictionary = play.watch(GameEvents.menu_opened)
	Game.quit_to_menu()
	await play.wait_watched(opened, 120)
	play.expect(Menus.current_menu() == &"main", "the main menu opens")
	play.expect(play.focused_text() == "Continue", "Continue comes first in the main menu")
	var page: Control = Menus.current_page()
	if page != null:
		play.expect_min_text(page, "main menu")
	counter = 0
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	await play.tap(&"ui_accept")
	var args: Array = await play.wait_watched(loaded, 300)
	play.expect(args == [latest], "Continue loads the latest slot")
	await play.physics_frames(2)
	_expect_restored(changed, expected, "after Continue")
	_expect_extra(SAVED_COUNTER, "after Continue")


## Once the level ends nothing saves (Save, autosave, checkpoints), and a save
## an older build made at that moment loads into the result or a game that
## goes on, never a level nobody can play.
func _check_level_over() -> void:
	await play.physics_frames(5)
	play.expect(not SaveGame.level_over(), "a running level is not over")
	SaveGame.request_autosave(true)
	play.expect(SaveGame.autosave_pending(), "an autosave is waiting before the level ends")
	if steps != null and steps.has_method(&"finish"):
		await steps.finish(play)
	else:
		GameEvents.level_failed.emit("save_load_test")
	await play.physics_frames(2)
	play.expect(SaveGame.level_over(), "the level is over once it says so")
	play.expect(not SaveGame.autosave_pending(), "the end of the level drops a waiting autosave")
	var ended: Dictionary = Game.capture_state()
	var saved: Dictionary = play.watch(SaveGame.saved)
	play.expect(SaveGame.save_slot(1) == ERR_UNAVAILABLE, "nothing is saved once the level is over")
	play.expect(SaveGame.autosave() == ERR_UNAVAILABLE, "no autosave once the level is over")
	SaveGame.request_autosave()
	play.expect(not SaveGame.autosave_pending(), "autosave requests wait for the next level")
	GameEvents.checkpoint_reached.emit("save_load_test")
	await play.physics_frames(5)
	_stop(saved)
	play.expect(play.fire_count(saved) == 0, "a checkpoint after the end does not autosave")
	play.expect(
		SaveGame.save_blocked_reason() == SaveGame.REASON_LEVEL_OVER, "the reason is the level end"
	)
	var entry: Dictionary = _entry(&"pause", &"save")
	play.expect(not bool((entry["enabled"] as Callable).call()), "Save is disabled after the end")
	play.expect(
		str((entry["reason"] as Callable).call()) == SaveGame.REASON_LEVEL_OVER,
		"the Save entry says why"
	)
	# Pause on the result screen (where the game lets it open) shows the same.
	await play.tap(&"pause")
	await play.physics_frames(2)
	var page: Control = Menus.current_page()
	if Menus.current_menu() == &"pause" and page != null:
		var button: Button = page.find_child("save", true, false) as Button
		play.expect(button != null and button.disabled, "the pause menu's Save is disabled")
		var why: Label = page.find_child("save_reason", true, false) as Label
		play.expect(
			why != null and why.text == SaveGame.REASON_LEVEL_OVER, "the pause menu says why"
		)
		var load_button: Button = page.find_child("load", true, false) as Button
		play.expect(load_button != null and not load_button.disabled, "Load still works")
		Menus.close_all()

	# A save made at that moment by a build that still allowed it.
	var envelope: Dictionary = _read_json(SaveGame.slot_path(1))
	var nodes: Dictionary = {}
	for id: Variant in ended:
		nodes[str(id)] = JSON.from_native(ended[id])
	envelope["nodes"] = nodes
	_write_text(SaveGame.slot_path(2), JSON.stringify(envelope))
	play.expect(not bool(SaveGame.slot_info(2)["damaged"]), "the end-of-level save is loadable")
	var loaded: Dictionary = play.watch(SaveGame.loaded)
	play.expect(SaveGame.load_slot(2) == OK, "the end-of-level save loads")
	var args: Array = await play.wait_watched(loaded, 300)
	play.expect(args == [2], "loaded(2) fires")
	await play.physics_frames(3)
	var level: Node = Game.current_level()
	play.expect(level != null, "a level runs after loading the end-of-level save")
	if level == null:
		return
	play.expect(
		SaveGame.level_over() or _playable(),
		"the end-of-level save shows the result or plays on (not a stuck level)"
	)
	if steps != null and steps.has_method(&"result_shown"):
		play.expect(
			SaveGame.level_over() and bool(steps.result_shown(level)),
			"the end-of-level save shows the result screen again"
		)
	var started: Dictionary = play.watch(GameEvents.level_started)
	Game.restart_level()
	await play.wait_watched(started, 300)
	await play.physics_frames(2)
	play.expect(not SaveGame.level_over(), "a restart clears the end")
	play.expect(SaveGame.save_slot(1) == OK, "saving works again after a restart")
	SaveGame.delete_slot(2)


## Every player can be steered (none is down or frozen by an end screen).
func _playable() -> bool:
	for player: Node in Game.players():
		if "controls_enabled" in player and not bool(player.get(&"controls_enabled")):
			return false
		if player.has_method(&"is_alive") and not bool(player.call(&"is_alive")):
			return false
	return true


func _check_delete_all() -> void:
	for slot: int in SaveGame.SLOTS:
		if SaveGame.has_slot(slot):
			play.expect(SaveGame.delete_slot(slot) == OK, "slot %d deletes" % slot)
	play.expect(SaveGame.delete_slot(1) == ERR_FILE_NOT_FOUND, "deleting an empty slot says so")
	play.expect(SaveGame.latest_slot() == -1, "nothing left to continue from")
	play.expect(_entry(&"main", &"continue").is_empty(), "Continue goes with the last save")
	for file_name: String in DirAccess.get_files_at(SaveGame.directory):
		play.expect(not file_name.begins_with("slot_"), "%s is gone" % file_name)


# --- helpers ---------------------------------------------------------------------

func _change() -> PackedStringArray:
	if steps == null:
		return PackedStringArray()
	var changed: PackedStringArray = await steps.change(play)
	return changed


func _on_level_started(level: Node) -> void:
	if steps != null and steps.has_method(&"prepare"):
		steps.prepare(level)


func _capture_extra() -> Dictionary:
	return {"counter": counter, "marker": marker}


func _restore_extra(data: Variant) -> void:
	if data is Dictionary:
		counter = int((data as Dictionary).get("counter", 0))
		var at: Variant = (data as Dictionary).get("marker", Vector2.ZERO)
		marker = at if at is Vector2 else Vector2.ZERO


func _expect_extra(value: int, where: String) -> void:
	play.expect(counter == value, "extra data comes back %s (%d, got %d)" % [where, value, counter])
	play.expect(marker.is_equal_approx(SAVED_MARKER), "a saved vector comes back %s" % where)


func _expect_restored(changed: PackedStringArray, expected: Dictionary, where: String) -> void:
	var now: Dictionary = Game.capture_state()
	for id: String in changed:
		play.expect(
			expected.has(id) and now.has(id) and _same(expected[id], now[id]),
			"%s is restored %s (saved %s, now %s)" % [id, where, expected.get(id), now.get(id)]
		)


func _entry(menu: StringName, id: StringName) -> Dictionary:
	for entry: Dictionary in Menus.entries(menu):
		if entry["id"] == id:
			return entry
	return {}


## The decoded `{save_id: state}` of a slot file.
func _slot_nodes(slot: int) -> Dictionary:
	return _decode(_read_json(SaveGame.slot_path(slot)).get("nodes", {}))


func _stop(record: Dictionary) -> void:
	var signal_ref: Signal = record["signal"]
	var catch: Variant = record.get("callable")
	if catch is Callable and signal_ref.is_connected(catch):
		signal_ref.disconnect(catch)
	# The recorder captures `record`; dropping it breaks the cycle.
	record.erase("callable")


static func _decode(map: Variant) -> Dictionary:
	var decoded: Dictionary = {}
	if map is Dictionary:
		for key: Variant in map:
			decoded[str(key)] = JSON.to_native(map[key])
	return decoded


static func _read_json(path: String) -> Dictionary:
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	return parsed if parsed is Dictionary else {}


static func _write_text(path: String, text: String) -> void:
	var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
	if file != null:
		file.store_string(text)
		file.close()


## `good` with one key replaced, as JSON text.
static func _variant(good: Dictionary, key: String, value: Variant) -> String:
	var copy: Dictionary = good.duplicate(true)
	copy[key] = value
	return JSON.stringify(copy)


static func _number(value: Variant) -> float:
	return float(value) if value is float or value is int else -1.0


## Equal, with numbers and vectors within TOLERANCE (JSON turns ints into
## floats, and a frame or two may pass between saving and comparing).
static func _same(a: Variant, b: Variant) -> bool:
	var numeric: Array[int] = [TYPE_INT, TYPE_FLOAT]
	if typeof(a) in numeric and typeof(b) in numeric:
		return absf(float(a) - float(b)) <= TOLERANCE
	var vectors: Array[int] = [TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I]
	if typeof(a) in vectors and typeof(a) == typeof(b):
		return _same_list(_components(a), _components(b))
	if a is Dictionary and b is Dictionary:
		var left: Dictionary = a
		var right: Dictionary = b
		if left.size() != right.size():
			return false
		for key: Variant in left:
			if not right.has(key) or not _same(left[key], right[key]):
				return false
		return true
	var list_a: Variant = _as_list(a)
	var list_b: Variant = _as_list(b)
	if list_a != null and list_b != null:
		return _same_list(list_a, list_b)
	return a == b


static func _same_list(a: Array, b: Array) -> bool:
	if a.size() != b.size():
		return false
	for index: int in a.size():
		if not _same(a[index], b[index]):
			return false
	return true


static func _components(vector: Variant) -> Array:
	var parts: Array = [vector.x, vector.y]
	if typeof(vector) in [TYPE_VECTOR3, TYPE_VECTOR3I]:
		parts.append(vector.z)
	return parts


## Arrays and packed arrays as a plain Array; null for anything else.
static func _as_list(value: Variant) -> Variant:
	if value is Array:
		return value
	if typeof(value) >= TYPE_PACKED_BYTE_ARRAY and typeof(value) < TYPE_MAX:
		var items: Array = []
		for item: Variant in value:
			items.append(item)
		return items
	return null
