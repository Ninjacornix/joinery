# gdlint: disable=max-public-methods
class_name PlayHarness
extends RefCounted
## Drives a game from a headless `--script` test: boots the main scene, injects
## input through the real input map, waits for signals and collects failures.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/game/play_test.gd
##        -- --autostart --settings-path=/tmp/settings.cfg
##
## The `--script` file is compiled before the autoloads exist, so it cannot name
## `Game`, `Menus`, `Settings` or `GameEvents` directly. Use `play.game`,
## `play.menus`, `play.settings` and `play.events` there instead; scripts the
## test loads later (and every game script) can use the autoload names.

var tree: SceneTree
var name: String
var failures: PackedStringArray = PackedStringArray()
## The core autoloads, for use from the `--script` test file.
var game: Node
var menus: Node
var settings: Node
var events: Node


func _init(scene_tree: SceneTree, test_name: String) -> void:
	tree = scene_tree
	name = test_name
	game = tree.root.get_node_or_null(^"Game")
	menus = tree.root.get_node_or_null(^"Menus")
	settings = tree.root.get_node_or_null(^"Settings")
	events = tree.root.get_node_or_null(^"GameEvents")
	expect(game != null and menus != null, "the core autoloads are loaded")


## Loads the main scene. With `autostart` it waits for the level to start;
## without, for the main menu to open.
func boot(autostart: bool = true) -> void:
	game.set(&"autostart", autostart)
	var main_scene: String = str(
		ProjectSettings.get_setting("application/run/main_scene", "res://core/main.tscn")
	)
	var waited: Signal = events.level_started if autostart else events.menu_opened
	var error: Error = tree.change_scene_to_file(main_scene)
	expect(error == OK, "main scene %s loads" % main_scene)
	await wait_for(waited, 600)


func press(action: StringName) -> void:
	_send_action(action, true)


func release(action: StringName) -> void:
	_send_action(action, false)


## Holds `action` for `frames` physics frames, then releases it.
func hold(action: StringName, frames: int) -> void:
	press(action)
	await physics_frames(frames)
	release(action)


## Presses for 2 physics frames, releases, and lets one more frame run.
func tap(action: StringName) -> void:
	press(action)
	await physics_frames(2)
	release(action)
	await physics_frames(1)


func mouse_motion(relative: Vector2) -> void:
	var event: InputEventMouseMotion = InputEventMouseMotion.new()
	var at: Vector2 = tree.root.get_mouse_position() + relative
	event.position = at
	event.global_position = at
	event.relative = relative
	event.screen_relative = relative
	_send(event)


## Clicks at `position` in viewport coordinates (press, one frame, release).
func click(position: Vector2, button: int = MOUSE_BUTTON_LEFT) -> void:
	var motion: InputEventMouseMotion = InputEventMouseMotion.new()
	motion.position = position
	motion.global_position = position
	_send(motion)
	for pressed: bool in [true, false]:
		var event: InputEventMouseButton = InputEventMouseButton.new()
		event.button_index = button as MouseButton
		event.position = position
		event.global_position = position
		event.pressed = pressed
		event.button_mask = ((1 << (button - 1)) if pressed else 0) as MouseButtonMask
		_send(event)
		await physics_frames(1)


## Clicks the centre of a visible Control.
func click_control(control: Control, button: int = MOUSE_BUTTON_LEFT) -> void:
	var rect: Rect2 = control.get_global_rect()
	var at: Vector2 = control.get_viewport().get_screen_transform() * rect.get_center()
	await click(at, button)


func physics_frames(count: int) -> void:
	for _i: int in count:
		await tree.physics_frame


func process_frames(count: int) -> void:
	for _i: int in count:
		await tree.process_frame


## Waits until `signal_ref` fires (returns its arguments) or `max_frames`
## physics frames pass (records a failure, returns []).
func wait_for(signal_ref: Signal, max_frames: int) -> Array:
	return await wait_watched(watch(signal_ref), max_frames)


## Starts recording `signal_ref` now, so input sent afterwards cannot fire it
## before the test waits. Read with `wait_watched`, `fired` or `fire_count`.
func watch(signal_ref: Signal) -> Dictionary:
	var record: Dictionary = {"signal": signal_ref, "count": 0, "args": []}
	var catch: Callable = func(...args: Array) -> void:
		record["count"] = int(record["count"]) + 1
		if int(record["count"]) == 1:
			record["args"] = args
	record["callable"] = catch
	signal_ref.connect(catch)
	return record


## Waits until a watched signal has fired (returns its first arguments) or
## `max_frames` physics frames pass (records a failure, returns []).
func wait_watched(record: Dictionary, max_frames: int) -> Array:
	var frames: int = 0
	while int(record["count"]) == 0 and frames < max_frames:
		await tree.physics_frame
		frames += 1
	unwatch(record)
	var signal_ref: Signal = record["signal"]
	expect(
		int(record["count"]) > 0,
		"signal %s fired within %d frames" % [signal_ref.get_name(), max_frames]
	)
	return record["args"]


## Stops recording a watched signal. A record read only with `fired` or
## `fire_count` must be passed here when the test is done with it: the
## recorder and the record point at each other, and Godot reports the pair as
## leaked at exit.
func unwatch(record: Dictionary) -> void:
	if not record.has("callable"):
		return
	var signal_ref: Signal = record["signal"]
	var catch: Callable = record["callable"]
	if is_instance_valid(signal_ref.get_object()) and signal_ref.is_connected(catch):
		signal_ref.disconnect(catch)
	record.erase("callable")


func fire_count(record: Dictionary) -> int:
	return int(record["count"])


func expect(condition: bool, message: String) -> void:
	if not condition:
		failures.append(message)
		print("FAIL: %s" % message)


func expect_near(a: float, b: float, tol: float, message: String) -> void:
	expect(absf(a - b) <= tol, "%s (got %s, expected %s ± %s)" % [message, a, b, tol])


## The control that has keyboard focus, or null.
func focused() -> Control:
	return tree.root.gui_get_focus_owner()


## Text of the focused button, or "".
func focused_text() -> String:
	var control: Control = focused()
	if control is Button:
		return (control as Button).text
	return ""


## Presses `ui_down` until the focused button reads `text` (at most `limit`
## steps). Returns whether it got there.
func focus_button(text: String, limit: int = 12) -> bool:
	for _i: int in limit:
		if focused_text() == text:
			return true
		await tap(&"ui_down")
	return focused_text() == text


## Every visible Label, Button and LineEdit under `root` is at least the
## project's minimum text size (`UiStyle.TEXT_MIN` at 1080p).
func expect_min_text(root: Node, where: String) -> void:
	var minimum: int = UiStyle.min_text_px()
	for node: Node in _visible_text_controls(root):
		var size: int = (node as Control).get_theme_font_size(&"font_size")
		expect(
			size >= minimum,
			"%s: %s uses %d px text, below %d" % [where, node.get_path(), size, minimum]
		)


func finish() -> void:
	if failures.is_empty():
		print("%s: ok" % name)
		tree.quit(0)
	else:
		print("%s: %d failure(s)" % [name, failures.size()])
		tree.quit(1)


func _visible_text_controls(root: Node) -> Array[Node]:
	var found: Array[Node] = []
	if root is CanvasItem and not (root as CanvasItem).is_visible_in_tree():
		return found
	if root is Label or root is Button or root is LineEdit:
		found.append(root)
	for child: Node in root.get_children():
		found.append_array(_visible_text_controls(child))
	return found


func _send_action(action: StringName, pressed: bool) -> void:
	if pressed:
		Input.action_press(action)
	else:
		Input.action_release(action)
	var event: InputEventAction = InputEventAction.new()
	event.action = action
	event.pressed = pressed
	event.strength = 1.0 if pressed else 0.0
	_send(event)


func _send(event: InputEvent) -> void:
	Input.parse_input_event(event)
	Input.flush_buffered_events()
